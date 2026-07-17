"""Tests for AgentProgressHook sensitive-tool logging layering.

The hook previously rendered every sensitive tool call as `<redacted>`,
hiding what the agent was actually doing. These tests pin the new
layered summaries for exec / message / web_fetch while keeping other
sensitive prefixes (e.g. `originagent_device_`) redacted.
"""

from __future__ import annotations

import pytest
from loguru import logger as loguru_logger

from OriginAgent.agent.hook import AgentHookContext
from OriginAgent.agent.progress_hook import AgentProgressHook
from OriginAgent.providers.base import LLMResponse, ToolCallRequest


def _make_context(tool_calls: list[ToolCallRequest]) -> AgentHookContext:
    return AgentHookContext(
        iteration=1,
        messages=[],
        response=LLMResponse(content="", tool_calls=tool_calls),
        tool_calls=tool_calls,
    )


def _capture_logs(record_sink: list[str]):
    """Register a loguru sink that appends formatted records to ``record_sink``."""
    handler_id = loguru_logger.add(
        lambda m: record_sink.append(str(m)),
        level="INFO",
        format="{message}",
    )
    return handler_id


@pytest.mark.asyncio
async def test_exec_tool_logs_command_shape() -> None:
    """exec calls should log command shape (first word + word count + operators)."""
    hook = AgentProgressHook(
        sensitive_tool_log_names={"exec", "message", "web_fetch"},
    )
    tc = ToolCallRequest(id="c1", name="exec", arguments={"command": "git status"})
    ctx = _make_context([tc])

    records: list[str] = []
    handler_id = _capture_logs(records)
    try:
        await hook.before_execute_tools(ctx)
    finally:
        loguru_logger.remove(handler_id)

    joined = "\n".join(records)
    assert "Tool call: exec(git:2:)" in joined, f"expected command shape, got: {joined!r}"
    assert "<redacted>" not in joined, "exec should not be fully redacted"


@pytest.mark.asyncio
async def test_message_tool_logs_channel_and_chars() -> None:
    """message calls should log channel, chat_id and content length (not content)."""
    hook = AgentProgressHook(
        sensitive_tool_log_names={"exec", "message", "web_fetch"},
    )
    tc = ToolCallRequest(
        id="c2",
        name="message",
        arguments={"channel": "telegram", "chat_id": "123", "content": "hello"},
    )
    ctx = _make_context([tc])

    records: list[str] = []
    handler_id = _capture_logs(records)
    try:
        await hook.before_execute_tools(ctx)
    finally:
        loguru_logger.remove(handler_id)

    joined = "\n".join(records)
    assert "channel=telegram" in joined, f"missing channel, got: {joined!r}"
    assert "content_chars=5" in joined, f"missing content_chars, got: {joined!r}"
    # Raw content must not leak into the log line.
    assert "hello" not in joined, "message content leaked into log"


@pytest.mark.asyncio
async def test_web_fetch_tool_logs_netloc_and_path() -> None:
    """web_fetch should log netloc + path only; query string must be redacted."""
    hook = AgentProgressHook(
        sensitive_tool_log_names={"exec", "message", "web_fetch"},
    )
    tc = ToolCallRequest(
        id="c3",
        name="web_fetch",
        arguments={"url": "https://example.com/path?token=secret"},
    )
    ctx = _make_context([tc])

    records: list[str] = []
    handler_id = _capture_logs(records)
    try:
        await hook.before_execute_tools(ctx)
    finally:
        loguru_logger.remove(handler_id)

    joined = "\n".join(records)
    assert "example.com" in joined, f"missing netloc, got: {joined!r}"
    assert "/path" in joined, f"missing path, got: {joined!r}"
    assert "token=secret" not in joined, "query string leaked into log"


@pytest.mark.asyncio
async def test_non_sensitive_tool_unchanged() -> None:
    """Non-sensitive tools must keep the full JSON arguments log format."""
    hook = AgentProgressHook(
        sensitive_tool_log_names={"exec", "message", "web_fetch"},
    )
    tc = ToolCallRequest(id="c4", name="read_file", arguments={"path": "/foo"})
    ctx = _make_context([tc])

    records: list[str] = []
    handler_id = _capture_logs(records)
    try:
        await hook.before_execute_tools(ctx)
    finally:
        loguru_logger.remove(handler_id)

    joined = "\n".join(records)
    assert 'Tool call: read_file({"path": "/foo"})' in joined, (
        f"non-sensitive log format changed, got: {joined!r}"
    )
