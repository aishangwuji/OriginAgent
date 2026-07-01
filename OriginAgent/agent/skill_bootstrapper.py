"""SkillBootstrapper — mine repeated action patterns and compile skill candidates.

Scans nearline thought artifacts (journal entries, reflection records) for
repeated tool-usage sequences, fingerprints them deterministically, and when
a pattern exceeds configurable thresholds synthesises a ``SkillCandidate``
that flows into the governed evolution review path.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from OriginAgent.agent.skill_bootstrapper_models import (
    ActionTraceDigest,
    _normalize_str_list,
    _normalize_text,
)


def _new_id(prefix: str = "sb") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def build_fingerprint(digest: ActionTraceDigest) -> str:
    """Return the deterministic fingerprint for an ``ActionTraceDigest``.

    Delegates to the model's own fingerprint computation so the result is
    always consistent with ``ActionTraceDigest.fingerprint``.
    """
    return digest.fingerprint


def fingerprint_from_tools(tool_sequence: list[str], param_preview: str = "") -> str:
    """Compute a deterministic fingerprint directly from a tool list + parameter preview."""
    tools = _normalize_str_list(tool_sequence, limit=16, max_chars=80)
    if not tools:
        return ""
    preview = _normalize_text(param_preview, max_chars=160)
    raw = json.dumps([tools, preview], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def tool_signature(tool_sequence: list[str]) -> str:
    """A human-readable collapsed signature like ``"read_file+grep"``."""
    return "+".join(_normalize_str_list(tool_sequence, limit=16, max_chars=80))
