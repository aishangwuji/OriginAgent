"""Tests for log_event attrs enrichment in the shared agent runner.

验证三个关键事件的 log_event 调用包含新增的 attrs：
- llm.request: stream / message_count
- tools.execute: tool_names
- run.complete: elapsed_ms
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from OriginAgent.agent.hook import AgentHook, AgentHookContext
from OriginAgent.agent.runner import AgentRunner, AgentRunSpec
from OriginAgent.config.schema import AgentDefaults
from OriginAgent.providers.base import LLMResponse, ToolCallRequest
from OriginAgent.security.policy import PolicyDeniedError

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


def _find_log_call(mock_log_event, event_name):
    """从 mock_log_event.call_args_list 中找出指定事件名的首次调用。"""
    for call in mock_log_event.call_args_list:
        if call.args and call.args[0] == event_name:
            return call
    return None


def _build_minimal_runner():
    """构造一个最小可运行的 AgentRunner：非流式、单轮、无工具调用。"""
    provider = MagicMock()
    provider.supports_progress_deltas = False

    async def _chat(**kwargs):
        # Windows 上 time.monotonic() 分辨率约 15ms，需睡足够久以测出正 elapsed_ms
        await asyncio.sleep(0.05)
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = _chat
    provider.chat_stream_with_retry = AsyncMock()
    tools = MagicMock()
    tools.get_definitions.return_value = []
    return AgentRunner(provider), tools


@pytest.mark.asyncio
async def test_llm_request_includes_stream_and_message_count():
    """llm.request 日志应包含 stream（bool）与 message_count（int）。"""
    runner, tools = _build_minimal_runner()

    with patch("OriginAgent.agent.runner.log_event") as mock_log_event:
        await runner.run(AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": "hi"},
            ],
            tools=tools,
            model="test-model",
            max_iterations=1,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        ))

    request_call = _find_log_call(mock_log_event, "llm.request")
    assert request_call is not None, "llm.request 未被记录"
    kwargs = request_call.kwargs
    assert "stream" in kwargs, "llm.request 缺少 stream 属性"
    assert isinstance(kwargs["stream"], bool)
    assert "message_count" in kwargs, "llm.request 缺少 message_count 属性"
    assert isinstance(kwargs["message_count"], int)
    assert kwargs["message_count"] >= 1


@pytest.mark.asyncio
async def test_tools_execute_includes_tool_names():
    """tools.execute 日志应包含 tool_names（逗号分隔的工具名）。"""
    runner, _tools = _build_minimal_runner()
    spec = AgentRunSpec(
        initial_messages=[],
        tools=MagicMock(),
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        session_key="test-session",
    )
    tool_calls = [
        ToolCallRequest(id="1", name="read_file", arguments={}),
        ToolCallRequest(id="2", name="exec", arguments={}),
    ]

    # 直接调用 _execute_tools 并短路批次划分，聚焦验证日志本身。
    with patch("OriginAgent.agent.runner.log_event") as mock_log_event, \
         patch.object(runner, "_partition_tool_batches", return_value=[]):
        await runner._execute_tools(spec, tool_calls, {}, {})

    execute_call = _find_log_call(mock_log_event, "tools.execute")
    assert execute_call is not None, "tools.execute 未被记录"
    kwargs = execute_call.kwargs
    assert "tool_names" in kwargs, "tools.execute 缺少 tool_names 属性"
    assert kwargs["tool_names"] == "read_file,exec"
    assert kwargs["tool_count"] == 2


@pytest.mark.asyncio
async def test_run_complete_includes_elapsed_ms():
    """run.complete 日志应包含 elapsed_ms（正数，毫秒）。"""
    runner, tools = _build_minimal_runner()

    with patch("OriginAgent.agent.runner.log_event") as mock_log_event:
        await runner.run(AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": "hi"},
            ],
            tools=tools,
            model="test-model",
            max_iterations=1,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        ))

    complete_call = _find_log_call(mock_log_event, "run.complete")
    assert complete_call is not None, "run.complete 未被记录"
    kwargs = complete_call.kwargs
    assert "elapsed_ms" in kwargs, "run.complete 缺少 elapsed_ms 属性"
    assert isinstance(kwargs["elapsed_ms"], (int, float))
    assert kwargs["elapsed_ms"] > 0, "elapsed_ms 必须为正数"


@pytest.mark.asyncio
async def test_llm_response_logs_finish_reason_and_tool_count():
    """llm.response 日志应包含 finish_reason / has_tool_calls / tool_call_count / content_chars / reasoning_chars。"""
    runner, _tools = _build_minimal_runner()
    # 覆盖 provider 返回带 tool_calls 的响应
    tool_calls = [
        ToolCallRequest(id="1", name="read_file", arguments={}),
        ToolCallRequest(id="2", name="exec", arguments={}),
    ]
    fake_response = LLMResponse(
        content="",
        tool_calls=tool_calls,
        finish_reason="tool_use",
        reasoning_content="xxx",
        usage={},
    )
    runner._provider.chat_with_retry = AsyncMock(return_value=fake_response)

    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=_tools,
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        session_key="test-session",
        llm_timeout_s=0,  # 禁用外层超时，走 return response 路径
    )
    hook = AgentHook()
    context = AgentHookContext(iteration=0, messages=spec.initial_messages)

    with patch("OriginAgent.agent.runner.log_event") as mock_log_event:
        await runner._request_model(runner._provider, spec, spec.initial_messages, hook, context)

    resp_call = _find_log_call(mock_log_event, "llm.response")
    assert resp_call is not None, "llm.response 未被记录"
    kwargs = resp_call.kwargs
    assert kwargs["finish_reason"] == "tool_use"
    assert kwargs["has_tool_calls"] is True
    assert kwargs["tool_call_count"] == 2
    assert kwargs["content_chars"] == 0
    assert kwargs["reasoning_chars"] == 3


def _build_tool_spec(execute_mock):
    """构造一个可直接调用 _run_tool 的最小 spec。"""
    tools = MagicMock()
    tools.get_definitions = MagicMock(return_value=[])
    tools.prepare_call = None
    tools.execute = execute_mock
    tools.audit_tool_result_async = None
    tools.audit_tool_result = None
    return AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        session_key="test-session",
    )


@pytest.mark.asyncio
async def test_tool_complete_logs_status_and_duration():
    """tool.complete 日志应包含 name / status / duration_ms / result_size / error_kind。"""
    runner, _ = _build_minimal_runner()
    tool_call = ToolCallRequest(id="1", name="read_file", arguments={})

    # 成功场景：工具返回 "result text"（11 字符）
    spec_ok = _build_tool_spec(AsyncMock(return_value="result text"))
    with patch("OriginAgent.agent.runner.log_event") as mock_log_event:
        await runner._run_tool(spec_ok, tool_call, {}, {}, set())

    ok_call = _find_log_call(mock_log_event, "tool.complete")
    assert ok_call is not None, "tool.complete 未被记录（成功场景）"
    kwargs = ok_call.kwargs
    assert kwargs["name"] == "read_file"
    assert kwargs["status"] == "success"
    assert isinstance(kwargs["duration_ms"], (int, float))
    assert kwargs["duration_ms"] >= 0
    assert kwargs["result_size"] == 11
    assert kwargs["error_kind"] is None

    # 失败场景：工具抛出 Exception
    spec_err = _build_tool_spec(AsyncMock(side_effect=Exception("boom")))
    with patch("OriginAgent.agent.runner.log_event") as mock_log_event:
        await runner._run_tool(spec_err, tool_call, {}, {}, set())

    err_call = _find_log_call(mock_log_event, "tool.complete")
    assert err_call is not None, "tool.complete 未被记录（失败场景）"
    kwargs = err_call.kwargs
    assert kwargs["name"] == "read_file"
    assert kwargs["status"] == "error"
    assert isinstance(kwargs["duration_ms"], (int, float))
    assert kwargs["duration_ms"] >= 0
    assert kwargs["result_size"] == 0
    assert kwargs["error_kind"] == "Exception"


@pytest.mark.asyncio
async def test_loop_continue_logs_on_tool_calls():
    """loop.continue 事件：LLM 返回 tool_calls 时应记录 reason=tool_calls/tool_count/iteration。"""
    runner, _tools = _build_minimal_runner()
    tool_call = ToolCallRequest(id="1", name="read_file", arguments={})
    # 第一轮返回 tool_calls 触发 loop.continue，第二轮返回无 tool_calls 让循环正常结束
    responses = [
        LLMResponse(content="", tool_calls=[tool_call], finish_reason="tool_calls", usage={}),
        LLMResponse(content="done", tool_calls=[], finish_reason="stop", usage={}),
    ]
    call_idx = [0]

    async def _chat(**kwargs):
        idx = call_idx[0]
        call_idx[0] += 1
        return responses[idx]

    runner._provider.chat_with_retry = _chat

    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=_tools,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        session_key="test-session",
    )

    with patch("OriginAgent.agent.runner.log_event") as mock_log_event, \
         patch.object(runner, "_execute_tools", new_callable=AsyncMock,
                      return_value=([], [], None)):
        await runner.run(spec)

    cont_call = _find_log_call(mock_log_event, "loop.continue")
    assert cont_call is not None, "loop.continue 未被记录"
    kwargs = cont_call.kwargs
    assert kwargs["reason"] == "tool_calls"
    assert kwargs["iteration"] == 0
    assert kwargs["tool_count"] == 1
    assert kwargs["session_key"] == "test-session"


@pytest.mark.asyncio
async def test_loop_finalize_logs_on_no_tool_calls():
    """loop.finalize 事件：LLM 无 tool_calls 时应记录 reason=no_tool_calls/finish_reason/iteration。"""
    runner, _tools = _build_minimal_runner()
    runner._provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="done", tool_calls=[], finish_reason="stop", usage={},
    ))

    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=_tools,
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        session_key="test-session",
    )

    with patch("OriginAgent.agent.runner.log_event") as mock_log_event:
        await runner.run(spec)

    fin_call = _find_log_call(mock_log_event, "loop.finalize")
    assert fin_call is not None, "loop.finalize 未被记录"
    kwargs = fin_call.kwargs
    assert kwargs["reason"] == "no_tool_calls"
    assert kwargs["iteration"] == 0
    assert kwargs["finish_reason"] == "stop"
    assert kwargs["session_key"] == "test-session"


@pytest.mark.asyncio
async def test_loop_max_iterations_logs_warning():
    """loop.max_iterations 事件：达到 max_iterations 时应记录事件并触发 logger.warning。"""
    runner, _tools = _build_minimal_runner()
    tool_call = ToolCallRequest(id="1", name="read_file", arguments={})
    # 每轮都返回 tool_calls，让循环耗尽 max_iterations
    runner._provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="", tool_calls=[tool_call], finish_reason="tool_calls", usage={},
    ))

    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=_tools,
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        session_key="test-session",
    )

    with patch("OriginAgent.agent.runner.log_event") as mock_log_event, \
         patch("OriginAgent.agent.runner.logger") as mock_logger, \
         patch.object(runner, "_execute_tools", new_callable=AsyncMock,
                      return_value=([], [], None)):
        await runner.run(spec)

    max_call = _find_log_call(mock_log_event, "loop.max_iterations")
    assert max_call is not None, "loop.max_iterations 未被记录"
    kwargs = max_call.kwargs
    assert kwargs["iteration"] == 1
    assert kwargs["tools_used_count"] == 1
    assert kwargs["session_key"] == "test-session"
    # 验证 WARNING 级别日志被触发（for-else 分支应额外记录 warning）
    assert mock_logger.warning.called, "logger.warning 未被调用"


@pytest.mark.asyncio
async def test_tool_denied_logs_warning():
    """工具被策略拒绝（policy_rule）时应记录 WARNING，消息含工具名/policy_rule/session_key。"""
    runner, _ = _build_minimal_runner()
    tool_call = ToolCallRequest(id="1", name="exec", arguments={})

    # 工具执行抛出 PolicyDeniedError，触发 execution_error 分支的 policy_rule 拒绝
    denied_exc = PolicyDeniedError(
        "tool 'exec' is not allowed by the current capability snapshot",
        code="capability_exec_denied",
        boundary="tool",
        policy_rule="capability_exec_denied",
    )
    spec = _build_tool_spec(AsyncMock(side_effect=denied_exc))

    with patch("OriginAgent.agent.runner.logger") as mock_logger:
        await runner._run_tool(spec, tool_call, {}, {}, set())

    assert mock_logger.warning.called, "logger.warning 未被调用"
    warning_args = mock_logger.warning.call_args.args
    all_args_str = " ".join(str(a) for a in warning_args)
    assert "exec" in all_args_str, f"warning 消息未包含工具名 'exec': {warning_args}"
    assert "capability_exec_denied" in all_args_str, (
        f"warning 消息未包含 policy_rule: {warning_args}"
    )
    assert "test-session" in all_args_str, (
        f"warning 消息未包含 session_key: {warning_args}"
    )
