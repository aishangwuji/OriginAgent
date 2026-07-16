"""MonologueFrame — structured output contract for one InnerMonologueEngine cycle.

Every cycle of the InnerMonologueEngine produces a ``MonologueFrame`` that
captures what the agent observed, what it hypothesises, what it plans, what
it needs to verify, and how confident it is.  This frame is then used to
enrich the open ``ThoughtFrame`` in the ``ThoughtSubstrate``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from OriginAgent.agent.meta_cognition_models import (
    _filter_known_fields,
    _normalize_float,
    _normalize_str_list,
    _normalize_text,
    _utcnow_iso,
)


@dataclass(frozen=True)
class MonologueFrame:
    """Structured output of one inner-monologue cycle.

    Maps to the CogniSphere master plan §8.3 "每轮最小输出" contract.
    """

    cycle_id: str
    session_key: str
    observation_summary: str = ""
    active_goal: str = ""
    candidate_hypotheses: list[str] = field(default_factory=list)
    intended_strategy: str = ""
    confidence: float = 0.0
    uncertainty_flags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_utcnow_iso)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cycle_id", _normalize_text(self.cycle_id, max_chars=160))
        object.__setattr__(self, "session_key", _normalize_text(self.session_key, max_chars=240))
        object.__setattr__(self, "observation_summary", _normalize_text(self.observation_summary, max_chars=480))
        object.__setattr__(self, "active_goal", _normalize_text(self.active_goal, max_chars=240))
        object.__setattr__(self, "candidate_hypotheses", _normalize_str_list(self.candidate_hypotheses, limit=8, max_chars=320))
        object.__setattr__(self, "intended_strategy", _normalize_text(self.intended_strategy, max_chars=480))
        object.__setattr__(self, "confidence", _normalize_float(self.confidence))
        object.__setattr__(self, "uncertainty_flags", _normalize_str_list(self.uncertainty_flags, limit=8, max_chars=120))
        object.__setattr__(self, "created_at", _normalize_text(self.created_at, max_chars=80) or _utcnow_iso())

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "MonologueFrame":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed)
        payload.setdefault("cycle_id", "")
        payload.setdefault("session_key", "")
        payload.setdefault("created_at", _utcnow_iso())
        return cls(**payload)
