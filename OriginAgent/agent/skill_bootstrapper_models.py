"""Data contracts for the SkillBootstrapper pattern miner.

ActionTraceDigest  — one normalised action trace from a turn/reflection.
RepeatedPattern   — a fingerprint that has been seen N times across sessions.
SkillCandidate    — a compiled candidate ready for the governed evolution path.
"""

from __future__ import annotations

import hashlib
import json
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


def _normalize_str_list(value: Any, *, limit: int = 32, max_chars: int = 120) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        t = _normalize_text(item, max_chars=max_chars)
        if t:
            out.append(t)
            if len(out) >= limit:
                break
    return out


def _normalize_float(value: Any, *, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(v, 1.0))


def _filter_known_fields(raw: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("payload must be an object")
    return {k: v for k, v in raw.items() if k in allowed}


def _build_fingerprint(tool_sequence: list[str], param_preview: str = "") -> str:
    if not tool_sequence:
        return ""
    raw = json.dumps([tool_sequence, param_preview], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class ActionTraceDigest:
    """A normalised trace of one action sequence extracted from a journal/reflection."""

    digest_id: str
    session_key: str
    tool_sequence: list[str] = field(default_factory=list)
    param_preview: str = ""
    correction_flag: bool = False
    task_reference: str = ""
    created_at: str = field(default_factory=_utcnow_iso)

    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        fp = _build_fingerprint(
            _normalize_str_list(self.tool_sequence, limit=16, max_chars=80),
            _normalize_text(self.param_preview, max_chars=160),
        )
        object.__setattr__(self, "fingerprint", fp)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "ActionTraceDigest":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed)
        payload.pop("fingerprint", None)  # init=False, computed in __post_init__
        payload.setdefault("digest_id", "")
        payload.setdefault("session_key", "")
        payload.setdefault("created_at", _utcnow_iso())
        return cls(**payload)


@dataclass(frozen=True)
class RepeatedPattern:
    """A tool-sequence fingerprint that has been observed multiple times."""

    pattern_id: str
    fingerprint_hash: str
    tool_signature: str
    repeat_count: int = 1
    session_keys: list[str] = field(default_factory=list)
    sample_digest_ids: list[str] = field(default_factory=list)
    first_seen_at: str = field(default_factory=_utcnow_iso)
    last_seen_at: str = field(default_factory=_utcnow_iso)
    confidence: float = 0.0
    has_correction: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "RepeatedPattern":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed)
        payload.setdefault("pattern_id", "")
        payload.setdefault("fingerprint_hash", "")
        payload.setdefault("tool_signature", "")
        return cls(**payload)


@dataclass(frozen=True)
class SkillCandidate:
    """A compiled skill candidate ready for the governed evolution review path."""

    candidate_id: str
    pattern_id: str
    skill_name: str
    description: str
    body: str
    confidence: float = 0.0
    governance_path: str = "review_required"
    dangerous_tools: list[str] = field(default_factory=list)
    verification_plan: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_utcnow_iso)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "SkillCandidate":
        allowed = set(cls.__dataclass_fields__.keys())
        payload = _filter_known_fields(raw, allowed)
        payload.setdefault("candidate_id", "")
        payload.setdefault("pattern_id", "")
        payload.setdefault("skill_name", "")
        payload.setdefault("description", "")
        payload.setdefault("body", "")
        return cls(**payload)
