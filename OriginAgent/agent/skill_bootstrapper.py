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
    RepeatedPattern,
    SkillCandidate,
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


import re

_DANGEROUS_TERMS_RE = re.compile(
    r"(?i)(?<![a-z0-9_])"
    r"(?:exec|shell|command|write_file|edit_file|cron|spawn|delete|remove|rm|"
    r"powershell|cmd\.exe|message|send_message)"
    r"(?![a-z0-9_])"
)


def _skill_name_from_signature(signature: str) -> str:
    return signature.replace("+", "-").replace("_", "-").lower().strip("-") or "unnamed-pattern"


def _build_skill_body(pattern: RepeatedPattern) -> str:
    lines = [
        f"# {_skill_name_from_signature(pattern.tool_signature)}",
        "",
        f"Auto-detected pattern (seen {pattern.repeat_count}x across "
        f"{len(pattern.session_keys)} session(s)).",
        "",
        "## Steps",
        "",
    ]
    for i, tool in enumerate(pattern.tool_signature.split("+"), 1):
        lines.append(f"{i}. Use the `{tool}` tool")
    lines.extend([
        "",
        "## When to Use",
        "",
        "This skill was bootstrapped from repeated usage patterns.",
    ])
    return "\n".join(lines)


class SkillCandidateCompiler:
    """Compile a ``RepeatedPattern`` into a ``SkillCandidate``."""

    def compile(
        self,
        pattern: RepeatedPattern,
        *,
        min_confidence: float = 0.5,
    ) -> SkillCandidate | None:
        if pattern.confidence < min_confidence:
            return None

        signature = pattern.tool_signature
        skill_name = _skill_name_from_signature(signature)

        dangerous: list[str] = []
        for tool in signature.split("+"):
            if _DANGEROUS_TERMS_RE.search(tool):
                dangerous.append(tool)

        body = _build_skill_body(pattern)
        governance = "danger_review" if dangerous else "review_required"

        return SkillCandidate(
            candidate_id=_new_id("sc"),
            pattern_id=pattern.pattern_id,
            skill_name=skill_name,
            description=f"Auto-detected repeated pattern: {signature} "
                        f"(seen {pattern.repeat_count}x, "
                        f"confidence={pattern.confidence:.2f})",
            body=body,
            confidence=pattern.confidence,
            governance_path=governance,
            dangerous_tools=dangerous,
            verification_plan=[f"Verify {t} usage is correct" for t in signature.split("+")],
        )


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compile_to_proposal_bundle(
    candidate: SkillCandidate,
    *,
    bundle_id: str | None = None,
) -> Any:
    """Wrap a ``SkillCandidate`` into a ``CompiledProposalBundle``."""
    from OriginAgent.agent.meta_programming import COMPILER_VERSION, CompiledProposalBundle

    return CompiledProposalBundle(
        bundle_id=bundle_id or _new_id("bundle"),
        source_pattern_id=candidate.pattern_id,
        source_signal_id="",
        target_type="skill",
        target_key=candidate.skill_name,
        input_summary_hash=_stable_hash([
            candidate.skill_name, candidate.body[:200],
        ]),
        compiler_version=COMPILER_VERSION,
        summary=candidate.description,
        hypothesis=f"Repeated pattern ({candidate.confidence:.2f} confidence) "
                   f"suggests a reusable skill",
        risk_level="high" if candidate.dangerous_tools else "medium",
        review_mode="danger_review" if candidate.dangerous_tools else "review_required",
        evidence_sources=[{"preview": candidate.body[:200]}],
        payload={"skill_name": candidate.skill_name, "body_preview": candidate.body[:500]},
        review_only=bool(candidate.dangerous_tools),
    )
