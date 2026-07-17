"""Tests for _SubagentHook sensitive-tool logging redaction.

The subagent hook previously logged full tool arguments at DEBUG level,
leaking exec commands, message content, and web_fetch URLs into logs
(rule 18 violation). These tests pin the redacted summaries to match
the main agent's AgentProgressHook behavior (Task 6).
"""

from __future__ import annotations

import pytest
from loguru import logger as loguru_logger

from OriginAgent.agent.hook import AgentHookContext
from OriginAgent.agent.subagent import _SubagentHook
from OriginAgent.providers.base import LLMResponse, ToolCallRequest


_SENSITIVE_NAMES = {"exec", "message", "web_fetch"}


def _make_context(tool_calls: list[ToolCallRequest]) -> AgentHookContext:
    return AgentHookContext(
        iteration=1,
        messages=[],
        response=LLMResponse(content="", tool_calls=tool_calls),
        tool_calls=tool_calls,
    )


def _capture_logs(record_sink: list[str], level: str = "DEBUG"):
    """Register a loguru sink that appends formatted records to ``record_sink``."""
    handler_id = loguru_logger.add(
        lambda m: record_sink.append(str(m)),
        level=level,
        format="{message}",
    )
    return handler_id


@pytest.mark.asyncio
async def test_subagent_exec_redacted() -> None:
    """exec calls in subagent must log command shape, not the raw command.

    Rule 18: subagent exec arguments must not leak as plaintext into logs.
    The summary shape matches the main agent: ``git:2:`` (first word + word
    count + pipe operators).
    """
    hook = _SubagentHook(
        task_id="test-task",
        sensitive_tool_log_names=_SENSITIVE_NAMES,
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
    assert "Tool call: exec(git:2:)" in joined, (
        f"expected redacted command shape 'exec(git:2:)', got: {joined!r}"
    )
    assert "git status" not in joined, "raw exec command leaked into subagent log"


@pytest.mark.asyncio
async def test_subagent_non_sensitive_unchanged() -> None:
    """Non-sensitive tools must keep the current subagent log format.

    The format ``Subagent [task_id] executing: name with arguments: args``
    must remain unchanged for tools not in sensitive_tool_log_names.
    """
    hook = _SubagentHook(
        task_id="test-task",
        sensitive_tool_log_names=_SENSITIVE_NAMES,
    )
    tc = ToolCallRequest(id="c2", name="read_file", arguments={"path": "/foo"})
    ctx = _make_context([tc])

    records: list[str] = []
    handler_id = _capture_logs(records)
    try:
        await hook.before_execute_tools(ctx)
    finally:
        loguru_logger.remove(handler_id)

    joined = "\n".join(records)
    expected = 'Subagent [test-task] executing: read_file with arguments: {"path": "/foo"}'
    assert expected in joined, (
        f"non-sensitive log format changed, got: {joined!r}"
    )
