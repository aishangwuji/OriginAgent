"""Structured sidecar meta-cognition contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


MetaTriggerType = Literal["tool_failure", "user_correction", "task_completion"]
MetaTriggerSeverity = Literal["low", "medium", "high"]
MetaDecision = Literal[
    "accepted",
    "suppressed_duplicate",
    "suppressed_cooldown",
    "suppressed_turn_limit",
    "dropped_runtime_disabled",
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


def _normalize_str_list(value: Any, *, limit: int = 16, max_chars: int = 240) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _normalize_text(item, max_chars=max_chars)
        if not text:
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return out


@dataclass(frozen=True)
class MetaTrigger:
    trigger_id: str
    session_key: str
    trigger_type: MetaTriggerType
    source_type: str
    source_reference: str
    severity: MetaTriggerSeverity = "medium"
    created_at: str = field(default_factory=_utcnow_iso)
    cooldown_key: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trigger_id", _normalize_text(self.trigger_id, max_chars=160))
        object.__setattr__(self, "session_key", _normalize_text(self.session_key, max_chars=240))
        object.__setattr__(self, "source_type", _normalize_text(self.source_type, max_chars=120))
        object.__setattr__(self, "source_reference", _normalize_text(self.source_reference, max_chars=240))
        object.__setattr__(self, "cooldown_key", _normalize_text(self.cooldown_key, max_chars=240))
        object.__setattr__(self, "evidence_refs", _normalize_str_list(self.evidence_refs))
        if self.trigger_type not in {"tool_failure", "user_correction", "task_completion"}:
            raise ValueError(f"invalid meta trigger_type: {self.trigger_type!r}")
        if self.severity not in {"low", "medium", "high"}:
            object.__setattr__(self, "severity", "medium")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "MetaTrigger":
        if not isinstance(raw, dict):
            raise ValueError("meta trigger must be an object")
        return cls(
            trigger_id=str(raw.get("trigger_id") or ""),
            session_key=str(raw.get("session_key") or ""),
            trigger_type=str(raw.get("trigger_type") or ""),
            source_type=str(raw.get("source_type") or ""),
            source_reference=str(raw.get("source_reference") or ""),
            severity=str(raw.get("severity") or "medium"),
            created_at=str(raw.get("created_at") or _utcnow_iso()),
            cooldown_key=str(raw.get("cooldown_key") or ""),
            evidence_refs=list(raw.get("evidence_refs") or []),
            payload=dict(raw.get("payload") or {}) if isinstance(raw.get("payload"), dict) else {},
        )


@dataclass(frozen=True)
class RecordTriggerResult:
    accepted: bool
    decision: MetaDecision
    suppression_reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "RecordTriggerResult":
        if not isinstance(raw, dict):
            raise ValueError("record trigger result must be an object")
        return cls(
            accepted=bool(raw.get("accepted")),
            decision=str(raw.get("decision") or "dropped_runtime_disabled"),
            suppression_reason=(
                str(raw.get("suppression_reason"))
                if raw.get("suppression_reason") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ThoughtJournalEntry:
    entry_id: str
    session_key: str
    created_at: str = field(default_factory=_utcnow_iso)
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "ThoughtJournalEntry":
        if not isinstance(raw, dict):
            raise ValueError("thought journal entry must be an object")
        return cls(
            entry_id=str(raw.get("entry_id") or ""),
            session_key=str(raw.get("session_key") or ""),
            created_at=str(raw.get("created_at") or _utcnow_iso()),
            summary=str(raw.get("summary") or ""),
            payload=dict(raw.get("payload") or {}) if isinstance(raw.get("payload"), dict) else {},
        )


@dataclass(frozen=True)
class ReflectionRecord:
    reflection_id: str
    session_key: str
    created_at: str = field(default_factory=_utcnow_iso)
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "ReflectionRecord":
        if not isinstance(raw, dict):
            raise ValueError("reflection record must be an object")
        return cls(
            reflection_id=str(raw.get("reflection_id") or ""),
            session_key=str(raw.get("session_key") or ""),
            created_at=str(raw.get("created_at") or _utcnow_iso()),
            summary=str(raw.get("summary") or ""),
            payload=dict(raw.get("payload") or {}) if isinstance(raw.get("payload"), dict) else {},
        )


@dataclass(frozen=True)
class ConfidenceTrace:
    trace_id: str
    session_key: str
    created_at: str = field(default_factory=_utcnow_iso)
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "ConfidenceTrace":
        if not isinstance(raw, dict):
            raise ValueError("confidence trace must be an object")
        return cls(
            trace_id=str(raw.get("trace_id") or ""),
            session_key=str(raw.get("session_key") or ""),
            created_at=str(raw.get("created_at") or _utcnow_iso()),
            summary=str(raw.get("summary") or ""),
            payload=dict(raw.get("payload") or {}) if isinstance(raw.get("payload"), dict) else {},
        )


@dataclass(frozen=True)
class ErrorPattern:
    pattern_id: str
    session_key: str
    created_at: str = field(default_factory=_utcnow_iso)
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "ErrorPattern":
        if not isinstance(raw, dict):
            raise ValueError("error pattern must be an object")
        return cls(
            pattern_id=str(raw.get("pattern_id") or ""),
            session_key=str(raw.get("session_key") or ""),
            created_at=str(raw.get("created_at") or _utcnow_iso()),
            summary=str(raw.get("summary") or ""),
            payload=dict(raw.get("payload") or {}) if isinstance(raw.get("payload"), dict) else {},
        )


@dataclass(frozen=True)
class EvolutionSeed:
    seed_id: str
    session_key: str
    created_at: str = field(default_factory=_utcnow_iso)
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "EvolutionSeed":
        if not isinstance(raw, dict):
            raise ValueError("evolution seed must be an object")
        return cls(
            seed_id=str(raw.get("seed_id") or ""),
            session_key=str(raw.get("session_key") or ""),
            created_at=str(raw.get("created_at") or _utcnow_iso()),
            summary=str(raw.get("summary") or ""),
            payload=dict(raw.get("payload") or {}) if isinstance(raw.get("payload"), dict) else {},
        )

