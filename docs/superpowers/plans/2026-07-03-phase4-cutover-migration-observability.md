# Phase 4: Production Cutover + Migration Template + Observability

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch production evolution code to SQLite, establish a reusable JSONL→SQLite migration template (validated on 2 follower stores), and add structured observability so runtime behavior changes are visible before they become outages.

**Architecture:** Three independent workstreams that can run in parallel after Task 1. Task 1 (cutover) is the gate — once SQLite is live in evolution, the migration template has a proven reference, and observability instrumentation covers the cutover path. Tasks 2 and 3 then fan out independently.

**Tech Stack:** Python 3.11+, sqlite3 stdlib, loguru, contextvars (stdlib), pytest asyncio auto

## Global Constraints

- Python >= 3.11
- Follow PEP 8 via ruff (select E, F, I, N, W; ignore E501)
- Line length: 100
- Never run `ruff format`
- Tests use pytest with `asyncio_mode = "auto"`
- Use `.\.venv\Scripts\python.exe` as Python interpreter
- Use `--basetemp=.pytest_tmp` for pytest
- All new code must have type annotations
- `EvolutionLedger` API MUST remain unchanged for existing callers
- `anchor test` (`tests/integration/test_minimal_turn_pipeline.py`) MUST stay green through all tasks
- Full evolution test suite (`tests/evolution/`) MUST stay green
- `busy_timeout=10000` on all new SQLite connections

---

## Scope Boundaries

**IN SCOPE:**
- Switch 10 `EvolutionLedger()` construction sites to use a configurable backend (JSONL or SQLite, default SQLite)
- Create `OriginAgent/storage/jsonl_migration.py` with reusable migration patterns
- Migrate 2 follower stores: `FactEventStore` (append-only) and `DesireStore` (read-modify-write)
- Add contextvars-based trace context + structured logging to evolution, turn pipeline, cognitive loop, and subagent spawn paths

**OUT OF SCOPE (follow-up work):**
- Migrating all ~50 JSONL stores (plan covers template + 2 stores as proof of concept)
- opentelemetry SDK / external trace exporters (this plan delivers the foundational infrastructure)
- Dashboard / visualization of trace data
- Performance optimization of SQLite queries

---

## File Map

| File | Role | Task |
|------|------|------|
| `OriginAgent/evolution/activation.py` | Switch `EvolutionLedger` → configurable backend | 1 |
| `OriginAgent/evolution/manager.py` | Switch `EvolutionLedger` → configurable backend | 1 |
| `OriginAgent/evolution/capability_gate.py` | Switch `EvolutionLedger` → configurable backend | 1 |
| `OriginAgent/evolution/state_branch.py` | Switch `EvolutionLedger` → configurable backend | 1 |
| `OriginAgent/evolution/recovery.py` | Switch `EvolutionLedger` → configurable backend | 1 |
| `OriginAgent/evolution/telemetry.py` | Switch `EvolutionLedger` → configurable backend | 1 |
| `OriginAgent/evolution/memory_vault.py` | Switch `EvolutionLedger` → configurable backend | 1 |
| `OriginAgent/agent/curator.py:799` | Switch `EvolutionLedger` → configurable backend | 1 |
| `OriginAgent/agent/evolution_snapshots.py:460` | Switch `EvolutionLedger` → configurable backend | 1 |
| `OriginAgent/config/schema.py` | Add `ledger_backend` to `EvolutionConfig` | 1 |
| `OriginAgent/storage/__init__.py` | **NEW** — storage package init | 2 |
| `OriginAgent/storage/jsonl_migration.py` | **NEW** — reusable migration patterns | 2 |
| `OriginAgent/storage/sqlite_helpers.py` | **NEW** — shared SQLite connection helpers | 2 |
| `OriginAgent/agent/facts.py` | Migrate `FactEventStore` to SQLite option | 2 |
| `OriginAgent/bdi/desire_store.py` | Migrate `DesireStore` to SQLite option | 2 |
| `OriginAgent/utils/tracing.py` | **NEW** — trace context + structured log helpers | 3 |
| `tests/evolution/test_ledger_cutover.py` | **NEW** — cutover regression tests | 1 |
| `tests/storage/test_jsonl_migration.py` | **NEW** — migration template tests | 2 |
| `tests/utils/test_tracing.py` | **NEW** — tracing unit tests | 3 |

---

## Phase 1: Production Cutover

### ⚠️ Impact Assessment

`EvolutionLedger()` is constructed at **10 sites** across the codebase. Two are in agent subsystem (curator.py, evolution_snapshots.py), eight in evolution subsystem. Every site follows this pattern:

```python
self.ledger = ledger or EvolutionLedger(self.workspace)
```

The cutover strategy: add a factory function that reads `EvolutionConfig.ledger_backend` (default `"sqlite"`) and returns either `SqliteEvolutionLedger` or `EvolutionLedger`. Each site replaces `EvolutionLedger(self.workspace)` with `_create_ledger(self.workspace, config)`.

### Task 1: Switch production to SQLite ledger with configurable fallback

**Files:**
- Create: `OriginAgent/evolution/ledger_factory.py`
- Modify: `OriginAgent/config/schema.py` — add `ledger_backend` field
- Modify: 10 construction sites (see file map above)
- Modify: `OriginAgent/agent/evolution_maintenance.py` — add SQLite VACUUM
- Create: `tests/evolution/test_ledger_cutover.py`

- [ ] **Step 1: Add `ledger_backend` to `EvolutionConfig`**

In `OriginAgent/config/schema.py`, add after the `mode` field (line 802):

```python
    ledger_backend: Literal["jsonl", "sqlite"] = Field(
        default="sqlite",
        validation_alias=AliasChoices("ledgerBackend", "ledger_backend"),
        serialization_alias="ledgerBackend",
    )
```

- [ ] **Step 2: Create ledger factory**

Create `OriginAgent/evolution/ledger_factory.py`:

```python
"""Factory for creating the configured evolution ledger backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from OriginAgent.evolution.identity import EvolutionIdentityStore
from OriginAgent.evolution.ledger import EvolutionLedger
from OriginAgent.evolution.ledger_sqlite import SqliteEvolutionLedger


def create_ledger(
    workspace: Path,
    *,
    config: Any | None = None,
    identity_store: EvolutionIdentityStore | None = None,
    sign_events: bool = False,
) -> EvolutionLedger | SqliteEvolutionLedger:
    """Create the configured evolution ledger backend.

    Reads ``ledger_backend`` from config (default ``"sqlite"``).
    Falls back to JSONL if config is unavailable or explicitly set to ``"jsonl"``.
    """
    backend = "sqlite"
    if config is not None:
        backend = getattr(config, "ledger_backend", "sqlite") or "sqlite"

    if backend == "jsonl":
        return EvolutionLedger(
            workspace=workspace,
            identity_store=identity_store,
            sign_events=sign_events,
        )

    return SqliteEvolutionLedger(
        workspace=workspace,
        identity_store=identity_store,
        sign_events=sign_events,
    )


def migrate_if_needed(
    workspace: Path,
    *,
    config: Any | None = None,
    identity_store: EvolutionIdentityStore | None = None,
) -> SqliteEvolutionLedger | None:
    """Migrate from JSONL to SQLite if the JSONL file exists and SQLite doesn't.

    Returns the SQLite ledger if migration occurred, None if already on SQLite.
    Raises ValueError if the JSONL chain is broken.
    """
    backend = "sqlite"
    if config is not None:
        backend = getattr(config, "ledger_backend", "sqlite") or "sqlite"

    if backend != "sqlite":
        return None

    jsonl_path = workspace / "memory" / "evolution_events.jsonl"
    db_path = workspace / "memory" / "evolution_ledger.sqlite3"

    if db_path.exists() or not jsonl_path.exists():
        return None

    return SqliteEvolutionLedger.migrate_from_jsonl(
        workspace=workspace,
        jsonl_path=jsonl_path,
        db_path=db_path,
        identity_store=identity_store,
    )
```

- [ ] **Step 3: Switch all 10 construction sites**

Every site follows the same transformation. Example for `activation.py:68`:

```python
# BEFORE:
self.ledger = ledger or EvolutionLedger(self.workspace)

# AFTER:
from OriginAgent.evolution.ledger_factory import create_ledger
self.ledger = ledger or create_ledger(self.workspace, config=self._load_config())
```

For sites that don't currently accept config (telemetry.py, capability_gate.py, state_branch.py, recovery.py, memory_vault.py, curator.py, evolution_snapshots.py), add an optional `config` parameter.

For `memory_vault.py:95` and `curator.py:799` and `evolution_snapshots.py:460` which construct `EvolutionLedger(self.workspace)` inline (not stored as `self.ledger`), replace the inline construction with `create_ledger(self.workspace, config=getattr(self, '_config', None))`.

- [ ] **Step 4: Add auto-migration on startup**

In `OriginAgent/evolution/manager.py:82`, add migration call after ledger construction:

```python
self.ledger = ledger or create_ledger(
    self.workspace,
    config=self._config_loader() if self._config_loader else None,
)

# Auto-migrate from JSONL on first SQLite startup
from OriginAgent.evolution.ledger_factory import migrate_if_needed
migrate_if_needed(
    self.workspace,
    config=self._config_loader() if self._config_loader else None,
)
```

- [ ] **Step 5: Add SQLite VACUUM to evolution maintenance**

In `OriginAgent/agent/evolution_maintenance.py`, in `run_evolution_maintenance()`, add after line 60:

```python
    # ── SQLite ledger maintenance ─────────────────────────────────
    from OriginAgent.evolution.ledger_factory import create_ledger
    from OriginAgent.evolution.ledger_sqlite import SqliteEvolutionLedger

    ledger = create_ledger(workspace, config=config)
    if isinstance(ledger, SqliteEvolutionLedger):
        conn = ledger._get_conn()
        conn.execute("PRAGMA incremental_vacuum")
        logger.debug("Evolution ledger SQLite VACUUM completed")
```

- [ ] **Step 6: Write cutover regression tests**

Create `tests/evolution/test_ledger_cutover.py`:

```python
"""Regression tests for production ledger cutover."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from OriginAgent.evolution.ledger_factory import create_ledger, migrate_if_needed
from OriginAgent.evolution.ledger import EvolutionLedger
from OriginAgent.evolution.ledger_sqlite import SqliteEvolutionLedger


class TestLedgerFactory:
    def test_default_creates_sqlite(self, tmp_path: Path) -> None:
        ledger = create_ledger(workspace=tmp_path)
        assert isinstance(ledger, SqliteEvolutionLedger)

    def test_jsonl_backend_creates_jsonl(self, tmp_path: Path) -> None:
        config = MagicMock(ledger_backend="jsonl")
        ledger = create_ledger(workspace=tmp_path, config=config)
        assert isinstance(ledger, EvolutionLedger)

    def test_missing_config_defaults_to_sqlite(self, tmp_path: Path) -> None:
        ledger = create_ledger(workspace=tmp_path, config=None)
        assert isinstance(ledger, SqliteEvolutionLedger)


class TestAutoMigration:
    def test_no_migration_when_db_exists(self, tmp_path: Path) -> None:
        # Create SQLite ledger first
        SqliteEvolutionLedger(workspace=tmp_path).close()
        result = migrate_if_needed(workspace=tmp_path)
        assert result is None

    def test_no_migration_when_no_jsonl(self, tmp_path: Path) -> None:
        result = migrate_if_needed(workspace=tmp_path)
        assert result is None

    def test_migration_from_existing_jsonl(self, tmp_path: Path) -> None:
        from OriginAgent.evolution.events import EventType, EvolutionEvent

        # Create JSONL ledger with events
        jsonl = EvolutionLedger(workspace=tmp_path)
        jsonl.append(EvolutionEvent(
            event_id="evt-1",
            event_type=EventType.MODULE_VERIFIED,
            actor_public_key="test-key",
            artifact_digest="digest-1",
            payload={"test": True},
        ))
        jsonl.append(EvolutionEvent(
            event_id="evt-2",
            event_type=EventType.MODULE_VERIFIED,
            actor_public_key="test-key",
            artifact_digest="digest-2",
            payload={"test": True},
        ))

        result = migrate_if_needed(workspace=tmp_path)
        assert result is not None
        assert isinstance(result, SqliteEvolutionLedger)
        verification = result.verify_chain()
        assert verification.chain_integrity == "ok"
        assert verification.event_count == 2
```

- [ ] **Step 7: Run full regression**

```
.\.venv\Scripts\python.exe -m pytest tests/evolution/ tests/integration/test_minimal_turn_pipeline.py -v --basetemp=.pytest_tmp
```

Expected: All tests pass. Anchor test green. Default SQLite backend transparent to existing behavior.

- [ ] **Step 8: Commit**

```bash
git add OriginAgent/evolution/ledger_factory.py OriginAgent/config/schema.py \
        OriginAgent/evolution/activation.py OriginAgent/evolution/manager.py \
        OriginAgent/evolution/capability_gate.py OriginAgent/evolution/state_branch.py \
        OriginAgent/evolution/recovery.py OriginAgent/evolution/telemetry.py \
        OriginAgent/evolution/memory_vault.py OriginAgent/agent/curator.py \
        OriginAgent/agent/evolution_snapshots.py OriginAgent/agent/evolution_maintenance.py \
        tests/evolution/test_ledger_cutover.py
git commit -m "feat: switch production evolution to SQLite ledger backend

Replace all 10 EvolutionLedger() construction sites with create_ledger()
factory that reads EvolutionConfig.ledger_backend (default 'sqlite').

Auto-migration from JSONL on first startup via migrate_if_needed().
SQLite VACUUM in evolution_maintenance. Fallback to JSONL via config."
```

---

## Phase 2: JSONL Migration Template + Follower Stores

### Design Principle

Every JSONL store falls into one of three patterns. The migration template provides a base class or mixin for each:

| Pattern | Characteristics | Examples | Migration Strategy |
|---------|----------------|----------|-------------------|
| **APPEND-ONLY** | New records always appended, never mutated, hash chain verification | `evolution_events.jsonl`, `fact_events.jsonl`, `audit/*.jsonl`, `memory_candidates.jsonl` | `INSERT` with hash validation |
| **READ-MODIFY-WRITE** | Records loaded, mutated, rewritten atomically | `desires.jsonl`, `plans.jsonl`, `reminders.jsonl`, `confirmation.json` | `SELECT` → mutate → `INSERT OR REPLACE` in transaction |
| **CURRENT-STATE REWRITE** | Full file rewritten on every mutation | `facts.jsonl`, `semantic_index.json` | Transactional `DELETE` + bulk `INSERT` |

The template delivers: (1) shared schema helpers, (2) shared connection management with WAL + busy_timeout, (3) per-pattern migration base classes. Task 2 migrates one APPEND-ONLY store (`FactEventStore`) and one READ-MODIFY-WRITE store (`DesireStore`) as proof of concept.

### Task 2: Create migration template + migrate FactEventStore and DesireStore

**Files:**
- Create: `OriginAgent/storage/__init__.py`
- Create: `OriginAgent/storage/sqlite_helpers.py`
- Create: `OriginAgent/storage/jsonl_migration.py`
- Modify: `OriginAgent/agent/facts.py` — add SQLite option to `FactEventStore`
- Modify: `OriginAgent/bdi/desire_store.py` — add SQLite option to `DesireStore`
- Create: `tests/storage/test_jsonl_migration.py`

- [ ] **Step 1: Create `sqlite_helpers.py` — shared connection management**

Create `OriginAgent/storage/sqlite_helpers.py`:

```python
"""Shared SQLite helpers for OriginAgent storage backends."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def connect(db_path: Path, *, wal: bool = True, timeout_ms: int = 10000) -> sqlite3.Connection:
    """Open a SQLite connection with OriginAgent-standard settings."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_schema(conn: sqlite3.Connection, ddl: str) -> None:
    """Execute DDL if tables don't exist. Idempotent."""
    conn.executescript(ddl)
    conn.commit()


def atomic_write(conn: sqlite3.Connection, fn, *args: Any, **kwargs: Any) -> Any:
    """Execute *fn(conn, *args, **kwargs)* inside a BEGIN/COMMIT transaction."""
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        return fn(conn, *args, **kwargs)
```

- [ ] **Step 2: Create `jsonl_migration.py` — pattern-based migration base classes**

Create `OriginAgent/storage/jsonl_migration.py`. The full implementation is too long for a step — key structure:

```python
"""Reusable JSONL → SQLite migration patterns."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from OriginAgent.storage.sqlite_helpers import connect, ensure_schema


@dataclass
class MigrationResult:
    ok: bool
    records_imported: int
    records_skipped: int
    error: str = ""


class AppendOnlyMigrator(ABC):
    """Migrate an append-only JSONL store to SQLite.
    
    Subclasses define: table DDL, row serializer, and validation logic.
    """
    
    def __init__(self, workspace: Path, db_path: Path, jsonl_path: Path):
        self.workspace = Path(workspace)
        self.db_path = db_path
        self.jsonl_path = jsonl_path
    
    @abstractmethod
    def table_ddl(self) -> str: ...
    
    @abstractmethod
    def validate_line(self, line: dict) -> bool: ...
    
    @abstractmethod
    def insert_row(self, conn, line: dict) -> None: ...
    
    def migrate(self) -> MigrationResult:
        if not self.jsonl_path.exists():
            return MigrationResult(ok=True, records_imported=0, records_skipped=0)
        
        conn = connect(self.db_path)
        try:
            ensure_schema(conn, self.table_ddl())
            imported = 0
            skipped = 0
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                with open(self.jsonl_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            skipped += 1
                            continue
                        if not self.validate_line(data):
                            skipped += 1
                            continue
                        self.insert_row(conn, data)
                        imported += 1
            return MigrationResult(ok=True, records_imported=imported, records_skipped=skipped)
        finally:
            conn.close()
```

(Add `ReadModifyWriteMigrator` base class following same pattern — loads all records, validates, inserts in transaction.)

- [ ] **Step 3: Migrate `FactEventStore` (APPEND-ONLY)**

In `OriginAgent/agent/facts.py`, add a `FactEventStoreSqlite` class alongside `FactEventStore`:

```python
class FactEventStoreSqlite(AppendOnlyMigrator):
    """SQLite-backed fact event store."""
    
    def __init__(self, workspace: Path, db_path: Path | None = None):
        memory_dir = workspace / "memory"
        super().__init__(
            workspace=workspace,
            db_path=db_path or memory_dir / "fact_events.sqlite3",
            jsonl_path=memory_dir / "audit" / "fact_events.jsonl",
        )
    
    def table_ddl(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS fact_events (
                event_id TEXT PRIMARY KEY,
                fact_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            ) STRICT;
            CREATE INDEX IF NOT EXISTS idx_fact_events_fact
                ON fact_events(fact_id, created_at);
        """
    
    def validate_line(self, line: dict) -> bool:
        return bool(line.get("event_id") and line.get("fact_id"))
    
    def insert_row(self, conn, line: dict) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO fact_events
               (event_id, fact_id, event_type, actor, payload_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                line.get("event_id", ""),
                line.get("fact_id", ""),
                line.get("event_type", ""),
                line.get("actor", ""),
                json.dumps(line, ensure_ascii=False),
            ),
        )
    
    # Query methods
    def events_for_fact(self, fact_id: str, *, limit: int = 100) -> list[dict]:
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM fact_events WHERE fact_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (fact_id, limit),
            ).fetchall()
            return [json.loads(row[0]) for row in rows]
        finally:
            conn.close()
```

- [ ] **Step 4: Migrate `DesireStore` (READ-MODIFY-WRITE)**

In `OriginAgent/bdi/desire_store.py`, add a `DesireStoreSqlite` class alongside `DesireStore` following the same pattern but with `SELECT`-and-`UPSERT` semantics instead of append-only.

- [ ] **Step 5: Write migration template tests**

Create `tests/storage/test_jsonl_migration.py`:

```python
class TestAppendOnlyMigration:
    def test_empty_jsonl_produces_empty_sqlite(self, tmp_path: Path): ...
    def test_migration_preserves_all_records(self, tmp_path: Path): ...
    def test_migration_is_idempotent(self, tmp_path: Path): ...
    def test_corrupt_lines_are_skipped(self, tmp_path: Path): ...

class TestFactEventStoreSqlite:
    def test_events_for_fact_returns_chronological(self, tmp_path: Path): ...
    def test_insert_ignore_prevents_duplicates(self, tmp_path: Path): ...

class TestDesireStoreSqlite:
    def test_crud_operations(self, tmp_path: Path): ...
    def test_atomic_update_preserves_consistency(self, tmp_path: Path): ...
```

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/storage/ OriginAgent/agent/facts.py \
        OriginAgent/bdi/desire_store.py tests/storage/
git commit -m "feat: add JSONL→SQLite migration template + 2 follower stores

OriginAgent.storage.jsonl_migration provides AppendOnlyMigrator and
ReadModifyWriteMigrator base classes. FactEventStore (append-only) and
DesireStore (read-modify-write) migrated as proof of concept.

Pattern: connect() + ensure_schema() + BEGIN IMMEDIATE + validate + insert.
All new connections use WAL + busy_timeout=10000 + synchronous=NORMAL."
```

---

## Phase 3: Structured Observability

### Task 3: Add trace context + structured logging to critical paths

**Files:**
- Create: `OriginAgent/utils/tracing.py`
- Modify: `OriginAgent/agent/agent_runtime.py` — turn boundaries
- Modify: `OriginAgent/agent/runner.py` — LLM calls, tool execution
- Modify: `OriginAgent/evolution/activation.py` — activation lifecycle
- Modify: `OriginAgent/agent/subagent.py` — spawn/completion
- Modify: `OriginAgent/agent/agent_cognitive_runtime.py` — schedule events
- Create: `tests/utils/test_tracing.py`

- [ ] **Step 1: Create `tracing.py` — trace context + structured logging**

Create `OriginAgent/utils/tracing.py`:

```python
"""Lightweight trace context and structured logging for OriginAgent.

Uses contextvars for async-safe trace propagation (no otel dependency).
Provides structured log helpers that decorate loguru with trace context.
"""

from __future__ import annotations

import contextvars
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable

from loguru import logger

# ── Trace context (contextvars — async-safe) ──────────────────────────

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=""
)
_span_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "span_id", default=""
)
_session_key: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_key", default=""
)


def new_trace() -> str:
    """Start a new trace and return its trace_id."""
    tid = uuid.uuid4().hex[:16]
    _trace_id.set(tid)
    _span_id.set("")
    return tid


def trace_context(
    *,
    trace_id: str | None = None,
    session_key: str | None = None,
) -> dict[str, Any]:
    """Return current trace context as a dict for structured logging."""
    ctx: dict[str, Any] = {}
    tid = trace_id or _trace_id.get("")
    if tid:
        ctx["trace_id"] = tid
    sid = _span_id.get("")
    if sid:
        ctx["span_id"] = sid
    sk = session_key or _session_key.get("")
    if sk:
        ctx["session_key"] = sk
    return ctx


@contextmanager
def span(name: str, *, attrs: dict[str, Any] | None = None):
    """Create a span — logs start/end with timing."""
    saved = _span_id.get("")
    sid = uuid.uuid4().hex[:8]
    _span_id.set(sid)
    start = time.monotonic()
    ctx = trace_context()
    ctx.update(attrs or {})
    ctx["span"] = name
    logger.bind(**ctx).info("span.start")
    try:
        yield
    except Exception:
        elapsed_ms = (time.monotonic() - start) * 1000
        ctx["elapsed_ms"] = round(elapsed_ms, 2)
        logger.bind(**ctx).error("span.error")
        raise
    finally:
        _span_id.set(saved)
        elapsed_ms = (time.monotonic() - start) * 1000
        ctx["elapsed_ms"] = round(elapsed_ms, 2)
        logger.bind(**ctx).info("span.end")


def log_event(event: str, **attrs: Any) -> None:
    """Log a structured event with current trace context."""
    ctx = trace_context()
    ctx["event"] = event
    ctx.update(attrs)
    logger.bind(**ctx).info("event.{event}", event=event)


# ── Convenience aliases ──────────────────────────────────────────────

def set_session(sk: str) -> None:
    _session_key.set(sk)
```

- [ ] **Step 2: Instrument turn pipeline — `AgentRuntime`**

In `OriginAgent/agent/agent_runtime.py`, in the message processing method, add:

```python
from OriginAgent.utils.tracing import span, set_session, log_event

# At turn start:
set_session(session_key)
with span("turn.process", attrs={"session_key": session_key, "turn_id": turn_id}):
    # ... existing turn processing logic ...
    log_event("turn.llm_call", model=self._deps.model)
    # ... after LLM response ...
    log_event("turn.complete", tool_calls=len(tool_calls), tokens_used=tokens)
```

- [ ] **Step 3: Instrument evolution activation**

In `OriginAgent/evolution/activation.py`, in `activate()`:

```python
from OriginAgent.utils.tracing import log_event

# At gate pass:
log_event("evolution.activate.requested", artifact_digest=artifact_digest, actor=actor)
# At gate reject:
log_event("evolution.activate.rejected", reason="manual_approval_required")
# At success:
log_event("evolution.activate.completed", module_id=result.module_id)
```

- [ ] **Step 4: Instrument subagent spawn**

In `OriginAgent/agent/subagent.py`, at spawn and completion:

```python
log_event("subagent.spawn", agent_id=aid, parent_session=parent_sk)
# ... on completion ...
log_event("subagent.completed", agent_id=aid, exit_code=code)
```

- [ ] **Step 5: Write tracing tests**

Create `tests/utils/test_tracing.py`:

```python
class TestTraceContext:
    def test_new_trace_generates_unique_id(self): ...
    def test_trace_context_populates_all_fields(self): ...
    def test_context_is_async_safe(self): ...

class TestSpanContextManager:
    def test_span_logs_start_and_end(self): ...
    def test_span_logs_error_on_exception(self): ...
    def test_span_nesting(self): ...
```

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/utils/tracing.py tests/utils/test_tracing.py \
        OriginAgent/agent/agent_runtime.py OriginAgent/agent/runner.py \
        OriginAgent/evolution/activation.py OriginAgent/agent/subagent.py \
        OriginAgent/agent/agent_cognitive_runtime.py
git commit -m "feat: add trace context and structured logging instrumentation

OriginAgent.utils.tracing provides contextvars-based trace propagation
(no external dependency) with span() context manager and log_event().
Instrumented: turn pipeline boundaries, LLM calls, evolution activation
lifecycle, subagent spawn/completion, cognitive loop events.

All log events carry trace_id + span_id + session_key for correlation."
```

---

## Verification Checklist

```bash
# Anchor test
.\.venv\Scripts\python.exe -m pytest tests/integration/test_minimal_turn_pipeline.py -v --basetemp=.pytest_tmp

# Full evolution regression (includes cutover tests)
.\.venv\Scripts\python.exe -m pytest tests/evolution/ -v --basetemp=.pytest_tmp

# Migration template tests
.\.venv\Scripts\python.exe -m pytest tests/storage/ -v --basetemp=.pytest_tmp

# Tracing tests
.\.venv\Scripts\python.exe -m pytest tests/utils/test_tracing.py -v --basetemp=.pytest_tmp

# Lint
.\.venv\Scripts\python.exe -m ruff check OriginAgent/evolution/ledger_factory.py \
    OriginAgent/storage/ OriginAgent/utils/tracing.py OriginAgent/config/schema.py
```

Expected: All green.
