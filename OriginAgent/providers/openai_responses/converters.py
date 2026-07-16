"""Convert Chat Completions messages/tools to Responses API format."""

from __future__ import annotations

import json
from typing import Any

from OriginAgent.utils.attachments import (
    AttachmentDescriptor,
    attachment_placeholder_text,
    parse_attachment,
)
from OriginAgent.utils.constants import RoleConstants


def convert_messages(
    messages: list[dict[str, Any]],
    *,
    native_attachment_kinds: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Convert Chat Completions messages to Responses API input items.

    Returns ``(system_prompt, input_items)`` where *system_prompt* is extracted
    from any ``system`` role message and *input_items* is the Responses API
    ``input`` array.
    """
    system_prompt = ""
    input_items: list[dict[str, Any]] = []

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")

        if role == RoleConstants.SYSTEM:
            system_prompt = content if isinstance(content, str) else ""
            continue

        if role == RoleConstants.USER:
            input_items.append(
                convert_user_message(
                    content,
                    native_attachment_kinds=native_attachment_kinds,
                )
            )
            continue

        if role == RoleConstants.ASSISTANT:
            if isinstance(content, str) and content:
                input_items.append({
                    "type": "message", "role": RoleConstants.ASSISTANT,
                    "content": [{"type": "output_text", "text": content}],
                    "status": "completed", "id": f"msg_{idx}",
                })
            for tool_call in msg.get("tool_calls", []) or []:
                fn = tool_call.get("function") or {}
                call_id, item_id = split_tool_call_id(tool_call.get("id"))
                input_items.append({
                    "type": "function_call",
                    "id": item_id or f"fc_{idx}",
                    "call_id": call_id or f"call_{idx}",
                    "name": fn.get("name"),
                    "arguments": fn.get("arguments") or "{}",
                })
            continue

        if role == "tool":
            call_id, _ = split_tool_call_id(msg.get("tool_call_id"))
            output_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            input_items.append({"type": "function_call_output", "call_id": call_id, "output": output_text})

    return system_prompt, input_items




def _native_attachment_content(
    descriptor: AttachmentDescriptor,
    *,
    native_attachment_kinds: set[str] | None,
) -> dict[str, Any] | None:
    if native_attachment_kinds is None or descriptor.kind not in native_attachment_kinds:
        return None
    path_value = descriptor.path.resolve(strict=False).as_uri()
    if descriptor.kind == "image":
        return {"type": "input_image", "image_url": path_value, "detail": "auto"}
    if descriptor.kind == "video":
        block: dict[str, Any] = {"type": "input_video", "video_url": path_value}
        return block
    if descriptor.kind == "audio":
        return {"type": "input_audio", "audio_url": path_value}
    if descriptor.kind == "document":
        return {"type": "input_file", "file_url": path_value}
    return None


def convert_user_message(
    content: Any,
    *,
    native_attachment_kinds: set[str] | None = None,
) -> dict[str, Any]:
    """Convert a user message's content to Responses API format.

    Handles plain strings, ``text`` blocks -> ``input_text``, and
    ``image_url`` blocks -> ``input_image``.
    """
    if isinstance(content, str):
        return {"role": RoleConstants.USER, "content": [{"type": "input_text", "text": content}]}
    if isinstance(content, list):
        converted: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                converted.append({"type": "input_text", "text": item.get("text", "")})
            elif item.get("type") == "image_url":
                url = (item.get("image_url") or {}).get("url")
                if url:
                    converted.append({"type": "input_image", "image_url": url, "detail": "auto"})
            elif item.get("type") == "attachment_ref":
                descriptor = parse_attachment(item)
                if descriptor is None:
                    continue
                native = _native_attachment_content(
                    descriptor,
                    native_attachment_kinds=native_attachment_kinds,
                )
                if native is not None:
                    converted.append(native)
                else:
                    converted.append({
                        "type": "input_text",
                        "text": attachment_placeholder_text(descriptor),
                    })
        if converted:
            return {"role": RoleConstants.USER, "content": converted}
    return {"role": RoleConstants.USER, "content": [{"type": "input_text", "text": ""}]}


def convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI function-calling tool schema to Responses API flat format."""
    converted: list[dict[str, Any]] = []
    for tool in tools:
        fn = (tool.get("function") or {}) if tool.get("type") == "function" else tool
        name = fn.get("name")
        if not name:
            continue
        params = fn.get("parameters") or {}
        converted.append({
            "type": "function",
            "name": name,
            "description": fn.get("description") or "",
            "parameters": params if isinstance(params, dict) else {},
        })
    return converted


def split_tool_call_id(tool_call_id: Any) -> tuple[str, str | None]:
    """Split a compound ``call_id|item_id`` string.

    Returns ``(call_id, item_id)`` where *item_id* may be ``None``.
    """
    if isinstance(tool_call_id, str) and tool_call_id:
        if "|" in tool_call_id:
            call_id, item_id = tool_call_id.split("|", 1)
            return call_id, item_id or None
        return tool_call_id, None
    return "call_0", None
