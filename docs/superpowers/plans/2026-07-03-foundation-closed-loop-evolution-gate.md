# Foundation: Minimum Closed-Loop + Evolution Safety Gate + Ledger Migration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a one-command integration anchor ("did I break anything?") covering the complete turn pipeline, then use that safety net to (a) add a human-approval gate to evolution activation and (b) migrate the evolution ledger from JSONL+FileLock to SQLite with atomic hash chains.

**Architecture:** Three sequential phases with a strict dependency chain. Phase 1 builds the safety net (a single end-to-end integration test). Phase 2-a adds the manual-approval gate — **this is an intentional breaking change** (see impact assessment below). Phase 2-c creates a parallel SQLite ledger implementation + migration tool; production cutover is a separate follow-up step, not part of this plan. Each phase's tests become the regression net for the next.

**⚠️ Scope note:** Phase 2-c delivers a validated `SqliteEvolutionLedger` with migration tooling, but does NOT switch `EvolutionModuleActivator` or `EvolutionModuleManager` to use it. The JSONL ledger remains the production default. The cutover is deferred to a follow-up plan after the SQLite implementation has been soak-tested alongside JSONL.

**Tech Stack:** Python 3.11+, pytest asyncio auto, sqlite3 stdlib, filelock (existing), AgentRunner (existing mock patterns in test_runner.py)

## Global Constraints

- Python >= 3.11
- Follow PEP 8 via ruff (select E, F, I, N, W; ignore E501)
- Line length: 100
- Never run `ruff format` — destroys git blame
- Tests use pytest with `asyncio_mode = "auto"`
- All new code must have type annotations on function signatures
- Existing tests must continue to pass
- Use `.\.venv\Scripts\python.exe` as Python interpreter
- Use `--basetemp=.pytest_tmp` for pytest (avoids Windows tmp_path PermissionError)
- `EvolutionConfig` is a Pydantic Base model — new fields auto-serialize to JSON config
- `EvolutionLedger` has existing `append()` and `verify_chain()` that MUST be preserved

---

## Phase 1: Minimum Closed-Loop Integration Test (地基)

### Task 1: End-to-end turn pipeline integration test

**Files:**
- Create: `tests/integration/test_minimal_turn_pipeline.py`

**Interfaces:**
- Consumes: `AgentLoop`, `MessageBus`, `AgentRunner`, `AgentRunSpec` (existing patterns from `tests/agent/test_runner.py:45-58`)
- Produces: `test_single_session_single_turn_completes` — one test that proves "if this fails, I broke the core loop"

- [ ] **Step 1: Write the minimum closed-loop test**

```python
"""Minimum closed-loop integration test: single session, single turn.

This test is the ANCHOR.  If it fails, the core agent loop is broken.
It uses a mock LLM provider to avoid network calls, and disables all
optional subsystems (evolution, subagents, meta-cognition, cron).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from OriginAgent.bus.queue import MessageBus
from OriginAgent.providers.base import LLMResponse


@pytest.mark.asyncio
async def test_single_session_single_turn_completes(tmp_path):
    """One session, one user message, one LLM response — end to end.

    If this test fails, the core turn pipeline is broken.  No excuses.
    """
    from OriginAgent.agent.loop import AgentLoop

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    # Mock LLM: one call returns a text response, no tool calls
    async def chat(*, messages, **kwargs):
        return LLMResponse(
            content="Hello! I received your message.",
            tool_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    provider.chat_with_retry = chat

    with (
        patch("OriginAgent.agent.loop.ContextBuilder"),
        patch("OriginAgent.agent.loop.SessionManager") as MockSessions,
        patch("OriginAgent.agent.loop.SubagentManager") as MockSubMgr,
    ):
        MockSubMgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        MockSessions.return_value.get_or_create.return_value = MagicMock()

        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=tmp_path,
        )

        # Subscribe to outbound messages on the bus
        responses: list[dict] = []

        async def collect(event_type, message):
            if event_type == "outbound":
                responses.append(message)

        bus.subscribe("outbound", collect)

        # Send a user message through the bus
        from OriginAgent.agent.runner import InboundMessage

        msg = InboundMessage(
            channel="test",
            chat_id="test-chat-1",
            message_id="msg-1",
            session_key="test-session",
            text="Hello, agent!",
            sender_name="test-user",
        )

        await bus.publish("inbound", msg)

        # Give the agent loop time to process
        await asyncio.sleep(1.0)

        # Assert: an outbound response was emitted
        assert len(responses) >= 1, (
            f"Expected at least 1 outbound response, got {len(responses)}. "
            f"Messages: {responses}"
        )

        # Assert: the response contains the LLM's text
        outbound = responses[0]
        assert outbound.get("channel") == "test"
        assert outbound.get("chat_id") == "test-chat-1"
        content = outbound.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        assert "Hello" in str(content), (
            f"Expected response to contain greeting, got: {content}"
        )
```

- [ ] **Step 2: Run the test — verify it fails or passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_minimal_turn_pipeline.py -v --basetemp=.pytest_tmp --timeout=30`

Expected: Determined by actual behavior. If the test passes on first run, the anchor is established. If it fails, fix the minimal issues until it passes (the test exercises the real AgentLoop with mocked provider — any failure reveals a real integration contract issue that must be addressed).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_minimal_turn_pipeline.py
git commit -m "test: add minimum closed-loop integration test

Single session, single turn, mocked LLM provider.
This test is the anchor — if it fails, the core agent loop is broken.
No optional subsystems (evolution, subagents, cron) are exercised."
```

---

## Phase 2-a: Evolution Manual-Approval Gate

### ⚠️ Breaking Change Impact Assessment (MUST READ before Task 2)

`activate()` currently has signature `(self, artifact_digest, *, actor="user")`. Adding `require_manual_approval=True` as default + `approved_by` parameter means **every existing caller that doesn't pass `approved_by` will switch from "activation proceeds" to "activation rejected"**. This is deliberate — the whole point of the gate — but it must be accounted for.

**Call-site census (verified 2026-07-03):**

| File | Line | Current Call | Action Required |
|------|------|-------------|-----------------|
| `evolution/manager.py` | 321 | `.activate(artifact_digest, actor=actor)` | Add `approved_by` passthrough through `activate_module()` |
| `tests/evolution/test_activation.py` | 110,122,143,158,170,198,210,235,269,286,301,311,313,330 | `manager.activate_module(digest)` | Add `approved_by="test"` to all 14 calls (or add a module-level fixture) |
| `tests/evolution/test_telemetry.py` | 336 | `manager.activate_module(digest)` | Add `approved_by="test"` |
| `tests/evolution/test_identity_recovery.py` | 66 | `manager.activate_module(staged.artifact_digest)` | Add `approved_by="test"` |
| `tests/evolution/test_capability_gate.py` | 56 | `manager.activate_module(staged.artifact_digest)` | Add `approved_by="test"` |
| `tests/evolution/test_manual_approval_gate.py` | (new) | 3 test functions | Already designed to test both paths |

**Total: 1 production call + 20 test calls affected.** All must include `approved_by="test"` (or equivalent) to preserve existing behavior after the gate is added.

### Task 2: Add `require_manual_approval` to EvolutionConfig and gate activation

**Files:**
- Modify: `OriginAgent/config/schema.py:799-838` (EvolutionConfig)
- Modify: `OriginAgent/evolution/activation.py:73-100` (EvolutionModuleActivator.activate)
- Modify: `OriginAgent/evolution/manager.py:310-321` (EvolutionModuleManager.activate_module — add `approved_by` passthrough)
- Modify: `tests/evolution/test_activation.py` (all 14 calls — add `approved_by="test"`)
- Modify: `tests/evolution/test_telemetry.py:336` (add `approved_by="test"`)
- Modify: `tests/evolution/test_identity_recovery.py:66` (add `approved_by="test"`)
- Modify: `tests/evolution/test_capability_gate.py:56` (add `approved_by="test"`)
- Create: `tests/evolution/test_manual_approval_gate.py`

**Interfaces:**
- Consumes: `EvolutionConfig` (Pydantic Base), `EvolutionModuleActivator.activate()`, `EvolutionActivationResult`
- Produces: `EvolutionConfig.require_manual_approval: bool` (default True), gate check in `activate()`

- [ ] **Step 1: Add `require_manual_approval` field to `EvolutionConfig`**

In `OriginAgent/config/schema.py`, after line 808 (after `allow_manual_override`):

```python
    require_manual_approval: bool = Field(
        default=True,
        validation_alias=AliasChoices("requireManualApproval", "require_manual_approval"),
        serialization_alias="requireManualApproval",
    )
```

- [ ] **Step 2: Thread `approved_by` through `EvolutionModuleManager.activate_module()`**

In `OriginAgent/evolution/manager.py`, modify `activate_module` at line 310-321:

```python
    def activate_module(
        self,
        artifact_digest: str,
        *,
        actor: str = "user",
        approved_by: str | None = None,
    ) -> EvolutionActivationResult:
        return EvolutionModuleActivator(
            self.workspace,
            ledger=self.ledger,
            config_loader=self._config_loader,
            config_saver=self._config_saver,
        ).activate(artifact_digest, actor=actor, approved_by=approved_by)
```

- [ ] **Step 3: Update ALL existing test callers to pass `approved_by="test"`**

In `tests/evolution/test_activation.py`, add `approved_by="test"` to every `manager.activate_module()` call (14 occurrences). Example:
```python
# Before:
result = manager.activate_module(staged.artifact_digest)
# After:
result = manager.activate_module(staged.artifact_digest, approved_by="test")
```

Same change in:
- `tests/evolution/test_telemetry.py:336`
- `tests/evolution/test_identity_recovery.py:66`
- `tests/evolution/test_capability_gate.py:56`

Run to verify these tests still pass after the mechanical update:
```
.\.venv\Scripts\python.exe -m pytest tests/evolution/test_activation.py tests/evolution/test_telemetry.py tests/evolution/test_identity_recovery.py tests/evolution/test_capability_gate.py -v --basetemp=.pytest_tmp
```
Expected: Tests that called `activate_module` without `approved_by` now fail with "not enough arguments" or "rejected". After adding `approved_by="test"`, they should pass again (gate accepts provided approver).

- [ ] **Step 4: Add the approval gate to `EvolutionModuleActivator.activate()`**

In `OriginAgent/evolution/activation.py`, in the `activate` method after line 73 (after `with self._locked():`):

```python
    def activate(
        self, artifact_digest: str, *,
        actor: str = "user",
        approved_by: str | None = None,
    ) -> EvolutionActivationResult:
        """Activate a verified staged module.

        When require_manual_approval is True (default), `approved_by`
        must be a non-empty string identifying the human approver.
        System-initiated activations without human approval are rejected.
        """
        with self._locked():
            # ── Manual approval gate ─────────────────────────────────
            config = self._load_config()
            require_approval = (
                config.get("require_manual_approval", True)
                if config else True
            )
            if require_approval and not (approved_by and approved_by.strip()):
                return EvolutionActivationResult(
                    ok=False,
                    status="rejected",
                    artifact_digest=artifact_digest,
                    error=(
                        "Manual approval required. Set approved_by to the "
                        "identifier of the human who approved this activation, "
                        "or disable require_manual_approval in evolution config."
                    ),
                )

            metadata = self._read_activation_metadata(artifact_digest)
            # ... rest of existing logic unchanged ...
```

- [ ] **Step 5: Add `_load_config` helper if not present**

Check if `EvolutionModuleActivator` already has config loading. If the `_config_loader` callable exists, use it:

```python
    def _load_config(self) -> dict | None:
        """Load evolution config if a loader is available."""
        if self._config_loader is not None:
            try:
                config = self._config_loader()
                if hasattr(config, "model_dump"):
                    return config.model_dump()
                return config
            except Exception:
                return None
        return None
```

If `_config_loader` is None (activator constructed without one), treat config as unavailable → default `require_manual_approval=True`.

- [ ] **Step 6: Write the gate test**

Create `tests/evolution/test_manual_approval_gate.py`:

```python
"""Tests for the manual-approval gate in evolution activation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from OriginAgent.evolution.activation import (
    EvolutionActivationResult,
    EvolutionModuleActivator,
)
from OriginAgent.evolution.events import EventType, EvolutionEvent


def _make_staged_artifact(workspace: Path, *, module_id: str = "test-mod") -> str:
    """Create a minimal staged artifact and return its digest."""
    import hashlib

    from OriginAgent.evolution.package import compute_artifact_digest

    artifact_dir = workspace / "memory" / "evolution_staging" / "test-digest" / "artifact"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "evolution_manifest.yaml").write_text(
        f"schema_version: originagent.evolution.module.v1\n"
        f"module_id: {module_id}\n"
        f"module_type: skill\n"
        f"version: 1.0.0\n"
    )
    (artifact_dir / "SKILL.md").write_text("# Test\n")
    digest = compute_artifact_digest(artifact_dir)

    # Stage it under the hash-based directory
    real_staging = workspace / "memory" / "evolution_staging" / digest
    if real_staging != artifact_dir:
        import shutil
        if real_staging.exists():
            shutil.rmtree(real_staging)
        shutil.copytree(artifact_dir.parent, real_staging)

    return digest


class TestManualApprovalGate:
    def test_activation_rejected_when_approval_required_and_no_approver(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        digest = _make_staged_artifact(workspace)

        # Config loader that requires manual approval
        config_loader = MagicMock(return_value=MagicMock(
            model_dump=MagicMock(return_value={"require_manual_approval": True})
        ))

        activator = EvolutionModuleActivator(
            workspace=workspace,
            config_loader=config_loader,
        )

        result = activator.activate(digest, actor="system")

        assert result.ok is False
        assert result.status == "rejected"
        assert "Manual approval required" in result.error

    def test_activation_accepted_with_approver_provided(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        digest = _make_staged_artifact(workspace)

        config_loader = MagicMock(return_value=MagicMock(
            model_dump=MagicMock(return_value={"require_manual_approval": True})
        ))

        activator = EvolutionModuleActivator(
            workspace=workspace,
            config_loader=config_loader,
        )

        result = activator.activate(digest, actor="system", approved_by="admin@example.com")

        # With an approver, should proceed past the gate (may fail on
        # later checks like staging metadata — that's fine, the gate itself passed)
        assert result.status != "rejected"

    def test_approval_not_required_when_config_disabled(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        digest = _make_staged_artifact(workspace)

        config_loader = MagicMock(return_value=MagicMock(
            model_dump=MagicMock(return_value={"require_manual_approval": False})
        ))

        activator = EvolutionModuleActivator(
            workspace=workspace,
            config_loader=config_loader,
        )

        result = activator.activate(digest, actor="system")

        # Gate should let it through (may fail on staging metadata — fine)
        assert result.status != "rejected"
```

- [ ] **Step 7: Run tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evolution/test_manual_approval_gate.py tests/evolution/test_activation.py tests/evolution/ -v --basetemp=.pytest_tmp`

Expected: `test_activation_rejected_when_approval_required_and_no_approver` passes. Existing activation tests pass with `approved_by="test"`. **This run is the explicit verification that the breaking change is correctly absorbed — there will be red tests from un-updated callers if any were missed in Steps 2-3.**

- [ ] **Step 8: Commit**

```bash
git add OriginAgent/config/schema.py OriginAgent/evolution/activation.py tests/evolution/test_manual_approval_gate.py
git commit -m "feat: add manual-approval gate to evolution activation

Add require_manual_approval (default True) to EvolutionConfig.
EvolutionModuleActivator.activate() now accepts approved_by parameter
and rejects system-initiated activations without human approval.

The default is 'safe': even without explicit config, approval is
required. EvolutionConfig.require_manual_approval can be set to false
for automated environments that have their own approval pipeline."
```

---

## Phase 2-c: Evolution Ledger JSONL → SQLite Migration

### Task 3: SQLite-backed EvolutionLedger with hash chain integrity

**Files:**
- Create: `OriginAgent/evolution/ledger_sqlite.py`
- Modify: `OriginAgent/evolution/ledger.py` (add migration helper, keep existing JSONL path as fallback)
- Create: `tests/evolution/test_ledger_sqlite.py`

**Interfaces:**
- Consumes: `EvolutionEvent`, `EvolutionLedger` (existing class), `EvolutionIdentityStore`, `canonical_dump`, `compute_event_hash`
- Produces: `SqliteEvolutionLedger` with `append()`, `verify_chain()`, `migrate_from_jsonl()` — same API as `EvolutionLedger`

- [ ] **Step 1: Create `SqliteEvolutionLedger`**

Create `OriginAgent/evolution/ledger_sqlite.py`:

```python
"""SQLite-backed evolution ledger with atomic hash chain integrity.

Replaces the JSONL+FileLock pattern with SQLite transactions.
Hash chain computation stays in Python (identity_store.sign() is a Python
service) — SQLite provides only atomicity and durability guarantees.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from OriginAgent.evolution.events import EvolutionEvent
from OriginAgent.evolution.identity import EvolutionIdentityStore
from OriginAgent.evolution.ledger import (
    LEDGER_ROTATION_EVENT_THRESHOLD,
    EvolutionLedger,
    LedgerStatus,
    LedgerVerificationResult,
    canonical_dump,
    compute_event_hash,
)


class SqliteEvolutionLedger:
    """SQLite-backed append-only evolution event ledger.

    Maintains the same hash-chain integrity guarantees as the JSONL ledger:
    - Every event's hash depends on the previous event's hash
    - Signatures are verified via EvolutionIdentityStore
    - Atomic writes via SQLite BEGIN/COMMIT transactions
    """

    def __init__(
        self,
        workspace: Path,
        db_path: Path | None = None,
        identity_store: EvolutionIdentityStore | None = None,
        sign_events: bool = False,
    ) -> None:
        self.workspace = Path(workspace)
        memory_dir = self.workspace / "memory"
        self.db_path = (
            Path(db_path) if db_path is not None
            else memory_dir / "evolution_ledger.sqlite3"
        )
        self._sign_events = sign_events
        self._identity_store = identity_store
        self._conn: sqlite3.Connection | None = None

    # ── Connection management ───────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        self._ensure_schema(conn)
        self._conn = conn
        return self._conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA busy_timeout=10000")  # 10s retry under concurrent writes
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS evolution_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                actor_public_key TEXT NOT NULL DEFAULT '',
                artifact_digest TEXT NOT NULL DEFAULT '',
                previous_event_hash TEXT NOT NULL DEFAULT '',
                event_hash TEXT NOT NULL DEFAULT '',
                signature TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                CHECK(length(event_hash) > 0)
            ) STRICT;
            CREATE INDEX IF NOT EXISTS idx_events_type
                ON evolution_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_digest
                ON evolution_events(artifact_digest);
        """)
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Core API (same signatures as EvolutionLedger) ────────────────

    def append(self, event: EvolutionEvent) -> EvolutionEvent:
        """Append an event with hash chain integrity in a single transaction."""
        from dataclasses import replace

        conn = self._get_conn()
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            # Get previous hash
            row = conn.execute(
                "SELECT event_hash FROM evolution_events "
                "ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            previous_hash = row["event_hash"] if row is not None else None

            # Compute event hash
            identity_store = self._identity_store
            actor_public_key = event.actor_public_key
            if self._sign_events:
                identity_store = identity_store or EvolutionIdentityStore()
                actor_public_key = identity_store.public_key_b64

            event_with_previous = replace(
                event,
                actor_public_key=actor_public_key,
                previous_event_hash=previous_hash or "",
                event_hash="",
                signature="",
            )
            event_hash = compute_event_hash(event_with_previous.to_dict())
            event_to_write = replace(event_with_previous, event_hash=event_hash)

            signature = ""
            if self._sign_events and identity_store is not None:
                signature = identity_store.sign(event_hash.encode("utf-8"))
                event_to_write = replace(event_to_write, signature=signature)

            # Atomic INSERT
            conn.execute(
                """INSERT INTO evolution_events
                   (event_id, event_type, actor_public_key, artifact_digest,
                    previous_event_hash, event_hash, signature, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_to_write.event_id,
                    event_to_write.event_type.value
                    if hasattr(event_to_write.event_type, 'value')
                    else str(event_to_write.event_type),
                    event_to_write.actor_public_key,
                    event_to_write.artifact_digest,
                    event_to_write.previous_event_hash or "",
                    event_to_write.event_hash,
                    event_to_write.signature or "",
                    canonical_dump(event_to_write.to_dict()).decode("utf-8"),
                ),
            )

            return event_to_write

    def verify_chain(
        self, verify_signatures: bool = False
    ) -> LedgerVerificationResult:
        """Verify the full hash chain from SQLite.

        Same O(n) algorithm as JSONL ledger — reads all rows, validates
        each hash matches previous+payload.
        """
        conn = self._get_conn()
        previous_hash: str | None = None
        terminal_hash: str | None = None
        count = 0
        unsigned_count = 0
        invalid_signature_count = 0

        rows = conn.execute(
            "SELECT * FROM evolution_events ORDER BY rowid"
        ).fetchall()

        for row in rows:
            count += 1
            event = dict(row)

            if event.get("previous_event_hash") != previous_hash:
                return LedgerVerificationResult(
                    chain_integrity="broken",
                    event_count=count,
                    terminal_event_hash=terminal_hash,
                    broken_at_index=count - 1,
                    broken_line=count,
                    expected_hash=previous_hash,
                    actual_hash=event.get("previous_event_hash"),
                    error="previous_event_hash mismatch",
                    unsigned_event_count=unsigned_count,
                    invalid_signature_count=invalid_signature_count,
                )

            payload = json.loads(event.get("payload_json", "{}"))
            expected_hash = compute_event_hash(payload)
            if event.get("event_hash") != expected_hash:
                return LedgerVerificationResult(
                    chain_integrity="broken",
                    event_count=count,
                    terminal_event_hash=terminal_hash,
                    broken_at_index=count - 1,
                    broken_line=count,
                    expected_hash=expected_hash,
                    actual_hash=event.get("event_hash"),
                    error="event_hash mismatch",
                    unsigned_event_count=unsigned_count,
                    invalid_signature_count=invalid_signature_count,
                )

            if verify_signatures and event.get("signature"):
                if not self._verify_row_signature(event):
                    invalid_signature_count += 1
            elif not event.get("signature"):
                unsigned_count += 1

            previous_hash = event["event_hash"]
            terminal_hash = previous_hash

        return LedgerVerificationResult(
            chain_integrity="ok",
            event_count=count,
            terminal_event_hash=terminal_hash,
            unsigned_event_count=unsigned_count,
            invalid_signature_count=invalid_signature_count,
        )

    def _verify_row_signature(self, event: dict) -> bool:
        from OriginAgent.evolution.identity import verify_event_signature
        return verify_event_signature(event)

    def status(self, verify_signatures: bool = False) -> LedgerStatus:
        verification = self.verify_chain(verify_signatures=verify_signatures)
        return LedgerStatus(
            chain_integrity=verification.chain_integrity,
            event_count=verification.event_count,
            terminal_event_hash=verification.terminal_event_hash,
            event_path=str(self.db_path),
            rotation_recommended=(
                verification.event_count > LEDGER_ROTATION_EVENT_THRESHOLD
            ),
            unsigned_event_count=verification.unsigned_event_count,
            invalid_signature_count=verification.invalid_signature_count,
            error=verification.error,
        )

    # ── Migration ───────────────────────────────────────────────────

    @classmethod
    def migrate_from_jsonl(
        cls,
        workspace: Path,
        jsonl_path: Path | None = None,
        db_path: Path | None = None,
        *,
        identity_store: EvolutionIdentityStore | None = None,
        sign_events: bool = False,
    ) -> SqliteEvolutionLedger:
        """Create a SQLite ledger and import all events from a JSONL ledger.

        Verifies the JSONL hash chain before migration and aborts if broken.
        """
        from OriginAgent.evolution.events import EvolutionEvent

        jsonl_ledger = EvolutionLedger(
            workspace=workspace,
            event_path=jsonl_path,
            identity_store=identity_store,
            sign_events=sign_events,
        )
        sqlite_ledger = cls(
            workspace=workspace,
            db_path=db_path,
            identity_store=identity_store,
            sign_events=False,  # Don't re-sign during migration
        )

        # Verify source chain
        verification = jsonl_ledger.verify_chain(verify_signatures=True)
        if not verification.ok:
            raise ValueError(
                f"Source JSONL ledger chain is broken: {verification.error}"
            )

        # Import events
        if jsonl_ledger.event_path.exists():
            with jsonl_ledger.event_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    event = EvolutionEvent.from_dict(data)
                    sqlite_ledger.append(event)

        return sqlite_ledger
```

**Design note on schema columns vs `payload_json` redundancy:**

The `event_type`, `artifact_digest`, `previous_event_hash`, and `event_hash` columns exist alongside `payload_json` (which contains the full serialized event dict) for a specific reason: **future query methods**. In this version:
- `append()` writes all columns but only reads `previous_event_hash` via `ORDER BY rowid DESC LIMIT 1`
- `verify_chain()` **recomputes** hashes from `payload_json` (because `compute_event_hash()` requires the full dict)
- The indexes on `event_type` and `artifact_digest` are **intentionally pre-built** for follow-up `find_by_digest()` and `events_by_type()` query methods

This version delivers **atomicity and durability** (the write safety net). Filtered queries come in a follow-up.

- [ ] **Step 2: Write migration and integrity tests**

Create `tests/evolution/test_ledger_sqlite.py`:

```python
"""Tests for SQLite-backed evolution ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from OriginAgent.evolution.events import EventType, EvolutionEvent
from OriginAgent.evolution.ledger import EvolutionLedger
from OriginAgent.evolution.ledger_sqlite import SqliteEvolutionLedger


def _make_event(event_id: str, **overrides) -> EvolutionEvent:
    return EvolutionEvent(
        event_id=event_id,
        event_type=overrides.pop("event_type", EventType.MODULE_VERIFIED),
        actor_public_key=overrides.pop("actor_public_key", "test-key"),
        artifact_digest=overrides.pop("artifact_digest", f"digest-{event_id}"),
        payload=overrides.pop("payload", {"test": True}),
        **overrides,
    )


class TestSqliteLedgerAppendAndVerify:
    def test_empty_ledger_has_ok_chain(self, tmp_path: Path) -> None:
        ledger = SqliteEvolutionLedger(workspace=tmp_path)
        result = ledger.verify_chain()
        assert result.chain_integrity == "ok"
        assert result.event_count == 0

    def test_single_event_has_valid_chain(self, tmp_path: Path) -> None:
        ledger = SqliteEvolutionLedger(workspace=tmp_path)
        ledger.append(_make_event("evt-1"))
        result = ledger.verify_chain()
        assert result.chain_integrity == "ok"
        assert result.event_count == 1

    def test_three_events_form_valid_chain(self, tmp_path: Path) -> None:
        ledger = SqliteEvolutionLedger(workspace=tmp_path)
        ledger.append(_make_event("evt-1"))
        ledger.append(_make_event("evt-2"))
        ledger.append(_make_event("evt-3"))
        result = ledger.verify_chain()
        assert result.chain_integrity == "ok"
        assert result.event_count == 3

    def test_hash_chain_is_sequential(self, tmp_path: Path) -> None:
        ledger = SqliteEvolutionLedger(workspace=tmp_path)
        e1 = ledger.append(_make_event("evt-1"))
        e2 = ledger.append(_make_event("evt-2"))
        assert e2.previous_event_hash == e1.event_hash

    def test_close_and_reopen_preserves_data(self, tmp_path: Path) -> None:
        db_path = tmp_path / "ledger.db"
        ledger = SqliteEvolutionLedger(workspace=tmp_path, db_path=db_path)
        ledger.append(_make_event("evt-1"))
        ledger.append(_make_event("evt-2"))
        ledger.close()

        # Reopen
        ledger2 = SqliteEvolutionLedger(workspace=tmp_path, db_path=db_path)
        result = ledger2.verify_chain()
        assert result.chain_integrity == "ok"
        assert result.event_count == 2


class TestJsonlToSqliteMigration:
    def test_migration_preserves_hash_chain(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        memory = workspace / "memory"
        memory.mkdir(parents=True)
        jsonl_path = memory / "evolution_events.jsonl"

        # Populate JSONL ledger
        jsonl_ledger = EvolutionLedger(workspace=workspace, event_path=jsonl_path)
        jsonl_ledger.append(_make_event("evt-1"))
        jsonl_ledger.append(_make_event("evt-2"))
        jsonl_ledger.append(_make_event("evt-3"))

        # Verify source chain
        src_verification = jsonl_ledger.verify_chain()
        assert src_verification.chain_integrity == "ok"

        # Migrate
        db_path = memory / "evolution_ledger.sqlite3"
        sqlite_ledger = SqliteEvolutionLedger.migrate_from_jsonl(
            workspace=workspace,
            jsonl_path=jsonl_path,
            db_path=db_path,
        )

        # Verify migrated chain
        result = sqlite_ledger.verify_chain()
        assert result.chain_integrity == "ok"
        assert result.event_count == 3
        assert result.terminal_event_hash == src_verification.terminal_event_hash

    def test_migration_refuses_broken_chain(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        memory = workspace / "memory"
        memory.mkdir(parents=True)
        jsonl_path = memory / "evolution_events.jsonl"

        # Write two events with a broken hash chain
        e1 = _make_event("evt-1")
        e2 = _make_event("evt-2", previous_event_hash="wrong-hash")
        from OriginAgent.evolution.ledger import canonical_dump, compute_event_hash

        from dataclasses import replace

        # Event 1: valid
        h1 = compute_event_hash(e1.to_dict())
        e1_final = replace(e1, event_hash=h1)
        jsonl_path.write_text(
            canonical_dump(e1_final.to_dict()).decode("utf-8") + "\n",
            encoding="utf-8",
        )

        # Event 2: broken previous hash
        e2_broken = replace(e2, previous_event_hash="broken-chain-hash")
        h2 = compute_event_hash(e2_broken.to_dict())
        e2_final = replace(e2_broken, event_hash=h2)
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(canonical_dump(e2_final.to_dict()).decode("utf-8") + "\n")

        db_path = memory / "evolution_ledger.sqlite3"
        with pytest.raises(ValueError, match="Source JSONL ledger chain is broken"):
            SqliteEvolutionLedger.migrate_from_jsonl(
                workspace=workspace,
                jsonl_path=jsonl_path,
                db_path=db_path,
            )
```

- [ ] **Step 3: Run ledger tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evolution/test_ledger_sqlite.py -v --basetemp=.pytest_tmp`

Expected: 7/7 tests pass.

- [ ] **Step 4: Run full evolution test suite to check for regressions**

Run: `.\.venv\Scripts\python.exe -m pytest tests/evolution/ -v --basetemp=.pytest_tmp`

Expected: All existing tests pass. `SqliteEvolutionLedger` is a new class that doesn't modify `EvolutionLedger` — no existing tests are affected.

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/evolution/ledger_sqlite.py tests/evolution/test_ledger_sqlite.py
git commit -m "feat: add SQLite-backed evolution ledger with hash chain integrity

SqliteEvolutionLedger replaces the JSONL+FileLock pattern with SQLite
transactions. Hash chain computation remains in Python (identity_store.sign()
requires Python logic), while SQLite provides atomic BEGIN/COMMIT for
durability guarantees.

Includes migrate_from_jsonl() that verifies the source chain before
migration and aborts if broken. Same API surface as EvolutionLedger
(append, verify_chain, status)."
```

---

## Verification Checklist

After all phases complete:

```bash
# Phase 1 anchor test
.\.venv\Scripts\python.exe -m pytest tests/integration/test_minimal_turn_pipeline.py -v --basetemp=.pytest_tmp

# Phase 2-a approval gate
.\.venv\Scripts\python.exe -m pytest tests/evolution/test_manual_approval_gate.py -v --basetemp=.pytest_tmp

# Phase 2-c SQLite ledger
.\.venv\Scripts\python.exe -m pytest tests/evolution/test_ledger_sqlite.py -v --basetemp=.pytest_tmp

# Full evolution regression
.\.venv\Scripts\python.exe -m pytest tests/evolution/ -v --basetemp=.pytest_tmp

# Lint
.\.venv\Scripts\python.exe -m ruff check OriginAgent/evolution/ OriginAgent/config/schema.py
```

Expected: All green. Anchor test passes. Approval gate blocks. SQLite ledger chain verifies.

---

## Follow-up Work (not in this plan)

1. **Observability:** Structured logging for cognitive_loop, evolution activation, and subagent spawn events — the "you can't fix what you can't see" layer (between Phase 2 and Phase 3)
2. **Expand anchor test:** Add tool-call round-trip, error recovery path, and timeout handling
3. **Remaining JSONL stores:** Apply SQLite migration template to facts, BDI desires, work queue, etc.
4. **RuntimeDependencies type migration:** Incrementally convert `Any` fields to concrete types using the sub-container access paths
