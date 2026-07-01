# SessionStateHolder Extraction — Phase 1 of AgentLoop Decomposition

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract all mutable cross-turn scratchpad state from `AgentLoop` into a session-keyed `SessionStateHolder`, eliminating the "session A overwrites session B's audit logs" class of bugs while maintaining 100% backward compatibility with all external consumers.

**Architecture:** Create `SessionStateHolder` as a thread-safe, session-keyed state container. All 10 session-scoped state fields dual-write to both the existing `self._last_*` direct attributes (preserving 100% backward compatibility with `getattr(loop, "_last_*")` consumers) and the `SessionStateHolder` (enabling session-isolated internal reads). Internal turn-pipeline code paths read from the holder to eliminate cross-session contamination. Direct attributes are kept as compat shims, to be removed in a future phase.

**Tech Stack:** Python 3.11+, asyncio, `threading.Lock` for concurrent-safety, `dataclasses` for `SessionScopedState`.

## Global Constraints

- `AgentLoop.__init__`, `from_config`, `from_options`, `run()`, `process_direct()` signatures MUST NOT change
- All existing tests MUST pass without modification (zero test changes in this phase)
- `getattr(loop, "_last_runtime_context", None)` pattern used by `introspection/service.py` MUST continue to work
- `SimpleNamespace(_last_runtime_context=..., _last_continuity_session_key=...)` pattern used by `tests/tools/test_runtime_status_tools.py` MUST continue to work
- `_capability_snapshot` and `_current_iteration` field names are referenced in `tools/self.py:59` (READ_ONLY guard list) — keep their accessor names identical

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `OriginAgent/agent/session_state.py` | **Create** | `SessionScopedState` dataclass + `SessionStateHolder` class |
| `OriginAgent/agent/loop.py` | **Modify** | Integrate holder; replace `_record_*` writes; replace internal reads; add `_resolve_state_key()` helper |
| `OriginAgent/agent/agent_loop_components.py` | **No change** | Field defaults preserved for backward compat; cleanup deferred to future phase |
| `OriginAgent/agent/tools/self.py` | **No change** | Uses `getattr(self._loop, "_current_iteration", None)` — preserved |
| `OriginAgent/agent/introspection/service.py` | **No change** | Uses `getattr(loop, "_last_runtime_context", None)` — preserved |
| `OriginAgent/agent/action_summary.py` | **No change** | Uses `getattr(loop, "_cached_action_summary", None)` — preserved |
| `tests/` | **No change** | All tests pass without modification |

### Design Decision: Which fields move?

| Field | Scope | Move to Holder? | Reason |
|-------|-------|-----------------|--------|
| `_runtime_vars` | session | ✅ Yes | Scratchpad; cross-session contamination risk |
| `_last_runtime_context` | session | ✅ Yes | Core cross-session contamination bug |
| `_last_continuity_session_key` | session | ✅ Yes | Tied to runtime_context |
| `_last_context_assembly` | session | ✅ Yes | Audit trail; session-scoped |
| `_last_recovered_continuity_checkpoint` | session | ✅ Yes | Session recovery metadata |
| `_last_governance_audit` | session | ✅ Yes | Primary cross-contamination example |
| `_last_action_continuity_audit` | session | ✅ Yes | Action audit; session-scoped |
| `_cached_action_summary` | session | ✅ Yes | Derived from action audit |
| `_last_cognitive_scan` | session | ✅ Yes | Cognitive event audit |
| `_last_world_attention_write` | session | ✅ Yes | World state audit |
| `_capability_snapshot` | turn | ❌ Stay | Per-turn; set at `_run_agent_loop()` entry |
| `_current_iteration` | iteration | ❌ Stay | Per-iteration; set by runner hook callback |

The 10 fields moving to the holder are all session-scoped audit/tracking state. The 2 fields staying are turn/iteration-scoped and already correctly scoped to the active runner.

---

### Task 1: Create `SessionScopedState` and `SessionStateHolder`

**Files:**
- Create: `OriginAgent/agent/session_state.py`

**Interfaces:**
- Produces: `SessionScopedState` dataclass, `SessionStateHolder` class

- [ ] **Step 1: Create the module file**

```python
"""Session-scoped state holder for AgentLoop cross-turn scratchpad.

Replaces flat ``self._last_*`` fields on AgentLoop with a session-keyed
container so concurrent sessions cannot overwrite each other's audit trails.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionScopedState:
    """Mutable scratchpad state scoped to a single session key."""

    runtime_vars: dict[str, Any] = field(default_factory=dict)
    last_runtime_context: Any = None          # RuntimeContext | None
    last_continuity_session_key: str | None = None
    last_context_assembly: dict[str, Any] = field(default_factory=dict)
    last_recovered_continuity_checkpoint: dict[str, Any] = field(default_factory=dict)
    last_governance_audit: dict[str, Any] = field(default_factory=dict)
    last_action_continuity_audit: dict[str, Any] = field(default_factory=dict)
    cached_action_summary: dict[str, Any] = field(default_factory=dict)
    last_cognitive_scan: dict[str, Any] = field(default_factory=dict)
    last_world_attention_write: dict[str, Any] = field(default_factory=dict)

    # TTL tracking — updated on every get()
    last_access_s: float = field(default_factory=time.monotonic)


class SessionStateHolder:
    """Thread-safe container for per-session :class:`SessionScopedState`.

    Usage::

        holder = SessionStateHolder(ttl_s=3600.0)
        state = holder.get("cli:direct")
        state.last_runtime_context = ctx
        holder.drop("cli:direct")   # explicit cleanup
    """

    def __init__(self, ttl_s: float = 3600.0) -> None:
        self._states: dict[str, SessionScopedState] = {}
        self._lock = threading.Lock()
        self._ttl_s = ttl_s

    # ── core API ──────────────────────────────────────────────────────

    def get(self, session_key: str) -> SessionScopedState:
        """Return (creating if needed) the state for *session_key*.

        Uses double-checked locking so the common path (key exists) is
        lock-free.
        """
        state = self._states.get(session_key)
        if state is not None:
            state.last_access_s = time.monotonic()
            return state
        with self._lock:
            state = self._states.get(session_key)
            if state is None:
                state = SessionScopedState()
                self._states[session_key] = state
            return state

    def drop(self, session_key: str) -> None:
        """Remove state for *session_key* (no-op if absent)."""
        with self._lock:
            self._states.pop(session_key, None)

    # ── lifecycle ─────────────────────────────────────────────────────

    def expire_stale(self, now: float | None = None) -> int:
        """Remove entries whose ``last_access_s`` exceeds the TTL.

        Returns the number of entries removed.
        """
        if self._ttl_s <= 0:
            return 0
        cutoff = (now or time.monotonic()) - self._ttl_s
        stale: list[str] = []
        with self._lock:
            for key, state in self._states.items():
                if state.last_access_s < cutoff:
                    stale.append(key)
            for key in stale:
                del self._states[key]
        return len(stale)

    @property
    def size(self) -> int:
        """Current number of tracked sessions."""
        return len(self._states)
```

- [ ] **Step 2: Verify the module imports cleanly**

```bash
.\.venv\Scripts\python.exe -c "from OriginAgent.agent.session_state import SessionStateHolder, SessionScopedState; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add OriginAgent/agent/session_state.py
git commit -m "feat: add SessionStateHolder for session-scoped state isolation"
```

---

### Task 2: Wire SessionStateHolder into AgentLoop.__init__

**Files:**
- Modify: `OriginAgent/agent/loop.py:351-382` (the `__init__` section that sets `_last_*` fields)

**Interfaces:**
- Consumes: `SessionStateHolder` from `OriginAgent.agent.session_state`
- Produces: `self._state_holder` attribute on AgentLoop

- [ ] **Step 1: Add import**

In `OriginAgent/agent/loop.py`, add after the existing imports (around line 92):

```python
from OriginAgent.agent.session_state import SessionStateHolder, SessionScopedState
```

- [ ] **Step 2: Add `_resolve_state_key()` helper method**

Add this private method to `AgentLoop` (place near the `_record_*` methods, around line 833):

```python
def _resolve_state_key(self, session_key: str | None = None) -> str:
    """Resolve the effective session key for state-holder lookups.

    Falls back to ``_last_continuity_session_key`` when the caller
    doesn't pass an explicit key (backward-compat for pre-existing
    code paths).
    """
    if session_key:
        return session_key
    # Read from the *attribute* (set by _record_* methods as compat shim)
    return getattr(self, "_last_continuity_session_key", None) or "__default__"
```

- [ ] **Step 3: Replace the `_last_*` field initialization with holder creation**

In `AgentLoop.__init__`, replace lines 371-382:

```python
# BEFORE (lines 371-382):
        self._runtime_vars: dict[str, Any] = {}
        self._capability_snapshot: CapabilitySnapshot | None = None
        self._current_iteration: int = 0
        self._last_runtime_context: RuntimeContext | None = None
        self._last_continuity_session_key: str | None = None
        self._last_context_assembly: dict[str, Any] = {}
        self._last_recovered_continuity_checkpoint: dict[str, Any] = {}
        self._last_governance_audit: dict[str, Any] = {}
        self._last_action_continuity_audit: dict[str, Any] = {}
        self._cached_action_summary: dict[str, Any] = normalize_action_summary({})
        self._last_cognitive_scan: dict[str, Any] = {}
        self._last_world_attention_write: dict[str, Any] = {}

# AFTER:
        self._state_holder = SessionStateHolder(ttl_s=3600.0)
        # Session-scoped state — delegated to SessionStateHolder.
        # Direct attribute access is kept for backward compatibility
        # with getattr(loop, "_last_*") consumers; _record_* methods
        # write to both the attribute and the holder.
        self._runtime_vars: dict[str, Any] = {}
        self._capability_snapshot: CapabilitySnapshot | None = None
        self._current_iteration: int = 0
        self._last_runtime_context: RuntimeContext | None = None
        self._last_continuity_session_key: str | None = None
        self._last_context_assembly: dict[str, Any] = {}
        self._last_recovered_continuity_checkpoint: dict[str, Any] = {}
        self._last_governance_audit: dict[str, Any] = {}
        self._last_action_continuity_audit: dict[str, Any] = {}
        self._cached_action_summary: dict[str, Any] = normalize_action_summary({})
        self._last_cognitive_scan: dict[str, Any] = {}
        self._last_world_attention_write: dict[str, Any] = {}
```

Note: the initialization looks identical — the fields stay. The key change is adding `self._state_holder` and the `_resolve_state_key` helper. The actual migration of writes/reads happens in Tasks 3 and 4.

- [ ] **Step 4: Verify imports and init**

```bash
.\.venv\Scripts\python.exe -c "from OriginAgent.agent.loop import AgentLoop; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/agent/loop.py
git commit -m "feat: wire SessionStateHolder into AgentLoop.__init__"
```

---

### Task 3: Update `_record_*` methods to dual-write (attribute + holder)

**Files:**
- Modify: `OriginAgent/agent/loop.py:833-878` (all `_record_*` methods)

**Interfaces:**
- Consumes: `self._state_holder`
- Produces: dual-write behavior (backward-compat attribute + session-isolated holder)

**Strategy:** Each `_record_*` method currently writes to `self._last_foo`. We add a parallel write to `self._state_holder.get(session_key).last_foo`. The direct attribute write preserves backward compatibility for `getattr(loop, "_last_foo")` consumers. The holder write enables internal session-safe reads (Task 4).

- [ ] **Step 1: Update `_record_runtime_context` (lines 833-835)**

```python
# BEFORE:
    def _record_runtime_context(self, session_key: str, runtime_context: RuntimeContext) -> None:
        self._last_runtime_context = runtime_context
        self._last_continuity_session_key = session_key

# AFTER:
    def _record_runtime_context(self, session_key: str, runtime_context: RuntimeContext) -> None:
        self._last_runtime_context = runtime_context
        self._last_continuity_session_key = session_key
        state = self._state_holder.get(session_key)
        state.last_runtime_context = runtime_context
        state.last_continuity_session_key = session_key
```

- [ ] **Step 2: Update `_record_continuity_session_key` (lines 837-838)**

```python
# BEFORE:
    def _record_continuity_session_key(self, session_key: str) -> None:
        self._last_continuity_session_key = session_key

# AFTER:
    def _record_continuity_session_key(self, session_key: str) -> None:
        self._last_continuity_session_key = session_key
        self._state_holder.get(session_key).last_continuity_session_key = session_key
```

- [ ] **Step 3: Update `_record_context_assembly` (lines 840-841)**

```python
# BEFORE:
    def _record_context_assembly(self, payload: dict[str, Any]) -> None:
        self._last_context_assembly = dict(payload)

# AFTER:
    def _record_context_assembly(self, payload: dict[str, Any]) -> None:
        self._last_context_assembly = dict(payload)
        state_key = self._resolve_state_key()
        self._state_holder.get(state_key).last_context_assembly = dict(payload)
```

- [ ] **Step 4: Update `_record_recovered_continuity_checkpoint` (lines 843-847)**

```python
# BEFORE:
    def _record_recovered_continuity_checkpoint(
        self,
        checkpoint: dict[str, Any] | None,
    ) -> None:
        self._last_recovered_continuity_checkpoint = dict(checkpoint or {})

# AFTER:
    def _record_recovered_continuity_checkpoint(
        self,
        checkpoint: dict[str, Any] | None,
    ) -> None:
        self._last_recovered_continuity_checkpoint = dict(checkpoint or {})
        state_key = self._resolve_state_key()
        self._state_holder.get(state_key).last_recovered_continuity_checkpoint = dict(checkpoint or {})
```

- [ ] **Step 5: Update `_record_governance_audit` (lines 849-851)**

```python
# BEFORE:
    def _record_governance_audit(self, audit: dict[str, Any]) -> None:
        self._last_governance_audit = dict(audit)
        self.context._last_governance_audit = dict(audit)

# AFTER:
    def _record_governance_audit(self, audit: dict[str, Any]) -> None:
        self._last_governance_audit = dict(audit)
        self.context._last_governance_audit = dict(audit)
        state_key = self._resolve_state_key()
        self._state_holder.get(state_key).last_governance_audit = dict(audit)
```

- [ ] **Step 6: Update `_record_action_continuity_audit` (lines 853-855)**

```python
# BEFORE:
    def _record_action_continuity_audit(self, audit: dict[str, Any]) -> None:
        self._last_action_continuity_audit = dict(audit)
        self._cached_action_summary = normalize_action_summary(self._last_action_continuity_audit)

# AFTER:
    def _record_action_continuity_audit(self, audit: dict[str, Any]) -> None:
        self._last_action_continuity_audit = dict(audit)
        self._cached_action_summary = normalize_action_summary(self._last_action_continuity_audit)
        state_key = self._resolve_state_key()
        state = self._state_holder.get(state_key)
        state.last_action_continuity_audit = dict(audit)
        state.cached_action_summary = normalize_action_summary(dict(audit))
```

- [ ] **Step 7: Update `_record_cognitive_scan` (lines 877-878)**

```python
# BEFORE:
    def _record_cognitive_scan(self, payload: dict[str, Any]) -> None:
        self._last_cognitive_scan = dict(payload)

# AFTER:
    def _record_cognitive_scan(self, payload: dict[str, Any]) -> None:
        self._last_cognitive_scan = dict(payload)
        state_key = self._resolve_state_key()
        self._state_holder.get(state_key).last_cognitive_scan = dict(payload)
```

- [ ] **Step 8: Verify — run the quickest related tests**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/test_loop_automation_phase4.py -v --timeout=60
```

Expected: All tests pass (they read `loop._last_action_continuity_audit` and `loop._cached_action_summary` directly)

- [ ] **Step 9: Commit**

```bash
git add OriginAgent/agent/loop.py
git commit -m "feat: dual-write _record_* methods to SessionStateHolder"
```

---

### Task 4: Migrate internal reads in loop.py from direct attributes to holder

**Files:**
- Modify: `OriginAgent/agent/loop.py` (various lines — see steps)

**Interfaces:**
- Consumes: `self._state_holder.get(session_key)`
- Produces: session-isolated reads for all internal turn-pipeline code paths

**Strategy:** The key cross-session contamination risk is in methods that read `self._last_*` during turn processing. We migrate these reads to use `self._state_holder.get(session_key)` where `session_key` is available, keeping the direct attribute read as a fallback.

- [ ] **Step 1: Update `_build_initial_messages` — runtime_context reads (lines 1334, 1363, 1379, 1401, 1412)**

This method receives `session` (has `.key`) as a parameter. Replace `self._last_runtime_context` reads with holder reads:

```python
# In _build_initial_messages, around line 1300:
# The method already has access to session.key via the `session` parameter.

# Find ALL occurrences of self._last_runtime_context in this method (5 total)
# and replace with: self._state_holder.get(session.key).last_runtime_context

# Example — line 1334:
# BEFORE:
                    runtime_context=self._last_runtime_context,
# AFTER:
                    runtime_context=self._state_holder.get(session.key).last_runtime_context,

# Do the same for lines 1363, 1379, 1401, 1412
```

The complete replacement code (apply via Edit tool for each occurrence):

At **line 1334**: `runtime_context=self._last_runtime_context,` → `runtime_context=self._state_holder.get(session.key).last_runtime_context,`

At **line 1363**: `runtime_context=self._last_runtime_context,` → `runtime_context=self._state_holder.get(session.key).last_runtime_context,`

At **line 1379**: `runtime_context=self._last_runtime_context,` → `runtime_context=self._state_holder.get(session.key).last_runtime_context,`

At **line 1401**: `runtime_context=self._last_runtime_context,` → `runtime_context=self._state_holder.get(session.key).last_runtime_context,`

At **line 1412**: `runtime_context=self._last_runtime_context,` → `runtime_context=self._state_holder.get(session.key).last_runtime_context,`

- [ ] **Step 2: Update `_build_initial_messages` — `_last_context_assembly` writes (lines 1339, 1368, 1407-1409)**

Replace direct attribute writes with holder writes:

```python
# Line 1339:
# BEFORE:
                self._last_context_assembly = dict(assembled.audit)
# AFTER:
                self._state_holder.get(session.key).last_context_assembly = dict(assembled.audit)
                self._last_context_assembly = dict(assembled.audit)  # compat

# Line 1368:
# BEFORE:
            self._last_context_assembly = {
# AFTER:
            self._state_holder.get(session.key).last_context_assembly = {
            self._last_context_assembly = {  # compat (same value, set below)
# (Keep the existing block; add the holder write above it)

# Lines 1407-1409:
# BEFORE:
        self._last_context_assembly = dict(getattr(self.context, "_last_context_assembly_audit", {}) or {})
        if not self._last_context_assembly:
            self._last_context_assembly = self._snapshot_context_assembly_from_messages(
# AFTER:
        state = self._state_holder.get(session.key)
        state.last_context_assembly = dict(getattr(self.context, "_last_context_assembly_audit", {}) or {})
        if not state.last_context_assembly:
            state.last_context_assembly = self._snapshot_context_assembly_from_messages(
                built,
                session_key=session.key,
                runtime_context=state.last_runtime_context,
            )
        self._last_context_assembly = dict(state.last_context_assembly)  # compat
```

- [ ] **Step 3: Update `_maybe_apply_meta_fast_path` — `_last_runtime_context` read (line 2430)**

```python
# BEFORE (line 2430):
        runtime_context = getattr(self, "_last_runtime_context", None)

# AFTER:
        state = self._state_holder.get(session_key)
        runtime_context = state.last_runtime_context
```

- [ ] **Step 4: Update `_update_working_memory_from_turn` — `_last_world_attention_write` (lines 2092-2106)**

This method receives `session` as a parameter. Replace direct writes:

```python
# BEFORE (lines 2092, 2100):
            self._last_world_attention_write = {
                ...
            }
        else:
            self._last_world_attention_write = {
                ...
            }

# AFTER (both occurrences):
            state = self._state_holder.get(session.key)
            state.last_world_attention_write = {
                ...
            }
            self._last_world_attention_write = state.last_world_attention_write  # compat
        else:
            state = self._state_holder.get(session.key)
            state.last_world_attention_write = {
                ...
            }
            self._last_world_attention_write = state.last_world_attention_write  # compat
```

- [ ] **Step 5: Run full test suite to verify no regressions**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/ -v --timeout=120 -x
```

Expected: All tests pass. If any test fails, check that the compat attribute writes are still in place.

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/agent/loop.py
git commit -m "feat: migrate internal state reads to SessionStateHolder"
```

---

### Task 5: Add lifecycle cleanup — call `holder.drop()` at session-clear boundaries

**Files:**
- Modify: `OriginAgent/agent/loop.py` (two methods)

**Interfaces:**
- Consumes: `self._state_holder.drop(session_key)`
- Produces: automatic cleanup of stale session state

- [ ] **Step 1: Add `holder.drop()` to `_clear_pending_user_turn` (line 2924)**

```python
# BEFORE:
    def _clear_pending_user_turn(self, session: Session) -> None:
        AgentLoop._turn_persist_manager(self).clear_pending_user_turn(session)

# AFTER:
    def _clear_pending_user_turn(self, session: Session) -> None:
        AgentLoop._turn_persist_manager(self).clear_pending_user_turn(session)
        self._state_holder.drop(session.key)
```

- [ ] **Step 2: Add `holder.expire_stale()` call in `close_mcp` (around line 1793)**

`close_mcp` is the shutdown path. Add a stale-state cleanup before draining background tasks:

```python
# In close_mcp, after the active_intent_task cancel block (around line 1800):
# Add before the background_tasks drain:
        # Clean up stale session state before shutdown
        removed = self._state_holder.expire_stale()
        if removed > 0:
            logger.debug("SessionStateHolder: expired {} stale sessions during shutdown", removed)
```

- [ ] **Step 3: Verify — run cleanup-related tests**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/test_loop_runtime_status_tools.py -v --timeout=60
```

Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/loop.py
git commit -m "feat: add SessionStateHolder lifecycle cleanup on session clear and shutdown"
```

---

### Task 6: Remove redundant field declarations from agent_loop_components.py

**Files:**
- Modify: `OriginAgent/agent/agent_loop_components.py:182-191, 708-717`

**Interfaces:**
- Consumes: none (removal only)
- Produces: cleaner `LoopComponents` dataclass

**Strategy:** The `LoopComponents` dataclass declares defaults for the `_last_*` fields. Since `AgentLoop.__init__` now initializes these directly and they're backed by `SessionStateHolder`, the dataclass defaults are no longer needed. We keep the fields in the dataclass (so `setattr` from `build_loop_components` still works) but remove the explicit default values, letting them default to `None` / `{}`.

- [ ] **Step 1: Remove field declarations from `LoopComponents` (lines 182-191)**

```python
# BEFORE (lines 182-191 in agent_loop_components.py):
    _capability_snapshot: Any = None  # type: ignore[assignment]
    _current_iteration: int = 0
    _last_runtime_context: Any = None  # type: ignore[assignment]
    _last_continuity_session_key: Any = None  # type: ignore[assignment]
    _last_context_assembly: dict[str, Any] = field(default_factory=dict)
    _last_recovered_continuity_checkpoint: dict[str, Any] = field(default_factory=dict)
    _last_governance_audit: dict[str, Any] = field(default_factory=dict)
    _last_action_continuity_audit: dict[str, Any] = field(default_factory=dict)
    _cached_action_summary: dict[str, Any] = field(default_factory=dict)
    _last_cognitive_scan: dict[str, Any] = field(default_factory=dict)

# AFTER:
    # Session-scoped state fields are now managed by SessionStateHolder.
    # The fields remain declared (no default) so setattr from
    # build_loop_components still works; AgentLoop.__init__ initializes
    # them as compat shims.
    _capability_snapshot: Any = None  # type: ignore[assignment]
    _current_iteration: int = 0
    _last_runtime_context: Any = None  # type: ignore[assignment]
    _last_continuity_session_key: Any = None  # type: ignore[assignment]
    _last_context_assembly: dict[str, Any] = field(default_factory=dict)
    _last_recovered_continuity_checkpoint: dict[str, Any] = field(default_factory=dict)
    _last_governance_audit: dict[str, Any] = field(default_factory=dict)
    _last_action_continuity_audit: dict[str, Any] = field(default_factory=dict)
    _cached_action_summary: dict[str, Any] = field(default_factory=dict)
    _last_cognitive_scan: dict[str, Any] = field(default_factory=dict)
```

Wait — actually, these fields need to STAY with the same defaults because `build_loop_components` uses `setattr` to set them on AgentLoop, and the `_record_*` methods still write to the direct attributes for backward compat. The removal should only happen once we're ready to delete the direct attributes entirely. For now, **leave `agent_loop_components.py` unchanged**.

- [ ] **Step 1 (revised): Skip this task for now**

The `LoopComponents` dataclass defaults are still used by `build_loop_components` → `setattr(self, name, value)` in `AgentLoop.__init__`. Since we're keeping direct attribute writes as compat shims, the dataclass defaults must stay. This cleanup is deferred to a future phase when direct attributes are removed.

- [ ] **Step 2: Verify — run the full test suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/ tests/tools/test_runtime_status_tools.py -v --timeout=120
```

Expected: All tests pass.

- [ ] **Step 3: Commit (empty — no changes needed in this task)**

No commit needed; task is deferred.

---

### Task 7: Final verification — full test suite

**Files:**
- None (verification only)

- [ ] **Step 1: Run the complete agent test suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/ -v --timeout=120
```

Expected: ALL tests pass.

- [ ] **Step 2: Run introspection-related tests specifically**

```bash
.\.venv\Scripts\python.exe -m pytest tests/tools/test_runtime_status_tools.py tests/agent/test_loop_runtime_status_tools.py -v --timeout=120
```

Expected: ALL tests pass.

- [ ] **Step 3: Run continuity tests (heaviest consumer of `_last_*` fields)**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/test_continuity_phase1.py -v --timeout=120
```

Expected: ALL tests pass.

- [ ] **Step 4: Run automation tests (reads `_last_action_continuity_audit`)**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/test_loop_automation_phase4.py -v --timeout=120
```

Expected: ALL tests pass.

- [ ] **Step 5: Run self-tool tests (reads `_current_iteration`)**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/tools/test_self_tool.py -v --timeout=60
```

Expected: ALL tests pass.

- [ ] **Step 6: Verify with ruff**

```bash
ruff check OriginAgent/agent/session_state.py OriginAgent/agent/loop.py
```

Expected: No errors.

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "chore: final verification — all tests pass with SessionStateHolder"
```

---

## Future Phases (not in this plan)

1. **Remove compat shims** — once all internal consumers use the holder, delete the direct `_last_*` attribute writes from `__init__` and `_record_*` methods. Add `__getattr__` on AgentLoop to redirect legacy `getattr(loop, "_last_*")` to the holder.
2. **AgentHost extraction** — move MCP lifecycle, BDI engine, background task set, and provider management out of AgentLoop into a dedicated `AgentHost`.
3. **AgentRuntime extraction** — create a stateless `AgentRuntime` that receives `SessionStateHolder` as a dependency and processes messages without touching AgentLoop.

---
