"""CogniSphere world-simulator data contracts (§9.3-§9.7).

Extracted from ``world_simulator.py`` and aligned with the CogniSphere master
plan section-9 field definitions.  Every model in this module is a frozen
dataclass with ``to_json()`` / ``from_json()`` serialisation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


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


def _normalize_probability(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _normalize_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _filter_known_fields(raw: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("payload must be an object")
    return {key: value for key, value in raw.items() if key in allowed}


# ── §9.3 HypothesisRecord ────────────────────────────────────────────────────


@dataclass(frozen=True)
class HypothesisRecord:
    """A hypothesis still being validated — one branch in the assumption space.

    Per CogniSphere §9.3.
    """

    hypothesis_id: str
    statement: str
    basis_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    status: str = "active"
    competing_hypotheses: list[str] = field(default_factory=list)
    expires_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "hypothesis_id", _normalize_text(self.hypothesis_id, max_chars=160))
        object.__setattr__(self, "statement", _normalize_text(self.statement, max_chars=480))
        object.__setattr__(self, "basis_refs", _normalize_str_list(self.basis_refs, limit=8, max_chars=160))
        object.__setattr__(self, "confidence", _normalize_float(self.confidence))
        if str(self.status).strip().lower() not in {"active", "confirmed", "refuted", "expired"}:
            object.__setattr__(self, "status", "active")
        object.__setattr__(
            self,
            "competing_hypotheses",
            _normalize_str_list(self.competing_hypotheses, limit=8, max_chars=160),
        )
        object.__setattr__(self, "expires_at", _normalize_text(self.expires_at, max_chars=80))

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "HypothesisRecord":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed)
        payload.setdefault("hypothesis_id", "")
        payload.setdefault("statement", "")
        payload.setdefault("status", "active")
        return cls(**payload)


# ── §9.5 EntityState ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EntityState:
    """Stable entity state in the world simulator.

    Per CogniSphere §9.5.
    """

    entity_id: str
    entity_type: str
    scope: str = ""
    owner_id: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    last_verified_at: str = ""
    stale_at: str = ""
    source_refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_id", _normalize_text(self.entity_id, max_chars=120))
        object.__setattr__(self, "entity_type", _normalize_text(self.entity_type, max_chars=80))
        object.__setattr__(self, "scope", _normalize_text(self.scope, max_chars=160))
        object.__setattr__(self, "owner_id", _normalize_text(self.owner_id, max_chars=160))
        object.__setattr__(self, "attributes", _normalize_dict(self.attributes))
        object.__setattr__(self, "confidence", _normalize_float(self.confidence, default=0.5))
        object.__setattr__(self, "last_verified_at", _normalize_text(self.last_verified_at, max_chars=80))
        object.__setattr__(self, "stale_at", _normalize_text(self.stale_at, max_chars=80))
        object.__setattr__(self, "source_refs", _normalize_str_list(self.source_refs, limit=12, max_chars=160))

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "EntityState":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed)
        payload.setdefault("entity_id", "")
        payload.setdefault("entity_type", "")
        return cls(**payload)


# ── §9.4 CausalEdge ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CausalEdge:
    """A causal relationship between two phenomena.

    Per CogniSphere §9.4.  Uses a Beta-Binomial model (alpha/beta) for
    incremental belief updating.
    """

    edge_id: str
    cause: str
    effect: str
    confidence: float
    alpha: float
    beta: float
    source_kind: str = ""
    source_fact_id: str | None = None
    support_count: int = 0
    contradiction_count: int = 0
    contradiction_refs: list[str] = field(default_factory=list)
    last_validated_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "edge_id", _normalize_text(self.edge_id, max_chars=160))
        object.__setattr__(self, "cause", _normalize_text(self.cause, max_chars=120))
        object.__setattr__(self, "effect", _normalize_text(self.effect, max_chars=120))
        object.__setattr__(self, "confidence", _normalize_float(self.confidence))
        object.__setattr__(self, "source_kind", _normalize_text(self.source_kind, max_chars=60))
        object.__setattr__(
            self,
            "contradiction_refs",
            _normalize_str_list(self.contradiction_refs, limit=8, max_chars=160),
        )
        object.__setattr__(self, "last_validated_at", _normalize_text(self.last_validated_at, max_chars=80))

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "CausalEdge":
        return cls(
            edge_id=str(raw.get("edge_id") or "").strip(),
            cause=str(raw.get("cause") or "").strip(),
            effect=str(raw.get("effect") or "").strip(),
            confidence=float(raw.get("confidence", 0.5) or 0.5),
            alpha=float(raw.get("alpha", 1.5) or 1.5),
            beta=float(raw.get("beta", 1.5) or 1.5),
            source_kind=str(raw.get("source_kind") or "").strip(),
            source_fact_id=str(raw.get("source_fact_id")).strip() if raw.get("source_fact_id") else None,
            support_count=max(0, int(raw.get("support_count", 0) or 0)),
            contradiction_count=max(0, int(raw.get("contradiction_count", 0) or 0)),
            contradiction_refs=list(raw.get("contradiction_refs") or []),
            last_validated_at=str(raw.get("last_validated_at") or "").strip(),
        )


# ── §9.6 SimulationRequest ───────────────────────────────────────────────────


@dataclass(frozen=True)
class SimulationRequest:
    """A counter-factual what-if request.

    Per CogniSphere §9.6.
    """

    request_id: str
    action: str
    scope: str
    trigger: str
    risk: str
    purpose: str = ""
    active_hypotheses: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    world_ref: str | None = None
    facts_ref: list[str] = field(default_factory=list)
    budget_tier: str = "normal"
    requested_by_frame_id: str = ""
    created_at: str = field(default_factory=_utcnow_iso)

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _normalize_text(self.request_id, max_chars=160))
        object.__setattr__(self, "action", _normalize_text(self.action, max_chars=80))
        object.__setattr__(self, "scope", _normalize_text(self.scope, max_chars=120))
        object.__setattr__(self, "trigger", _normalize_text(self.trigger, max_chars=60))
        if str(self.risk).strip().lower() not in {"low", "medium", "high"}:
            object.__setattr__(self, "risk", "medium")
        object.__setattr__(self, "purpose", _normalize_text(self.purpose, max_chars=320))
        object.__setattr__(
            self,
            "active_hypotheses",
            _normalize_str_list(self.active_hypotheses, limit=8, max_chars=160),
        )
        object.__setattr__(self, "payload", _normalize_dict(self.payload))
        if str(self.budget_tier).strip().lower() not in {"economy", "normal", "deep"}:
            object.__setattr__(self, "budget_tier", "normal")
        object.__setattr__(
            self, "requested_by_frame_id",
            _normalize_text(self.requested_by_frame_id, max_chars=160),
        )

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "SimulationRequest":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed)
        payload.setdefault("request_id", "")
        payload.setdefault("action", "")
        payload.setdefault("scope", "")
        payload.setdefault("trigger", "")
        payload.setdefault("created_at", _utcnow_iso())
        return cls(**payload)


# ── §9.7 SimulationTrace ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class SimulationTrace:
    """Structured result of one simulation.

    Per CogniSphere §9.7.
    """

    trace_id: str
    request_id: str
    status: str
    predicted_outcomes: list[dict[str, Any]] = field(default_factory=list)
    risk_score: float = 0.0
    risk_flags: list[str] = field(default_factory=list)
    uncertainty_score: float = 0.0
    confidence: float = 0.0
    assumptions: list[str] = field(default_factory=list)
    recommended_confirmation: bool = False
    evidence_refs: dict[str, list[str] | str] = field(default_factory=dict)
    observed_mismatch_refs: list[str] = field(default_factory=list)
    simulation_skipped_reason: str | None = None
    created_at: str = field(default_factory=_utcnow_iso)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trace_id", _normalize_text(self.trace_id, max_chars=160))
        object.__setattr__(self, "request_id", _normalize_text(self.request_id, max_chars=160))
        object.__setattr__(self, "status", _normalize_text(self.status, max_chars=40))
        object.__setattr__(self, "risk_score", _normalize_float(self.risk_score))
        object.__setattr__(
            self,
            "risk_flags",
            _normalize_str_list(self.risk_flags, limit=8, max_chars=80),
        )
        object.__setattr__(self, "uncertainty_score", _normalize_float(self.uncertainty_score))
        object.__setattr__(self, "confidence", _normalize_float(self.confidence))
        object.__setattr__(
            self,
            "assumptions",
            _normalize_str_list(self.assumptions, limit=12, max_chars=320),
        )
        object.__setattr__(
            self,
            "observed_mismatch_refs",
            _normalize_str_list(self.observed_mismatch_refs, limit=8, max_chars=160),
        )

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "SimulationTrace":
        from_json_fields = {
            "trace_id": str(raw.get("trace_id") or "").strip(),
            "request_id": str(raw.get("request_id") or "").strip(),
            "status": str(raw.get("status") or "not_applicable").strip(),
            "predicted_outcomes": list(raw.get("predicted_outcomes") or []),
            "risk_score": float(raw.get("risk_score", 0.0) or 0.0),
            "risk_flags": list(raw.get("risk_flags") or []),
            "uncertainty_score": float(raw.get("uncertainty_score", 0.0) or 0.0),
            "confidence": float(raw.get("confidence", 0.0) or 0.0),
            "assumptions": [str(item) for item in (raw.get("assumptions") or [])],
            "recommended_confirmation": bool(raw.get("recommended_confirmation", False)),
            "evidence_refs": dict(raw.get("evidence_refs") or {}),
            "observed_mismatch_refs": list(raw.get("observed_mismatch_refs") or []),
            "simulation_skipped_reason": (
                str(raw.get("simulation_skipped_reason")).strip()
                if raw.get("simulation_skipped_reason")
                else None
            ),
            "created_at": str(raw.get("created_at") or _utcnow_iso()).strip(),
        }
        return cls(**from_json_fields)


# ── SimulationFeedback ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class SimulationFeedback:
    """Real-world outcome observed after a simulation prediction."""

    trace_id: str
    outcome: str
    observed_outcome: dict[str, Any] = field(default_factory=dict)
    mismatch_score: float | None = None
    evidence_refs: dict[str, Any] = field(default_factory=dict)
    observed_at: str = field(default_factory=_utcnow_iso)
    calibration_state: str = "pending"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "SimulationFeedback":
        mismatch = raw.get("mismatch_score")
        return cls(
            trace_id=str(raw.get("trace_id") or "").strip(),
            outcome=str(raw.get("outcome") or "").strip(),
            observed_outcome=dict(raw.get("observed_outcome") or {}),
            mismatch_score=float(mismatch) if mismatch is not None else None,
            evidence_refs=dict(raw.get("evidence_refs") or {}),
            observed_at=str(raw.get("observed_at") or _utcnow_iso()).strip(),
            calibration_state=str(raw.get("calibration_state") or "pending").strip() or "pending",
        )


# ── CalibrationSummary ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class CalibrationSummary:
    """Aggregate result from a calibration pass."""

    processed_feedback: int = 0
    updated_edges: int = 0
    skipped_feedback: int = 0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
