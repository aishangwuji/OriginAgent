"""Tests for turn-scoped tool idempotency in the agent runner."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from OriginAgent.config.schema import AgentDefaults
from OriginAgent.providers.base import LLMResponse, ToolCallRequest

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


@pytest.mark.asyncio
async def test_duplicate_tool_call_rejected():
    """同一 turn 内重复调用同一工具同一参数，第二次应被幂等检查拒绝。"""
    from OriginAgent.agent.runner import AgentRunSpec, AgentRunner

    provider = MagicMock()
    llm_call_count = {"n": 0}
    execute_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        llm_call_count["n"] += 1
        if llm_call_count["n"] == 1:
            return LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="call_1", name="cron_add", arguments={"name": "job1"})],
                finish_reason="tool_calls",
                usage={},
            )
        if llm_call_count["n"] == 2:
            # LLM 再次调用同一工具同一参数
            return LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="call_2", name="cron_add", arguments={"name": "job1"})],
                finish_reason="tool_calls",
                usage={},
            )
        return LLMResponse(content="done", tool_calls=[], finish_reason="stop", usage={})

    provider.chat_with_retry = chat_with_retry

    tools = MagicMock()
    tools.get_definitions.return_value = []

    async def mock_execute(name, params):
        execute_count["n"] += 1
        return "cron job created"
    tools.execute = mock_execute

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "create a cron job"},
        ],
        tools=tools,
        model="test-model",
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    # 工具只应执行一次，第二次被幂等检查拒绝
    assert execute_count["n"] == 1
    assert result.final_content == "done"

    # tool_events 应包含一个 ok 和一个 skipped
    statuses = [e.get("status") for e in result.tool_events]
    assert statuses.count("ok") == 1
    assert statuses.count("skipped") == 1

    # 第二次调用的 tool result 消息应包含幂等拒绝提示
    tool_messages = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_messages) == 2
    assert "已执行，勿重复" in str(tool_messages[1]["content"])


@pytest.mark.asyncio
async def test_different_args_tool_call_allowed():
    """同一 turn 内调用同一工具但参数不同，两次都应正常执行。"""
    from OriginAgent.agent.runner import AgentRunSpec, AgentRunner

    provider = MagicMock()
    llm_call_count = {"n": 0}
    execute_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        llm_call_count["n"] += 1
        if llm_call_count["n"] == 1:
            return LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="call_1", name="cron_add", arguments={"name": "job1"})],
                finish_reason="tool_calls",
                usage={},
            )
        if llm_call_count["n"] == 2:
            # 同一工具但参数不同，幂等键不同，应正常执行
            return LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="call_2", name="cron_add", arguments={"name": "job2"})],
                finish_reason="tool_calls",
                usage={},
            )
        return LLMResponse(content="done", tool_calls=[], finish_reason="stop", usage={})

    provider.chat_with_retry = chat_with_retry

    tools = MagicMock()
    tools.get_definitions.return_value = []

    async def mock_execute(name, params):
        execute_count["n"] += 1
        return f"cron job created: {params.get('name')}"
    tools.execute = mock_execute

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "create cron jobs"},
        ],
        tools=tools,
        model="test-model",
        max_iterations=5,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    # 两次都应正常执行
    assert execute_count["n"] == 2
    assert result.final_content == "done"

    # tool_events 应有两个 ok，无 skipped
    statuses = [e.get("status") for e in result.tool_events]
    assert statuses.count("ok") == 2
    assert statuses.count("skipped") == 0

    # 两次的 tool result 都不应包含幂等拒绝提示
    tool_messages = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_messages) == 2
    for msg in tool_messages:
        assert "已执行，勿重复" not in str(msg["content"])
