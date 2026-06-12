"""Builders for bounded meta-cognition triggers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from OriginAgent.agent.meta_cognition_models import MetaTrigger
from OriginAgent.session.goal_state import goal_state_raw, parse_goal_state

_USER_CORRECTION_PATTERNS = (
    "不是",
    "不对",
    "你错了",
    "我不是这个意思",
    "不是这个问题",
    "你理解错了",
)
_USER_CORRECTION_EXCLUDES = (
    "继续",
    "然后呢",
    "还有吗",
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_digest(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _trim_text(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def build_tool_failure_trigger(
    *,
    session_key: str,
    tool_name: str,
    params: dict[str, Any],
    status: str,
    error_kind: str | None = None,
    policy_rule: str | None = None,
) -> MetaTrigger | None:
    if status not in {"error", "policy_denied"}:
        return None
    source_reference = f"{tool_name}:{_safe_digest(params)}"
    trigger_id = f"meta:tool_failure:{session_key}:{source_reference}"
    return MetaTrigger(
        trigger_id=trigger_id,
        session_key=session_key,
        trigger_type="tool_failure",
        source_type="tool_execution_observer",
        source_reference=source_reference,
        severity="medium" if status == "error" else "low",
        created_at=_utcnow_iso(),
        cooldown_key=f"{session_key}:tool_failure:{source_reference}",
        evidence_refs=[f"tool:{tool_name}", f"status:{status}"],
        payload={
            "tool_name": tool_name,
            "status": status,
            "error_kind": error_kind,
            "policy_rule": policy_rule,
        },
    )


def build_task_completion_trigger(
    *,
    session_key: str,
    session_metadata: dict[str, Any] | None,
    params: dict[str, Any],
) -> MetaTrigger | None:
    goal = parse_goal_state(goal_state_raw(session_metadata))
    if not isinstance(goal, dict) or goal.get("status") != "completed":
        return None
    recap = _trim_text(params.get("recap"), max_chars=200)
    trigger_id = f"meta:task_completion:{session_key}:{_safe_digest({'recap': recap, 'goal': goal})}"
    return MetaTrigger(
        trigger_id=trigger_id,
        session_key=session_key,
        trigger_type="task_completion",
        source_type="complete_goal",
        source_reference="complete_goal",
        severity="medium",
        created_at=_utcnow_iso(),
        cooldown_key=f"{session_key}:task_completion:complete_goal",
        evidence_refs=["tool:complete_goal", f"goal_status:{goal.get('status')}"],
        payload={
            "goal_status_before_completion": "active",
            "recap_present": bool(recap),
            "recap_preview": recap,
        },
    )


def build_user_correction_trigger(
    *,
    session_key: str,
    user_message: str,
    last_assistant_message: str | None,
) -> MetaTrigger | None:
    text = _trim_text(user_message, max_chars=500)
    if not text:
        return None
    if any(excluded in text for excluded in _USER_CORRECTION_EXCLUDES):
        return None
    matched = next((pattern for pattern in _USER_CORRECTION_PATTERNS if pattern in text), None)
    if not matched:
        return None
    assistant_preview = _trim_text(last_assistant_message, max_chars=160)
    source_reference = f"user_correction:{_safe_digest({'user': text, 'assistant': assistant_preview})}"
    return MetaTrigger(
        trigger_id=f"meta:user_correction:{session_key}:{source_reference}",
        session_key=session_key,
        trigger_type="user_correction",
        source_type="turn_end_scan",
        source_reference=source_reference,
        severity="medium",
        created_at=_utcnow_iso(),
        cooldown_key=f"{session_key}:user_correction:{source_reference}",
        evidence_refs=["user_message", "last_assistant_message"],
        payload={
            "matched_pattern": matched,
            "user_message_preview": text[:200],
            "assistant_message_preview": assistant_preview,
        },
    )


def latest_assistant_message(all_messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(all_messages or []):
        if str(message.get("role") or "") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return None
