from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.loop import AgentLoop, TurnContext, TurnState
from OriginAgent.agent.turn_orchestrator import TurnOrchestrator, TurnOrchestratorDeps
from OriginAgent.bus.events import InboundMessage, OutboundMessage


def _ctx(state: TurnState) -> TurnContext:
    return TurnContext(
        msg=InboundMessage(channel="cli", sender_id="alice", chat_id="chat", content="hello"),
        session_key="cli:chat",
        state=state,
        turn_id=f"turn:{state.name.lower()}",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "wrapper_name", "delegate_name"),
    [
        (TurnState.RESTORE, "_state_restore", "state_restore"),
        (TurnState.COMPACT, "_state_compact", "state_compact"),
        (TurnState.COMMAND, "_state_command", "state_command"),
        (TurnState.BUILD, "_state_build", "state_build"),
        (TurnState.RUN, "_state_run", "state_run"),
        (TurnState.SAVE, "_state_save", "state_save"),
        (TurnState.AUTOMATION, "_state_automation", "state_automation"),
        (TurnState.RESPOND, "_state_respond", "state_respond"),
    ],
)
async def test_state_wrapper_delegates_to_turn_pipeline(
    state: TurnState,
    wrapper_name: str,
    delegate_name: str,
) -> None:
    loop = AgentLoop.__new__(AgentLoop)
    delegate = AsyncMock(return_value="ok")
    loop._turn_pipeline = SimpleNamespace(**{delegate_name: delegate})

    ctx = _ctx(state)
    result = await getattr(loop, wrapper_name)(ctx)

    assert result == "ok"
    delegate.assert_awaited_once_with(ctx)


@pytest.mark.asyncio
async def test_process_message_non_system_uses_turn_pipeline_state_machine() -> None:
    class RecordingPipeline:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def state_restore(self, ctx: TurnContext) -> str:
            self.calls.append(ctx.state.name)
            return "ok"

        async def state_compact(self, ctx: TurnContext) -> str:
            self.calls.append(ctx.state.name)
            return "ok"

        async def state_command(self, ctx: TurnContext) -> str:
            self.calls.append(ctx.state.name)
            return "dispatch"

        async def state_build(self, ctx: TurnContext) -> str:
            self.calls.append(ctx.state.name)
            return "ok"

        async def state_run(self, ctx: TurnContext) -> str:
            self.calls.append(ctx.state.name)
            return "ok"

        async def state_save(self, ctx: TurnContext) -> str:
            self.calls.append(ctx.state.name)
            return "ok"

        async def state_automation(self, ctx: TurnContext) -> str:
            self.calls.append(ctx.state.name)
            return "skip"

        async def state_respond(self, ctx: TurnContext) -> str:
            self.calls.append(ctx.state.name)
            ctx.outbound = OutboundMessage(channel="cli", chat_id="chat", content="done")
            return "ok"

    loop = AgentLoop.__new__(AgentLoop)
    loop._refresh_provider_snapshot = MagicMock()
    loop._process_system_message = AsyncMock()
    loop._turn_pipeline = RecordingPipeline()
    loop._current_meta_turn_id = None
    loop._turn_orchestrator = TurnOrchestrator(
        TurnOrchestratorDeps(
            turn_pipeline=loop._turn_pipeline,
            transitions=loop._TRANSITIONS,
            system_turn_handler=SimpleNamespace(process_message=loop._process_system_message),
            scan_meta_triggers_for_turn=MagicMock(),
            schedule_meta_cognition_reflection=MagicMock(),
            set_current_meta_turn_id=lambda turn_id: setattr(loop, "_current_meta_turn_id", turn_id),
            clear_current_meta_turn_id=lambda: setattr(loop, "_current_meta_turn_id", None),
        )
    )

    outbound = await loop._process_message(
        InboundMessage(channel="cli", sender_id="alice", chat_id="chat", content="hello"),
        session_key="cli:chat",
    )

    assert outbound is not None
    assert outbound.content == "done"
    assert loop._turn_pipeline.calls == [
        "RESTORE",
        "COMPACT",
        "COMMAND",
        "BUILD",
        "RUN",
        "SAVE",
        "AUTOMATION",
        "RESPOND",
    ]
    loop._refresh_provider_snapshot.assert_called_once()
    loop._process_system_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_system_message_delegates_to_system_turn_handler() -> None:
    loop = AgentLoop.__new__(AgentLoop)
    loop._system_turn_handler = SimpleNamespace(
        process_message=AsyncMock(
            return_value=OutboundMessage(channel="system", chat_id="chat", content="done")
        )
    )

    result = await loop._process_system_message(
        InboundMessage(channel="system", sender_id="agent", chat_id="cli:chat", content="event")
    )

    assert result is not None
    assert result.content == "done"
    loop._system_turn_handler.process_message.assert_awaited_once()


def test_get_message_dispatcher_returns_prebound_instance() -> None:
    loop = AgentLoop.__new__(AgentLoop)
    dispatcher = SimpleNamespace(name="dispatcher")
    loop._message_dispatcher = dispatcher

    assert loop._get_message_dispatcher() is dispatcher
