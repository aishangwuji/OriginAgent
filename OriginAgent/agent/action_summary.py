"""Shared action runtime summary normalization."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

ACTION_SUMMARY_SOURCE = "AgentLoop._cached_action_summary"


def action_summary_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_action_summary(
    audit: dict[str, Any] | None,
    *,
    cache_timestamp: str | None = None,
) -> dict[str, Any]:
    """Return the stable P5A action summary shape from a raw audit payload."""

    raw = dict(audit or {})
    planner_result = raw.get("planner_result")
    normalized_planner_result = dict(planner_result) if isinstance(planner_result, dict) else {}
    planning_inputs = raw.get("planning_inputs")
    planning_evidence = raw.get("planning_evidence")
    skipped_reasons = raw.get("skipped_reasons")
    preconditions = raw.get("preconditions")
    execution_result = raw.get("execution_result")
    continuity_writeback = raw.get("continuity_writeback")
    return {
        "source": ACTION_SUMMARY_SOURCE,
        "available": bool(normalized_planner_result),
        "status": raw.get("status", "idle"),
        "reason": raw.get("reason"),
        "planning_inputs": dict(planning_inputs) if isinstance(planning_inputs, dict) else {},
        "planning_evidence": dict(planning_evidence) if isinstance(planning_evidence, dict) else {},
        "automation_origin": raw.get("automation_origin"),
        "planner_result": normalized_planner_result,
        "selected_proposal_digest": raw.get("selected_proposal_digest"),
        "skipped_reasons": list(skipped_reasons) if isinstance(skipped_reasons, list) else [],
        "preconditions": dict(preconditions) if isinstance(preconditions, dict) else {},
        "execution_result": dict(execution_result) if isinstance(execution_result, dict) else {},
        "continuity_writeback": dict(continuity_writeback) if isinstance(continuity_writeback, dict) else {},
        "selection_reason": raw.get("selection_reason"),
        "cache_timestamp": cache_timestamp or str(raw.get("cache_timestamp") or action_summary_timestamp()),
    }


def action_summary_from_loop(loop: Any | None) -> dict[str, Any]:
    """Read the cached action summary, backfilling from the latest audit when needed."""

    if loop is None:
        return normalize_action_summary({})
    cached = getattr(loop, "_cached_action_summary", None)
    if isinstance(cached, dict) and cached:
        return normalize_action_summary(cached, cache_timestamp=cached.get("cache_timestamp"))
    audit = getattr(loop, "_last_action_continuity_audit", {}) or {}
    summary = normalize_action_summary(audit)
    try:
        setattr(loop, "_cached_action_summary", dict(summary))
    except Exception:
        pass
    return summary
