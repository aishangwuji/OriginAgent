"""Canonicalize session history into nearline MemCell boundaries."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from OriginAgent.memory.models import CanonicalMessage, MemCell
from OriginAgent.utils.helpers import stringify_text_blocks

_DEFAULT_ROLE = "assistant"
_LEGAL_ROLES = {"user", "assistant", "tool", "system"}


def canonicalize_session_messages(
    session_key: str,
    messages: list[dict[str, Any]],
    *,
    start_index: int = 0,
    channel: str = "",
    chat_id: str = "",
) -> list[CanonicalMessage]:
    """Normalize persisted session messages into stable nearline records."""
    canonical: list[CanonicalMessage] = []
    for offset, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or _DEFAULT_ROLE)
        if role not in _LEGAL_ROLES:
            role = _DEFAULT_ROLE
        timestamp = _string_value(message.get("timestamp"))
        if not timestamp:
            continue
        content = _normalize_content(message.get("content"))
        tool_name = _string_value(message.get("name")) or None
        tool_call_id = _string_value(message.get("tool_call_id")) or None
        sender_id = _string_value(message.get("sender_id")) or None
        message_id = _build_message_id(
            session_key=session_key,
            absolute_index=start_index + offset,
            role=role,
            timestamp=timestamp,
            content=content,
            tool_call_id=tool_call_id,
        )
        metadata = _message_metadata(message)
        canonical.append(
            CanonicalMessage(
                message_id=message_id,
                session_key=session_key,
                role=role,
                content=content,
                timestamp=timestamp,
                channel=channel,
                chat_id=chat_id,
                sender_id=sender_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                metadata=metadata,
            )
        )
    return canonical


def segment_memcells(
    messages: list[CanonicalMessage],
    *,
    idle_gap_seconds: int = 900,
    max_messages_per_memcell: int = 12,
) -> list[MemCell]:
    """Split canonical messages into stable MemCell windows."""
    if not messages:
        return []
    idle_gap_seconds = max(0, int(idle_gap_seconds))
    max_messages_per_memcell = max(1, int(max_messages_per_memcell))

    chunks: list[list[CanonicalMessage]] = []
    current: list[CanonicalMessage] = []
    for message in messages:
        if current and _should_split(
            current=current,
            incoming=message,
            idle_gap_seconds=idle_gap_seconds,
            max_messages_per_memcell=max_messages_per_memcell,
        ):
            chunks.append(current)
            current = []
        current.append(message)
    if current:
        chunks.append(current)
    return [_build_memcell(chunk) for chunk in chunks]


def _should_split(
    *,
    current: list[CanonicalMessage],
    incoming: CanonicalMessage,
    idle_gap_seconds: int,
    max_messages_per_memcell: int,
) -> bool:
    if len(current) >= max_messages_per_memcell:
        return True
    previous = current[-1]
    if idle_gap_seconds > 0:
        gap = _timestamp_gap_seconds(previous.timestamp, incoming.timestamp)
        if gap is not None and gap > idle_gap_seconds:
            return True
    current_roles = {message.role for message in current}
    if incoming.role == "assistant" and incoming.metadata.get("tool_calls"):
        return "user" in current_roles
    if incoming.role == "user" and current_roles & {"assistant", "tool"}:
        return True
    if previous.role == "tool" and incoming.role == "assistant":
        return True
    if previous.role == "assistant" and incoming.role == "tool":
        return not bool(previous.metadata.get("tool_calls"))
    if previous.role == "system" and incoming.role != "system":
        return True
    return False


def _build_memcell(messages: list[CanonicalMessage]) -> MemCell:
    started_at = messages[0].timestamp
    ended_at = messages[-1].timestamp
    joined_content = "\n\n".join(
        f"{message.role}: {message.content}".rstrip()
        for message in messages
        if message.content or message.role == "tool"
    )
    kind = _classify_memcell_kind(messages)
    message_ids = [message.message_id for message in messages]
    digest = hashlib.sha1("|".join(message_ids).encode("utf-8")).hexdigest()[:12]
    return MemCell(
        memcell_id=f"memcell_{digest}",
        session_key=messages[0].session_key,
        kind=kind,
        started_at=started_at,
        ended_at=ended_at,
        message_ids=message_ids,
        roles=[message.role for message in messages],
        content=joined_content,
        messages=list(messages),
        metadata={
            "message_count": len(messages),
            "contains_tool_call": any(message.role == "tool" for message in messages),
        },
    )


def _classify_memcell_kind(messages: list[CanonicalMessage]) -> str:
    roles = {message.role for message in messages}
    if roles == {"tool"}:
        return "tool_span"
    if "tool" in roles:
        return "mixed_turn"
    return "conversation_turn"


def _timestamp_gap_seconds(previous: str, current: str) -> float | None:
    try:
        previous_dt = datetime.fromisoformat(previous)
        current_dt = datetime.fromisoformat(current)
    except ValueError:
        return None
    return (current_dt - previous_dt).total_seconds()


def _normalize_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_content = stringify_text_blocks(content)
        if text_content is not None:
            return text_content
        return json.dumps(content, ensure_ascii=False)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


def _message_metadata(message: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in (
        "tool_calls",
        "reasoning_content",
        "thinking_blocks",
        "media",
        "injected_event",
        "subagent_task_id",
    ):
        value = message.get(key)
        if value is not None:
            metadata[key] = value
    return metadata


def _build_message_id(
    *,
    session_key: str,
    absolute_index: int,
    role: str,
    timestamp: str,
    content: str,
    tool_call_id: str | None,
) -> str:
    payload = {
        "content": content,
        "index": absolute_index,
        "role": role,
        "session_key": session_key,
        "timestamp": timestamp,
        "tool_call_id": tool_call_id or "",
    }
    digest = hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    return f"msg_{absolute_index}_{digest}"


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else ""
