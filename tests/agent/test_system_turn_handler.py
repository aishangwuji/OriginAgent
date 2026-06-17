from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.loop import AgentLoop
from OriginAgent.agent.system_turn_handler import SystemTurnHandler, SystemTurnHandlerDeps
from OriginAgent.bus.events import InboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.providers.base import LLMResponse


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok"))
    return AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")


def _handler(loop: AgentLoop) -> SystemTurnHandler:
    return SystemTurnHandler(
        SystemTurnHandlerDeps(
            services=loop.services,
            loop_context=loop._build_system_turn_loop_context(),
        )
    )


@pytest.mark.asyncio
async def test_system_turn_handler_processes_basic_system_message(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(return_value=(  # type: ignore[method-assign]
        "done",
        [],
        [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "done"},
        ],
        "stop",
        False,
    ))

    outbound = await _handler(loop).process_message(
        InboundMessage(
            channel="system",
            sender_id="agent",
            chat_id="cli:test",
            content="background event",
        )
    )

    assert outbound is not None
    assert outbound.content == "done"
    loop._run_agent_loop.assert_awaited_once()


@pytest.mark.asyncio
async def test_system_turn_handler_persists_subagent_followup_before_prompt_build(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    seen: dict[str, list[dict]] = {}

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        seen["initial_messages"] = initial_messages
        return (
            "done",
            [],
            [*initial_messages, {"role": "assistant", "content": "done"}],
            "stop",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

    await _handler(loop).process_message(
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="cli:test",
            content="subagent result",
            metadata={"injected_event": "subagent_result", "subagent_task_id": "sub-1"},
        )
    )

    assert any("subagent result" in str(message.get("content")) for message in seen["initial_messages"])
    session = loop.sessions.get_or_create("cli:test")
    assert any(message.get("subagent_task_id") == "sub-1" for message in session.messages)


@pytest.mark.asyncio
async def test_system_turn_handler_restores_checkpoint_and_pending_turn(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    restore_runtime = MagicMock(return_value=True)
    restore_pending = MagicMock(return_value=True)
    handler = SystemTurnHandler(
        SystemTurnHandlerDeps(
            services=loop.services,
            loop_context=dataclasses_replace(
                loop._build_system_turn_loop_context(),
                restore_runtime_checkpoint=restore_runtime,
                restore_pending_user_turn=restore_pending,
            ),
        )
    )
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(return_value=("done", [], [{"role": "assistant", "content": "done"}], "stop", False))  # type: ignore[method-assign]

    await handler.process_message(
        InboundMessage(
            channel="system",
            sender_id="agent",
            chat_id="cli:test",
            content="event",
        )
    )

    session = loop.sessions.get_or_create("cli:test")
    restore_runtime.assert_called_once_with(session)
    restore_pending.assert_called_once_with(session)


@pytest.mark.asyncio
async def test_system_turn_handler_schedules_nearline_for_active_intent(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.nearline_memory = SimpleNamespace(
        enabled=True,
        process_turn=AsyncMock(return_value=None),
    )
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(return_value=("done", [], [{"role": "assistant", "content": "done"}], "stop", False))  # type: ignore[method-assign]

    await _handler(loop).process_message(
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
async def test_system_turn_handler_websocket_outbound_keeps_goal_state(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(return_value=("done", [], [{"role": "assistant", "content": "done"}], "stop", False))  # type: ignore[method-assign]

    outbound = await _handler(loop).process_message(
        InboundMessage(
            channel="system",
            sender_id="agent",
            chat_id="websocket:test",
            content="event",
        )
    )

    assert outbound is not None
    assert outbound.channel == "websocket"
    assert "goal_state" in outbound.metadata


def dataclasses_replace(obj, **changes):
    from dataclasses import replace

    return replace(obj, **changes)
