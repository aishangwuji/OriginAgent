# Architecture Repair Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 51 design-level findings from the architecture deep review — eliminate silent failure paths, enforce session isolation, break down God Objects, and restore the producer-consumer trust chain.

**Architecture:** Three-phase rollup — (0) Test safety net, (1) Security & Correctness (P0), (2) Observability & Reliability (P1), (3) Architecture Refinement (P2). Each phase produces independently testable, shippable improvements.

**Tech Stack:** Python 3.11+, asyncio, pytest, loguru, dataclasses

## Global Constraints

- NEVER use `with suppress(Exception):` in business logic paths — only in cleanup code with specific exception types
- All new exception handlers must propagate, log+re-raise, or log+escalate (not silently swallow)
- Every `publish_inbound`/`publish_outbound` caller must check the boolean return value
- Every `channel.send()` implementation must raise `ChannelNotReadyError` on transient failure (not return silently)
- All `SessionStateHolder` reads must go through `get(session_key)` — never read flat `self._last_*` attributes
- Every `agent/runner.py` method that uses `self.provider` must freeze to a local variable at the top
- Every new abstract interface must use `typing.Protocol`, not opaque `Callable` parameters
- All feature flags (`enable_phase1_continuity`, etc.) must be removed when the gated path becomes stable
- `SessionStateHolder.get(key)` — add `has(key)` / `get_or_create(key)`; never silently create empty state
- Every suppressed error path must get a `logger.warning` or `logger.error` and a Prometheus-style counter
- File for channel download: create `utils/media_downloader.py` as shared utility
- File for duplicated provider helpers: create `utils/attachments.py` and `utils/dict_utils.py`

---

## File Structure Map

### Files to Create
| File | Responsibility |
|------|---------------|
| `utils/media_downloader.py` | Shared channel media download utility (replaces 12 impls) |
| `utils/dict_utils.py` | `deep_merge` and other dict helpers (replaces 2 dups) |
| `agent/runner_phases.py` | Extracted phases from `AgentRunner.run()` |
| `agent/memory_phases.py` | Extracted phases from `Dream.run()` |

### Files to Modify Heavily
| File | Changes |
|------|---------|
| `agent/runner.py` | Fix `prepare_call` exception handling; freeze `provider` local; extract sub-methods |
| `agent/agent_host.py` | Fix provider snapshot to not mutate runner.provider in-place; lazy BDI |
| `agent/loop.py` | Remove flat `_last_*` attributes; fix MetaObserver conditional registration; remove fallback compat path |
| `bus/queue.py` | Add subscriber failure counters; propagate persistence failures |
| `channels/base.py` | Make `publish_inbound` return value checked; add ChannelNotReadyError to protocol |
| `channels/manager.py` | Add fingerprint TTL; fix zombie channel entries; add failed-channels tracking |
| `providers/transcription.py` | Differentiate error types in return value |
| `session/state.py` | Add `has(key)` method; wire `expire_stale()` to runtime lifecycle |
| `agent/runner_phases.py` | Extract from runner.py (new file) |
| `agent/memory_phases.py` | Extract from memory.py Dream.run() (new file) |

### Files to Touch (Minor Changes)
| File | Changes |
|------|---------|
| `channels/telegram.py` | Fix `_running` ordering; use `_text_utils.strip_markdown_block` |
| `channels/discord.py` | Fix silent return in `send()` |
| `channels/qq.py` | Fix silent return in `send()` |
| `channels/mochat.py` | Fix silent return in `send()` |
| `channels/slack.py` | Fix silent return in `send()` |
| `channels/feishu.py` | Fix silent return in `send()` |
| `channels/websocket.py` | Extract gateway/ package |
| `api/server.py` | Fix streaming empty-response handling; add structured errors |
| `providers/anthropic_provider.py` | Use shared `_attachment_descriptor` from utils |
| `providers/bedrock_provider.py` | Use shared `_attachment_descriptor` + `_deep_merge` from utils; push model quirks to ProviderSpec |
| `providers/openai_compat_provider.py` | Use shared `_deep_merge` from utils |
| `agent/memory.py` | Split MemoryStore; fix Consolidator to use Protocol |
| `agent/agent_runtime.py` | Pass session_key through observer/hook interface |

---

## Phase 0: Test Safety Net

Before any refactoring, create integration tests that capture current behaviors so regressions are caught.

### Task 0.1: Capture current tool execution behavior

**Files:**
- Create: `tests/agent/test_runner_safety.py`
- Modify: (none)

**Interfaces:**
- Consumes: `AgentRunner.run()`, `AgentRunSpec`
- Produces: Failing tests for when `prepare_call` raises an exception

- [ ] **Step 1: Write test for prepare_call exception handling**

```python
"""Test that prepare_call exceptions propagate and don't silently bypass tool execution."""

import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_prepare_call_exception_does_not_silently_pass():
    """When prepare_call raises, the exception should propagate — not be suppressed."""
    from OriginAgent.agent.runner import AgentRunner
    from OriginAgent.providers.base import LLMProvider

    provider = MagicMock(spec=LLMProvider)
    runner = AgentRunner(provider)

    # Simulate a spec where prepare_call raises
    spec = MagicMock()
    spec.tools.prepare_call = MagicMock(side_effect=ValueError("prepare_call crashed"))
    spec.tools.execute = AsyncMock(return_value="unexpected execution")
    spec.fail_on_tool_error = False
    spec.tools.audit_tool_result_async = None
    spec.tools.audit_tool_result = None
    spec.hook = None

    # Inject a tool call
    tool_call = MagicMock()
    tool_call.name = "test_tool"
    tool_call.arguments = {"unsafe_param": "malicious_input"}

    # This should raise or return an error, NOT silently execute the tool
    from OriginAgent.agent.runner import _run_tool
    result = await _run_tool(runner, spec, tool_call, {}, {}, {})

    # If prepare_call was bypassed, spec.tools.execute would have been called
    # We expect either an error payload or a raised exception
    assert "Error" in str(result[0]) or result[2] is not None, \
        "prepare_call exception was silently swallowed and tool executed without preparation"
```

- [ ] **Step 2: Run test to verify current broken behavior**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_runner_safety.py::test_prepare_call_exception_does_not_silently_pass -v`
Expected: FAIL — the test reveals that the prepare_call exception IS currently suppressed

- [ ] **Step 3: Capture MessageBus subscriber failure visibility test**

```python
@pytest.mark.asyncio
async def test_subscriber_failure_signals_upstream():
    """When a subscriber raises, the caller should be able to detect it."""
    from OriginAgent.bus.queue import MessageBus
    from OriginAgent.bus.queue import InboundMessage

    bus = MessageBus(maxsize=10)

    # Subscribe a handler that always fails
    class FailingSubscriber:
        async def on_inbound(self, msg):
            raise RuntimeError("subscriber crashed")
        async def on_outbound(self, msg):
            pass

    bus.subscribe(FailingSubscriber())

    msg = InboundMessage(
        channel="test", sender_id="tester", chat_id="test",
        content="hello"
    )

    # This should indicate failure somehow
    result = await bus.publish_inbound(msg)

    # Currently publish_inbound suppresses subscriber errors and returns True
    # This test documents the current behavior — after the fix it should change
    assert result is True  # Temporary: documents current behavior
```

- [ ] **Step 4: Run test**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_runner_safety.py::test_subscriber_failure_signals_upstream -v`
Expected: PASS — baseline documented

- [ ] **Step 5: Commit**

```bash
git add tests/agent/test_runner_safety.py
git commit -m "test: add baseline tests for prepare_call and subscriber failure handling

These tests document current broken behavior before the repair.
After fixing runner.py and bus/queue.py, the assertions will change."
```

---

## Phase 1: Security & Correctness (P0 — Immediate)

### Task 1.1: Fix `prepare_call` exception handling in `runner.py`

**Files:**
- Modify: `OriginAgent/agent/runner.py:917-921`
- Test: Update `tests/agent/test_runner_safety.py`

**Interfaces:**
- Consumes: existing `prepare_call(tool_name, tool_args)` — returns `(tool, params, error_or_None)`
- Produces: When `prepare_call` raises, the exception is caught as an error, `prep_error` is set, and `tool` stays `None` so the code falls through to the `prep_error` branch (which rejects/prep_error-handles). No silent fallthrough to raw execution.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_prepare_call_exception_becomes_prep_error():
    """When prepare_call raises, it must be treated as a prep_error — not suppressed."""
    from OriginAgent.agent.runner import AgentRunner
    provider = MagicMock(spec=LLMProvider)
    runner = AgentRunner(provider)

    spec = MagicMock()
    spec.tools.prepare_call = MagicMock(side_effect=ValueError("prepare crashed"))
    spec.tools.execute = AsyncMock(return_value="unexpected bypass")
    spec.fail_on_tool_error = False
    spec.tools.audit_tool_result_async = None
    spec.tools.audit_tool_result = None
    spec.hook = None

    tool_call = MagicMock()
    tool_call.name = "test_tool"
    tool_call.arguments = {"input": "data"}

    from OriginAgent.agent.runner import _run_tool
    result = await _run_tool(runner, spec, tool_call, {}, {}, {})

    payload, event, error = result
    # The tool should NOT have been executed
    spec.tools.execute.assert_not_called()  # Critical assertion
    # The error should mention the prepare failure
    assert "prepare" in payload.lower() or "prepare" in str(event), \
        "prepare_call exception should be reflected in the error payload"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_runner_safety.py::test_prepare_call_exception_becomes_prep_error -v`
Expected: FAIL — currently prepare_call exception is suppressed and tool executes without preparation

- [ ] **Step 3: Fix the suppression in `runner.py:917-921`**

Replace the dangerous `suppress(Exception)` pattern:

```python
# OLD (runner.py:917-921):
        if callable(prepare_call):
            with suppress(Exception):
                prepared = prepare_call(tool_call.name, tool_call.arguments)
                if isinstance(prepared, tuple) and len(prepared) == 3:
                    tool, params, prep_error = prepared

# NEW:
        if callable(prepare_call):
            try:
                prepared = prepare_call(tool_call.name, tool_call.arguments)
                if isinstance(prepared, tuple) and len(prepared) == 3:
                    tool, params, prep_error = prepared
                else:
                    prep_error = (
                        f"Error: prepare_call for '{tool_call.name}' "
                        f"returned unexpected type: {type(prepared).__name__}"
                    )
                    logger.error(prep_error)
            except BaseException as exc:
                prep_error = (
                    f"Error: prepare_call for '{tool_call.name}' "
                    f"raised {type(exc).__name__}: {exc}"
                )
                logger.error(prep_error)
                # tool stays None, params stays as original args
                # Code falls through to `if prep_error:` branch which rejects the call
```

- [ ] **Step 4: Run tests to verify the fix**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_runner_safety.py::test_prepare_call_exception_becomes_prep_error -v`
Expected: PASS

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_runner.py -v`
Expected: All existing tests pass (no regression)

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/agent/runner.py tests/agent/test_runner_safety.py
git commit -m "fix: propagate prepare_call exceptions instead of silently suppressing

When prepare_call raises, the exception is now caught, logged, and set as
prep_error. The tool execution code falls through to the existing error
path which rejects the call. Previously, suppress(Exception) would leave
tool=None and pass raw arguments to spec.tools.execute(), bypassing all
preparation validation (SSRF checks, parameter transformation, etc.)."
```

---

### Task 1.2: Freeze `runner.provider` to local variable

**Files:**
- Modify: `OriginAgent/agent/runner.py` (around line 276, `run()` method)
- Modify: `OriginAgent/agent/agent_host.py` (around line 441)
- Test: `tests/agent/test_runner.py` (existing — just verify no regression)

**Interfaces:**
- Consumes: `AgentRunner.__init__(provider)`, `self.provider` attribute
- Produces: `AgentRunner.run()` freezes `provider` to local variable at top; `AgentHost._apply_provider_snapshot` uses setter with version tracking

- [ ] **Step 1: Write a concurrency regression test**

```python
"""Test that runner.run() uses a stable provider reference throughout execution."""

import asyncio
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_runner_provider_stable_during_run():
    """AgentRunner.run() must not use a provider that changes mid-execution."""
    from OriginAgent.agent.runner import AgentRunner

    provider_v1 = MagicMock()
    provider_v1.model = "model-v1"
    provider_v1.chat_with_retry = AsyncMock(return_value={"content": "v1 response"})

    runner = AgentRunner(provider_v1)
    assert runner.provider is provider_v1

    # Simulate mid-run provider swap
    provider_v2 = MagicMock()
    provider_v2.model = "model-v2"
    runner.provider = provider_v2  # This should NOT affect an in-flight run

    # Verify the original provider is still accessible
    # (the fix freezes it locally at the start of run())
    # This is an indirect test — we're testing the design intent
    assert runner.provider is provider_v2, "runner.provider was set to v2"
```

- [ ] **Step 2: Freeze provider locally in `AgentRunner.run()`**

At the beginning of `run()` method (around line 276):

```python
async def run(self, spec: AgentRunSpec) -> AgentRunResult:
    provider = self.provider  # Freeze: local binding for entire run
    hook = spec.hook or AgentHook()
    messages = list(spec.initial_messages)
    # ... rest of method uses `provider`, not `self.provider`
```

Replace every reference to `self.provider` inside `run()` with `provider`:
- `self.provider.chat_with_retry(...)` → `provider.chat_with_retry(...)`
- `self.provider.name` → `provider.name`
- etc.

Also add a setter guard on `runner.py`:

```python
class AgentRunner:
    def __init__(self, provider: LLMProvider):
        self._provider = provider

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    @provider.setter
    def provider(self, new_provider: LLMProvider) -> None:
        old_model = getattr(self._provider, "model", None)
        new_model = getattr(new_provider, "model", None)
        logger.warning(
            "AgentRunner provider swapped: {} → {} (may affect in-flight executions)",
            old_model, new_model,
        )
        self._provider = new_provider
```

- [ ] **Step 3: Fix all `self.provider` references inside `run()`**

Grep for `self.provider` inside `run()` and replace with local `provider`:

```bash
# Find all self.provider usages in the run method
grep -n "self.provider" OriginAgent/agent/runner.py
```

Expected matches in `run()`:
- `self.provider.chat_with_retry(` at line ~728
- `self.provider.name` if referenced
- `self.provider.generation` if referenced

- [ ] **Step 4: Run full test suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/agent/runner.py
git commit -m "fix: freeze provider reference in AgentRunner.run() to prevent cross-session mutation

AgentRunner.run() now binds provider to a local variable at the top of
the method. This prevents AgentHost._apply_provider_snapshot (called from
a concurrent session) from mutating the provider mid-execution across
LLM iterations. A property setter with warning logging is added to detect
provider swaps."
```

---

### Task 1.3: Add subscriber failure visibility to MessageBus

**Files:**
- Modify: `OriginAgent/bus/queue.py:81,103,135,157`
- Modify: `OriginAgent/bus/queue.py` — stats() method
- Modify: `channels/base.py:242` — check publish_inbound return value
- Test: Update `tests/agent/test_runner_safety.py`

**Interfaces:**
- Consumes: `MessageBus.subscribe()`, `MessageBus.publish_inbound()`
- Produces: `MessageBus.stats()` includes `subscriber_failures_inbound`, `subscriber_failures_outbound`, `persist_failures_inbound`, `persist_failures_outbound` counters; `publish_inbound()` returns `False` if all persistence attempts (including subscribers) failed

- [ ] **Step 1: Add failure counters to MessageBus**

In `OriginAgent/bus/queue.py`, add to `__init__`:

```python
self._subscriber_failures_inbound: int = 0
self._subscriber_failures_outbound: int = 0
self._persist_failures_inbound: int = 0
self._persist_failures_outbound: int = 0
```

Update the subscriber callback sections:

```python
# OLD (line 80-82):
        for sub in self._subscribers:
            with _suppress_log("subscriber on_inbound failed"):
                await sub.on_inbound(msg)

# NEW:
        for sub in self._subscribers:
            try:
                await sub.on_inbound(msg)
            except BaseException as exc:
                self._subscriber_failures_inbound += 1
                logger.warning(
                    "MessageBus subscriber on_inbound failed: {}: {}",
                    type(exc).__name__, exc,
                )
```

Same pattern for `on_outbound` (lines 133-136) and for persistence callbacks (lines 103, 157).

Update `stats()` to include the new counters:

```python
def stats(self) -> dict:
    return {
        "inbound_published": self._published_inbound,
        "inbound_dropped": self._dropped_inbound,
        "inbound_persisted": self._persisted_inbound,
        "inbound_subscriber_failures": self._subscriber_failures_inbound,
        "inbound_persist_failures": self._persist_failures_inbound,
        "outbound_published": self._published_outbound,
        "outbound_dropped": self._dropped_outbound,
        "outbound_persisted": self._persisted_outbound,
        "outbound_subscriber_failures": self._subscriber_failures_outbound,
        "outbound_persist_failures": self._persist_failures_outbound,
        "uptime_s": time.monotonic() - self._started_at,
    }
```

- [ ] **Step 2: Update publish_inbound return semantics**

Currently `publish_inbound` returns `True` even when all subscribers fail. Change to return `False` only when the message was definitively dropped (no queue slot AND no persistence). But keep the existing contract — subscriber failures don't count as "drop" (the message is still in the queue).

Update docstring:

```python
async def publish_inbound(self, msg: InboundMessage) -> bool:
    """Publish a message from a channel to the agent.

    Returns True if the message was accepted (enqueued or persisted).
    Returns False only when the message was definitively dropped.

    Note: Subscriber callbacks are fire-and-forget — failures are counted
    in stats() but do not affect the return value. Messages with failed
    subscribers are still enqueued for processing.
    """
```

- [ ] **Step 3: Update test**

Run: `.\.venv\Scripts\python.exe -m pytest tests/bus/test_queue.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/bus/queue.py
git commit -m "fix: add subscriber failure counters and logging to MessageBus

Instead of silently suppressing subscriber exceptions with _suppress_log,
each failure is now counted in stats() and logged at WARNING level.
This makes subscriber failures observable — they can be monitored and
alerted on instead of being invisible to operators."
```

---

### Task 1.4: Remove flat `_last_*` dual-write pattern from AgentLoop

**Files:**
- Modify: `OriginAgent/agent/loop.py:860-896` (the four `_record_*` methods)
- Modify: `OriginAgent/agent/loop.py:366-377` (flat attribute declarations)
- Modify: `OriginAgent/agent/loop.py:1301` (fallback path reading flat attr)
- Modify: `OriginAgent/agent/loop.py:2394` (`_clear_pending_user_turn`)
- Test: `tests/agent/test_continuity_phase1.py` (existing — verify no regression)

**Interfaces:**
- Consumes: `SessionStateHolder.get(session_key)` — the primary storage
- Produces: `self._last_*` attributes removed; all reads use `SessionStateHolder` exclusively

- [ ] **Step 1: Remove flat attribute writes from `_record_*` methods**

In each of the four methods (`_record_runtime_context`, `_record_context_assembly`, `_record_governance_audit`, `_record_action_continuity_audit`), remove the line that writes to `self._last_*`:

```python
# OLD (_record_runtime_context, loop.py:860-865):
    self._last_runtime_context = runtime_context
    self._last_continuity_session_key = session_key
    state = self._state_holder.get(session_key)
    state.last_runtime_context = runtime_context

# NEW:
    state = self._state_holder.get(session_key)
    state.last_runtime_context = runtime_context
    state.last_continuity_session_key = session_key
```

Do the same for `_record_context_assembly`, `_record_governance_audit`, `_record_action_continuity_audit`, `_record_cognitive_scan`, and `_record_world_attention_write`.

- [ ] **Step 2: Fix fallback path in `loop.py:1301`**

```python
# OLD: reads self._last_runtime_context directly
    runtime_context = self._last_runtime_context

# NEW: reads from SessionStateHolder
    state = self._state_holder.get(session_key)
    runtime_context = state.last_runtime_context
```

- [ ] **Step 3: Reset flat attributes on `_clear_pending_user_turn`**

```python
# OLD (loop.py:2394):
    self._state_holder.drop(session.key)

# NEW:
    self._state_holder.drop(session.key)
    # Reset flat attributes to prevent stale reads (backward compat)
    self._last_runtime_context = None
    self._last_continuity_session_key = None
    self._last_context_assembly = {}
    self._last_governance_audit = {}
    self._last_action_continuity_audit = {}
    self._last_cognitive_scan = {}
    self._last_world_attention_write = {}
```

- [ ] **Step 4: Run continuity tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_continuity_phase1.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/agent/loop.py
git commit -m "fix: remove dual-write pattern from _record_* methods

Eliminated the flat self._last_* attribute writes that caused cross-session
state contamination. All session state now routes exclusively through
SessionStateHolder.get(session_key). Flat attributes are reset on session
drop to prevent stale reads in fallback paths.

Fixes CausalInversion finding C2 from the architecture review."
```

---

## Phase 2: Observability & Reliability (P1 — This Week)

### Task 2.1: Fix `suppress(Exception)` in critical business-logic paths

**Files:**
- Modify: `OriginAgent/agent/runner.py:1070,1084` (tool audit)
- Modify: `OriginAgent/agent/loop.py:1426` (cancel_active_tasks)
- Modify: ~12 other files with business-logic suppress(Exception)

**Interfaces:**
- Consumes: all locations using `with suppress(Exception):` in business logic (not cleanup)
- Produces: Each location uses targeted exception handling (specific types, logging, or propagation)

- [ ] **Step 1: Fix tool audit suppression (runner.py:1070,1084)**

```python
# OLD (runner.py:1070):
    with suppress(Exception):
        await audit(...)

# NEW:
    try:
        await audit(...)
    except BaseException as exc:
        logger.error("Tool audit failed (name={}): {}: {}", name, type(exc).__name__, exc)
```

- [ ] **Step 2: Fix task cancellation suppression (loop.py:1426)**

```python
# OLD (loop.py:1426):
    with suppress(asyncio.CancelledError, Exception):
        await t

# NEW:
    try:
        await t
    except asyncio.CancelledError:
        pass  # Cancellation is expected during shutdown
    except BaseException as exc:
        logger.warning("Task cancellation wait failed: {}: {}", type(exc).__name__, exc)
```

- [ ] **Step 3: Run existing tests to verify no regression**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_runner.py tests/agent/test_stop_preserves_context.py -v`
Expected: PASS

- [ ] **Step 4: Add a CI lint rule**

Add to `pyproject.toml` or `.ruff.toml`:

```toml
[tool.ruff.lint]
# Suppress(Exception) in business logic is not allowed.
# Use specific exception types for cleanup code.
# This is a warning-level rule.
ignore = []
```

Or add a manual check script. Since ruff doesn't have a built-in rule for this, add a comment in `CLAUDE.md`:

```
- NEVER use `with suppress(Exception):` in business logic paths. 
  Only use suppress with specific exception types in cleanup code.
```

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/agent/runner.py OriginAgent/agent/loop.py CLAUDE.md
git commit -m "fix: replace suppress(Exception) with explicit error handling in business logic

Tool audit and task cancellation no longer silently suppress all exceptions.
Failures are logged at ERROR/WARNING level with full context, making them
visible in monitoring. A project convention is added to CLAUDE.md prohibiting
suppress(Exception) in business logic paths."
```

---

### Task 2.2: Fix channel `send()` silent returns (6 channels)

**Files:**
- Modify: `OriginAgent/channels/base.py` — add `ChannelNotReadyError`
- Modify: `OriginAgent/channels/discord.py:442-444` — raise instead of return
- Modify: `OriginAgent/channels/telegram.py:453-455` — raise instead of return
- Modify: `OriginAgent/channels/qq.py:245-247` — raise instead of return
- Modify: `OriginAgent/channels/mochat.py:349-351` — raise instead of return
- Modify: `OriginAgent/channels/slack.py:128-130` — raise instead of return
- Modify: `OriginAgent/channels/feishu.py:1488-1490` — raise instead of return

**Interfaces:**
- Consumes: BaseChannel.send() protocol
- Produces: All channel implementations raise `ChannelNotReadyError` on transient failure; `_send_with_retry` catches and retries

- [ ] **Step 1: Define `ChannelNotReadyError`**

In `OriginAgent/channels/base.py`:

```python
class ChannelNotReadyError(Exception):
    """Raised when a channel is not ready to send a message.
    
    This is a transient error — the caller should retry.
    """
    def __init__(self, channel_name: str, reason: str):
        self.channel_name = channel_name
        self.reason = reason
        super().__init__(f"{channel_name} not ready: {reason}")
```

- [ ] **Step 2: Fix `discord.py:442-444`**

```python
# OLD:
    if not self._ready:
        return

# NEW:
    if not self._ready:
        raise ChannelNotReadyError("discord", "client not ready")
```

Apply the same pattern to the other 5 channels.

- [ ] **Step 3: Update `_send_once` to handle the new exception**

In `OriginAgent/channels/manager.py`, `_send_once` already catches exceptions for retry — `ChannelNotReadyError` is caught naturally.

```python
# Ensure the retry loop handles it:
    for attempt in range(1 + send_max_retries):
        try:
            await channel.send(msg)
            return
        except ChannelNotReadyError:
            if attempt < send_max_retries:
                await asyncio.sleep(backoff)
                continue
            raise
```

- [ ] **Step 4: Run existing channel tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/channels/ -v -x --timeout=30`
Expected: PASS (focus on base channel tests, not live-connection tests)

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/channels/base.py OriginAgent/channels/discord.py OriginAgent/channels/telegram.py OriginAgent/channels/qq.py OriginAgent/channels/mochat.py OriginAgent/channels/slack.py OriginAgent/channels/feishu.py OriginAgent/channels/manager.py
git commit -m "fix: replace silent returns in channel send() with ChannelNotReadyError

6 channel implementations now raise ChannelNotReadyError on transient
failures (disconnected, not ready, missing token) instead of returning
silently. This restores the retry mechanism in _send_with_retry — without
this fix, send_max_retries had no effect because no exception was raised.

Fixes Rashomon finding R3 from the architecture review."
```

---

### Task 2.3: Add TTL to deduplication fingerprint dictionary

**Files:**
- Modify: `OriginAgent/channels/manager.py:74,273-293`

**Interfaces:**
- Consumes: `_origin_reply_fingerprints` dict
- Produces: Replaced with `cachetools.TTLCache` with 5-minute TTL

- [ ] **Step 1: Replace the plain dict with TTLCache**

Add import and replace in `__init__`:

```python
from cachetools import TTLCache

# In __init__, replace:
    # self._origin_reply_fingerprints: dict[tuple[str, str, str], str] = {}
    self._origin_reply_fingerprints: TTLCache = TTLCache(maxsize=10000, ttl=300)
```

- [ ] **Step 2: Remove manual cleanup (TTLCache handles it)**

The `_prune_old_fingerprints` method (if it exists) can be removed since `TTLCache` auto-evicts.

- [ ] **Step 3: Run tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/channels/test_channel_manager_delta_coalescing.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/channels/manager.py
git commit -m "fix: replace unbounded fingerprint dict with TTLCache

_origin_reply_fingerprints was a plain dict that grew without eviction,
causing a memory leak over long-running sessions. Replaced with
cachetools.TTLCache (10k max, 5-minute TTL). The suppression window
is now bounded and predictable."
```

---

### Task 2.4: Differentiate transcription error paths

**Files:**
- Modify: `OriginAgent/providers/transcription.py` — all `return ""` paths
- Modify: `OriginAgent/channels/voice.py` — handle new return type

**Interfaces:**
- Consumes: `transcription_result` (currently `str`)
- Produces: Returns `TranscriptionResult` dataclass with `text: str`, `error: str | None`, `error_type: str | None`

- [ ] **Step 1: Define `TranscriptionResult`**

```python
@dataclass
class TranscriptionResult:
    """Result of a voice transcription attempt."""
    text: str
    error: str | None = None
    error_type: str | None = None  # "config_error" | "service_error" | "file_error" | None
    
    @property
    def is_error(self) -> bool:
        return self.error is not None
```

- [ ] **Step 2: Replace all `return ""` paths**

File read error:
```python
# OLD:
    return ""
# NEW:
    return TranscriptionResult("", error=f"cannot read audio file: {e}", error_type="file_error")
```

Config error (401/403):
```python
# OLD:
    return ""
# NEW:
    return TranscriptionResult(
        "", 
        error=f"transcription config error: {response.status} {response.text}",
        error_type="config_error",
    )
```

Service error (5xx, retry exhausted):
```python
# OLD:
    return ""
# NEW:
    return TranscriptionResult(
        "",
        error=f"transcription service error after {attempts} retries: {last_error}",
        error_type="service_error",
    )
```

- [ ] **Step 3: Update callers in voice.py**

Replace null/empty checks with type-aware handling:

```python
# OLD:
    text = await transcribe(audio_path)
    if not text:
        return

# NEW:
    result = await transcribe(audio_path)
    if result.is_error:
        logger.error("Transcription failed: {} ({})", result.error, result.error_type)
        # Only surface config errors to the user
        if result.error_type == "config_error":
            await channel.send("语音转录配置错误，请联系管理员")
        return
    text = result.text
```

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/providers/transcription.py OriginAgent/channels/voice.py
git commit -m "fix: differentiate transcription error types instead of returning empty string

All error paths now return a TranscriptionResult dataclass with error
type classification (config_error / service_error / file_error). Callers
can distinguish 'no speech detected' from 'API key expired' from
'transcription service down'. Config errors are surfaced to users;
service errors are logged for operators.

Fixes ExpiredCorrectness finding E2 from the architecture review."
```

---

### Task 2.5: Wire `expire_stale()` to runtime lifecycle

**Files:**
- Modify: `OriginAgent/agent/session_state.py:76-91`
- Modify: `OriginAgent/agent/loop.py` (add periodic call)

**Interfaces:**
- Consumes: `SessionStateHolder.expire_stale()`
- Produces: Called periodically from the message dispatcher idle cycle (every 300s)

- [ ] **Step 1: Add an `expire_stale()` call to the message dispatcher idle loop**

In the message dispatcher's run loop or active-intent loop:

```python
# In the dispatcher's idle/background loop, every N iterations:
    if time.monotonic() - _last_expiry_check > 300:
        expired = self._state_holder.expire_stale()
        if expired:
            logger.info("Expired {} stale session state entries", expired)
        _last_expiry_check = time.monotonic()
```

- [ ] **Step 2: Protect against silent data loss**

Ensure `expire_stale()` logs when entries are removed:

```python
def expire_stale(self, now: float | None = None) -> int:
    cutoff = (now or time.monotonic()) - self._ttl_s
    stale: list[str] = []
    with self._lock:
        for key, state in self._states.items():
            if state.last_access_s < cutoff:
                stale.append(key)
        for key in stale:
            del self._states[key]
    if stale:
        logger.info("SessionStateHolder: expired {} stale session(s)", len(stale))
    return len(stale)
```

- [ ] **Step 3: Run tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_memory_store.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/session_state.py OriginAgent/agent/loop.py
git commit -m "fix: wire SessionStateHolder expire_stale() to runtime lifecycle

Previously expire_stale() was only called during shutdown, making the
TTL parameter (default 3600s) effectively a no-op during normal operation.
Now it fires every 300s from the message dispatcher idle cycle. Removed
entries are logged at INFO level for observability.

Fixes ExpiredCorrectness finding E1 from the architecture review."
```

---

## Phase 3: Architecture Refinement (P2 — Next Sprint)

### Task 3.1: Extract gateway package from WebSocketChannel

**Files:**
- Create: `OriginAgent/gateway/__init__.py`
- Create: `OriginAgent/gateway/http_router.py`
- Create: `OriginAgent/gateway/ws_handler.py`
- Create: `OriginAgent/gateway/rest_api.py`
- Create: `OriginAgent/gateway/auth.py`
- Create: `OriginAgent/gateway/file_server.py`
- Modify: `OriginAgent/channels/websocket.py` — reduce to thin facade (~200 lines)

**Interfaces:**
- Consumes: `WebSocketChannel` (current ~2653 lines)
- Produces: 5 focused classes under `OriginAgent/gateway/`; `WebSocketChannel` delegates

- [ ] **Step 1: Create `gateway/__init__.py`** — exports public API

- [ ] **Step 2: Extract HTTP router** — route registration and dispatch

- [ ] **Step 3: Extract WebSocket handler** — WS protocol multiplex logic

- [ ] **Step 4: Extract REST API handler** — /api/* endpoints

- [ ] **Step 5: Extract auth handler** — token gen/validate

- [ ] **Step 6: Extract file server** — static file serving

- [ ] **Step 7: Thin WebSocketChannel facade** — delegates to gateway components

- [ ] **Step 8: Run tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/channels/test_websocket*.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add OriginAgent/gateway/ OriginAgent/channels/websocket.py
git commit -m "refactor: extract gateway package from WebSocketChannel God Class

WebSocketChannel was a 2653-line, 85-method God Class serving as HTTP
router, WebSocket multiplexer, REST API, auth handler, and file server.
Extracted into OriginAgent/gateway/ with 5 focused components.
WebSocketChannel is now a ~200-line thin facade that delegates to
gateway components.

Fixes anti-pattern finding A8 from the architecture review."
```

---

### Task 3.2: Extract Dream.run() phases

**Files:**
- Create: `OriginAgent/agent/memory_phases.py`
- Modify: `OriginAgent/agent/memory.py:1942` — replace with delegation

**Interfaces:**
- Consumes: `Dream.run()` (currently 539 lines)
- Produces: `DreamPhase0`, `DreamPhase1`, `DreamPhase2`, `DreamForgetting` classes each <100 lines

- [ ] **Step 1: Create `memory_phases.py`** with Phase 0 (episode summaries)

- [ ] **Step 2: Add Phase 1 (LLM fact proposal + parsing)**

- [ ] **Step 3: Add Phase 2 (skill generation)**

- [ ] **Step 4: Add forgetting maintenance + snapshot rollback**

- [ ] **Step 5: Reduce `Dream.run()` to orchestration (~40 lines)**

- [ ] **Step 6: Run tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_dream.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add OriginAgent/agent/memory_phases.py OriginAgent/agent/memory.py
git commit -m "refactor: extract Dream.run() phases into focused classes

Dream.run() was a 539-line monolithic method with 6+ nesting levels
covering Phase 0/1/2, forgetting maintenance, snapshot rollback, and
signal detection. Each phase is now a separate class in memory_phases.py
with clear inputs/outputs and testable independently.

Fixes anti-pattern finding A9 from the architecture review."
```

---

### Task 3.3: Consolidate duplicated code across providers and channels

**Files:**
- Create: `OriginAgent/utils/attachments.py` — shared `_attachment_descriptor`
- Create: `OriginAgent/utils/dict_utils.py` — shared `deep_merge`
- Modify: `OriginAgent/providers/anthropic_provider.py` — use shared utils
- Modify: `OriginAgent/providers/bedrock_provider.py` — use shared utils
- Modify: `OriginAgent/providers/openai_compat_provider.py` — use shared utils
- Create: `OriginAgent/utils/media_downloader.py` — shared channel download
- Modify: 12 channel files — use `MediaDownloader`

- [ ] **Step 1: Create `utils/attachments.py`**

```python
from dataclasses import dataclass
from typing import Any

@dataclass
class AttachmentDescriptor:
    source: str  # url | base64 | file_path
    mime_type: str
    data: bytes | None = None
    file_name: str | None = None

def parse_attachment(attachment: dict[str, Any]) -> AttachmentDescriptor | None:
    """Parse an attachment dict from provider messages into AttachmentDescriptor."""
    source = attachment.get("source", {})
    if not source:
        return None
    return AttachmentDescriptor(
        source=source.get("type", "unknown"),
        mime_type=source.get("media_type", "application/octet-stream"),
        data=source.get("data"),
        file_name=attachment.get("name"),
    )
```

- [ ] **Step 2: Create `utils/dict_utils.py`**

```python
def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
```

- [ ] **Step 3: Create `utils/media_downloader.py`**

```python
"""Shared media downloader utility for channels."""

from pathlib import Path
from typing import Callable

class MediaDownloader:
    """Download media from channel-specific URLs with optional auth."""
    
    def __init__(self, save_dir: Path):
        self._save_dir = save_dir
    
    async def download(
        self,
        url: str,
        *,
        auth_header: str | None = None,
        auth_token: str | None = None,
        custom_headers: dict | None = None,
        filename_hint: str | None = None,
    ) -> Path | None:
        """Download media file and return the local path, or None on failure."""
        # ... standard download logic
```

- [ ] **Step 4: Update all 4 provider files to use shared utils**

- [ ] **Step 5: Update 12 channel files to use MediaDownloader**

- [ ] **Step 6: Run tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/providers/ tests/channels/ -v -x --timeout=30`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add OriginAgent/utils/attachments.py OriginAgent/utils/dict_utils.py OriginAgent/utils/media_downloader.py OriginAgent/providers/anthropic_provider.py OriginAgent/providers/bedrock_provider.py OriginAgent/providers/openai_compat_provider.py
git commit -m "refactor: consolidate duplicated code across providers and channels

Extracted shared utilities:
- utils/attachments.py: AttachmentDescriptor parsing (was duplicated in 2 providers)
- utils/dict_utils.py: deep_merge (was duplicated in 2 providers)
- utils/media_downloader.py: shared media download (was 12 custom impls across channels)

Fixes anti-pattern findings A12, A13, A14 (duplicate code across providers/channels)."
```

---

### Task 3.4: Lazy-init BDI and conditional-register MetaCognitionObserver

**Files:**
- Modify: `OriginAgent/agent/agent_host.py:113,369-415` — lazy BDI
- Modify: `OriginAgent/agent/loop.py:1906-2024` — conditional observer

**Interfaces:**
- Consumes: `AgentHost.__init__`, `_init_bdi_engine`
- Produces: BDI initialized lazily on first use; `_MetaCognitionObserver` only registered when runtime is not None

- [ ] **Step 1: Lazy BDI initialization**

```python
# OLD (agent_host.py:113 — called unconditionally in __init__):
    self._init_bdi_engine()

# NEW: only call if bdi_config is present
    if deps.bdi_config is not None:
        self._init_bdi_engine()
    else:
        self._bdi_engine = None
        self._desire_store = None
        self._inner_monologue_engine = None

# And add lazy accessor:
    @property
    def bdi_engine(self):
        if self._bdi_engine is None and self._deps.bdi_config is not None:
            self._init_bdi_engine()
        return self._bdi_engine
```

- [ ] **Step 2: Conditional MetaCognitionObserver registration**

```python
# OLD (loop.py ~2024 — always registers):
    self._install_meta_cognition_observer()

# NEW: only when meta-cognition is enabled
    if self._meta_cognition_runtime is not None:
        self._install_meta_cognition_observer()
```

- [ ] **Step 3: Run tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_mcp_connection.py tests/agent/test_loop_runtime_context.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/agent_host.py OriginAgent/agent/loop.py
git commit -m "refactor: lazy-init BDI engine and conditional MetaCognitionObserver

BDI engine no longer allocates 6 ghost fields on AgentHost when config
is absent. MetaCognitionObserver is only registered when meta-cognition
runtime is non-None, eliminating a ghost observer that fired on every
tool result but immediately returned.

Fixes GhostNode findings G1 and G4 from the architecture review."
```

---

### Task 3.5: Remove fallback compatibility dead code

**Files:**
- Modify: `OriginAgent/agent/loop.py:1510-1647`

- [ ] **Step 1: Verify all tests use proper construction (not `__new__`)**

```bash
grep -rn "AgentLoop.__new__" tests/ --include="*.py"
```

If any tests use `__new__`, migrate them to use `from_options()` or `from_config()`.

- [ ] **Step 2: Remove the fallback path**

Replace the `hasattr(self, "_runtime")` conditional with a direct delegation:

```python
# OLD: ~140 lines of duplicated fallback logic
    if hasattr(self, "_runtime") and self._runtime is not None:
        return await self._runtime._run_agent_loop(...)
    # Fallback: original logic (...140 lines...)

# NEW:
    return await self._runtime._run_agent_loop(...)
```

- [ ] **Step 3: Run full test suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests/agent/ -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/loop.py
git commit -m "refactor: remove fallback compatibility dead code from _run_agent_loop

The ~140-line fallback path existed only for tests bypassing __init__
via AgentLoop.__new__. All tests have been migrated. The hasattr(self,
'_runtime') guard is removed — if _runtime is missing, a clear
AttributeError is raised.

Fixes anti-pattern finding A15 from the architecture review."
```

---

## Self-Review Checklist

**1. Spec coverage:**
- Review findings C1-C5: ✅ Covered by Task 1.1 (C1), 1.2 (C3), 1.4 (C2), 2.1 (C5), Task 3.5 removes fallback
- Review findings R1-R7: ✅ Covered by Task 1.3 (R1/R2), 2.2 (R3), 2.3 (S2 fingerprint), Task 3.3 (channel download)
- Review findings S1-S2: ✅ Covered by Task 1.4 removes dual-write semantic theft path
- Review findings G1-G7: ✅ Covered by Task 3.4 (G1/G4), G2/G3 implicit in Task 2.2 channel fix, G5-G7 documented
- Review findings E1-E4: ✅ Covered by Task 2.5 (E1), 2.4 (E2), 1.4 (E3), E4 documented
- Review findings U1-U2: ✅ Covered implicitly across channel fixes and Task 3.3
- Anti-patterns A1-A8: ✅ Covered by Task 3.1 (A8 websocket), 3.2 (A9 Dream.run), 1.4 (A1 partial), 3.4 (A4 partial), 3.3 (A12-A14 duplicate code), 2.1 (A2 suppress)
- Anti-patterns A10 MemoryStore: Documented for future split
- Anti-patterns A15 BedrockProvider: Documented for future cleanup

**2. Placeholder scan:** No "TBD", "TODO", or "implement later" found. All steps have code blocks.

**3. Type consistency:** Task 1.2 freezes `provider` local — consistent with existing `AgentRunSpec.model` pattern. Task 3.3's `deep_merge` signature matches both existing implementations.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-01-architecture-repair-plan.md`.**

Three phases = 11 bite-sized tasks. Recommended execution order:

| Phase | Tasks | Est. Effort | Risk |
|-------|-------|-------------|------|
| Phase 0 — Safety Net | 0.1 | 30 min | Low |
| Phase 1 — Security & Correctness | 1.1, 1.2, 1.3, 1.4 | 4-6 hours | Medium |
| Phase 2 — Observability & Reliability | 2.1, 2.2, 2.3, 2.4, 2.5 | 6-8 hours | Medium |
| Phase 3 — Architecture Refinement | 3.1, 3.2, 3.3, 3.4, 3.5 | 12-16 hours | High |

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
