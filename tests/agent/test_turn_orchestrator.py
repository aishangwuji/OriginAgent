from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.agent_turn_pipeline import TurnContext, TurnState
from OriginAgent.agent.system_turn_handler import SystemTurnHandler
from OriginAgent.agent.turn_orchestrator import TurnOrchestrator, TurnOrchestratorDeps
from OriginAgent.bus.events import InboundMessage, OutboundMessage


def _make_orchestrator(
    *,
    turn_pipeline: object,
    transitions: dict[tuple[TurnState, str], TurnState],
    system_turn_handler: SystemTurnHandler | object | None = None,
    scan_meta_triggers_for_turn: MagicMock | None = None,
    schedule_meta_cognition_reflection: MagicMock | None = None,
) -> tuple[TurnOrchestrator, MagicMock, MagicMock]:
    scan = scan_meta_triggers_for_turn or MagicMock()
    schedule = schedule_meta_cognition_reflection or MagicMock()
    handler = system_turn_handler or SimpleNamespace(process_message=AsyncMock())
    orchestrator = TurnOrchestrator(
        TurnOrchestratorDeps(
            turn_pipeline=turn_pipeline,
            transitions=transitions,
            system_turn_handler=handler,  # type: ignore[arg-type]
            scan_meta_triggers_for_turn=scan,
            schedule_meta_cognition_reflection=schedule,
        )
    )
    return orchestrator, scan, schedule


@pytest.mark.asyncio
async def test_turn_orchestrator_delegates_system_messages() -> None:
    system_turn_handler = SimpleNamespace(
        process_message=AsyncMock(
            return_value=OutboundMessage(channel="system", chat_id="x", content="ok")
        )
    )
    orchestrator, _scan, _schedule = _make_orchestrator(
        turn_pipeline=SimpleNamespace(),
        transitions={},
        system_turn_handler=system_turn_handler,
    )

    result = await orchestrator.process_message(
        InboundMessage(channel="system", sender_id="agent", chat_id="cli:chat", content="event"),
        session_key="cli:chat",
    )

    assert result is not None
    assert result.content == "ok"
    system_turn_handler.process_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_turn_orchestrator_runs_transition_driver_and_meta_hooks() -> None:
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

    pipeline = RecordingPipeline()
    orchestrator, scan, schedule = _make_orchestrator(
        turn_pipeline=pipeline,
        transitions={
            (TurnState.RESTORE, "ok"): TurnState.COMPACT,
            (TurnState.COMPACT, "ok"): TurnState.COMMAND,
            (TurnState.COMMAND, "dispatch"): TurnState.BUILD,
            (TurnState.BUILD, "ok"): TurnState.RUN,
            (TurnState.RUN, "ok"): TurnState.SAVE,
            (TurnState.SAVE, "ok"): TurnState.AUTOMATION,
            (TurnState.AUTOMATION, "skip"): TurnState.RESPOND,
            (TurnState.RESPOND, "ok"): TurnState.DONE,
        },
    )

    outbound = await orchestrator.process_message(
        InboundMessage(channel="cli", sender_id="alice", chat_id="chat", content="hello"),
        session_key="cli:chat",
    )

    assert outbound is not None
    assert outbound.content == "done"
    assert pipeline.calls == [
        "RESTORE",
        "COMPACT",
        "COMMAND",
        "BUILD",
        "RUN",
        "SAVE",
        "AUTOMATION",
        "RESPOND",
    ]
    scan.assert_called_once()
    schedule.assert_called_once()


@pytest.mark.asyncio
async def test_turn_orchestrator_records_state_trace() -> None:
    class RecordingPipeline:
        async def state_restore(self, ctx: TurnContext) -> str:
            return "ok"

        async def state_compact(self, ctx: TurnContext) -> str:
            return "ok"

        async def state_command(self, ctx: TurnContext) -> str:
            return "dispatch"

        async def state_build(self, ctx: TurnContext) -> str:
            return "ok"

        async def state_run(self, ctx: TurnContext) -> str:
            return "ok"

        async def state_save(self, ctx: TurnContext) -> str:
            return "ok"

        async def state_automation(self, ctx: TurnContext) -> str:
            return "skip"

        async def state_respond(self, ctx: TurnContext) -> str:
            ctx.outbound = OutboundMessage(channel="cli", chat_id="chat", content="done")
            return "ok"

    captured_ctx: list[TurnContext] = []

    def capture_scan(ctx: TurnContext) -> None:
        captured_ctx.append(ctx)

    orchestrator, _scan, _schedule = _make_orchestrator(
        turn_pipeline=RecordingPipeline(),
        transitions={
            (TurnState.RESTORE, "ok"): TurnState.COMPACT,
            (TurnState.COMPACT, "ok"): TurnState.COMMAND,
            (TurnState.COMMAND, "dispatch"): TurnState.BUILD,
            (TurnState.BUILD, "ok"): TurnState.RUN,
            (TurnState.RUN, "ok"): TurnState.SAVE,
            (TurnState.SAVE, "ok"): TurnState.AUTOMATION,
            (TurnState.AUTOMATION, "skip"): TurnState.RESPOND,
            (TurnState.RESPOND, "ok"): TurnState.DONE,
        },
        scan_meta_triggers_for_turn=MagicMock(side_effect=capture_scan),
    )

    outbound = await orchestrator.process_message(
        InboundMessage(channel="cli", sender_id="alice", chat_id="chat", content="hello"),
        session_key="cli:chat",
    )

    assert outbound is not None
    assert len(captured_ctx) == 1
    trace = captured_ctx[0].trace
    assert [entry.state for entry in trace] == [
        TurnState.RESTORE,
        TurnState.COMPACT,
        TurnState.COMMAND,
        TurnState.BUILD,
        TurnState.RUN,
        TurnState.SAVE,
        TurnState.AUTOMATION,
        TurnState.RESPOND,
    ]
    assert [entry.event for entry in trace] == [
        "ok",
        "ok",
        "dispatch",
        "ok",
        "ok",
        "ok",
        "skip",
        "ok",
    ]
    assert all(entry.duration_ms >= 0 for entry in trace)


@pytest.mark.asyncio
async def test_turn_orchestrator_records_exception_trace_and_reraises() -> None:
    class FailingPipeline:
        async def state_restore(self, ctx: TurnContext) -> str:
            return "ok"

        async def state_compact(self, ctx: TurnContext) -> str:
            raise RuntimeError("boom")

    orchestrator, scan, schedule = _make_orchestrator(
        turn_pipeline=FailingPipeline(),
        transitions={
            (TurnState.RESTORE, "ok"): TurnState.COMPACT,
        },
    )

    with pytest.raises(RuntimeError, match="boom"):
        await orchestrator.process_message(
            InboundMessage(channel="cli", sender_id="alice", chat_id="chat", content="hello"),
            session_key="cli:chat",
        )

    scan.assert_not_called()
    schedule.assert_not_called()


@pytest.mark.asyncio
async def test_turn_orchestrator_reraises_cancelled_error_and_clears_turn_id() -> None:
    class CancelledPipeline:
        async def state_restore(self, ctx: TurnContext) -> str:
            raise asyncio.CancelledError()

    orchestrator, scan, schedule = _make_orchestrator(
        turn_pipeline=CancelledPipeline(),
        transitions={},
    )

    with pytest.raises(asyncio.CancelledError):
        await orchestrator.process_message(
            InboundMessage(channel="cli", sender_id="alice", chat_id="chat", content="hello"),
            session_key="cli:chat",
        )

    scan.assert_not_called()
    schedule.assert_not_called()


def test_get_turn_orchestrator_returns_prebound_instance() -> None:
    from OriginAgent.agent.loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    orchestrator = SimpleNamespace(name="orchestrator")
    loop._turn_orchestrator = orchestrator

    assert loop._get_turn_orchestrator() is orchestrator
