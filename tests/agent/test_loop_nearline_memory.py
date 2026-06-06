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
