"""Immutable data models for the BDI deliberation system."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from OriginAgent.agent.action_runtime import ActionIntent


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
    intentions_formed: int = 0
    intentions: list[DeliberationIntention] = field(default_factory=list)
    desires_updated: list[str] = field(default_factory=list)  # desire_ids
    reasoning: str = ""
    next_check_at: str | None = None   # When should the engine run next?
    model_used: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)
    error: str = ""

    def __post_init__(self) -> None:
        """Derive intentions_formed from the length of intentions list."""
        object.__setattr__(self, "intentions_formed", len(self.intentions))

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
