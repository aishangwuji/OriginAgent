"""Structured sidecar meta-cognition contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


MetaTriggerType = Literal[
    "tool_failure", "user_correction", "task_completion",
    "cognitive_nudge", "world_change", "device_event",
]
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


def _normalize_float(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(number, 1.0))


def _normalize_int(value: Any, *, default: int = 0, minimum: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, number)


def _normalize_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(number, 1.0))


def _filter_known_fields(raw: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("meta cognition payload must be an object")
    return {key: value for key, value in raw.items() if key in allowed}


def _normalize_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _normalize_retention_hint(value: Any) -> str:
    hint = str(value or "").strip().lower()
    if hint in {"discard", "short", "review", "candidate"}:
        return hint
    return "discard"


def _normalize_rule_kind(value: Any) -> str:
    kind = str(value or "").strip().lower()
    if kind in {"preference", "task_pattern", "constraint", "fact"}:
        return kind
    return ""


def _normalize_signal_target_type(value: Any) -> str:
    target = str(value or "").strip().lower()
    if target in {"workflow_candidate", "skill_candidate"}:
        return target
    return ""


def _normalize_rule_candidate(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    kind = _normalize_rule_kind(value.get("kind"))
    summary = _normalize_text(value.get("summary"), max_chars=240)
    if not kind or not summary:
        return None
    scope_hint = _normalize_text(value.get("scope_hint"), max_chars=120)
    sensitivity = _normalize_text(value.get("sensitivity"), max_chars=40) or "low"
    return {
        "kind": kind,
        "summary": summary,
        "confidence": _normalize_float(value.get("confidence"), default=0.0),
        "scope_hint": scope_hint or "user",
        "sensitivity": sensitivity,
        "supporting_refs": _normalize_str_list(value.get("supporting_refs"), limit=8, max_chars=160),
    }


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
        if self.trigger_type not in {
            "tool_failure", "user_correction", "task_completion",
            "cognitive_nudge", "world_change", "device_event",
        }:
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
    frame_id: str = ""
    created_at: str = field(default_factory=_utcnow_iso)
    trigger_type: str = ""
    task_reference: str = ""
    strategy_summary: str = ""
    assumptions: list[str] = field(default_factory=list)
    event_refs: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    expected_outcome: str = ""
    actual_outcome: str = ""
    mismatch_summary: str = ""
    suggested_next_action: str = ""
    summary: str = ""
    retention_hint: str = "discard"
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_id", _normalize_text(self.entry_id, max_chars=160))
        object.__setattr__(self, "session_key", _normalize_text(self.session_key, max_chars=240))
        object.__setattr__(self, "frame_id", _normalize_text(self.frame_id, max_chars=160))
        object.__setattr__(self, "trigger_type", _normalize_text(self.trigger_type, max_chars=80))
        object.__setattr__(self, "task_reference", _normalize_text(self.task_reference, max_chars=160))
        object.__setattr__(self, "strategy_summary", _normalize_text(self.strategy_summary, max_chars=240))
        object.__setattr__(self, "assumptions", _normalize_str_list(self.assumptions, limit=8, max_chars=160))
        object.__setattr__(self, "event_refs", _normalize_str_list(self.event_refs, limit=8, max_chars=160))
        object.__setattr__(self, "evidence_refs", _normalize_str_list(self.evidence_refs, limit=8, max_chars=160))
        object.__setattr__(self, "confidence", _normalize_float(self.confidence))
        object.__setattr__(self, "expected_outcome", _normalize_text(self.expected_outcome, max_chars=240))
        object.__setattr__(self, "actual_outcome", _normalize_text(self.actual_outcome, max_chars=240))
        object.__setattr__(self, "mismatch_summary", _normalize_text(self.mismatch_summary, max_chars=240))
        object.__setattr__(self, "suggested_next_action", _normalize_text(self.suggested_next_action, max_chars=240))
        object.__setattr__(self, "summary", _normalize_text(self.summary, max_chars=240))
        object.__setattr__(self, "retention_hint", _normalize_retention_hint(self.retention_hint))
        object.__setattr__(self, "payload", _normalize_dict(self.payload))

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "ThoughtJournalEntry":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed)
        payload.setdefault("entry_id", "")
        payload.setdefault("session_key", "")
        payload.setdefault("frame_id", "")
        payload.setdefault("created_at", _utcnow_iso())
        payload.setdefault("summary", "")
        payload.setdefault("event_refs", [])
        payload.setdefault("retention_hint", "discard")
        payload.setdefault("payload", {})
        return cls(**payload)


@dataclass(frozen=True)
class ReflectionRecord:
    reflection_id: str
    session_key: str
    created_at: str = field(default_factory=_utcnow_iso)
    source_entry_ids: list[str] = field(default_factory=list)
    reflection_kind: str = ""
    outcome_class: str = ""
    root_cause_hypotheses: list[str] = field(default_factory=list)
    what_worked: list[str] = field(default_factory=list)
    what_failed: list[str] = field(default_factory=list)
    learned_rule_candidate: dict[str, Any] | None = None
    confidence: float = 0.0
    retention_hint: str = "discard"
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reflection_id", _normalize_text(self.reflection_id, max_chars=160))
        object.__setattr__(self, "session_key", _normalize_text(self.session_key, max_chars=240))
        object.__setattr__(self, "source_entry_ids", _normalize_str_list(self.source_entry_ids, limit=8, max_chars=160))
        object.__setattr__(self, "reflection_kind", _normalize_text(self.reflection_kind, max_chars=80))
        object.__setattr__(self, "outcome_class", _normalize_text(self.outcome_class, max_chars=80))
        object.__setattr__(self, "root_cause_hypotheses", _normalize_str_list(self.root_cause_hypotheses, limit=8, max_chars=180))
        object.__setattr__(self, "what_worked", _normalize_str_list(self.what_worked, limit=8, max_chars=180))
        object.__setattr__(self, "what_failed", _normalize_str_list(self.what_failed, limit=8, max_chars=180))
        object.__setattr__(self, "learned_rule_candidate", _normalize_rule_candidate(self.learned_rule_candidate))
        object.__setattr__(self, "confidence", _normalize_float(self.confidence))
        object.__setattr__(self, "retention_hint", _normalize_retention_hint(self.retention_hint))
        object.__setattr__(self, "summary", _normalize_text(self.summary, max_chars=240))
        object.__setattr__(self, "payload", _normalize_dict(self.payload))

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "ReflectionRecord":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed)
        payload.setdefault("reflection_id", "")
        payload.setdefault("session_key", "")
        payload.setdefault("created_at", _utcnow_iso())
        payload.setdefault("summary", "")
        payload.setdefault("payload", {})
        return cls(**payload)


@dataclass(frozen=True)
class ConfidenceTrace:
    trace_id: str
    session_key: str
    created_at: str = field(default_factory=_utcnow_iso)
    subject_type: str = ""
    subject_reference: str = ""
    initial_confidence: float | None = None
    final_confidence: float = 0.0
    change_reason: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trace_id", _normalize_text(self.trace_id, max_chars=160))
        object.__setattr__(self, "session_key", _normalize_text(self.session_key, max_chars=240))
        object.__setattr__(self, "subject_type", _normalize_text(self.subject_type, max_chars=80))
        object.__setattr__(self, "subject_reference", _normalize_text(self.subject_reference, max_chars=160))
        object.__setattr__(self, "initial_confidence", _normalize_optional_float(self.initial_confidence))
        object.__setattr__(self, "final_confidence", _normalize_float(self.final_confidence))
        object.__setattr__(self, "change_reason", _normalize_text(self.change_reason, max_chars=180))
        object.__setattr__(self, "evidence_refs", _normalize_str_list(self.evidence_refs, limit=8, max_chars=160))
        object.__setattr__(self, "summary", _normalize_text(self.summary, max_chars=240))
        object.__setattr__(self, "payload", _normalize_dict(self.payload))

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "ConfidenceTrace":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed)
        payload.setdefault("trace_id", "")
        payload.setdefault("session_key", "")
        payload.setdefault("created_at", _utcnow_iso())
        payload.setdefault("summary", "")
        payload.setdefault("payload", {})
        return cls(**payload)


@dataclass(frozen=True)
class ErrorPattern:
    pattern_id: str
    pattern_key: str = ""
    owner_id: str = ""
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)
    source_reflection_ids: list[str] = field(default_factory=list)
    source_entry_ids: list[str] = field(default_factory=list)
    source_session_keys: list[str] = field(default_factory=list)
    trigger_types: list[str] = field(default_factory=list)
    capability_domain: str = ""
    severity: str = "low"
    frequency: int = 0
    distinct_turn_count: int = 0
    recency_score: float = 0.0
    pattern_score: float = 0.0
    example_refs: list[str] = field(default_factory=list)
    candidate_target_type: str | None = None
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "pattern_id", _normalize_text(self.pattern_id, max_chars=160))
        object.__setattr__(self, "pattern_key", _normalize_text(self.pattern_key, max_chars=160))
        object.__setattr__(self, "owner_id", _normalize_text(self.owner_id, max_chars=160))
        object.__setattr__(self, "updated_at", _normalize_text(self.updated_at, max_chars=80) or self.created_at)
        object.__setattr__(
            self,
            "source_reflection_ids",
            _normalize_str_list(self.source_reflection_ids, limit=24, max_chars=160),
        )
        object.__setattr__(
            self,
            "source_entry_ids",
            _normalize_str_list(self.source_entry_ids, limit=24, max_chars=160),
        )
        object.__setattr__(
            self,
            "source_session_keys",
            _normalize_str_list(self.source_session_keys, limit=24, max_chars=240),
        )
        object.__setattr__(
            self,
            "trigger_types",
            _normalize_str_list(self.trigger_types, limit=12, max_chars=80),
        )
        object.__setattr__(self, "capability_domain", _normalize_text(self.capability_domain, max_chars=120))
        if self.severity not in {"low", "medium", "high"}:
            object.__setattr__(self, "severity", "low")
        object.__setattr__(self, "frequency", _normalize_int(self.frequency, minimum=0))
        object.__setattr__(self, "distinct_turn_count", _normalize_int(self.distinct_turn_count, minimum=0))
        object.__setattr__(self, "recency_score", _normalize_float(self.recency_score))
        object.__setattr__(self, "pattern_score", _normalize_float(self.pattern_score))
        object.__setattr__(self, "example_refs", _normalize_str_list(self.example_refs, limit=12, max_chars=160))
        target_type = _normalize_signal_target_type(self.candidate_target_type)
        object.__setattr__(self, "candidate_target_type", target_type or None)
        object.__setattr__(self, "summary", _normalize_text(self.summary, max_chars=320))

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "ErrorPattern":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed | {"session_key"})
        payload.setdefault("pattern_id", "")
        payload.setdefault("pattern_key", "")
        payload.setdefault("owner_id", str(raw.get("session_key") or ""))
        payload.setdefault("created_at", _utcnow_iso())
        payload.setdefault("updated_at", str(payload.get("created_at") or _utcnow_iso()))
        payload.setdefault("recency_score", 0.0)
        payload.setdefault("pattern_score", 0.0)
        payload.setdefault("summary", "")
        return cls(**{key: value for key, value in payload.items() if key in allowed})


@dataclass(frozen=True)
class EvolutionSeed:
    seed_id: str
    pattern_id: str = ""
    pattern_key: str = ""
    owner_id: str = ""
    created_at: str = field(default_factory=_utcnow_iso)
    change_target_type: str = ""
    target_key: str = ""
    title: str = ""
    summary: str = ""
    hypothesis: str = ""
    confidence: float = 0.0
    severity: str = "medium"
    evidence_refs: list[str] = field(default_factory=list)
    supporting_reflection_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "seed_id", _normalize_text(self.seed_id, max_chars=160))
        object.__setattr__(self, "pattern_id", _normalize_text(self.pattern_id, max_chars=160))
        object.__setattr__(self, "pattern_key", _normalize_text(self.pattern_key, max_chars=160))
        object.__setattr__(self, "owner_id", _normalize_text(self.owner_id, max_chars=160))
        target_type = _normalize_signal_target_type(self.change_target_type)
        object.__setattr__(self, "change_target_type", target_type)
        object.__setattr__(self, "target_key", _normalize_text(self.target_key, max_chars=240))
        object.__setattr__(self, "title", _normalize_text(self.title, max_chars=160))
        object.__setattr__(self, "summary", _normalize_text(self.summary, max_chars=320))
        object.__setattr__(self, "hypothesis", _normalize_text(self.hypothesis, max_chars=240))
        object.__setattr__(self, "confidence", _normalize_float(self.confidence))
        if self.severity not in {"medium", "high"}:
            object.__setattr__(self, "severity", "medium")
        object.__setattr__(self, "evidence_refs", _normalize_str_list(self.evidence_refs, limit=12, max_chars=160))
        object.__setattr__(
            self,
            "supporting_reflection_ids",
            _normalize_str_list(self.supporting_reflection_ids, limit=12, max_chars=160),
        )

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "EvolutionSeed":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed | {"session_key"})
        payload.setdefault("seed_id", "")
        payload.setdefault("pattern_id", "")
        payload.setdefault("pattern_key", "")
        payload.setdefault("owner_id", str(raw.get("session_key") or ""))
        payload.setdefault("created_at", _utcnow_iso())
        payload.setdefault("summary", "")
        return cls(**{key: value for key, value in payload.items() if key in allowed})
