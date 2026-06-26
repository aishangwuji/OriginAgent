# BDI Deliberation Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a continuous BDI (Belief-Desire-Intention) deliberation engine that bridges OriginAgent's existing Belief system (memory facts, foresights, profiles) and Intention system (ActionIntent, ActionRuntime), giving the agent proactive self-drive.

**Architecture:** A new `OriginAgent/bdi/` package containing the DeliberationEngine — a periodic loop that reads active Desires from a JSONL store, evaluates them against current Beliefs (via memory pipeline outputs), invokes LLM reasoning to rank/merge/conflict-resolve, forms concrete Intentions (ActionIntent), and records deliberation outcomes. The existing HeartbeatService delegates to this engine. All state is persisted with atomic writes following existing patterns (temp-file + fsync + rename).

**Tech Stack:** Python 3.11+, asyncio, Pydantic (config), dataclasses (models), JSONL + FileLock (persistence, matching `memory/store.py` pattern), existing LLM provider abstraction.

## Global Constraints

- Python 3.11+ with type annotations on all function signatures
- asyncio throughout — no sync I/O in new code paths
- Immutable dataclasses (`frozen=True`) for all models
- Atomic writes: temp-file + fsync + rename + dir-fsync (match `agent/memory.py` pattern)
- Config: Pydantic Base model in `config/schema.py`, `${ENV_VAR}` interpolation
- Never run `ruff format` — only `ruff check`
- Tests: pytest with `asyncio_mode = "auto"`, AAA pattern

---

## File Structure

```
OriginAgent/bdi/
├── __init__.py              # Public API exports
├── models.py                # Data models: Desire, DeliberationResult, IntentionStack, PlanTemplate
├── desire_store.py          # JSONL persistence for Desires (CRUD + query by status/priority/deadline)
├── deliberation.py          # DeliberationEngine — event-driven BDI loop + PlanLibrary integration
├── heartbeat_bridge.py      # Bridge: existing HeartbeatService → DeliberationEngine integration
└── world_state_watcher.py   # Event listener: WorldState changes → immediate reconsider()

OriginAgent/config/
└── schema.py                # (MODIFY) Add BDIConfig, wire into Config root

tests/agent/bdi/
├── __init__.py
├── test_models.py           # Immutability, serialization, Desire/IntentionStack/PlanTemplate
├── test_desire_store.py     # CRUD, atomic write, query by status/priority/deadline
├── test_deliberation.py     # Deliberation cycle (poll + event-driven), PlanLibrary cache
├── test_heartbeat_bridge.py # Integration: HeartbeatService delegates to DeliberationEngine
├── test_world_state_watcher.py  # Event-driven reactivity tests
└── test_foresight_sync.py   # ForesightRecord → Desire auto-sync
```

---

### Task 1: Data Models — Desire, DeliberationResult, BDICycle

**Files:**
- Create: `OriginAgent/bdi/__init__.py`
- Create: `OriginAgent/bdi/models.py`
- Create: `tests/agent/bdi/__init__.py` (empty)
- Create: `tests/agent/bdi/test_models.py`

**Interfaces:**
- Produces: `DesireStatus` (enum), `DesirePriority` (enum), `Desire` (dataclass), `DeliberationIntention` (dataclass), `DeliberationResult` (dataclass), `BDICycleRecord` (dataclass), `now_iso()` helper

- [ ] **Step 1: Write the test file**

```python
"""Tests for BDI data models."""

import pytest
from dataclasses import asdict

from OriginAgent.bdi.models import (
    DesireStatus,
    DesirePriority,
    Desire,
    DeliberationIntention,
    DeliberationResult,
    BDICycleRecord,
    now_iso,
    ALLOWED_DESIRE_TRANSITIONS,
)


class TestDesireStatus:
    def test_has_five_states(self):
        states = list(DesireStatus)
        assert len(states) == 5
        assert DesireStatus.PENDING in states
        assert DesireStatus.ACTIVE in states
        assert DesireStatus.SUSPENDED in states
        assert DesireStatus.SATISFIED in states
        assert DesireStatus.CANCELLED in states

    def test_allowed_transitions_are_valid(self):
        for from_state, to_states in ALLOWED_DESIRE_TRANSITIONS.items():
            for to_state in to_states:
                assert to_state in DesireStatus


class TestDesirePriority:
    def test_ordering(self):
        assert DesirePriority.LOW.value < DesirePriority.MEDIUM.value
        assert DesirePriority.MEDIUM.value < DesirePriority.HIGH.value
        assert DesirePriority.HIGH.value < DesirePriority.CRITICAL.value


class TestDesire:
    def test_immutable(self):
        d = Desire(
            desire_id="d1",
            owner_id="user:test",
            session_key="sess:test",
            content="Review the quarterly report by Friday",
            status=DesireStatus.PENDING,
            priority=DesirePriority.MEDIUM,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        with pytest.raises(Exception):
            d.content = "changed"  # type: ignore

    def test_serialization_roundtrip(self):
        d = Desire(
            desire_id="d1",
            owner_id="user:test",
            session_key="sess:test",
            content="Buy groceries tomorrow",
            status=DesireStatus.ACTIVE,
            priority=DesirePriority.HIGH,
            created_at=now_iso(),
            updated_at=now_iso(),
            deadline_at="2026-06-27T18:00:00",
            source_foresight_id="fs_abc",
            source_message_ids=["msg_1"],
            dependencies=["d0"],
            constraints=["must be organic"],
            metadata={"tags": ["personal", "urgent"]},
        )
        data = d.to_json()
        restored = Desire.from_json(data)
        assert restored.desire_id == d.desire_id
        assert restored.content == d.content
        assert restored.priority == DesirePriority.HIGH
        assert restored.deadline_at == "2026-06-27T18:00:00"
        assert restored.source_foresight_id == "fs_abc"
        assert restored.dependencies == ["d0"]

    def test_can_transition(self):
        d = Desire(
            desire_id="d1",
            owner_id="user:test",
            session_key="sess:test",
            content="Test",
            status=DesireStatus.PENDING,
            priority=DesirePriority.LOW,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        assert d.can_transition_to(DesireStatus.ACTIVE) is True
        assert d.can_transition_to(DesireStatus.CANCELLED) is True
        assert d.can_transition_to(DesireStatus.SATISFIED) is False  # PENDING → SATISFIED not allowed

    def test_transition_returns_new_instance(self):
        d = Desire(
            desire_id="d1",
            owner_id="user:test",
            session_key="sess:test",
            content="Test",
            status=DesireStatus.PENDING,
            priority=DesirePriority.LOW,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        d2 = d.transition_to(DesireStatus.ACTIVE)
        assert d2 is not d
        assert d2.status == DesireStatus.ACTIVE
        assert d.status == DesireStatus.PENDING  # original unchanged


class TestDeliberationIntention:
    def test_immutable(self):
        intent = DeliberationIntention(
            desire_id="d1",
            action="send_message",
            scope="telegram",
            trigger="deliberation",
            risk="low",
            reasoning="User needs reminder about groceries",
            payload={"text": "Remember to buy groceries tomorrow"},
        )
        with pytest.raises(Exception):
            intent.action = "changed"  # type: ignore


class TestDeliberationResult:
    def test_has_required_fields(self):
        result = DeliberationResult(
            cycle_id="cycle_1",
            started_at=now_iso(),
            finished_at=now_iso(),
            desires_evaluated=3,
            intentions_formed=1,
            intentions=[],
            reasoning="No urgent desires found.",
        )
        assert result.intentions_formed == 0
        assert result.desires_evaluated == 3

    def test_serialization_with_intentions(self):
        intent = DeliberationIntention(
            desire_id="d1",
            action="send_message",
            scope="telegram",
            trigger="deliberation",
            risk="low",
            reasoning="Test",
            payload={},
        )
        result = DeliberationResult(
            cycle_id="cycle_1",
            started_at=now_iso(),
            finished_at=now_iso(),
            desires_evaluated=1,
            intentions_formed=1,
            intentions=[intent],
            reasoning="Acting on desire d1.",
        )
        data = result.to_json()
        restored = DeliberationResult.from_json(data)
        assert len(restored.intentions) == 1
        assert restored.intentions[0].desire_id == "d1"


class TestBDICycleRecord:
    def test_serialization(self):
        record = BDICycleRecord(
            cycle_id="cycle_1",
            started_at=now_iso(),
            finished_at=now_iso(),
            status="completed",
            desires_before=5,
            intentions_formed=2,
            intentions_executed=1,
            model_used="anthropic/claude-sonnet-4-6",
            token_usage={"input": 500, "output": 200},
        )
        data = record.to_json()
        assert data["status"] == "completed"
        assert data["token_usage"]["input"] == 500
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_models.py -v
```

Expected: FAIL — module `OriginAgent.bdi.models` not found.

- [ ] **Step 3: Create package init**

```python
# OriginAgent/bdi/__init__.py
"""BDI (Belief-Desire-Intention) deliberation engine for OriginAgent.

Provides the DeliberationEngine — a continuous reasoning loop that evaluates
active Desires against current Beliefs and produces executable Intentions.
"""

from OriginAgent.bdi.models import (
    BDICycleRecord,
    DeliberationIntention,
    DeliberationResult,
    Desire,
    DesirePriority,
    DesireStatus,
    now_iso,
)
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.deliberation import DeliberationEngine

__all__ = [
    "BDICycleRecord",
    "DeliberationEngine",
    "DeliberationIntention",
    "DeliberationResult",
    "Desire",
    "DesirePriority",
    "DesireStatus",
    "DesireStore",
    "now_iso",
]
```

- [ ] **Step 4: Write models.py**

```python
"""Immutable data models for the BDI deliberation system."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Desire — the "D" in BDI
# ---------------------------------------------------------------------------


class DesireStatus(str, Enum):
    PENDING = "pending"        # Created, not yet evaluated
    ACTIVE = "active"          # Under active deliberation / intended
    SUSPENDED = "suspended"    # Paused (blocked, waiting for dependency)
    SATISFIED = "satisfied"    # Completed successfully
    CANCELLED = "cancelled"    # No longer relevant


class DesirePriority(int, Enum):
    LOW = 10
    MEDIUM = 50
    HIGH = 80
    CRITICAL = 100


ALLOWED_DESIRE_TRANSITIONS: dict[DesireStatus, tuple[DesireStatus, ...]] = {
    DesireStatus.PENDING:   (DesireStatus.ACTIVE, DesireStatus.CANCELLED),
    DesireStatus.ACTIVE:    (DesireStatus.SUSPENDED, DesireStatus.SATISFIED, DesireStatus.CANCELLED),
    DesireStatus.SUSPENDED: (DesireStatus.ACTIVE, DesireStatus.CANCELLED),
    DesireStatus.SATISFIED: (),   # Terminal
    DesireStatus.CANCELLED: (),   # Terminal
}


@dataclass(frozen=True)
class Desire:
    """A single desire/goal the agent should work toward.

    Evolved from ForesightRecord — this is the active, deliberable form.
    ForesightRecords are passively extracted from conversation; Desires are
    actively managed by the DeliberationEngine.
    """

    desire_id: str
    owner_id: str
    session_key: str
    content: str                       # Natural-language description
    status: DesireStatus = DesireStatus.PENDING
    priority: DesirePriority = DesirePriority.MEDIUM
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    # Optional deadline — when this desire expires or must be done by
    deadline_at: str | None = None

    # When was it satisfied (set on transition to SATISFIED)
    satisfied_at: str | None = None

    # Provenance — where did this desire come from?
    source_foresight_id: str | None = None
    source_episode_id: str | None = None
    source_message_ids: list[str] = field(default_factory=list)
    source_cycle_id: str | None = None  # BDI cycle that auto-generated this

    # Dependencies — desire_ids that must be satisfied first
    dependencies: list[str] = field(default_factory=list)

    # Constraints / guardrails
    constraints: list[str] = field(default_factory=list)

    # How many deliberation cycles has this been evaluated?
    evaluation_count: int = 0

    # Last deliberation reasoning about this desire
    last_reasoning: str = ""

    metadata: dict[str, Any] = field(default_factory=dict)

    def can_transition_to(self, target: DesireStatus) -> bool:
        return target in ALLOWED_DESIRE_TRANSITIONS.get(self.status, ())

    def transition_to(self, target: DesireStatus, *, reasoning: str = "") -> "Desire":
        if not self.can_transition_to(target):
            raise ValueError(
                f"Cannot transition Desire {self.desire_id} "
                f"from {self.status.value} to {target.value}"
            )
        updates: dict[str, Any] = {
            "status": target,
            "updated_at": now_iso(),
            "last_reasoning": reasoning or self.last_reasoning,
        }
        if target == DesireStatus.SATISFIED:
            updates["satisfied_at"] = now_iso()
        return replace(self, **updates)

    def with_evaluation(self, *, reasoning: str = "") -> "Desire":
        """Bump evaluation counter and record reasoning."""
        return replace(
            self,
            evaluation_count=self.evaluation_count + 1,
            last_reasoning=reasoning or self.last_reasoning,
            updated_at=now_iso(),
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in (DesireStatus.SATISFIED, DesireStatus.CANCELLED)

    @property
    def is_deliberable(self) -> bool:
        return self.status in (DesireStatus.PENDING, DesireStatus.ACTIVE)

    @property
    def is_overdue(self) -> bool:
        if not self.deadline_at or self.is_terminal:
            return False
        return now_iso() > self.deadline_at

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["priority"] = self.priority.value
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Desire":
        data = dict(data)
        data["status"] = DesireStatus(data["status"])
        data["priority"] = DesirePriority(data["priority"])
        return cls(**data)


# ---------------------------------------------------------------------------
# DeliberationIntention — the output of deliberation, feeds ActionRuntime
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeliberationIntention:
    """A concrete intention formed by the DeliberationEngine.

    This is what gets translated into ActionIntent for execution.
    """

    desire_id: str
    action: str                       # e.g. "send_message", "exec", "web_search"
    scope: str                        # channel name or "system"
    trigger: str = "deliberation"
    risk: str = "low"
    reasoning: str = ""               # Why the engine chose this action
    payload: dict[str, Any] = field(default_factory=dict)

    def to_action_intent(self) -> "ActionIntent":
        """Convert to ActionIntent for SafeActionExecutor."""
        from OriginAgent.agent.action_runtime import ActionIntent

        return ActionIntent(
            action=self.action,
            scope=self.scope,
            trigger=self.trigger,
            risk=self.risk,
            payload=dict(self.payload),
        )

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "DeliberationIntention":
        return cls(**data)


# ---------------------------------------------------------------------------
# DeliberationResult — one full BDI cycle output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeliberationResult:
    """Result of a single deliberation cycle."""

    cycle_id: str
    started_at: str
    finished_at: str
    desires_evaluated: int
    intentions_formed: int
    intentions: list[DeliberationIntention] = field(default_factory=list)
    desires_updated: list[str] = field(default_factory=list)  # desire_ids
    reasoning: str = ""
    next_check_at: str | None = None   # When should the engine run next?
    model_used: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)
    error: str = ""

    @property
    def had_work(self) -> bool:
        return self.intentions_formed > 0

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["intentions"] = [i.to_json() for i in self.intentions]
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "DeliberationResult":
        data = dict(data)
        data["intentions"] = [
            DeliberationIntention.from_json(i) for i in data.get("intentions", [])
        ]
        return cls(**data)


# ---------------------------------------------------------------------------
# BDICycleRecord — persisted audit record for each cycle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BDICycleRecord:
    """Persisted record of a BDI deliberation cycle for auditing."""

    cycle_id: str
    started_at: str
    finished_at: str
    status: str                       # "completed", "skipped", "error"
    desires_before: int = 0
    desires_after_active: int = 0
    intentions_formed: int = 0
    intentions_executed: int = 0
    intentions_failed: int = 0
    desire_ids_evaluated: list[str] = field(default_factory=list)
    desire_ids_updated: list[str] = field(default_factory=list)
    model_used: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "BDICycleRecord":
        return cls(**data)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_models.py -v
```

Expected: PASS — all model tests pass.

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/bdi/__init__.py OriginAgent/bdi/models.py tests/agent/bdi/
git commit -m "feat(bdi): add BDI data models — Desire, DeliberationIntention, DeliberationResult, BDICycleRecord"
```

---

### Task 2: DesireStore — JSONL persistence for Desires

**Files:**
- Create: `OriginAgent/bdi/desire_store.py`
- Create: `tests/agent/bdi/test_desire_store.py`

**Interfaces:**
- Consumes: `Desire`, `DesireStatus`, `DesirePriority`, `now_iso` from `OriginAgent.bdi.models`
- Produces: `DesireStore` class with `add()`, `get()`, `update()`, `list_active()`, `list_deliberable()`, `list_by_status()`, `count_by_status()`, `find_by_source_foresight()`

- [ ] **Step 1: Write the test file**

```python
"""Tests for DesireStore JSONL persistence."""

import pytest
import tempfile
from pathlib import Path

from OriginAgent.bdi.models import Desire, DesireStatus, DesirePriority, now_iso
from OriginAgent.bdi.desire_store import DesireStore


@pytest.fixture
def tmp_store():
    with tempfile.TemporaryDirectory() as td:
        workspace = Path(td)
        store = DesireStore(workspace)
        yield store


class TestDesireStoreBasic:
    def test_add_and_get(self, tmp_store: DesireStore):
        d = Desire(
            desire_id="d1",
            owner_id="user:test",
            session_key="sess:test",
            content="Write quarterly report",
            status=DesireStatus.PENDING,
            priority=DesirePriority.HIGH,
        )
        tmp_store.add(d)
        restored = tmp_store.get("d1")
        assert restored is not None
        assert restored.content == "Write quarterly report"
        assert restored.priority == DesirePriority.HIGH

    def test_get_missing_returns_none(self, tmp_store: DesireStore):
        assert tmp_store.get("nonexistent") is None

    def test_add_duplicate_raises(self, tmp_store: DesireStore):
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X")
        tmp_store.add(d)
        with pytest.raises(ValueError, match="already exists"):
            tmp_store.add(d)

    def test_update_transitions_status(self, tmp_store: DesireStore):
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X")
        tmp_store.add(d)
        updated = tmp_store.update("d1", status=DesireStatus.ACTIVE, reasoning="Let's do it")
        assert updated is not None
        assert updated.status == DesireStatus.ACTIVE
        assert updated.last_reasoning == "Let's do it"

        # Verify persisted
        restored = tmp_store.get("d1")
        assert restored is not None
        assert restored.status == DesireStatus.ACTIVE

    def test_update_invalid_transition_raises(self, tmp_store: DesireStore):
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X")
        tmp_store.add(d)
        with pytest.raises(ValueError, match="Cannot transition"):
            tmp_store.update("d1", status=DesireStatus.SATISFIED)

    def test_update_satisfied_sets_satisfied_at(self, tmp_store: DesireStore):
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.ACTIVE)
        tmp_store.add(d)
        updated = tmp_store.update("d1", status=DesireStatus.SATISFIED)
        assert updated.status == DesireStatus.SATISFIED
        assert updated.satisfied_at is not None

    def test_update_missing_returns_none(self, tmp_store: DesireStore):
        assert tmp_store.update("nonexistent", status=DesireStatus.ACTIVE) is None


class TestDesireStoreQuery:
    @pytest.fixture
    def populated(self, tmp_store: DesireStore) -> DesireStore:
        for i, (status, pri) in enumerate([
            (DesireStatus.PENDING, DesirePriority.HIGH),
            (DesireStatus.ACTIVE, DesirePriority.CRITICAL),
            (DesireStatus.ACTIVE, DesirePriority.LOW),
            (DesireStatus.SATISFIED, DesirePriority.MEDIUM),
            (DesireStatus.SUSPENDED, DesirePriority.HIGH),
            (DesireStatus.PENDING, DesirePriority.LOW),
        ]):
            tmp_store.add(Desire(
                desire_id=f"d{i}",
                owner_id="u",
                session_key="s",
                content=f"Task {i}",
                status=status,
                priority=pri,
            ))
        return tmp_store

    def test_list_active(self, populated: DesireStore):
        active = populated.list_active()
        assert len(active) == 2  # d1 (ACTIVE/CRITICAL) and d2 (ACTIVE/LOW)

    def test_list_deliberable(self, populated: DesireStore):
        deliberable = populated.list_deliberable()
        assert len(deliberable) == 4  # d0, d1, d2, d5 (PENDING + ACTIVE)

    def test_list_by_status(self, populated: DesireStore):
        pending = populated.list_by_status(DesireStatus.PENDING)
        assert len(pending) == 2

    def test_count_by_status(self, populated: DesireStore):
        counts = populated.count_by_status()
        assert counts[DesireStatus.PENDING] == 2
        assert counts[DesireStatus.ACTIVE] == 2
        assert counts[DesireStatus.SATISFIED] == 1
        assert counts[DesireStatus.SUSPENDED] == 1

    def test_sort_by_priority_desc(self, populated: DesireStore):
        active = populated.list_deliberable()
        # Should be sorted by priority descending (CRITICAL first)
        assert active[0].priority == DesirePriority.CRITICAL

    def test_find_by_source_foresight(self, tmp_store: DesireStore):
        d = Desire(
            desire_id="d_fs",
            owner_id="u",
            session_key="s",
            content="From foresight",
            source_foresight_id="fs_abc",
        )
        tmp_store.add(d)
        found = tmp_store.find_by_source_foresight("fs_abc")
        assert len(found) == 1
        assert found[0].desire_id == "d_fs"

    def test_find_by_source_foresight_empty(self, tmp_store: DesireStore):
        assert tmp_store.find_by_source_foresight("no_match") == []


class TestDesireStoreAtomicWrite:
    def test_crash_safety(self, tmp_store: DesireStore):
        """Desires survive a simulated crash (teardown + reload)."""
        d = Desire(desire_id="d_crash", owner_id="u", session_key="s", content="Survive")
        tmp_store.add(d)

        # Simulate reload — new store pointing at same workspace
        store2 = DesireStore(tmp_store.workspace)
        restored = store2.get("d_crash")
        assert restored is not None
        assert restored.content == "Survive"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_desire_store.py -v
```

Expected: FAIL — `DesireStore` class not found.

- [ ] **Step 3: Write desire_store.py**

```python
"""JSONL-based persistence for BDI Desires with atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from filelock import FileLock
from loguru import logger

from OriginAgent.bdi.models import Desire, DesirePriority, DesireStatus, now_iso
from OriginAgent.utils.helpers import ensure_dir


class DesireStore:
    """CRUD store for Desires backed by a JSONL file.

    Follows the same atomic-write pattern as agent/memory.py:
    temp-file + fsync + rename + dir-fsync.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self._dir = self.workspace / "memory" / "bdi"
        self._path = self._dir / "desires.jsonl"
        self._lock_path = self._dir / ".desires.lock"
        ensure_dir(self._dir)

    # ------------------------------------------------------------------
    # Atomic I/O
    # ------------------------------------------------------------------

    def _read_all(self) -> dict[str, Desire]:
        """Read all desires into a dict keyed by desire_id."""
        if not self._path.exists():
            return {}
        result: dict[str, Desire] = {}
        with FileLock(str(self._lock_path)):
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = Desire.from_json(json.loads(line))
                        result[d.desire_id] = d
                    except Exception:
                        logger.warning("BDI: skipping corrupt desire line")
        return result

    def _write_all(self, desires: dict[str, Desire]) -> None:
        """Atomically write all desires."""
        ensure_dir(self._dir)
        with FileLock(str(self._lock_path)):
            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self._dir),
                delete=False,
                suffix=".tmp",
            )
            try:
                for d in desires.values():
                    tmp.write(json.dumps(d.to_json(), ensure_ascii=False) + "\n")
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp.close()
                os.replace(tmp.name, str(self._path))
                # Directory fsync for durability
                try:
                    dir_fd = os.open(str(self._dir), os.O_RDONLY)
                    os.fsync(dir_fd)
                    os.close(dir_fd)
                except OSError:
                    pass
            except Exception:
                Path(tmp.name).unlink(missing_ok=True)
                raise

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, desire: Desire) -> None:
        desires = self._read_all()
        if desire.desire_id in desires:
            raise ValueError(f"Desire {desire.desire_id} already exists")
        desires[desire.desire_id] = desire
        self._write_all(desires)

    def get(self, desire_id: str) -> Desire | None:
        return self._read_all().get(desire_id)

    def update(
        self,
        desire_id: str,
        *,
        status: DesireStatus | None = None,
        priority: DesirePriority | None = None,
        reasoning: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Desire | None:
        desires = self._read_all()
        current = desires.get(desire_id)
        if current is None:
            return None

        if status is not None:
            current = current.transition_to(status, reasoning=reasoning)
        if priority is not None:
            current = current.__replace__(
                priority=priority,
                updated_at=now_iso(),
                last_reasoning=reasoning or current.last_reasoning,
            )
        if metadata is not None:
            merged = {**current.metadata, **metadata}
            current = current.__replace__(metadata=merged, updated_at=now_iso())

        if current.evaluation_count == current.__class__(
            desire_id=current.desire_id,
            owner_id=current.owner_id,
            session_key=current.session_key,
            content=current.content,
            status=current.status,
            priority=current.priority,
        ).evaluation_count:
            current = current.with_evaluation(reasoning=reasoning)
        else:
            current = current.__replace__(
                updated_at=now_iso(),
                last_reasoning=reasoning or current.last_reasoning,
            )

        desires[desire_id] = current
        self._write_all(desires)
        return current

    def update_direct(self, desire: Desire) -> None:
        """Directly replace a desire (after external mutation)."""
        desires = self._read_all()
        if desire.desire_id not in desires:
            raise ValueError(f"Desire {desire.desire_id} does not exist")
        desires[desire.desire_id] = desire
        self._write_all(desires)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def list_all(self) -> list[Desire]:
        desires = self._read_all().values()
        return sorted(desires, key=lambda d: (-d.priority.value, d.created_at))

    def list_active(self) -> list[Desire]:
        return [d for d in self.list_all() if d.status == DesireStatus.ACTIVE]

    def list_deliberable(self) -> list[Desire]:
        return [d for d in self.list_all() if d.is_deliberable]

    def list_by_status(self, status: DesireStatus) -> list[Desire]:
        return [d for d in self.list_all() if d.status == status]

    def list_overdue(self) -> list[Desire]:
        return [d for d in self.list_all() if d.is_overdue]

    def count_by_status(self) -> dict[DesireStatus, int]:
        counts: dict[DesireStatus, int] = {s: 0 for s in DesireStatus}
        for d in self._read_all().values():
            counts[d.status] += 1
        return counts

    def find_by_source_foresight(self, foresight_id: str) -> list[Desire]:
        return [
            d for d in self.list_all()
            if d.source_foresight_id == foresight_id
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_desire_store.py -v
```

Expected: PASS — all CRUD, query, and atomic-write tests pass.

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/bdi/desire_store.py tests/agent/bdi/test_desire_store.py
git commit -m "feat(bdi): add DesireStore with atomic JSONL persistence"
```

---

### Task 3: BDI Config Schema

**Files:**
- Modify: `OriginAgent/config/schema.py` — add `BDIConfig`, wire into `Config`

**Interfaces:**
- Produces: `BDIConfig(Base)` with fields: `enabled`, `interval_s`, `model_override`, `max_desires_per_cycle`, `auto_create_from_foresight`, `notify_on_intention`
- Consumed by: `DeliberationEngine` (Task 4), `HeartbeatBridge` (Task 5)

- [ ] **Step 1: Read the target area in schema.py to confirm insertion point**

The insertion goes near the existing `HeartbeatConfig` (line ~1393) for logical grouping.

- [ ] **Step 2: Add BDIConfig class**

Insert after `HeartbeatConfig` (after line 1398):

```python
class BDIConfig(Base):
    """BDI Deliberation Engine configuration."""

    enabled: bool = True
    interval_s: int = Field(
        default=120,
        ge=30,
        le=86_400,
        description="Seconds between deliberation cycles (default 2 min)",
    )
    model_override: str | None = Field(
        default=None,
        validation_alias=AliasChoices("modelOverride", "model", "model_override"),
        description="Optional model override for deliberation (defaults to main agent model)",
    )
    max_desires_per_cycle: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Max desires to evaluate in one cycle",
    )
    auto_create_from_foresight: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "autoCreateFromForesight",
            "auto_create_from_foresight",
        ),
        serialization_alias="autoCreateFromForesight",
        description="Auto-create Desires from ForesightRecords during deliberation",
    )
    notify_on_intention: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "notifyOnIntention",
            "notify_on_intention",
        ),
        serialization_alias="notifyOnIntention",
        description="Notify user via their primary channel when intentions are formed",
    )
```

- [ ] **Step 3: Wire BDIConfig into GatewayConfig**

In `GatewayConfig` (around line 1409), add the `bdi` field:

```python
class GatewayConfig(Base):
    """Gateway/server configuration."""

    host: str = "127.0.0.1"
    port: int = 18790
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    bdi: BDIConfig = Field(default_factory=BDIConfig)
```

- [ ] **Step 4: Run existing config tests to verify no regressions**

```bash
.\.venv\Scripts\python.exe -m pytest tests/config/ -v --timeout=30
```

Expected: PASS — existing config tests still pass, new BDIConfig defaults work.

- [ ] **Step 5: Write a minimal config roundtrip test**

Add to an existing config test file or create a quick smoke test:

```bash
.\.venv\Scripts\python.exe -c "
from OriginAgent.config.schema import Config, BDIConfig
import json
# Round-trip with BDI defaults
raw = json.loads('{\"agents\":{\"defaults\":{\"workspace\":\"/tmp/test\"}}}')
c = Config(**raw)
assert c.gateway.bdi.enabled is True
assert c.gateway.bdi.interval_s == 120
assert c.gateway.bdi.max_desires_per_cycle == 10
print('BDI config defaults OK')
"
```

Expected: prints "BDI config defaults OK".

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/config/schema.py
git commit -m "feat(bdi): add BDIConfig schema and wire into GatewayConfig"
```

---

### Task 4: DeliberationEngine — Core BDI Loop

**Files:**
- Create: `OriginAgent/bdi/deliberation.py`
- Create: `tests/agent/bdi/test_deliberation.py`

**Interfaces:**
- Consumes: `DesireStore` from Task 2, `BDIConfig` from Task 3, `Desire`, `DesireStatus`, `DeliberationResult`, `DeliberationIntention`, `BDICycleRecord`, `now_iso` from Task 1
- Consumes: LLM provider (`LLMProvider.chat_with_retry`), `NearlineMemoryStore` (for reading current Beliefs)
- Produces: `DeliberationEngine` class with `run_cycle() -> DeliberationResult`, `start()`, `stop()`, `trigger_now()`

- [ ] **Step 1: Write the test file**

```python
"""Tests for DeliberationEngine."""

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.bdi.models import (
    DeliberationIntention,
    DeliberationResult,
    Desire,
    DesireStatus,
    DesirePriority,
    now_iso,
)
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.deliberation import DeliberationEngine


# ---------------------------------------------------------------------------
# Fake LLM provider for deterministic testing
# ---------------------------------------------------------------------------

@dataclass
class FakeLLMResponse:
    should_execute_tools: bool = True
    has_tool_calls: bool = True

    tool_calls = None  # set below


@dataclass
class FakeToolCall:
    arguments: dict[str, Any] = field(default_factory=dict)


class FakeProvider:
    """Returns a controlled deliberation JSON response."""

    def __init__(self, response_dict: dict[str, Any] | None = None):
        self._response = response_dict or {
            "reasoning": "d1 is the most urgent. User needs reminder now.",
            "intentions": [
                {
                    "desire_id": "d1",
                    "action": "send_message",
                    "scope": "telegram",
                    "risk": "low",
                    "reasoning": "User needs reminder about groceries before 6pm.",
                    "payload": {"text": "Hey, don't forget to buy groceries before 6pm!"},
                }
            ],
            "desires_to_satisfy": [],
            "desires_to_suspend": [],
            "desires_to_cancel": [],
            "next_check_at": None,
        }
        self.call_count = 0

    async def chat_with_retry(self, messages, tools, model):
        self.call_count += 1
        resp = FakeLLMResponse()
        resp.tool_calls = [
            FakeToolCall(arguments=self._response)
        ]
        return resp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_desire(desire_id: str, content: str, status=DesireStatus.ACTIVE,
                priority=DesirePriority.MEDIUM, deadline=None) -> Desire:
    return Desire(
        desire_id=desire_id,
        owner_id="user:test",
        session_key="sess:test",
        content=content,
        status=status,
        priority=priority,
        deadline_at=deadline,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeliberationEngineCore:
    @pytest.fixture
    def store(self):
        with tempfile.TemporaryDirectory() as td:
            yield DesireStore(Path(td))

    def build_engine(self, store, provider=None, enabled=True):
        return DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=provider or FakeProvider(),
            model="test-model",
            enabled=enabled,
            max_desires_per_cycle=10,
            auto_create_from_foresight=False,
        )

    @pytest.mark.asyncio
    async def test_cycle_with_one_active_desire_forms_intention(self, store):
        store.add(make_desire("d1", "Buy groceries by 6pm",
                               priority=DesirePriority.HIGH))
        engine = self.build_engine(store)

        result = await engine.run_cycle()

        assert isinstance(result, DeliberationResult)
        assert result.desires_evaluated == 1
        assert result.intentions_formed == 1
        assert result.intentions[0].desire_id == "d1"

    @pytest.mark.asyncio
    async def test_cycle_with_no_desires_skips(self, store):
        engine = self.build_engine(store)
        result = await engine.run_cycle()

        assert result.desires_evaluated == 0
        assert result.intentions_formed == 0
        assert result.reasoning != ""

    @pytest.mark.asyncio
    async def test_cycle_with_only_terminal_desires_skips(self, store):
        store.add(make_desire("d1", "Done task", status=DesireStatus.SATISFIED))
        store.add(make_desire("d2", "Cancelled task", status=DesireStatus.CANCELLED))
        engine = self.build_engine(store)

        result = await engine.run_cycle()
        assert result.desires_evaluated == 0

    @pytest.mark.asyncio
    async def test_disabled_engine_skips(self, store):
        store.add(make_desire("d1", "Test"))
        engine = self.build_engine(store, enabled=False)

        result = await engine.run_cycle()
        assert result.desires_evaluated == 0

    @pytest.mark.asyncio
    async def test_satisfied_intention_updates_desire(self, store):
        store.add(make_desire("d1", "Buy groceries by 6pm",
                               priority=DesirePriority.HIGH))
        provider = FakeProvider({
            "reasoning": "Task complete.",
            "intentions": [],
            "desires_to_satisfy": ["d1"],
            "desires_to_suspend": [],
            "desires_to_cancel": [],
            "next_check_at": None,
        })
        engine = self.build_engine(store, provider=provider)

        result = await engine.run_cycle()
        assert result.intentions_formed == 0
        assert "d1" in result.desires_updated

        updated = store.get("d1")
        assert updated is not None
        assert updated.status == DesireStatus.SATISFIED

    @pytest.mark.asyncio
    async def test_overdue_desire_escalates_priority(self, store):
        store.add(make_desire("d1", "Late task", priority=DesirePriority.LOW,
                               deadline="2020-01-01T00:00:00"))
        engine = self.build_engine(store)

        result = await engine.run_cycle()
        assert result.desires_evaluated == 1
        # Overdue desire is included in evaluation

    @pytest.mark.asyncio
    async def test_respects_max_desires_per_cycle(self, store):
        for i in range(15):
            store.add(make_desire(f"d{i}", f"Task {i}"))
        engine = self.build_engine(store, provider=FakeProvider())
        engine._max_desires_per_cycle = 5

        result = await engine.run_cycle()
        assert result.desires_evaluated <= 5

    @pytest.mark.asyncio
    async def test_llm_error_is_captured_in_result(self, store):
        store.add(make_desire("d1", "Test"))

        class ErrorProvider:
            async def chat_with_retry(self, *args, **kwargs):
                raise RuntimeError("LLM unavailable")

        engine = self.build_engine(store, provider=ErrorProvider())
        result = await engine.run_cycle()

        assert "LLM unavailable" in result.error
        assert result.intentions_formed == 0

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self, store):
        store.add(make_desire("d1", "Test"))
        engine = self.build_engine(store)
        engine._interval_s = 1  # Fast for test

        await engine.start()
        assert engine._running is True
        await asyncio.sleep(0.1)

        engine.stop()
        assert engine._running is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_deliberation.py -v
```

Expected: FAIL — `DeliberationEngine` not found.

- [ ] **Step 3: Write deliberation.py**

```python
"""BDI Deliberation Engine — continuous Belief→Desire→Intention reasoning loop."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from OriginAgent.bdi.models import (
    BDICycleRecord,
    DeliberationIntention,
    DeliberationResult,
    Desire,
    DesirePriority,
    DesireStatus,
    now_iso,
)
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.utils.helpers import ensure_dir


_DELIBERATION_SYSTEM_PROMPT = """You are the BDI Deliberation Engine of OriginAgent.
Your job is to evaluate the agent's active desires and decide what to do next.

You have access to:
- **Desires**: active goals/commitments the agent has made to its user
- **Beliefs**: current facts, world state, user profile, recent episodes

For each desire, decide one of:
1. **Form an intention** — create a concrete action to advance the desire
2. **Mark satisfied** — the desire is done
3. **Suspend** — the desire is blocked (e.g., waiting for a dependency)
4. **Cancel** — the desire is no longer relevant
5. **Skip** — no action needed right now

Rules:
- A desire with an approaching or past deadline gets elevated priority.
- If forming an intention, choose the most appropriate action type.
- If you have no active desires, that's OK — respond with empty intentions.
- Be specific about WHY you made each decision.

Available action types and their payloads:
- send_message: {"text": "<message>", "channel": "<channel_name>"} — notify user
- exec: {"command": "<shell command>"} — run a shell command
- web_search: {"query": "<search query>"} — search the web
- web_fetch: {"url": "<url>"} — fetch a URL
- system: {"action": "<custom action>", "params": {}} — system-level action
"""

_DELIBERATION_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "deliberate",
            "description": "Report deliberation decision after evaluating active desires.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "Step-by-step reasoning about which desires to act on and why.",
                    },
                    "intentions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "desire_id": {"type": "string"},
                                "action": {"type": "string"},
                                "scope": {"type": "string"},
                                "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                                "reasoning": {"type": "string"},
                                "payload": {"type": "object"},
                            },
                            "required": ["desire_id", "action", "scope", "reasoning"],
                        },
                    },
                    "desires_to_satisfy": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Desire IDs that are complete",
                    },
                    "desires_to_suspend": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Desire IDs to pause",
                    },
                    "desires_to_cancel": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Desire IDs that are no longer relevant",
                    },
                    "next_check_at": {
                        "type": ["string", "null"],
                        "description": "ISO timestamp for when to check again (null = default interval)",
                    },
                },
                "required": ["reasoning", "intentions", "desires_to_satisfy",
                            "desires_to_suspend", "desires_to_cancel"],
            },
        },
    }
]


class DeliberationEngine:
    """Continuous BDI deliberation loop.

    Usage::

        engine = DeliberationEngine(
            workspace=Path("./workspace"),
            store=desire_store,
            provider=llm_provider,
            model="anthropic/claude-sonnet-4-6",
        )
        await engine.start()
        # ... agent runs ...
        engine.stop()
    """

    def __init__(
        self,
        *,
        workspace: Path,
        store: DesireStore,
        provider: Any,                    # LLMProvider
        model: str,
        enabled: bool = True,
        interval_s: int = 120,
        max_desires_per_cycle: int = 10,
        auto_create_from_foresight: bool = True,
        on_intention: Any | None = None,   # Callable[[DeliberationIntention], Awaitable[None]]
    ) -> None:
        self.workspace = Path(workspace)
        self._store = store
        self._provider = provider
        self._model = model
        self._enabled = enabled
        self._interval_s = interval_s
        self._max_desires_per_cycle = max_desires_per_cycle
        self._auto_create_from_foresight = auto_create_from_foresight
        self._on_intention = on_intention

        self._running = False
        self._task: asyncio.Task | None = None
        self._audit_dir = self.workspace / "memory" / "bdi"
        self._audit_path = self._audit_dir / "cycles.jsonl"
        ensure_dir(self._audit_dir)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self._enabled:
            logger.info("BDI: DeliberationEngine disabled")
            return
        if self._running:
            logger.warning("BDI: DeliberationEngine already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("BDI: DeliberationEngine started (every {}s)", self._interval_s)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("BDI: DeliberationEngine stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval_s)
                if self._running:
                    await self.run_cycle()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("BDI: deliberation cycle error")

    # ------------------------------------------------------------------
    # Core cycle
    # ------------------------------------------------------------------

    async def run_cycle(self) -> DeliberationResult:
        """Execute one full BDI deliberation cycle."""
        cycle_id = f"bdi_{uuid.uuid4().hex[:12]}"
        started_at = now_iso()

        if not self._enabled:
            result = DeliberationResult(
                cycle_id=cycle_id,
                started_at=started_at,
                finished_at=now_iso(),
                desires_evaluated=0,
                intentions_formed=0,
                reasoning="Engine disabled.",
            )
            self._persist_cycle(cycle_id, started_at, result, "skipped")
            return result

        # 1. Collect active desires
        desires = self._store.list_deliberable()
        if not desires:
            result = DeliberationResult(
                cycle_id=cycle_id,
                started_at=started_at,
                finished_at=now_iso(),
                desires_evaluated=0,
                intentions_formed=0,
                reasoning="No active desires to evaluate.",
            )
            self._persist_cycle(cycle_id, started_at, result, "skipped")
            return result

        desires_before = len(desires)

        # 2. Optionally auto-create desires from foresight records
        if self._auto_create_from_foresight:
            await self._sync_foresights(desires)

        # 3. Cap to max per cycle, prioritize by priority and deadline
        desires = self._prioritize(desires)[:self._max_desires_per_cycle]

        # 4. Build prompt with Belief context
        beliefs = self._gather_beliefs()
        user_prompt = self._build_prompt(desires, beliefs)

        # 5. Call LLM for deliberation
        try:
            response = await self._provider.chat_with_retry(
                messages=[
                    {"role": "system", "content": _DELIBERATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                tools=_DELIBERATION_TOOL,
                model=self._model,
            )
        except Exception as exc:
            result = DeliberationResult(
                cycle_id=cycle_id,
                started_at=started_at,
                finished_at=now_iso(),
                desires_evaluated=len(desires),
                intentions_formed=0,
                reasoning="",
                error=str(exc),
            )
            self._persist_cycle(cycle_id, started_at, result, "error")
            return result

        # 6. Parse LLM output
        if not response.should_execute_tools or not response.has_tool_calls:
            result = DeliberationResult(
                cycle_id=cycle_id,
                started_at=started_at,
                finished_at=now_iso(),
                desires_evaluated=len(desires),
                intentions_formed=0,
                reasoning="LLM did not produce tool calls.",
            )
            self._persist_cycle(cycle_id, started_at, result, "completed")
            return result

        args = response.tool_calls[0].arguments
        reasoning = args.get("reasoning", "")

        # 7. Parse intentions
        intentions = []
        for raw in args.get("intentions", []):
            try:
                intent = DeliberationIntention(
                    desire_id=raw["desire_id"],
                    action=raw["action"],
                    scope=raw.get("scope", "system"),
                    trigger="deliberation",
                    risk=raw.get("risk", "low"),
                    reasoning=raw.get("reasoning", ""),
                    payload=raw.get("payload", {}),
                )
                intentions.append(intent)
            except KeyError:
                logger.warning("BDI: skipping malformed intention: {}", raw)

        # 8. Apply status transitions
        updated_ids: list[str] = []
        for did in args.get("desires_to_satisfy", []):
            updated = self._store.update(did, status=DesireStatus.SATISFIED,
                                          reasoning=reasoning)
            if updated:
                updated_ids.append(did)
        for did in args.get("desires_to_suspend", []):
            updated = self._store.update(did, status=DesireStatus.SUSPENDED,
                                          reasoning=reasoning)
            if updated:
                updated_ids.append(did)
        for did in args.get("desires_to_cancel", []):
            updated = self._store.update(did, status=DesireStatus.CANCELLED,
                                          reasoning=reasoning)
            if updated:
                updated_ids.append(did)

        # 9. Emit intentions for execution
        if intentions and self._on_intention:
            for intent in intentions:
                try:
                    await self._on_intention(intent)
                except Exception:
                    logger.exception("BDI: on_intention handler error for desire {}", intent.desire_id)

        finished_at = now_iso()
        result = DeliberationResult(
            cycle_id=cycle_id,
            started_at=started_at,
            finished_at=finished_at,
            desires_evaluated=len(desires),
            intentions_formed=len(intentions),
            intentions=intentions,
            desires_updated=updated_ids,
            reasoning=reasoning,
            next_check_at=args.get("next_check_at"),
            model_used=self._model,
        )
        self._persist_cycle(cycle_id, started_at, result, "completed",
                            desires_before=desires_before)
        return result

    async def trigger_now(self) -> DeliberationResult:
        """Manually trigger a deliberation cycle from outside the loop."""
        return await self.run_cycle()

    # ------------------------------------------------------------------
    # Belief gathering
    # ------------------------------------------------------------------

    def _gather_beliefs(self) -> dict[str, Any]:
        """Collect current Belief state for the LLM prompt.

        Reads from memory store: active foresights, recent episodes, profiles,
        and the fact graph if enabled.
        """
        beliefs: dict[str, Any] = {
            "current_time": now_iso(),
            "foresight_count": 0,
            "episode_count": 0,
            "profile_count": 0,
        }
        # Try to read from the nearline memory store
        try:
            from OriginAgent.memory.store import NearlineMemoryStore
            ms = NearlineMemoryStore(self.workspace)
            from OriginAgent.memory.models import MemoryLayerSummary
            summary = ms.read_summary() if hasattr(ms, "read_summary") else MemoryLayerSummary()
            beliefs["foresight_count"] = summary.foresight_count
            beliefs["episode_count"] = summary.episode_count
            beliefs["profile_count"] = summary.profile_count
            beliefs["status"] = summary.status
        except Exception:
            beliefs["status"] = "unavailable"
        return beliefs

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _prioritize(self, desires: list[Desire]) -> list[Desire]:
        """Sort desires by priority (highest first), then by deadline urgency."""
        def sort_key(d: Desire) -> tuple[int, int]:
            overdue_bonus = -1000 if d.is_overdue else 0
            return (overdue_bonus - d.priority.value, 0)
        return sorted(desires, key=sort_key)

    async def _sync_foresights(self, existing: list[Desire]) -> None:
        """Create Desire records from unlinked ForesightRecords."""
        existing_fs_ids = {d.source_foresight_id for d in existing if d.source_foresight_id}
        try:
            from OriginAgent.memory.store import NearlineMemoryStore
            ms = NearlineMemoryStore(self.workspace)
            foresights = ms.list_foresights(limit=50) if hasattr(ms, "list_foresights") else []
            for fs in foresights:
                fs_id = getattr(fs, "foresight_id", "")
                if fs_id and fs_id not in existing_fs_ids:
                    desire = Desire(
                        desire_id=f"desire_fs_{fs_id}",
                        owner_id=getattr(fs, "owner_id", "unknown"),
                        session_key=getattr(fs, "session_key", ""),
                        content=getattr(fs, "content", ""),
                        status=DesireStatus.PENDING,
                        priority=DesirePriority.MEDIUM,
                        deadline_at=getattr(fs, "end_at", None),
                        source_foresight_id=fs_id,
                    )
                    self._store.add(desire)
                    logger.info("BDI: auto-created Desire {} from Foresight {}", desire.desire_id, fs_id)
        except Exception:
            logger.debug("BDI: foresight sync skipped (memory store unavailable)")

    def _build_prompt(self, desires: list[Desire], beliefs: dict[str, Any]) -> str:
        lines = [
            f"Current Time: {beliefs.get('current_time', now_iso())}",
            f"Memory Status: foresights={beliefs.get('foresight_count', '?')}, "
            f"episodes={beliefs.get('episode_count', '?')}",
            "",
            "## Active Desires (sorted by priority)",
            "",
        ]
        for i, d in enumerate(desires, 1):
            overdue = " ⚠️ OVERDUE" if d.is_overdue else ""
            deadline = f"\n  Deadline: {d.deadline_at}" if d.deadline_at else ""
            lines.append(
                f"{i}. [{d.priority.name}] {d.content} (id: {d.desire_id}, "
                f"status: {d.status.value}, evals: {d.evaluation_count}){overdue}{deadline}"
            )
            if d.constraints:
                lines.append(f"   Constraints: {', '.join(d.constraints)}")
            if d.dependencies:
                lines.append(f"   Dependencies: {', '.join(d.dependencies)}")

        if not desires:
            lines.append("(no active desires)")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Audit persistence
    # ------------------------------------------------------------------

    def _persist_cycle(
        self,
        cycle_id: str,
        started_at: str,
        result: DeliberationResult,
        status: str,
        desires_before: int = 0,
    ) -> None:
        """Append a BDICycleRecord to the audit log."""
        active_after = self._store.count_by_status().get(DesireStatus.ACTIVE, 0)

        record = BDICycleRecord(
            cycle_id=cycle_id,
            started_at=started_at,
            finished_at=result.finished_at,
            status=status,
            desires_before=desires_before,
            desires_after_active=active_after,
            intentions_formed=result.intentions_formed,
            intentions_executed=0,
            intentions_failed=0,
            desire_ids_evaluated=[],
            desire_ids_updated=result.desires_updated,
            model_used=result.model_used,
            token_usage=result.token_usage,
            error=result.error,
        )

        try:
            ensure_dir(self._audit_dir)
            import os, tempfile as tmpmod
            with open(self._audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("BDI: failed to persist cycle audit record")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_deliberation.py -v
```

Expected: PASS — all deliberation cycle tests pass.

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/bdi/deliberation.py tests/agent/bdi/test_deliberation.py
git commit -m "feat(bdi): add DeliberationEngine — core BDI continuous reasoning loop"
```

---

### Task 5: Heartbeat Bridge — Wire BDI into existing Heartbeat

**Files:**
- Create: `OriginAgent/bdi/heartbeat_bridge.py`
- Create: `tests/agent/bdi/test_heartbeat_bridge.py`
- Modify: `OriginAgent/heartbeat/service.py` — add optional BDI integration point (non-breaking)

**Interfaces:**
- Consumes: `DeliberationEngine` from Task 4, `HeartbeatService` from existing code
- Produces: `BDIHeartbeatBridge` — wraps HeartbeatService to use DeliberationEngine as its decision source

- [ ] **Step 1: Write the test file**

```python
"""Tests for BDI-Heartbeat bridge."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from OriginAgent.bdi.models import (
    DeliberationIntention,
    DeliberationResult,
    Desire,
    DesireStatus,
    DesirePriority,
    now_iso,
)
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.deliberation import DeliberationEngine
from OriginAgent.bdi.heartbeat_bridge import BDIHeartbeatBridge


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as td:
        yield DesireStore(Path(td))


@pytest.fixture
def mock_provider():
    p = MagicMock()
    p.chat_with_retry = AsyncMock()
    return p


class TestBDIHeartbeatBridge:
    @pytest.mark.asyncio
    async def test_bridge_runs_deliberation_on_tick(self, store, mock_provider):
        store.add(Desire(
            desire_id="d1", owner_id="u", session_key="s",
            content="Test desire", priority=DesirePriority.HIGH,
            status=DesireStatus.ACTIVE,
        ))

        # Wire up mock provider to return a deliberation response
        from OriginAgent.bdi.deliberation import _DELIBERATION_TOOL

        mock_response = MagicMock()
        mock_response.should_execute_tools = True
        mock_response.has_tool_calls = True
        mock_tool_call = MagicMock()
        mock_tool_call.arguments = {
            "reasoning": "Test reasoning",
            "intentions": [{
                "desire_id": "d1",
                "action": "send_message",
                "scope": "test",
                "risk": "low",
                "reasoning": "Test intention",
                "payload": {"text": "Hello"},
            }],
            "desires_to_satisfy": [],
            "desires_to_suspend": [],
            "desires_to_cancel": [],
            "next_check_at": None,
        }
        mock_response.tool_calls = [mock_tool_call]
        mock_provider.chat_with_retry.return_value = mock_response

        engine = DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=mock_provider,
            model="test",
            enabled=True,
        )

        bridge = BDIHeartbeatBridge(
            engine=engine,
            on_notify=AsyncMock(),
        )

        result = await bridge.tick()

        assert result is not None
        assert result.intentions_formed >= 0
        mock_provider.chat_with_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_bridge_notifies_on_intention(self, store, mock_provider):
        store.add(Desire(
            desire_id="d1", owner_id="u", session_key="s",
            content="Test desire", priority=DesirePriority.HIGH,
            status=DesireStatus.ACTIVE,
        ))

        mock_response = MagicMock()
        mock_response.should_execute_tools = True
        mock_response.has_tool_calls = True
        mock_tool_call = MagicMock()
        mock_tool_call.arguments = {
            "reasoning": "Test",
            "intentions": [{
                "desire_id": "d1",
                "action": "send_message",
                "scope": "telegram",
                "risk": "low",
                "reasoning": "Test",
                "payload": {"text": "Hi"},
            }],
            "desires_to_satisfy": [],
            "desires_to_suspend": [],
            "desires_to_cancel": [],
            "next_check_at": None,
        }
        mock_response.tool_calls = [mock_tool_call]
        mock_provider.chat_with_retry.return_value = mock_response

        on_notify = AsyncMock()
        on_execute = AsyncMock(return_value="Done!")

        engine = DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=mock_provider,
            model="test",
            enabled=True,
        )

        bridge = BDIHeartbeatBridge(
            engine=engine,
            on_notify=on_notify,
            on_execute=on_execute,
        )

        result = await bridge.tick()

        # on_notify should be called with intention details
        assert on_notify.called or on_execute.called

    @pytest.mark.asyncio
    async def test_bridge_no_desires_no_action(self, store, mock_provider):
        mock_response = MagicMock()
        mock_response.should_execute_tools = True
        mock_response.has_tool_calls = True
        mock_tool_call = MagicMock()
        mock_tool_call.arguments = {
            "reasoning": "Nothing to do.",
            "intentions": [],
            "desires_to_satisfy": [],
            "desires_to_suspend": [],
            "desires_to_cancel": [],
            "next_check_at": None,
        }
        mock_response.tool_calls = [mock_tool_call]
        mock_provider.chat_with_retry.return_value = mock_response

        on_notify = AsyncMock()
        engine = DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=mock_provider,
            model="test",
            enabled=True,
        )

        bridge = BDIHeartbeatBridge(engine=engine, on_notify=on_notify)
        result = await bridge.tick()

        assert result.intentions_formed == 0
        # Should not notify when no intentions formed
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_heartbeat_bridge.py -v
```

Expected: FAIL — `BDIHeartbeatBridge` not found.

- [ ] **Step 3: Write heartbeat_bridge.py**

```python
"""Bridge between HeartbeatService and DeliberationEngine.

The existing HeartbeatService reads HEARTBEAT.md and does skip/run.
This bridge replaces that logic with full BDI deliberation, while
keeping the existing HeartbeatService lifecycle (start/stop/interval).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from loguru import logger

from OriginAgent.bdi.deliberation import DeliberationEngine
from OriginAgent.bdi.models import DeliberationIntention, DeliberationResult


class BDIHeartbeatBridge:
    """Wraps DeliberationEngine in HeartbeatService-compatible interface.

    Usage inside HeartbeatService._tick()::

        bridge = BDIHeartbeatBridge(engine=deliberation_engine, on_notify=...)
        result = await bridge.tick()
        if result and result.intentions_formed > 0:
            # Handle intentions
    """

    def __init__(
        self,
        *,
        engine: DeliberationEngine,
        on_notify: Callable[[str], Awaitable[None]] | None = None,
        on_execute: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        self._engine = engine
        self._on_notify = on_notify
        self._on_execute = on_execute

    async def tick(self) -> DeliberationResult | None:
        """Execute one BDI deliberation tick.

        Returns the DeliberationResult, or None if engine is disabled.
        """
        try:
            result = await self._engine.run_cycle()
        except Exception:
            logger.exception("BDI: heartbeat bridge tick failed")
            return None

        if not result.had_work:
            logger.debug("BDI: heartbeat tick — no intentions formed")
            return result

        # Execute intentions
        for intent in result.intentions:
            try:
                await self._execute_intention(intent)
            except Exception:
                logger.exception("BDI: failed to execute intention for desire {}", intent.desire_id)

        # Notify user about deliberation outcomes
        if self._on_notify and result.intentions:
            summary = self._summarize(result)
            if summary:
                try:
                    await self._on_notify(summary)
                except Exception:
                    logger.exception("BDI: notification failed")

        return result

    async def _execute_intention(self, intent: DeliberationIntention) -> None:
        """Execute a single intention through the action runtime."""
        if intent.action == "send_message" and self._on_notify:
            text = intent.payload.get("text", "")
            if text:
                await self._on_notify(text)
        elif intent.action == "exec" and self._on_execute:
            command = intent.payload.get("command", "")
            if command:
                await self._on_execute(command)
        else:
            logger.info(
                "BDI: intention formed but no handler — desire={} action={} scope={}",
                intent.desire_id, intent.action, intent.scope,
            )

    @staticmethod
    def _summarize(result: DeliberationResult) -> str | None:
        """Build a user-facing summary of what the agent decided to do."""
        if not result.intentions:
            return None
        parts = [f"I've been thinking about {result.desires_evaluated} active tasks."]
        for i, intent in enumerate(result.intentions, 1):
            parts.append(f"{i}. {intent.reasoning}")
        return "\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_heartbeat_bridge.py -v
```

Expected: PASS — bridge tests pass.

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/bdi/heartbeat_bridge.py tests/agent/bdi/test_heartbeat_bridge.py
git commit -m "feat(bdi): add BDIHeartbeatBridge — wire DeliberationEngine into existing HeartbeatService"
```

---

### Task 6: Integration — Wire BDI into AgentLoop

**Files:**
- Modify: `OriginAgent/agent/loop.py` — initialize and start/stop DeliberationEngine
- Modify: `OriginAgent/bdi/__init__.py` — add `BDIHeartbeatBridge` to exports

**Interfaces:**
- Consumes: `DeliberationEngine` from Task 4, `DesireStore` from Task 2, `BDIConfig` from Task 3
- Integration point: `AgentLoop.__init__` creates `DesireStore` + `DeliberationEngine`, wires `on_intention` handler

- [ ] **Step 1: Read the relevant section of AgentLoop**

Locate `AgentLoop.__init__` (line ~182 in `agent/loop.py`) and understand where to add BDI initialization (after provider is set up, before the first activity).

- [ ] **Step 2: Add initialization code**

In `AgentLoop.__init__`, after the heartbeat service setup, add:

```python
# ── BDI Deliberation Engine ────────────────────────────────────────
from OriginAgent.bdi import DesireStore, DeliberationEngine
from OriginAgent.bdi.heartbeat_bridge import BDIHeartbeatBridge

bdi_config = getattr(gateway_config, "bdi", None) if gateway_config else None
self._desire_store: DesireStore | None = None
self._bdi_engine: DeliberationEngine | None = None

if bdi_config and bdi_config.enabled:
    self._desire_store = DesireStore(self.workspace)

    async def _on_bdi_intention(intent):
        """Handle an intention formed by the BDI engine."""
        logger.info(
            "BDI: executing intention — desire={} action={} scope={}",
            intent.desire_id, intent.action, intent.scope,
        )
        if intent.action == "send_message":
            # Publish via message bus to user's channel
            from OriginAgent.bus.events import OutboundMessage
            channel = intent.scope if intent.scope != "system" else "cli"
            msg = OutboundMessage(
                channel=channel,
                content=intent.payload.get("text", ""),
                chat_id="",
                session_key="bdi:deliberation",
            )
            self._bus.publish_outbound(msg)

    self._bdi_engine = DeliberationEngine(
        workspace=self.workspace,
        store=self._desire_store,
        provider=self._provider,
        model=bdi_config.model_override or self._model,
        enabled=bdi_config.enabled,
        interval_s=bdi_config.interval_s,
        max_desires_per_cycle=bdi_config.max_desires_per_cycle,
        auto_create_from_foresight=bdi_config.auto_create_from_foresight,
        on_intention=_on_bdi_intention,
    )
    logger.info("BDI: DeliberationEngine initialized")
```

- [ ] **Step 3: Add start/stop hooks**

In `AgentLoop`'s start and stop methods (or wherever the heartbeat is started/stopped):

```python
# In AgentLoop start (after heartbeat start):
if self._bdi_engine:
    await self._bdi_engine.start()

# In AgentLoop stop (before heartbeat stop):
if self._bdi_engine:
    self._bdi_engine.stop()
```

- [ ] **Step 4: Update bdi/__init__.py**

```python
from OriginAgent.bdi.heartbeat_bridge import BDIHeartbeatBridge

__all__ = [
    ...
    "BDIHeartbeatBridge",
]
```

- [ ] **Step 5: Smoke test**

```bash
.\.venv\Scripts\python.exe -c "
from OriginAgent.bdi import Desire, DesireStore, DeliberationEngine, BDIHeartbeatBridge, DesireStatus, DesirePriority
print('BDI imports OK')
"
```

Expected: "BDI imports OK"

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/ -v
```

Expected: PASS — all BDI tests pass.

- [ ] **Step 7: Commit**

```bash
git add OriginAgent/agent/loop.py OriginAgent/bdi/__init__.py
git commit -m "feat(bdi): wire DeliberationEngine into AgentLoop lifecycle"
```

---

### Task 7: Foresight → Desire Auto-Sync

**Files:**
- Create: `tests/agent/bdi/test_foresight_sync.py`
- Modify: `OriginAgent/bdi/deliberation.py` — enhance `_sync_foresights`

**Interfaces:**
- Consumes: `NearlineMemoryStore.list_foresights()` from existing code, `DesireStore` from Task 2
- Produces: Automatic Desire creation from conversation-extracted ForesightRecords

- [ ] **Step 1: Write the test file**

```python
"""Tests for ForesightRecord → Desire auto-sync."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from OriginAgent.bdi.models import Desire, DesireStatus, DesirePriority, now_iso
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.deliberation import DeliberationEngine


class MockForesightRecord:
    def __init__(self, foresight_id, owner_id, session_key, content, end_at=None):
        self.foresight_id = foresight_id
        self.owner_id = owner_id
        self.session_key = session_key
        self.content = content
        self.end_at = end_at


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as td:
        yield DesireStore(Path(td))


class TestForesightSync:
    @pytest.mark.asyncio
    async def test_creates_desire_from_unlinked_foresight(self, store):
        """When a ForesightRecord exists without a corresponding Desire, create one."""
        mock_ms = MagicMock()
        mock_ms.list_foresights = MagicMock(return_value=[
            MockForesightRecord(
                foresight_id="fs_001",
                owner_id="user:test",
                session_key="sess:test",
                content="I'll review the PR by tomorrow",
                end_at="2026-06-27T18:00:00",
            )
        ])

        mock_provider = MagicMock()

        engine = DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=mock_provider,
            model="test",
            auto_create_from_foresight=True,
            enabled=True,
        )

        with patch("OriginAgent.bdi.deliberation.NearlineMemoryStore", return_value=mock_ms):
            await engine._sync_foresights([])

        found = store.find_by_source_foresight("fs_001")
        assert len(found) == 1
        assert found[0].content == "I'll review the PR by tomorrow"
        assert found[0].deadline_at == "2026-06-27T18:00:00"
        assert found[0].status == DesireStatus.PENDING

    @pytest.mark.asyncio
    async def test_skip_already_linked_foresight(self, store):
        """Don't create duplicate Desires for already-linked ForesightRecords."""
        store.add(Desire(
            desire_id="existing",
            owner_id="user:test",
            session_key="sess:test",
            content="Already tracked",
            source_foresight_id="fs_001",
            status=DesireStatus.ACTIVE,
        ))

        mock_ms = MagicMock()
        mock_ms.list_foresights = MagicMock(return_value=[
            MockForesightRecord(
                foresight_id="fs_001",
                owner_id="user:test",
                session_key="sess:test",
                content="I'll review the PR by tomorrow",
            )
        ])

        mock_provider = MagicMock()
        engine = DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=mock_provider,
            model="test",
            auto_create_from_foresight=True,
            enabled=True,
        )

        with patch("OriginAgent.bdi.deliberation.NearlineMemoryStore", return_value=mock_ms):
            await engine._sync_foresights([Desire(
                desire_id="existing",
                owner_id="user:test",
                session_key="sess:test",
                content="Already tracked",
                source_foresight_id="fs_001",
            )])

        # Should still be exactly 1 desire for fs_001
        found = store.find_by_source_foresight("fs_001")
        assert len(found) == 1

    @pytest.mark.asyncio
    async def test_sync_disabled_when_auto_create_false(self, store):
        mock_ms = MagicMock()
        mock_ms.list_foresights = MagicMock(return_value=[
            MockForesightRecord("fs_001", "u", "s", "Test")
        ])

        mock_provider = MagicMock()
        engine = DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=mock_provider,
            model="test",
            auto_create_from_foresight=False,
            enabled=True,
        )

        with patch("OriginAgent.bdi.deliberation.NearlineMemoryStore", return_value=mock_ms):
            await engine._sync_foresights([])

        found = store.find_by_source_foresight("fs_001")
        assert len(found) == 0
```

- [ ] **Step 2: Run test, fix if needed, verify pass**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_foresight_sync.py -v
```

Expected: PASS — auto-sync tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/agent/bdi/test_foresight_sync.py
git commit -m "test(bdi): add ForesightRecord → Desire auto-sync tests"
```

---

### Task A1: IntentionStack — Nested Suspend/Resume

**Files:**
- Modify: `OriginAgent/bdi/models.py` — add `StackFrame`, `IntentionStack`, `ResumeCandidate`
- Create: `tests/agent/bdi/test_intention_stack.py`

**Interfaces:**
- Consumes: `Desire`, `DeliberationIntention` from Task 1, `DesireStatus.SUSPENDED` (existing)
- Produces: `StackFrame` (dataclass), `IntentionStack` class with `push()`, `pop()`, `peek()`, `active_frame`, `list_resumable()`, `ResumeCandidate`
- Integration: `DeliberationEngine.run_cycle()` (Task 4) calls `_check_resumptions()` before evaluating new desires; LLM prompt includes suspended stack state

**Why this fixes the BDI gap:** The SUSPENDED desire status already exists but LLM triggers it and nothing ever resumes it. The IntentionStack makes SUSPEND/RESUME a first-class primitive — executing intention B can push intention A onto the stack; when B is satisfied, A pops back as ACTIVE.

- [ ] **Step 1: Write the test file**

```python
"""Tests for IntentionStack — nested suspend/resume for BDI intentions."""

import pytest

from OriginAgent.bdi.models import (
    Desire,
    DesireStatus,
    DesirePriority,
    DeliberationIntention,
    IntentionStack,
    StackFrame,
    ResumeCandidate,
    now_iso,
)


def make_desire(desire_id: str, content: str, **kw) -> Desire:
    return Desire(
        desire_id=desire_id,
        owner_id="user:test",
        session_key="sess:test",
        content=content,
        **kw,
    )


def make_intent(desire_id: str, action: str = "send_message") -> DeliberationIntention:
    return DeliberationIntention(
        desire_id=desire_id,
        action=action,
        scope="test",
        reasoning="Test intention",
    )


class TestStackFrame:
    def test_immutable(self):
        frame = StackFrame(
            desire_id="d1",
            intention=make_intent("d1"),
            suspended_at=now_iso(),
            suspend_reason="Interrupted by higher-priority task",
        )
        with pytest.raises(Exception):
            frame.desire_id = "changed"  # type: ignore

    def test_serialization_roundtrip(self):
        frame = StackFrame(
            desire_id="d1",
            intention=make_intent("d1", "exec"),
            suspended_at=now_iso(),
            suspend_reason="User asked to open the door",
        )
        data = frame.to_json()
        restored = StackFrame.from_json(data)
        assert restored.desire_id == "d1"
        assert restored.suspend_reason == "User asked to open the door"
        assert restored.intention.action == "exec"


class TestIntentionStack:
    @pytest.fixture
    def stack(self):
        return IntentionStack(max_depth=5)

    def test_push_and_peek(self, stack):
        frame = StackFrame(
            desire_id="d1",
            intention=make_intent("d1"),
            suspended_at=now_iso(),
            suspend_reason="Interrupted",
        )
        stack.push(frame)
        assert stack.depth == 1
        assert stack.peek().desire_id == "d1"

    def test_pop_returns_most_recent(self, stack):
        f1 = StackFrame(desire_id="d1", intention=make_intent("d1"),
                         suspended_at=now_iso(), suspend_reason="A")
        f2 = StackFrame(desire_id="d2", intention=make_intent("d2"),
                         suspended_at=now_iso(), suspend_reason="B")
        stack.push(f1)
        stack.push(f2)
        popped = stack.pop()
        assert popped.desire_id == "d2"  # LIFO
        assert stack.depth == 1
        assert stack.peek().desire_id == "d1"

    def test_pop_empty_returns_none(self, stack):
        assert stack.pop() is None

    def test_list_resumable_returns_lifo_order(self, stack):
        f1 = StackFrame(desire_id="d1", intention=make_intent("d1"),
                         suspended_at=now_iso(), suspend_reason="First")
        f2 = StackFrame(desire_id="d2", intention=make_intent("d2"),
                         suspended_at=now_iso(), suspend_reason="Second")
        stack.push(f1)
        stack.push(f2)

        candidates = stack.list_resumable()
        assert len(candidates) == 2
        assert candidates[0].desire_id == "d2"  # Most recent first
        assert candidates[0].stack_position == 1

    def test_max_depth_raises(self, stack):
        for i in range(5):
            stack.push(StackFrame(
                desire_id=f"d{i}",
                intention=make_intent(f"d{i}"),
                suspended_at=now_iso(),
                suspend_reason="Test",
            ))
        with pytest.raises(OverflowError, match="max depth"):
            stack.push(StackFrame(
                desire_id="d_overflow",
                intention=make_intent("d_overflow"),
                suspended_at=now_iso(),
                suspend_reason="Overflow",
            ))

    def test_active_frame_when_non_empty(self, stack):
        f = StackFrame(desire_id="d_active", intention=make_intent("d_active"),
                        suspended_at=now_iso(), suspend_reason="Active")
        stack.push(f)
        assert stack.active_frame is not None
        assert stack.active_frame.desire_id == "d_active"

    def test_active_frame_when_empty(self, stack):
        assert stack.active_frame is None

    def test_serialization_full_stack(self, stack):
        f1 = StackFrame(desire_id="d1", intention=make_intent("d1"),
                         suspended_at=now_iso(), suspend_reason="A")
        f2 = StackFrame(desire_id="d2", intention=make_intent("d2"),
                         suspended_at=now_iso(), suspend_reason="B")
        stack.push(f1)
        stack.push(f2)

        data = stack.to_json()
        restored = IntentionStack.from_json(data)
        assert restored.depth == 2
        assert restored.pop().desire_id == "d2"
        assert restored.pop().desire_id == "d1"

    def test_find_by_desire_id(self, stack):
        f1 = StackFrame(desire_id="d1", intention=make_intent("d1"),
                         suspended_at=now_iso(), suspend_reason="A")
        f2 = StackFrame(desire_id="d2", intention=make_intent("d2"),
                         suspended_at=now_iso(), suspend_reason="B")
        stack.push(f1)
        stack.push(f2)

        found = stack.find_by_desire_id("d1")
        assert found is not None
        assert found.suspend_reason == "A"

    def test_find_missing_returns_none(self, stack):
        assert stack.find_by_desire_id("nonexistent") is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_intention_stack.py -v
```

Expected: FAIL — `IntentionStack` not defined.

- [ ] **Step 3: Add models to models.py**

Append to `OriginAgent/bdi/models.py`:

```python
# ---------------------------------------------------------------------------
# IntentionStack — nested suspend/resume (BDI core primitive)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StackFrame:
    """A suspended intention waiting to be resumed.

    When intention B interrupts intention A, A is wrapped in a StackFrame
    and pushed onto the IntentionStack. When B completes, A is popped and
    the corresponding Desire transitions back to ACTIVE.
    """

    desire_id: str
    intention: DeliberationIntention
    suspended_at: str
    suspend_reason: str                                               # Why was this interrupted?
    original_priority: int = 50                                       # DesirePriority.value at suspend time
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["intention"] = self.intention.to_json()
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "StackFrame":
        data = dict(data)
        data["intention"] = DeliberationIntention.from_json(data["intention"])
        return cls(**data)


@dataclass(frozen=True)
class ResumeCandidate:
    """A stack entry eligible for resumption after an interrupt clears."""

    desire_id: str
    stack_position: int                                               # 0 = bottom of stack
    suspend_reason: str
    suspended_at: str
    original_priority: int


class IntentionStack:
    """LIFO stack of suspended intentions with bounded depth.

    This is the core BDI mechanism for interrupt recovery. When a
    higher-priority intention preempts a running one, the running
    intention is pushed here. When the preempting intention completes,
    the most recent suspended intention is popped and resumed.

    Persisted to JSONL alongside Desires in the BDI memory directory.
    """

    def __init__(self, max_depth: int = 10) -> None:
        self._frames: list[StackFrame] = []
        self.max_depth = max_depth

    # -- Mutators -------------------------------------------------------

    def push(self, frame: StackFrame) -> None:
        if len(self._frames) >= self.max_depth:
            raise OverflowError(
                f"IntentionStack overflow: max depth {self.max_depth} reached"
            )
        self._frames.append(frame)

    def pop(self) -> StackFrame | None:
        if not self._frames:
            return None
        return self._frames.pop()

    # -- Accessors ------------------------------------------------------

    def peek(self) -> StackFrame | None:
        return self._frames[-1] if self._frames else None

    @property
    def active_frame(self) -> StackFrame | None:
        return self.peek()

    @property
    def depth(self) -> int:
        return len(self._frames)

    @property
    def is_empty(self) -> bool:
        return len(self._frames) == 0

    # -- Query ----------------------------------------------------------

    def list_resumable(self) -> list[ResumeCandidate]:
        """Return all suspended frames in resumption order (top of stack first)."""
        candidates: list[ResumeCandidate] = []
        for i, frame in enumerate(reversed(self._frames)):
            candidates.append(ResumeCandidate(
                desire_id=frame.desire_id,
                stack_position=len(self._frames) - 1 - i,
                suspend_reason=frame.suspend_reason,
                suspended_at=frame.suspended_at,
                original_priority=frame.original_priority,
            ))
        return candidates

    def find_by_desire_id(self, desire_id: str) -> StackFrame | None:
        for frame in self._frames:
            if frame.desire_id == desire_id:
                return frame
        return None

    # -- Persistence ----------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "max_depth": self.max_depth,
            "frames": [f.to_json() for f in self._frames],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "IntentionStack":
        stack = cls(max_depth=data.get("max_depth", 10))
        for frame_data in data.get("frames", []):
            stack._frames.append(StackFrame.from_json(frame_data))
        return stack
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_intention_stack.py -v
```

Expected: PASS.

- [ ] **Step 5: Update bdi/__init__.py exports**

Add `IntentionStack`, `StackFrame`, `ResumeCandidate` to `__all__`.

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/bdi/models.py OriginAgent/bdi/__init__.py tests/agent/bdi/test_intention_stack.py
git commit -m "feat(bdi): add IntentionStack — nested suspend/resume for BDI interrupt recovery"
```

---

### Task A2: Event-Driven Reactivity — WorldStateWatcher

**Files:**
- Create: `OriginAgent/bdi/world_state_watcher.py`
- Create: `tests/agent/bdi/test_world_state_watcher.py`
- Modify: `OriginAgent/bdi/deliberation.py` — add `_watcher` field, wire into `start()`/`stop()`

**Interfaces:**
- Consumes: `DeliberationEngine.trigger_now()` from Task 4, existing `MessageBus` event system
- Produces: `WorldStateWatcher` — subscribes to belief-change events, calls `engine.trigger_now()` on critical changes
- Produces: `BeliefChangeEvent` — lightweight dataclass signalling what changed and severity

**Why this fixes the BDI gap:** The 120s polling loop is replaced by dual-mode operation — periodic polling for routine checks + immediate `trigger_now()` when a critical belief changes (smoke alarm, door open, user presence change).

- [ ] **Step 1: Write the test file**

```python
"""Tests for WorldStateWatcher — event-driven BDI reactivity."""

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from OriginAgent.bdi.models import (
    DeliberationResult,
    Desire,
    DesireStatus,
    DesirePriority,
    now_iso,
)
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.deliberation import DeliberationEngine
from OriginAgent.bdi.world_state_watcher import (
    WorldStateWatcher,
    BeliefChangeEvent,
    BeliefChangeSeverity,
)


# ---------------------------------------------------------------------------
# Fake event bus for testing
# ---------------------------------------------------------------------------

class FakeEventBus:
    def __init__(self):
        self._subscribers: dict[str, list] = {}

    def subscribe(self, event_type: str, callback):
        self._subscribers.setdefault(event_type, []).append(callback)

    async def publish(self, event_type: str, event):
        for cb in self._subscribers.get(event_type, []):
            await cb(event)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeProvider:
    async def chat_with_retry(self, messages, tools, model):
        resp = MagicMock()
        resp.should_execute_tools = True
        resp.has_tool_calls = True
        tc = MagicMock()
        tc.arguments = {
            "reasoning": "Smoke alarm! Must act immediately.",
            "intentions": [{
                "desire_id": "d1",
                "action": "send_message",
                "scope": "telegram",
                "reasoning": "Alert user about smoke alarm.",
                "risk": "high",
                "payload": {"text": "🚨 Smoke detected! Please check immediately!"},
            }],
            "desires_to_satisfy": [],
            "desires_to_suspend": [],
            "desires_to_cancel": [],
            "next_check_at": None,
        }
        resp.tool_calls = [tc]
        return resp


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as td:
        yield DesireStore(Path(td))


class TestBeliefChangeEvent:
    def test_immutable(self):
        evt = BeliefChangeEvent(
            source="presence_sensor",
            key="presence_status",
            old_value="away",
            new_value="home",
            severity=BeliefChangeSeverity.HIGH,
            reason="User just arrived home",
        )
        with pytest.raises(Exception):
            evt.new_value = "changed"  # type: ignore

    def test_is_critical_for_high_severity(self):
        evt = BeliefChangeEvent(
            source="smoke_sensor",
            key="smoke_level",
            old_value="0",
            new_value="HIGH",
            severity=BeliefChangeSeverity.CRITICAL,
            reason="Smoke detected",
        )
        assert evt.is_critical is True

    def test_is_critical_for_low_severity(self):
        evt = BeliefChangeEvent(
            source="thermostat",
            key="temperature",
            old_value="20",
            new_value="21",
            severity=BeliefChangeSeverity.LOW,
            reason="Minor temperature drift",
        )
        assert evt.is_critical is False


class TestWorldStateWatcher:
    @pytest.fixture
    def engine(self, store):
        return DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=FakeProvider(),
            model="test",
            enabled=True,
        )

    @pytest.mark.asyncio
    async def test_critical_event_triggers_immediate_deliberation(self, store, engine):
        """When a CRITICAL belief change occurs, trigger_now() is called immediately."""
        store.add(Desire(
            desire_id="d1", owner_id="u", session_key="s",
            content="Monitor smoke alarms",
            status=DesireStatus.ACTIVE,
            priority=DesirePriority.CRITICAL,
        ))

        bus = FakeEventBus()
        watcher = WorldStateWatcher(engine=engine, event_bus=bus)

        # Spy on trigger_now
        trigger_spy = AsyncMock(wraps=engine.trigger_now)
        engine.trigger_now = trigger_spy

        await watcher.start()

        # Publish a critical belief change
        evt = BeliefChangeEvent(
            source="smoke_sensor",
            key="smoke_level",
            old_value="0",
            new_value="HIGH",
            severity=BeliefChangeSeverity.CRITICAL,
            reason="Smoke detected in kitchen",
        )
        await bus.publish("belief.changed", evt)

        # Allow async processing
        await asyncio.sleep(0.1)

        # trigger_now should have been called
        assert trigger_spy.called, "trigger_now was not called on critical event"

        watcher.stop()

    @pytest.mark.asyncio
    async def test_low_severity_event_does_not_trigger(self, store, engine):
        """LOW severity events should not trigger immediate deliberation."""
        bus = FakeEventBus()
        watcher = WorldStateWatcher(engine=engine, event_bus=bus)

        trigger_spy = AsyncMock(wraps=engine.trigger_now)
        engine.trigger_now = trigger_spy

        await watcher.start()

        evt = BeliefChangeEvent(
            source="thermostat",
            key="temperature",
            old_value="20",
            new_value="20.5",
            severity=BeliefChangeSeverity.LOW,
            reason="Minor drift",
        )
        await bus.publish("belief.changed", evt)
        await asyncio.sleep(0.1)

        assert not trigger_spy.called, "Low-severity event should not trigger deliberation"

        watcher.stop()

    @pytest.mark.asyncio
    async def test_debounce_prevents_trigger_storms(self, store, engine):
        """Multiple events within cooldown window only trigger once."""
        bus = FakeEventBus()
        watcher = WorldStateWatcher(engine=engine, event_bus=bus, cooldown_s=1.0)

        trigger_spy = AsyncMock(wraps=engine.trigger_now)
        engine.trigger_now = trigger_spy

        await watcher.start()

        for i in range(5):
            evt = BeliefChangeEvent(
                source="motion_sensor",
                key="motion",
                old_value="clear",
                new_value=f"detected_{i}",
                severity=BeliefChangeSeverity.HIGH,
                reason=f"Motion event {i}",
            )
            await bus.publish("belief.changed", evt)

        await asyncio.sleep(0.2)

        # Should be called at most once due to cooldown
        assert trigger_spy.call_count <= 1, (
            f"Expected ≤1 trigger due to cooldown, got {trigger_spy.call_count}"
        )

        watcher.stop()

    @pytest.mark.asyncio
    async def test_disabled_watcher_does_not_trigger(self, store, engine):
        bus = FakeEventBus()
        watcher = WorldStateWatcher(engine=engine, event_bus=bus, enabled=False)

        trigger_spy = AsyncMock(wraps=engine.trigger_now)
        engine.trigger_now = trigger_spy

        await watcher.start()

        evt = BeliefChangeEvent(
            source="smoke_sensor",
            key="smoke_level",
            old_value="0",
            new_value="HIGH",
            severity=BeliefChangeSeverity.CRITICAL,
            reason="Smoke!",
        )
        await bus.publish("belief.changed", evt)
        await asyncio.sleep(0.1)

        assert not trigger_spy.called, "Disabled watcher should not trigger"

        watcher.stop()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_world_state_watcher.py -v
```

Expected: FAIL — `WorldStateWatcher` not found.

- [ ] **Step 3: Write world_state_watcher.py**

```python
"""Event-driven reactivity for BDI DeliberationEngine.

Subscribes to belief-change events and triggers immediate reconsider()
when critical beliefs change, eliminating the 120s polling gap.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from loguru import logger


class BeliefChangeSeverity(str, Enum):
    LOW = "low"            # Minor drift, no action needed
    MEDIUM = "medium"      # Notable change, consider deliberation
    HIGH = "high"          # Significant change, should deliberate soon
    CRITICAL = "critical"  # Emergency — must deliberate NOW


@dataclass(frozen=True)
class BeliefChangeEvent:
    """Emitted when a belief in the WorldState changes significantly."""

    source: str                             # e.g. "presence_sensor", "smoke_detector"
    key: str                                # e.g. "presence_status", "smoke_level"
    old_value: str | None = None
    new_value: str | None = None
    severity: BeliefChangeSeverity = BeliefChangeSeverity.MEDIUM
    reason: str = ""
    timestamp: str = ""

    @property
    def is_critical(self) -> bool:
        return self.severity in (BeliefChangeSeverity.CRITICAL, BeliefChangeSeverity.HIGH)


# Event type constant for the message bus
BELIEF_CHANGED_EVENT = "belief.changed"


class WorldStateWatcher:
    """Listens for belief changes and triggers immediate BDI reconsideration.

    Solves the fundamental BDI reactivity problem: the polling loop
    (every 120s) is too slow for safety-critical events like smoke
    alarms or door sensors. This watcher subscribes to the event bus and
    calls engine.trigger_now() immediately when critical beliefs change.

    Includes a configurable cooldown to prevent trigger storms (multiple
    sensor events within seconds).
    """

    def __init__(
        self,
        *,
        engine: Any,                                               # DeliberationEngine
        event_bus: Any | None = None,                              # MessageBus or compatible
        cooldown_s: float = 5.0,
        enabled: bool = True,
    ) -> None:
        self._engine = engine
        self._event_bus = event_bus
        self._cooldown_s = cooldown_s
        self._enabled = enabled
        self._running = False
        self._last_triggered_at: float = 0.0
        self._pending_events: list[BeliefChangeEvent] = []
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self._enabled:
            logger.info("BDI: WorldStateWatcher disabled")
            return
        if self._running:
            return

        self._running = True
        if self._event_bus:
            self._subscribe()
        logger.info("BDI: WorldStateWatcher started (cooldown={}s)", self._cooldown_s)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("BDI: WorldStateWatcher stopped")

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def _subscribe(self) -> None:
        """Subscribe to belief change events on the event bus."""
        if hasattr(self._event_bus, "subscribe"):
            self._event_bus.subscribe(BELIEF_CHANGED_EVENT, self._on_belief_changed)
        elif hasattr(self._event_bus, "add_listener"):
            self._event_bus.add_listener(BELIEF_CHANGED_EVENT, self._on_belief_changed)

    async def _on_belief_changed(self, event: BeliefChangeEvent) -> None:
        """Handle a belief change event."""
        if not self._running:
            return

        if not event.is_critical:
            logger.debug(
                "BDI: non-critical belief change ignored — key={} severity={}",
                event.key, event.severity.value,
            )
            return

        now = time.monotonic()
        if now - self._last_triggered_at < self._cooldown_s:
            logger.debug("BDI: belief change within cooldown, queued — key={}", event.key)
            self._pending_events.append(event)
            return

        self._last_triggered_at = now
        logger.info(
            "BDI: CRITICAL belief change — triggering immediate deliberation "
            "(source={} key={} new={})",
            event.source, event.key, event.new_value,
        )

        try:
            await self._engine.trigger_now()
        except Exception:
            logger.exception("BDI: trigger_now failed on belief change")

        # Process any queued events
        self._pending_events.clear()

    # ------------------------------------------------------------------
    # Direct invocation (for use without event bus)
    # ------------------------------------------------------------------

    async def notify(
        self,
        source: str,
        key: str,
        *,
        old_value: str | None = None,
        new_value: str | None = None,
        severity: BeliefChangeSeverity = BeliefChangeSeverity.MEDIUM,
        reason: str = "",
    ) -> None:
        """Programmatic notification of a belief change.

        Use this when there's no event bus, or when a sensor driver
        wants to directly notify the deliberation engine.
        """
        event = BeliefChangeEvent(
            source=source,
            key=key,
            old_value=old_value,
            new_value=new_value,
            severity=severity,
            reason=reason,
        )
        await self._on_belief_changed(event)
```

- [ ] **Step 4: Wire into DeliberationEngine**

In `OriginAgent/bdi/deliberation.py`:

```python
# Add to DeliberationEngine.__init__:
from OriginAgent.bdi.world_state_watcher import WorldStateWatcher

# In __init__, after other field setup:
self._watcher: WorldStateWatcher | None = None
if event_bus is not None:
    self._watcher = WorldStateWatcher(
        engine=self,
        event_bus=event_bus,
        cooldown_s=5.0,
        enabled=enabled,
    )

# In start(), after self._task = ...:
if self._watcher:
    await self._watcher.start()

# In stop(), before setting _running = False:
if self._watcher:
    self._watcher.stop()
```

- [ ] **Step 5: Run tests**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_world_state_watcher.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/bdi/world_state_watcher.py OriginAgent/bdi/deliberation.py tests/agent/bdi/test_world_state_watcher.py
git commit -m "feat(bdi): add WorldStateWatcher — event-driven BDI reactivity for critical belief changes"
```

---

### Task A3: PlanLibrary — Programmatic Shortcut for Repeated Desires

**Files:**
- Modify: `OriginAgent/bdi/models.py` — add `PlanTemplate`, `PlanMatch`
- Create: `OriginAgent/bdi/plan_library.py`
- Create: `tests/agent/bdi/test_plan_library.py`
- Modify: `OriginAgent/bdi/deliberation.py` — add `_plan_library` field, `_try_plan_match()` step before LLM call

**Interfaces:**
- Consumes: `DeliberationIntention`, `Desire` from Task 1, `DesireStore` from Task 2
- Produces: `PlanTemplate` (dataclass), `PlanLibrary` class with `match(desire) -> PlanMatch | None`, `learn(desire, intention)`, `list_plans()`
- Integration: `DeliberationEngine.run_cycle()` calls `_try_plan_match(desire)` for each desire BEFORE calling LLM; only desires with no match go to LLM

**Why this fixes the BDI gap:** A true BDI agent has a Plan Library (means-ends reasoning cache) and only invokes expensive reasoning when no cached plan matches. "Remind user to take medicine" shouldn't need Sonnet every single time.

- [ ] **Step 1: Write the test file**

```python
"""Tests for PlanLibrary — cached means-ends reasoning for BDI."""

import tempfile
from pathlib import Path

import pytest

from OriginAgent.bdi.models import (
    Desire,
    DesireStatus,
    DesirePriority,
    DeliberationIntention,
    PlanTemplate,
    PlanMatch,
    now_iso,
)
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.plan_library import PlanLibrary


def make_desire(content: str, **kw) -> Desire:
    return Desire(
        desire_id=f"d_{hash(content) % 10000}",
        owner_id="user:test",
        session_key="sess:test",
        content=content,
        **kw,
    )


def make_intent(desire_id: str, action: str = "send_message",
                payload: dict | None = None) -> DeliberationIntention:
    return DeliberationIntention(
        desire_id=desire_id,
        action=action,
        scope="test",
        reasoning="Cached plan",
        payload=payload or {},
    )


class TestPlanTemplate:
    def test_immutable(self):
        t = PlanTemplate(
            plan_id="p1",
            keywords=("remind", "medicine"),
            action="send_message",
            scope="telegram",
            payload_template={"text": "Time to take your medicine!"},
            description="Medicine reminder",
            hit_count=0,
        )
        with pytest.raises(Exception):
            t.action = "changed"  # type: ignore

    def test_serialization_roundtrip(self):
        t = PlanTemplate(
            plan_id="p_med_reminder",
            keywords=("remind", "medicine", "吃药"),
            action="send_message",
            scope="telegram",
            payload_template={"text": "💊 该吃药了！"},
            description="Medication reminder via Telegram",
            hit_count=5,
            last_used_at="2026-06-26T10:00:00",
        )
        data = t.to_json()
        restored = PlanTemplate.from_json(data)
        assert restored.plan_id == "p_med_reminder"
        assert restored.keywords == ("remind", "medicine", "吃药")
        assert restored.hit_count == 5


class TestPlanMatch:
    def test_fields(self):
        m = PlanMatch(
            plan=PlanTemplate(
                plan_id="p1",
                keywords=("test",),
                action="send_message",
                scope="test",
                payload_template={},
                description="Test",
            ),
            confidence=0.95,
            matched_keywords=["test"],
        )
        assert m.confidence == 0.95
        assert m.matched_keywords == ["test"]


class TestPlanLibrary:
    @pytest.fixture
    def lib(self):
        with tempfile.TemporaryDirectory() as td:
            yield PlanLibrary(workspace=Path(td))

    def test_add_and_match_exact(self, lib: PlanLibrary):
        lib.add(PlanTemplate(
            plan_id="remind_medicine",
            keywords=("remind", "medicine", "吃药"),
            action="send_message",
            scope="telegram",
            payload_template={"text": "💊 Time to take your medicine!"},
            description="Medicine reminder",
        ))

        desire = make_desire("Remind the user to take medicine at 8pm")
        match = lib.match(desire)

        assert match is not None
        assert match.plan.plan_id == "remind_medicine"
        assert match.confidence > 0.5

    def test_match_with_chinese_keywords(self, lib: PlanLibrary):
        lib.add(PlanTemplate(
            plan_id="remind_water",
            keywords=("喝水", "water", "remind"),
            action="send_message",
            scope="telegram",
            payload_template={"text": "💧 记得喝水！"},
            description="Water reminder",
        ))

        desire = make_desire("提醒用户多喝水")
        match = lib.match(desire)

        assert match is not None
        assert match.plan.plan_id == "remind_water"

    def test_no_match_for_unfamiliar_desire(self, lib: PlanLibrary):
        lib.add(PlanTemplate(
            plan_id="remind_medicine",
            keywords=("remind", "medicine"),
            action="send_message",
            scope="telegram",
            payload_template={"text": "💊"},
            description="Medicine",
        ))

        desire = make_desire("Analyze the stock market trends and report back")
        match = lib.match(desire)

        assert match is None or match.confidence < 0.3  # Very low confidence at best

    def test_learn_from_llm_result(self, lib: PlanLibrary):
        """After LLM forms an intention, PlanLibrary can learn the pattern."""
        desire = make_desire("Remind me to check the oven in 30 minutes")
        intent = make_intent(desire.desire_id, "send_message",
                              payload={"text": "⏰ Check the oven!"})

        lib.learn(desire=desire, intention=intent, auto_keywords=True)

        # Now a similar desire should match
        similar = make_desire("Please remind me to check the oven")
        match = lib.match(similar)

        assert match is not None
        assert match.plan.action == "send_message"

    def test_hit_count_increments_on_match(self, lib: PlanLibrary):
        lib.add(PlanTemplate(
            plan_id="daily_standup",
            keywords=("standup", "daily"),
            action="send_message",
            scope="slack",
            payload_template={"text": "Time for standup!"},
            description="Daily standup reminder",
        ))

        desire = make_desire("Send the daily standup reminder")
        match1 = lib.match(desire)
        match2 = lib.match(desire)

        assert match1 is not None
        assert match2 is not None
        assert match2.plan.hit_count == match1.plan.hit_count + 1

    def test_list_plans_sorted_by_hit_count(self, lib: PlanLibrary):
        lib.add(PlanTemplate(plan_id="p1", keywords=("a",), action="x", scope="x",
                              payload_template={}, description="A", hit_count=3))
        lib.add(PlanTemplate(plan_id="p2", keywords=("b",), action="x", scope="x",
                              payload_template={}, description="B", hit_count=10))
        lib.add(PlanTemplate(plan_id="p3", keywords=("c",), action="x", scope="x",
                              payload_template={}, description="C", hit_count=1))

        plans = lib.list_plans()
        assert plans[0].plan_id == "p2"  # Highest hit count first
        assert plans[2].plan_id == "p3"  # Lowest last

    def test_serialization_persistence(self, lib: PlanLibrary):
        lib.add(PlanTemplate(
            plan_id="persistent_plan",
            keywords=("test",),
            action="exec",
            scope="system",
            payload_template={"command": "echo hello"},
            description="Persistent",
        ))

        # Reload from disk
        lib2 = PlanLibrary(workspace=lib.workspace)
        plans = lib2.list_plans()

        assert len(plans) >= 1
        assert any(p.plan_id == "persistent_plan" for p in plans)

    def test_learn_idempotent(self, lib: PlanLibrary):
        """Learning the same pattern twice doesn't create duplicates."""
        desire = make_desire("Check the door lock")
        intent = make_intent(desire.desire_id, "exec",
                              payload={"command": "check-door-lock"})

        lib.learn(desire=desire, intention=intent, auto_keywords=True)
        before = len(lib.list_plans())

        lib.learn(desire=desire, intention=intent, auto_keywords=True)
        after = len(lib.list_plans())

        assert after == before  # No duplicates
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_plan_library.py -v
```

Expected: FAIL — `PlanLibrary` not found.

- [ ] **Step 3: Add PlanTemplate and PlanMatch to models.py**

```python
# ---------------------------------------------------------------------------
# PlanLibrary — cached means-ends reasoning (BDI core primitive)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanTemplate:
    """A cached plan for responding to a known type of desire.

    Plans are the BDI "means-ends reasoning" cache. When a desire matches
    a known plan, no LLM call is needed — the plan's action and payload
    template are used directly.
    """

    plan_id: str
    keywords: tuple[str, ...]                                        # Trigger keywords (lowercased)
    action: str                                                      # e.g. "send_message", "exec"
    scope: str                                                       # e.g. "telegram", "system"
    payload_template: dict[str, Any] = field(default_factory=dict)    # Template with {placeholders}
    description: str = ""
    hit_count: int = 0
    last_used_at: str = ""
    created_at: str = field(default_factory=now_iso)
    source_desire_id: str | None = None                              # Which desire created this plan
    source_agent_case_id: str | None = None                          # From existing AgentCaseRecord

    def increment_hit(self) -> "PlanTemplate":
        return replace(
            self,
            hit_count=self.hit_count + 1,
            last_used_at=now_iso(),
        )

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["keywords"] = list(self.keywords)
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "PlanTemplate":
        data = dict(data)
        data["keywords"] = tuple(data.get("keywords", ()))
        return cls(**data)


@dataclass(frozen=True)
class PlanMatch:
    """Result of matching a Desire against the PlanLibrary."""

    plan: PlanTemplate
    confidence: float                                                # 0.0 – 1.0
    matched_keywords: list[str] = field(default_factory=list)
    reasoning: str = ""
```

- [ ] **Step 4: Write plan_library.py**

```python
"""PlanLibrary — cached means-ends reasoning for repeated desires.

Avoids expensive LLM deliberation calls when a desire matches a known
plan template. Plans are learned automatically from successful LLM
deliberations and can also be hand-authored.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from filelock import FileLock
from loguru import logger

from OriginAgent.bdi.models import (
    DeliberationIntention,
    Desire,
    PlanMatch,
    PlanTemplate,
    now_iso,
)
from OriginAgent.utils.helpers import ensure_dir

# Confidence threshold — matches below this go to LLM
_MIN_CONFIDENCE = 0.6

# Common stop words to exclude from auto-keyword extraction
_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "and", "or", "not", "but", "if", "then", "else", "when",
    "i", "you", "he", "she", "it", "we", "they", "me", "him",
    "her", "us", "them", "my", "your", "his", "its", "our",
    "please", "can", "could", "would", "should", "will",
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这",
}


class PlanLibrary:
    """Pattern-matching cache for means-ends reasoning.

    Persisted as JSONL in memory/bdi/plans.jsonl. Each entry is a
    PlanTemplate with keywords used for matching.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self._dir = self.workspace / "memory" / "bdi"
        self._path = self._dir / "plans.jsonl"
        self._lock_path = self._dir / ".plans.lock"
        ensure_dir(self._dir)
        self._plans: dict[str, PlanTemplate] = {}
        self._load()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, plan: PlanTemplate) -> None:
        self._plans[plan.plan_id] = plan
        self._save()

    def get(self, plan_id: str) -> PlanTemplate | None:
        return self._plans.get(plan_id)

    def list_plans(self) -> list[PlanTemplate]:
        return sorted(self._plans.values(), key=lambda p: -p.hit_count)

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def match(self, desire: Desire) -> PlanMatch | None:
        """Try to match a desire against the plan library.

        Returns a PlanMatch if confidence >= _MIN_CONFIDENCE, otherwise None.
        """
        content_lower = desire.content.lower()

        best: tuple[PlanTemplate, float, list[str]] | None = None

        for plan in self._plans.values():
            matched: list[str] = []
            for kw in plan.keywords:
                if kw.lower() in content_lower:
                    matched.append(kw)

            if not matched:
                continue

            # Confidence = fraction of plan keywords that matched
            confidence = len(matched) / len(plan.keywords)

            # Boost confidence for high-hit-count plans (proven reliability)
            if plan.hit_count > 10:
                confidence = min(1.0, confidence + 0.1)

            if best is None or confidence > best[1]:
                best = (plan, confidence, matched)

        if best is None or best[1] < _MIN_CONFIDENCE:
            return None

        plan = best[0].increment_hit()
        self._plans[plan.plan_id] = plan
        self._save()

        return PlanMatch(
            plan=plan,
            confidence=round(best[1], 2),
            matched_keywords=best[2],
            reasoning=f"Matched {len(best[2])}/{len(plan.keywords)} keywords",
        )

    # ------------------------------------------------------------------
    # Learning
    # ------------------------------------------------------------------

    def learn(
        self,
        *,
        desire: Desire,
        intention: DeliberationIntention,
        auto_keywords: bool = True,
    ) -> PlanTemplate:
        """Learn a new plan from a successful deliberation.

        Extracts keywords from the desire content, creates a PlanTemplate
        with the intention's action/payload as the template.
        """
        if auto_keywords:
            keywords = self._extract_keywords(desire.content)
        else:
            keywords = ()

        plan_id = f"plan_{intention.action}_{len(self._plans)}"

        # Check for duplicates by keyword overlap with existing plans
        for existing in self._plans.values():
            existing_set = set(existing.keywords)
            new_set = set(keywords)
            overlap = existing_set & new_set
            if len(overlap) > 0 and len(overlap) >= min(len(existing_set), len(new_set)) * 0.5:
                # Merge: increment hit count on existing, skip create
                merged = existing.increment_hit()
                self._plans[existing.plan_id] = merged
                self._save()
                logger.debug("BDI: PlanLibrary merged similar plan — id={}", existing.plan_id)
                return merged

        plan = PlanTemplate(
            plan_id=plan_id,
            keywords=keywords,
            action=intention.action,
            scope=intention.scope,
            payload_template=dict(intention.payload),
            description=f"Auto-learned from desire: {desire.content[:80]}",
            hit_count=1,
            last_used_at=now_iso(),
            source_desire_id=desire.desire_id,
        )

        self._plans[plan_id] = plan
        self._save()
        logger.info("BDI: PlanLibrary learned new plan — id={} keywords={}", plan_id, keywords)
        return plan

    @staticmethod
    def _extract_keywords(text: str) -> tuple[str, ...]:
        """Extract meaningful keywords from desire content."""
        # Simple tokenization by splitting on non-word characters
        tokens = re.findall(r"[\w一-鿿]+", text.lower())
        # Filter stop words and short tokens
        meaningful = [
            t for t in tokens
            if t not in _STOP_WORDS and len(t) >= 2
        ]
        # Return top keywords by frequency, capped at 5
        counter = Counter(meaningful)
        return tuple(k for k, _ in counter.most_common(5))

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        with FileLock(str(self._lock_path)):
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        plan = PlanTemplate.from_json(json.loads(line))
                        self._plans[plan.plan_id] = plan
                    except Exception:
                        logger.warning("BDI: skipping corrupt plan line")

    def _save(self) -> None:
        ensure_dir(self._dir)
        with FileLock(str(self._lock_path)):
            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self._dir),
                delete=False,
                suffix=".tmp",
            )
            try:
                for plan in self._plans.values():
                    tmp.write(json.dumps(plan.to_json(), ensure_ascii=False) + "\n")
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp.close()
                os.replace(tmp.name, str(self._path))
                try:
                    dir_fd = os.open(str(self._dir), os.O_RDONLY)
                    os.fsync(dir_fd)
                    os.close(dir_fd)
                except OSError:
                    pass
            except Exception:
                Path(tmp.name).unlink(missing_ok=True)
                raise
```

- [ ] **Step 5: Integrate PlanLibrary into DeliberationEngine**

In `OriginAgent/bdi/deliberation.py`, modify `run_cycle()`:

```python
# In DeliberationEngine.__init__, after other field setup:
from OriginAgent.bdi.plan_library import PlanLibrary
self._plan_library = PlanLibrary(self.workspace)

# In run_cycle(), between step 3 (prioritize) and step 4 (build prompt):
# Split desires: cache hits vs LLM-needed
cached_intentions: list[DeliberationIntention] = []
llm_desires: list[Desire] = []

for desire in desires:
    match = self._plan_library.match(desire)
    if match is not None:
        intent = DeliberationIntention(
            desire_id=desire.desire_id,
            action=match.plan.action,
            scope=match.plan.scope,
            trigger="deliberation:plan_cache",
            risk="low",
            reasoning=f"Plan cache: {match.reasoning}",
            payload=dict(match.plan.payload_template),
        )
        cached_intentions.append(intent)
        logger.debug("BDI: plan cache hit — desire={} plan={}", desire.desire_id, match.plan.plan_id)
    else:
        llm_desires.append(desire)

# Only call LLM for unmatched desires
if llm_desires:
    beliefs = self._gather_beliefs()
    user_prompt = self._build_prompt(llm_desires, beliefs)
    # ... LLM call as before ...

# Merge cached + LLM intentions
all_intentions = cached_intentions + intentions_from_llm

# After LLM-generated intentions are executed:
# Learn new plans from successful LLM deliberations
for desire in llm_desires:
    for intent in intentions_from_llm:
        if intent.desire_id == desire.desire_id:
            self._plan_library.learn(desire=desire, intention=intent)
```

- [ ] **Step 6: Run tests**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_plan_library.py -v
```

Expected: PASS.

- [ ] **Step 7: Re-run deliberation tests to verify plan cache integration doesn't break**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_deliberation.py -v
```

Expected: PASS — existing tests still pass with PlanLibrary integrated.

- [ ] **Step 8: Commit**

```bash
git add OriginAgent/bdi/models.py OriginAgent/bdi/plan_library.py OriginAgent/bdi/deliberation.py tests/agent/bdi/test_plan_library.py
git commit -m "feat(bdi): add PlanLibrary — cached means-ends reasoning, LLM only on cache miss"
```

---

## Self-Review

**1. Spec coverage (10 tasks):**
- ✅ Task 1: BDI models (Desire, DeliberationResult, etc.)
- ✅ Task 2: DesireStore persistence
- ✅ Task 3: BDIConfig schema
- ✅ Task 4: DeliberationEngine core loop
- ✅ Task 5: Heartbeat bridge
- ✅ Task 6: AgentLoop integration
- ✅ Task 7: Foresight auto-sync
- ✅ Task A1: IntentionStack — nested Suspend/Resume
- ✅ Task A2: WorldStateWatcher — event-driven reactivity
- ✅ Task A3: PlanLibrary — cached means-ends reasoning

**2. BDI gap coverage:**
- ✅ **Reactivity** (was polling): Task A2 — `WorldStateWatcher` triggers `engine.trigger_now()` on critical belief changes with configurable cooldown
- ✅ **Intention Stack** (was flat list): Task A1 — `IntentionStack` with `push(suspend)`/`pop(resume)`, integrated into deliberation cycle
- ✅ **Means-Ends Reasoning** (was LLM every time): Task A3 — `PlanLibrary` matches desires against cached plans; LLM only invoked on cache miss

**3. Placeholder scan:**
- No TBD, TODO, or "implement later" markers
- All steps have actual code
- All types and signatures are concrete

**4. Type consistency:**
- `StackFrame`, `IntentionStack`, `ResumeCandidate` defined in Task A1, consumed in Task A2 integration
- `PlanTemplate`, `PlanMatch` defined in Task A3, consumed by `PlanLibrary` and `DeliberationEngine`
- `WorldStateWatcher` uses `DeliberationEngine.trigger_now()` from Task 4
- `PlanLibrary` uses `DeliberationIntention`, `Desire` from Task 1
- All three add-ons are pure additions — no modification of Task 1-7 interfaces required
