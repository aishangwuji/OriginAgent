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
from typing import Any

from OriginAgent.agent.skill_bootstrapper_models import (
    ActionTraceDigest,
    _normalize_str_list,
    _normalize_text,
)


def _new_id(prefix: str = "sb") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def build_fingerprint(digest: ActionTraceDigest) -> str:
    """Return the deterministic fingerprint for an ``ActionTraceDigest``."""
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

from collections import defaultdict

from OriginAgent.agent.skill_bootstrapper_models import (
    ActionTraceDigest,
    RepeatedPattern,
    _utcnow_iso,
)


class SkillBootstrapperScanner:
    """Scan digests and group by fingerprint to find repeated patterns."""

    def scan(
        self,
        digests: list[ActionTraceDigest],
        *,
        min_repeats: int = 3,
        min_confidence: float = 0.5,
    ) -> list[RepeatedPattern]:
        """Group digests by fingerprint and return patterns exceeding thresholds."""
        groups: dict[str, list[ActionTraceDigest]] = defaultdict(list)
        for d in digests:
            fp = build_fingerprint(d)
            if fp:
                groups[fp].append(d)

        patterns: list[RepeatedPattern] = []
        for fp, matched in groups.items():
            if len(matched) < min_repeats:
                continue
            session_keys = list({d.session_key for d in matched})
            has_correction = any(d.correction_flag for d in matched)
            confidence = min(0.95, 0.5 + (len(matched) - 1) * 0.1)
            if confidence < min_confidence:
                continue

            sample = matched[0]
            patterns.append(RepeatedPattern(
                pattern_id=_new_id("rp"),
                fingerprint_hash=fp,
                tool_signature=tool_signature(sample.tool_sequence),
                repeat_count=len(matched),
                session_keys=session_keys,
                sample_digest_ids=[d.digest_id for d in matched],
                first_seen_at=min(d.created_at for d in matched),
                last_seen_at=max(d.created_at for d in matched),
                confidence=confidence,
                has_correction=has_correction,
            ))
        return patterns
