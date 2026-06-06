from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.loop import AgentLoop, TurnContext, TurnState
from OriginAgent.bus.events import InboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.providers.base import LLMResponse
from OriginAgent.session.manager import Session


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok"))
    return AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")


@pytest.mark.asyncio
async def test_schedule_nearline_memory_runs_in_background_for_successful_turn(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.nearline_memory = SimpleNamespace(
        enabled=True,
        process_turn=AsyncMock(return_value=None),
    )
    session = Session(key="websocket:chat1")
    ctx = TurnContext(
        msg=InboundMessage(channel="websocket", sender_id="user-1", chat_id="chat1", content="hi"),
        session_key="websocket:chat1",
        state=TurnState.SAVE,
        turn_id="turn-1",
        session=session,
        final_content="done",
        stop_reason="stop",
        runtime_context=SimpleNamespace(actor_id="user-1"),
    )

    loop._schedule_nearline_memory(ctx)
    await asyncio.gather(*loop._background_tasks, return_exceptions=True)

    loop.nearline_memory.process_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_schedule_nearline_memory_skips_error_turns_and_disabled_service(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.nearline_memory = SimpleNamespace(
        enabled=False,
        process_turn=AsyncMock(return_value=None),
    )
    session = Session(key="websocket:chat2")
    ctx = TurnContext(
        msg=InboundMessage(channel="websocket", sender_id="user-1", chat_id="chat2", content="hi"),
        session_key="websocket:chat2",
        state=TurnState.SAVE,
        turn_id="turn-2",
        session=session,
        stop_reason="tool_error",
    )

    loop._schedule_nearline_memory(ctx)

    assert loop.nearline_memory.process_turn.await_count == 0
    assert len(loop._background_tasks) == 0


@pytest.mark.parametrize("stop_reason", ["ask_user", "max_iterations", "empty_final_response"])
@pytest.mark.asyncio
async def test_schedule_nearline_memory_skips_non_successful_turns(tmp_path: Path, stop_reason: str) -> None:
    loop = _make_loop(tmp_path)
    loop.nearline_memory = SimpleNamespace(
        enabled=True,
        process_turn=AsyncMock(return_value=None),
    )
    session = Session(key="websocket:chat3")
    ctx = TurnContext(
        msg=InboundMessage(channel="websocket", sender_id="user-1", chat_id="chat3", content="hi"),
        session_key="websocket:chat3",
        state=TurnState.SAVE,
        turn_id="turn-3",
        session=session,
        final_content="not a successful completion",
        stop_reason=stop_reason,
        runtime_context=SimpleNamespace(actor_id="user-1"),
    )

    loop._schedule_nearline_memory(ctx)

    assert loop.nearline_memory.process_turn.await_count == 0
    assert len(loop._background_tasks) == 0


@pytest.mark.asyncio
async def test_process_system_message_schedules_nearline_for_subagent_result(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.nearline_memory = SimpleNamespace(
        enabled=True,
        process_turn=AsyncMock(return_value=None),
    )
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(return_value=("done", [], [{"role": "assistant", "content": "done"}], "stop", False))  # type: ignore[method-assign]

    await loop._process_message(
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="cli:test",
            content="subagent result",
            metadata={"injected_event": "subagent_result", "subagent_task_id": "sub-1"},
        )
    )
    await asyncio.gather(*loop._background_tasks, return_exceptions=True)

    loop.nearline_memory.process_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_system_message_schedules_nearline_for_active_intent(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.nearline_memory = SimpleNamespace(
        enabled=True,
        process_turn=AsyncMock(return_value=None),
    )
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(return_value=("done", [], [{"role": "assistant", "content": "done"}], "stop", False))  # type: ignore[method-assign]

    await loop._process_message(
        InboundMessage(
            channel="system",
            sender_id="agent_active",
            chat_id="cli:test",
            content="active intent",
            metadata={"injected_event": "active_intent"},
        )
    )
    await asyncio.gather(*loop._background_tasks, return_exceptions=True)

    loop.nearline_memory.process_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_system_message_skips_nearline_for_non_successful_turn(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.nearline_memory = SimpleNamespace(
        enabled=True,
        process_turn=AsyncMock(return_value=None),
    )
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(return_value=("need input", [], [{"role": "assistant", "content": "need input"}], "ask_user", False))  # type: ignore[method-assign]

    await loop._process_message(
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="cli:test",
            content="subagent result",
            metadata={"injected_event": "subagent_result", "subagent_task_id": "sub-1"},
        )
    )

    assert loop.nearline_memory.process_turn.await_count == 0
