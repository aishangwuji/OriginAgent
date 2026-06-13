"""Rule-based consolidation of repeated meta-cognition reflections into error patterns."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from OriginAgent.agent.meta_cognition_models import ErrorPattern, ReflectionRecord
from OriginAgent.agent.meta_cognition_redact import redact_meta_list, redact_meta_text

_SPACE_RE = re.compile(r"\s+")
_TOOL_REF_RE = re.compile(r"tool[:/](?P<tool>[a-zA-Z0-9_.-]+)")


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_rule_text(value: Any) -> str:
    text = redact_meta_text(value, max_chars=120).lower()
    if not text:
        return ""
    return _SPACE_RE.sub(" ", text).strip()


def _stable_hash(values: list[Any]) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PatternConsolidationResult:
    patterns: list[ErrorPattern]
    eligible_patterns: list[ErrorPattern]
    recent_reflections: list[ReflectionRecord]


def consolidate_error_patterns(
    *,
    reflections: list[ReflectionRecord],
    config: Any,
    owner_id: str,
    current_turn_reflection_ids: set[str] | None = None,
    now: datetime | None = None,
) -> PatternConsolidationResult:
    recent = _recent_window(reflections, config=config, now=now)
    current_ids = set(current_turn_reflection_ids or set())
    owner_key = str(owner_id or "").strip()
    groups: dict[tuple[str, str, str, str, str], list[ReflectionRecord]] = {}
    for reflection in recent:
        if str(reflection.payload.get("owner_id") or "").strip() != owner_key:
            continue
        if not _is_pattern_candidate(reflection):
            continue
        trigger_contexts = list(reflection.payload.get("trigger_contexts") or [])
        dominant_trigger_type = _dominant_trigger_type(trigger_contexts)
        target_type = _candidate_target_type(reflection, dominant_trigger_type)
        normalized_rule_text = _normalized_rule_text(reflection)
        capability_domain = _capability_domain(reflection, dominant_trigger_type)
        outcome_class = redact_meta_text(reflection.outcome_class, max_chars=80).lower()
        group_key = (
            target_type or "",
            capability_domain,
            dominant_trigger_type,
            normalized_rule_text,
            outcome_class,
        )
        groups.setdefault(group_key, []).append(reflection)

    patterns: list[ErrorPattern] = []
    eligible: list[ErrorPattern] = []
    for group_key, items in groups.items():
        pattern = _build_pattern(
            owner_id=owner_id,
            group_key=group_key,
            reflections=items,
            config=config,
        )
        patterns.append(pattern)
        if _is_eligible_pattern(pattern, config=config, current_turn_reflection_ids=current_ids):
            eligible.append(pattern)
    patterns.sort(key=lambda item: (item.frequency, item.updated_at, item.pattern_id), reverse=True)
    eligible.sort(key=lambda item: (item.severity == "high", item.frequency, item.updated_at), reverse=True)
    return PatternConsolidationResult(
        patterns=patterns,
        eligible_patterns=eligible,
        recent_reflections=recent,
    )


def _recent_window(
    reflections: list[ReflectionRecord],
    *,
    config: Any,
    now: datetime | None = None,
) -> list[ReflectionRecord]:
    days = max(1, int(getattr(config, "pattern_window_days", 14) or 14))
    limit = max(1, int(getattr(config, "pattern_window_max_reflections", 200) or 200))
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    filtered = [
        item for item in reflections
        if (_parse_iso(item.created_at) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
    ]
    filtered.sort(
        key=lambda item: _parse_iso(item.created_at) or datetime.min.replace(tzinfo=timezone.utc)
    )
    return filtered[-limit:]


def _is_pattern_candidate(reflection: ReflectionRecord) -> bool:
    contexts = list(reflection.payload.get("trigger_contexts") or [])
    if contexts and all(
        str(item.get("status") or "").strip().lower() == "policy_denied"
        for item in contexts
        if isinstance(item, dict)
    ):
        return False
    candidate = reflection.learned_rule_candidate if isinstance(reflection.learned_rule_candidate, dict) else None
    if candidate and str(candidate.get("kind") or "").strip().lower() == "preference":
        return False
    return bool(candidate or reflection.root_cause_hypotheses or reflection.what_failed)


def _dominant_trigger_type(trigger_contexts: list[Any]) -> str:
    counts: dict[str, int] = {}
    for item in trigger_contexts:
        if not isinstance(item, dict):
            continue
        trigger_type = str(item.get("trigger_type") or "").strip().lower()
        if not trigger_type:
            continue
        counts[trigger_type] = counts.get(trigger_type, 0) + 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda entry: (entry[1], entry[0]), reverse=True)[0][0]


def _candidate_target_type(reflection: ReflectionRecord, dominant_trigger_type: str) -> str | None:
    candidate = reflection.learned_rule_candidate if isinstance(reflection.learned_rule_candidate, dict) else None
    kind = str(candidate.get("kind") or "").strip().lower() if candidate else ""
    if kind == "task_pattern":
        return "workflow_candidate"
    if kind in {"fact", "constraint"}:
        return "skill_candidate"
    if dominant_trigger_type == "user_correction":
        return "skill_candidate"
    return None


def _normalized_rule_text(reflection: ReflectionRecord) -> str:
    candidate = reflection.learned_rule_candidate if isinstance(reflection.learned_rule_candidate, dict) else None
    for value in (
        candidate.get("summary") if candidate else None,
        reflection.root_cause_hypotheses[0] if reflection.root_cause_hypotheses else None,
        reflection.what_failed[0] if reflection.what_failed else None,
    ):
        text = _normalize_rule_text(value)
        if text:
            return text
    return ""


def _capability_domain(reflection: ReflectionRecord, dominant_trigger_type: str) -> str:
    if dominant_trigger_type == "user_correction":
        return "answer_quality"
    candidate = reflection.learned_rule_candidate if isinstance(reflection.learned_rule_candidate, dict) else None
    if str(candidate.get("kind") or "").strip().lower() == "task_pattern":
        return "task_execution"
    for ref in list(reflection.payload.get("evidence_refs") or []):
        match = _TOOL_REF_RE.search(str(ref))
        if match:
            return f"tool/{match.group('tool')}"
    for entry_id in reflection.source_entry_ids:
        match = _TOOL_REF_RE.search(entry_id)
        if match:
            return f"tool/{match.group('tool')}"
    return "general_reasoning"


def _pattern_severity(reflections: list[ReflectionRecord], dominant_trigger_type: str) -> str:
    outcome_values = {
        redact_meta_text(item.outcome_class, max_chars=80).strip().lower()
        for item in reflections
        if redact_meta_text(item.outcome_class, max_chars=80)
    }
    if dominant_trigger_type == "tool_failure":
        return "high"
    if dominant_trigger_type == "user_correction" and (
        {"error", "incorrect", "misunderstood", "wrong"} & outcome_values
        or not outcome_values
    ):
        return "high"
    if {"mismatch", "partial", "inefficient", "recovered"} & outcome_values:
        return "medium"
    return "low"


def _build_pattern(
    *,
    owner_id: str,
    group_key: tuple[str, str, str, str, str],
    reflections: list[ReflectionRecord],
    config: Any,
) -> ErrorPattern:
    candidate_target_type, capability_domain, dominant_trigger_type, normalized_rule_text, dominant_outcome = group_key
    pattern_key = _stable_hash(list(group_key))
    ordered = sorted(
        reflections,
        key=lambda item: _parse_iso(item.created_at) or datetime.min.replace(tzinfo=timezone.utc),
    )
    latest = ordered[-1]
    source_entry_ids = _unique([entry for item in ordered for entry in item.source_entry_ids], limit=24)
    source_session_keys = _unique([item.session_key for item in ordered], limit=24)
    trigger_types = _unique(
        [dominant_trigger_type] + [
            str(ctx.get("trigger_type") or "")
            for item in ordered
            for ctx in list(item.payload.get("trigger_contexts") or [])
            if isinstance(ctx, dict)
        ],
        limit=12,
    )
    max_example_refs = max(1, int(getattr(config, "pattern_max_example_refs", 5) or 5))
    example_refs = _unique(
        [f"meta:reflection:{item.reflection_id}" for item in ordered[-5:]]
        + [f"meta:journal:{entry_id}" for entry_id in source_entry_ids[:2]],
        limit=max_example_refs,
    )
    summary = redact_meta_text(
        normalized_rule_text
        or reflections[-1].summary
        or f"{capability_domain} repeated {dominant_trigger_type or 'meta'} issue",
        max_chars=240,
    )
    distinct_turns = len({
        f"{item.session_key}:{entry_id}"
        for item in ordered
        for entry_id in (item.source_entry_ids or [item.reflection_id])
    })
    return ErrorPattern(
        pattern_id=f"meta_pattern_{pattern_key[:16]}",
        pattern_key=pattern_key,
        owner_id=owner_id,
        created_at=ordered[0].created_at,
        updated_at=latest.created_at,
        source_reflection_ids=[item.reflection_id for item in ordered][-24:],
        source_entry_ids=source_entry_ids,
        source_session_keys=source_session_keys,
        trigger_types=trigger_types,
        capability_domain=capability_domain,
        severity=_pattern_severity(ordered, dominant_trigger_type),
        frequency=len(ordered),
        distinct_turn_count=distinct_turns,
        example_refs=redact_meta_list(example_refs, max_items=max_example_refs, max_chars=160),
        candidate_target_type=candidate_target_type or None,
        summary=summary or dominant_outcome or capability_domain,
    )


def _is_eligible_pattern(
    pattern: ErrorPattern,
    *,
    config: Any,
    current_turn_reflection_ids: set[str],
) -> bool:
    if pattern.severity not in {"medium", "high"}:
        return False
    if pattern.frequency < max(1, int(getattr(config, "pattern_min_frequency", 3) or 3)):
        return False
    if pattern.distinct_turn_count < max(1, int(getattr(config, "pattern_min_distinct_turns", 2) or 2)):
        return False
    if not pattern.candidate_target_type:
        return False
    return bool(current_turn_reflection_ids & set(pattern.source_reflection_ids))


def _unique(values: list[str], *, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out
