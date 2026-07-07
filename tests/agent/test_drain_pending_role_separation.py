"""Tests for _drain_pending role separation.

内部事件（active_intent / subagent_result）必须使用 "system" role，
而非 "user" role，以避免 LLM 将内部事件误解为用户指令。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from OriginAgent.agent.agent_runtime import AgentRuntime, RuntimeDependencies
from OriginAgent.agent.runner import AgentRunResult
from OriginAgent.bus.events import InboundMessage


def _build_runtime_and_capture() -> tuple[AgentRuntime, dict]:
    """构建 AgentRuntime 并捕获 runner.run 收到的 AgentRunSpec"""
    captured: dict = {}

    async def fake_run(spec):
        captured["spec"] = spec
        return AgentRunResult(
            final_content="done",
            messages=[],
            tools_used=[],
            stop_reason="completed",
            had_injections=False,
        )

    # mock context builder，返回最小化的 block
    context = MagicMock()
    context.timezone = "UTC"
    context.build_runtime_context_block.return_value = {
        "type": "text", "text": "runtime", "_meta": {"kind": "runtime"},
    }
    context.build_internal_event_block.return_value = {
        "type": "text", "text": "internal event", "_meta": {"kind": "internal_event"},
    }
    context._build_user_content.return_value = [
        {"type": "text", "text": "user content"},
    ]

    tools = MagicMock()
    runner = MagicMock()
    runner.run = fake_run

    deps = RuntimeDependencies(
        tools=tools,
        context=context,
        runner=runner,
        file_state_store=MagicMock(),
        sessions=MagicMock(),
        model="test-model",
        max_iterations=10,
        tool_hint_max_length=40,
    )
    return AgentRuntime(deps), captured


def _make_session() -> SimpleNamespace:
    return SimpleNamespace(key="cli:test", metadata={})


async def _drain_items(
    runtime: AgentRuntime, captured: dict, messages: list[InboundMessage],
) -> list[dict]:
    """将 messages 放入 pending_queue，调用 _run_agent_loop，返回 drain 出的 items"""
    queue: asyncio.Queue = asyncio.Queue()
    for msg in messages:
        queue.put_nowait(msg)

    # patch file_state 操作以避免真实文件状态管理
    with patch("OriginAgent.agent.tools.file_state.bind_file_states", return_value="token"), \
         patch("OriginAgent.agent.tools.file_state.reset_file_states"):
        await runtime._run_agent_loop(
            initial_messages=[],
            session=_make_session(),
            session_key="cli:test",
            pending_queue=queue,
            capability_snapshot=MagicMock(),
        )

    spec = captured["spec"]
    return await spec.injection_callback()


@pytest.mark.asyncio
async def test_active_intent_nudge_uses_system_role():
    """active_intent 内部事件应使用 system role，而非 user role"""
    runtime, captured = _build_runtime_and_capture()
    msg = InboundMessage(
        channel="system",
        sender_id="system",
        chat_id="test",
        content="Continue working on the task",
        metadata={"injected_event": "active_intent"},
    )
    items = await _drain_items(runtime, captured, [msg])
    assert len(items) == 1
    assert items[0]["role"] == "system"


@pytest.mark.asyncio
async def test_subagent_result_uses_system_role():
    """subagent_result 内部事件应使用 system role，而非 user role"""
    runtime, captured = _build_runtime_and_capture()
    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="test",
        content="Sub-agent completed: result data",
        metadata={"injected_event": "subagent_result"},
    )
    items = await _drain_items(runtime, captured, [msg])
    assert len(items) == 1
    assert items[0]["role"] == "system"


@pytest.mark.asyncio
async def test_real_user_message_uses_user_role():
    """普通用户消息应使用 user role"""
    runtime, captured = _build_runtime_and_capture()
    msg = InboundMessage(
        channel="cli",
        sender_id="user_123",
        chat_id="test",
        content="Hello, what can you do?",
        metadata={},
    )
    items = await _drain_items(runtime, captured, [msg])
    assert len(items) == 1
    assert items[0]["role"] == "user"
