"""RuntimeEvent — unified event envelope for the CogniSphere perception layer.

Every event source (cognitive loop, tool executor, world state, user input,
device telemetry, cron) produces a ``RuntimeEvent`` before it is bridged into
a ``MetaTrigger`` for the metacognition pipeline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


RuntimeEventType = Literal[
    "cognitive_nudge",
    "tool_result",
    "world_change",
    "user_message",
    "device_event",
    "cron_tick",
]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def _normalize_float(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(number, 1.0))


def _normalize_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _filter_known_fields(raw: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("runtime event payload must be an object")
    return {key: value for key, value in raw.items() if key in allowed}


@dataclass(frozen=True)
class RuntimeEvent:
    """Unified event envelope normalised from any runtime signal source.

    Every event that enters the CogniSphere thought layer passes through this
    structure so that downstream consumers (trigger builders, ThoughtSubstrate,
    world simulator) see a consistent schema regardless of origin.
    """

    event_id: str
    session_key: str
    event_type: str
    source: str
    scope: str = ""
    owner_id: str = ""
    summary: str = ""
    confidence: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow_iso)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _normalize_text(self.event_id, max_chars=160))
        object.__setattr__(self, "session_key", _normalize_text(self.session_key, max_chars=240))
        object.__setattr__(self, "event_type", _normalize_text(self.event_type, max_chars=60))
        object.__setattr__(self, "source", _normalize_text(self.source, max_chars=80))
        object.__setattr__(self, "scope", _normalize_text(self.scope, max_chars=160))
        object.__setattr__(self, "owner_id", _normalize_text(self.owner_id, max_chars=160))
        object.__setattr__(self, "summary", _normalize_text(self.summary, max_chars=480))
        object.__setattr__(self, "confidence", _normalize_float(self.confidence))
        object.__setattr__(self, "payload", _normalize_dict(self.payload))

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "RuntimeEvent":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed)
        payload.setdefault("event_id", "")
        payload.setdefault("session_key", "")
        payload.setdefault("event_type", "")
        payload.setdefault("source", "")
        payload.setdefault("created_at", _utcnow_iso())
        return cls(**payload)
