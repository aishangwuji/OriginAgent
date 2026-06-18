"""Normalize meta-cognition patterns into governed evolution opportunity signals."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from OriginAgent.agent.evolution import (
    SIGNAL_KIND_SKILL,
    SIGNAL_KIND_WORKFLOW,
    OpportunitySignalCandidate,
    OpportunitySignalStore,
)
from OriginAgent.agent.meta_cognition_models import ErrorPattern, EvolutionSeed
from OriginAgent.agent.meta_cognition_redact import redact_meta_text

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class EvolutionBridgeResult:
    decision: str
    signal: dict[str, Any] | None = None
    seed: EvolutionSeed | None = None
    pattern: ErrorPattern | None = None


def bridge_patterns_to_signals(
    *,
    workspace: Any,
    patterns: list[ErrorPattern],
    config: Any,
    artifact_lookup: dict[str, dict[str, Any]],
) -> list[EvolutionBridgeResult]:
    if not getattr(config, "evolution_bridge_enabled", False):
        return [EvolutionBridgeResult(decision="bridge_disabled", pattern=pattern) for pattern in patterns]
    allowed = {
        str(item or "").strip().lower()
        for item in tuple(getattr(config, "allowed_evolution_target_types", ()) or ())
        if str(item or "").strip()
    }
    max_upserts = max(1, int(getattr(config, "max_signal_upserts_per_turn", 1) or 1))
    selected = _prioritize_patterns(patterns)[:max_upserts]
    skipped = [pattern for pattern in patterns if pattern not in selected]
    results: list[EvolutionBridgeResult] = [
        EvolutionBridgeResult(decision="suppressed_turn_limit", pattern=pattern)
        for pattern in skipped
    ]
    store = OpportunitySignalStore(workspace)
    existing_by_target = {signal.target_key: signal for signal in store.read_all()}
    for pattern in selected:
        target_type = str(pattern.candidate_target_type or "").strip().lower()
        if target_type not in allowed:
            results.append(EvolutionBridgeResult(decision="target_type_disallowed", pattern=pattern))
            continue
        target_key = _target_key(pattern, target_type=target_type)
        existing = existing_by_target.get(target_key)
        if existing is not None and existing.status == "suppressed":
            results.append(EvolutionBridgeResult(decision="suppressed_signal_present", pattern=pattern))
            continue
        seed = _build_seed(pattern, target_type=target_type, artifact_lookup=artifact_lookup)
        candidate = OpportunitySignalCandidate(
            kind=target_type,
            target_key=seed.target_key,
            title=seed.title,
            summary=seed.summary,
            source_pattern_id=pattern.pattern_id,
            evidence_sources=_build_evidence_sources(
                seed,
                pattern,
                artifact_lookup=artifact_lookup,
                max_items=max(1, int(getattr(config, "signal_max_evidence_refs", 6) or 6)),
            ),
            risk_level="high" if seed.severity == "high" else "medium",
        )
        changed = store.upsert_candidates([candidate])
        signal = changed[0].to_record() if changed else None
        results.append(EvolutionBridgeResult(decision="queued", signal=signal, seed=seed, pattern=pattern))
    return results


def _prioritize_patterns(patterns: list[ErrorPattern]) -> list[ErrorPattern]:
    return sorted(
        patterns,
        key=lambda item: (
            float(item.pattern_score or 0.0),
            1 if item.severity == "high" else 0,
            int(item.frequency or 0),
            1 if "tool_failure" in item.trigger_types else (1 if "user_correction" in item.trigger_types else 0),
            item.updated_at,
        ),
        reverse=True,
    )


def _build_seed(
    pattern: ErrorPattern,
    *,
    target_type: str,
    artifact_lookup: dict[str, dict[str, Any]],
) -> EvolutionSeed:
    dominant_trigger_type = pattern.trigger_types[0] if pattern.trigger_types else "meta"
    slug = _pattern_slug(pattern.summary)
    prefix = "meta.workflow" if target_type == SIGNAL_KIND_WORKFLOW else "meta.skill"
    target_key = (
        f"{prefix}.{pattern.capability_domain or 'general_reasoning'}."
        f"{dominant_trigger_type}.{slug}.{pattern.pattern_key[:8]}"
    )
    target_label = "workflow" if target_type == SIGNAL_KIND_WORKFLOW else "skill"
    summary = redact_meta_text(
        "\n".join(
            [
                "Origin: meta_cognition",
                f"Repeated pattern: {pattern.summary}",
                f"Frequency: {pattern.frequency} across {pattern.distinct_turn_count} turns",
                f"Suggested target: {target_type}",
            ]
        ),
        max_chars=320,
    )
    return EvolutionSeed(
        seed_id=f"meta_seed_{uuid.uuid4().hex}",
        pattern_id=pattern.pattern_id,
        pattern_key=pattern.pattern_key,
        owner_id=pattern.owner_id,
        created_at=pattern.updated_at or pattern.created_at,
        change_target_type=target_type,
        target_key=target_key,
        title=redact_meta_text(
            f"Meta {target_label} candidate: {pattern.capability_domain or dominant_trigger_type}",
            max_chars=160,
        ),
        summary=summary,
        hypothesis=redact_meta_text(pattern.summary, max_chars=240),
        confidence=_seed_confidence(pattern, artifact_lookup=artifact_lookup),
        severity="high" if pattern.severity == "high" else "medium",
        evidence_refs=_seed_evidence_refs(pattern),
        supporting_reflection_ids=list(pattern.source_reflection_ids[:6]),
    )


def _seed_confidence(
    pattern: ErrorPattern,
    *,
    artifact_lookup: dict[str, dict[str, Any]],
) -> float:
    base = 0.68 if pattern.severity == "high" else 0.62
    frequency_bonus = min(float(pattern.frequency or 0.0) * 0.03, 0.15)
    recency_bonus = float(pattern.recency_score or 0.0) * 0.08
    uncertainty_penalty = _pattern_mean_uncertainty(pattern, artifact_lookup=artifact_lookup) * 0.12
    return max(0.0, min(base + frequency_bonus + recency_bonus - uncertainty_penalty, 0.95))


def _pattern_mean_uncertainty(
    pattern: ErrorPattern,
    *,
    artifact_lookup: dict[str, dict[str, Any]],
) -> float:
    uncertainties: list[float] = []
    for reflection_id in list(pattern.source_reflection_ids or [])[:3]:
        row = artifact_lookup.get(f"meta:reflection:{reflection_id}") or {}
        payload = dict(row.get("payload") or {}) if isinstance(row.get("payload"), dict) else {}
        item = payload.get("uncertainty_score")
        try:
            uncertainties.append(max(0.0, min(float(item), 1.0)))
        except (TypeError, ValueError):
            continue
    if not uncertainties:
        return 0.0
    return sum(uncertainties) / len(uncertainties)


def _seed_evidence_refs(pattern: ErrorPattern) -> list[str]:
    refs = [f"meta:pattern:{pattern.pattern_id}"]
    refs.extend(f"meta:reflection:{ref_id}" for ref_id in pattern.source_reflection_ids[:4])
    refs.extend(f"meta:journal:{entry_id}" for entry_id in pattern.source_entry_ids[:2])
    out: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        out.append(ref)
    return out[:6]


def _build_evidence_sources(
    seed: EvolutionSeed,
    pattern: ErrorPattern,
    *,
    artifact_lookup: dict[str, dict[str, Any]],
    max_items: int,
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for ref in seed.evidence_refs[:max_items]:
        artifact = artifact_lookup.get(ref) or {}
        session_key = str(artifact.get("session_key") or "")
        timestamp = str(artifact.get("created_at") or pattern.updated_at or pattern.created_at)
        preview = str(artifact.get("summary") or pattern.summary or ref)
        sources.append(
            {
                "cursor": ref,
                "session_key": session_key,
                "timestamp": timestamp,
                "preview": redact_meta_text(preview, max_chars=240),
            }
        )
    return sources[:max_items]


def _target_key(pattern: ErrorPattern, *, target_type: str) -> str:
    dominant_trigger_type = pattern.trigger_types[0] if pattern.trigger_types else "meta"
    prefix = "meta.workflow" if target_type == SIGNAL_KIND_WORKFLOW else "meta.skill"
    return (
        f"{prefix}.{pattern.capability_domain or 'general_reasoning'}."
        f"{dominant_trigger_type}.{_pattern_slug(pattern.summary)}.{pattern.pattern_key[:8]}"
    )


def _pattern_slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = _SLUG_RE.sub("-", text).strip("-")
    return text[:32] or "unknown"
