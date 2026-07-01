"""ThoughtFrame — structured representation of a single thinking frame.

ThoughtFrame is the CogniSphere "current thinking state" counterpart to the
historical ThoughtJournalEntry.  It captures what the system is observing,
what goal it is pursuing, what hypotheses it holds, and what it plans to do
next — all as a frozen dataclass suitable for lightweight persistence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Reuse the same normalisation helpers as all other meta-cognition models.
from OriginAgent.agent.meta_cognition_models import (
    _filter_known_fields,
    _normalize_float,
    _normalize_str_list,
    _normalize_text,
    _utcnow_iso,
)


@dataclass(frozen=True)
class ThoughtFrame:
    """A single structured thinking frame — the current cognitive state.

    Maps directly to CogniSphere master plan §9.1.
    """

    frame_id: str
    session_key: str
    trigger_refs: list[str] = field(default_factory=list)
    observation_summary: str = ""
    active_goal: str = ""
    candidate_hypotheses: list[str] = field(default_factory=list)
    intended_strategy: str = ""
    verification_needs: list[str] = field(default_factory=list)
    simulation_requests: list[str] = field(default_factory=list)
    confidence: float = 0.0
    uncertainty_flags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_utcnow_iso)

    def __post_init__(self) -> None:
        object.__setattr__(self, "frame_id", _normalize_text(self.frame_id, max_chars=160))
        object.__setattr__(self, "session_key", _normalize_text(self.session_key, max_chars=240))
        object.__setattr__(self, "trigger_refs", _normalize_str_list(self.trigger_refs, limit=16, max_chars=160))
        object.__setattr__(self, "observation_summary", _normalize_text(self.observation_summary, max_chars=480))
        object.__setattr__(self, "active_goal", _normalize_text(self.active_goal, max_chars=240))
        object.__setattr__(self, "candidate_hypotheses", _normalize_str_list(self.candidate_hypotheses, limit=8, max_chars=320))
        object.__setattr__(self, "intended_strategy", _normalize_text(self.intended_strategy, max_chars=480))
        object.__setattr__(self, "verification_needs", _normalize_str_list(self.verification_needs, limit=8, max_chars=240))
        object.__setattr__(self, "simulation_requests", _normalize_str_list(self.simulation_requests, limit=8, max_chars=160))
        object.__setattr__(self, "confidence", _normalize_float(self.confidence))
        object.__setattr__(self, "uncertainty_flags", _normalize_str_list(self.uncertainty_flags, limit=8, max_chars=120))
        object.__setattr__(self, "created_at", _normalize_text(self.created_at, max_chars=80) or _utcnow_iso())

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "ThoughtFrame":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed)
        payload.setdefault("frame_id", "")
        payload.setdefault("session_key", "")
        payload.setdefault("created_at", _utcnow_iso())
        return cls(**payload)
