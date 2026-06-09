"""Shared cognitive event and decision models for backend cognition."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


CognitiveEventType = Literal[
    "goal_nudge",
    "pending_confirmation_nudge",
    "scheduled_reminder",
    "foresight_nudge",
]
CognitivePriority = Literal["low", "medium", "high"]
CognitiveAction = Literal["emit", "suppress", "skip", "error"]
CognitiveOutcome = Literal["emitted", "suppressed", "skipped", "errored"]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


@dataclass(frozen=True)
class CognitiveEvent:
    """One backend cognition opportunity before delivery policy is applied."""

    event_id: str
    session_key: str
    event_type: CognitiveEventType
    source_type: str
    source_reference: str
    summary: str = ""
    priority: CognitivePriority = "medium"
    created_at: str = field(default_factory=_utcnow_iso)
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _normalize_text(self.event_id, max_chars=160))
        object.__setattr__(self, "session_key", _normalize_text(self.session_key, max_chars=240))
        object.__setattr__(self, "source_type", _normalize_text(self.source_type, max_chars=120))
        object.__setattr__(self, "source_reference", _normalize_text(self.source_reference, max_chars=240))
        object.__setattr__(self, "summary", _normalize_text(self.summary, max_chars=240))
        if self.priority not in {"low", "medium", "high"}:
            object.__setattr__(self, "priority", "medium")
        if self.event_type not in {
            "goal_nudge",
            "pending_confirmation_nudge",
            "scheduled_reminder",
            "foresight_nudge",
        }:
            raise ValueError(f"invalid cognitive event_type: {self.event_type!r}")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "CognitiveEvent":
        if not isinstance(raw, dict):
            raise ValueError("cognitive event must be an object")
        return cls(
            event_id=str(raw.get("event_id") or ""),
            session_key=str(raw.get("session_key") or ""),
            event_type=str(raw.get("event_type") or ""),
            source_type=str(raw.get("source_type") or ""),
            source_reference=str(raw.get("source_reference") or ""),
            summary=str(raw.get("summary") or ""),
            priority=str(raw.get("priority") or "medium"),
            created_at=str(raw.get("created_at") or _utcnow_iso()),
            payload=dict(raw.get("payload") or {}) if isinstance(raw.get("payload"), dict) else {},
        )


@dataclass(frozen=True)
class CognitiveDecision:
    """One bounded policy decision over a cognitive event."""

    decision_id: str
    event_id: str
    session_key: str
    action: CognitiveAction
    outcome: CognitiveOutcome
    suppression_reason: str | None = None
    cooldown_key: str = ""
    written_to_working_memory: bool = False
    published_internal_event: bool = False
    created_at: str = field(default_factory=_utcnow_iso)
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _normalize_text(self.decision_id, max_chars=160))
        object.__setattr__(self, "event_id", _normalize_text(self.event_id, max_chars=160))
        object.__setattr__(self, "session_key", _normalize_text(self.session_key, max_chars=240))
        object.__setattr__(self, "cooldown_key", _normalize_text(self.cooldown_key, max_chars=240))
        reason = self.suppression_reason
        object.__setattr__(
            self,
            "suppression_reason",
            _normalize_text(reason, max_chars=240) if reason is not None else None,
        )
        if self.action not in {"emit", "suppress", "skip", "error"}:
            raise ValueError(f"invalid cognitive action: {self.action!r}")
        if self.outcome not in {"emitted", "suppressed", "skipped", "errored"}:
            raise ValueError(f"invalid cognitive outcome: {self.outcome!r}")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "CognitiveDecision":
        if not isinstance(raw, dict):
            raise ValueError("cognitive decision must be an object")
        return cls(
            decision_id=str(raw.get("decision_id") or ""),
            event_id=str(raw.get("event_id") or ""),
            session_key=str(raw.get("session_key") or ""),
            action=str(raw.get("action") or ""),
            outcome=str(raw.get("outcome") or ""),
            suppression_reason=(
                str(raw.get("suppression_reason"))
                if raw.get("suppression_reason") is not None
                else None
            ),
            cooldown_key=str(raw.get("cooldown_key") or ""),
            written_to_working_memory=bool(raw.get("written_to_working_memory")),
            published_internal_event=bool(raw.get("published_internal_event")),
            created_at=str(raw.get("created_at") or _utcnow_iso()),
            payload=dict(raw.get("payload") or {}) if isinstance(raw.get("payload"), dict) else {},
        )
