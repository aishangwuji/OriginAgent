"""Tests for typed Turn state machine."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger as loguru_logger

from OriginAgent.agent.agent_turn_pipeline import (
    AgentTurnPipeline,
    TURN_PIPELINE_TRANSITIONS,
    TurnContext,
    TurnEvent,
    TurnState,
)
from OriginAgent.agent.turn_orchestrator import TurnOrchestrator, TurnOrchestratorDeps
from OriginAgent.bus.events import InboundMessage, OutboundMessage


def test_turnevent_is_enum():
    event = TurnEvent.OK
    assert isinstance(event, TurnEvent)


def test_all_transitions_use_turnevent():
    for (state, event), next_state in TURN_PIPELINE_TRANSITIONS.items():
        assert isinstance(state, TurnState), f"State {state} is not TurnState"
        assert isinstance(event, TurnEvent), f"Event {event} is not TurnEvent"
        assert isinstance(next_state, TurnState), f"Next state {next_state} is not TurnState"


def test_all_states_reachable():
    """Every state (except the entry point and terminal) must appear as a target."""
    all_states = set(TurnState)
    target_states = set(TURN_PIPELINE_TRANSITIONS.values())
    start_and_terminal = {TurnState.RESTORE, TurnState.DONE}
    unreachable = (all_states - start_and_terminal) - target_states
    assert not unreachable, f"States never reached: {unreachable}"


def test_no_dead_end_except_done():
    all_sources = {s for (s, _) in TURN_PIPELINE_TRANSITIONS.keys()}
    all_targets = set(TURN_PIPELINE_TRANSITIONS.values())
    terminal = {TurnState.DONE}
    dead_ends = (all_targets - all_sources) - terminal
    assert not dead_ends, f"States with no outgoing transitions: {dead_ends}"


# ── Task A2: turn 状态机死状态修复（HANDLE_ERROR / HANDLE_TIMEOUT 可达性）──


@pytest.fixture
def captured_log_records():
    """Capture loguru records emitted via the module-level ``logger``."""
    records: list = []

    def _sink(message) -> None:
        records.append(message.record)

    handler_id = loguru_logger.add(_sink, level="DEBUG")
    try:
        yield records
    finally:
        loguru_logger.remove(handler_id)


class _ErrorHandlingPipeline(AgentTurnPipeline):
    """Real error-handling states (state_run / state_handle_error / state_handle_timeout)
    inherited from ``AgentTurnPipeline``; the remaining happy-path states are stubbed
    so the orchestrator can drive a full turn into the error states in isolation.
    """

    async def state_restore(self, ctx: TurnContext) -> TurnEvent:
        return TurnEvent.OK

    async def state_compact(self, ctx: TurnContext) -> TurnEvent:
        return TurnEvent.OK

    async def state_command(self, ctx: TurnContext) -> TurnEvent:
        return TurnEvent.DISPATCH

    async def state_build(self, ctx: TurnContext) -> TurnEvent:
        return TurnEvent.OK

    async def state_save(self, ctx: TurnContext) -> TurnEvent:
        return TurnEvent.OK

    async def state_automation(self, ctx: TurnContext) -> TurnEvent:
        return TurnEvent.SKIP

    async def state_respond(self, ctx: TurnContext) -> TurnEvent:
        ctx.outbound = OutboundMessage(channel="cli", chat_id="chat", content="recovered")
        return TurnEvent.OK


def _build_error_orchestrator(run_agent_loop: AsyncMock) -> tuple[TurnOrchestrator, list[TurnContext]]:
    """Build a TurnOrchestrator wired to the real transitions and a pipeline whose
    ``run_agent_loop`` raises as configured. Captured contexts are collected for assertions.
    """
    deps = SimpleNamespace(
        bus=MagicMock(),
        resolve_runtime_context=MagicMock(
            return_value=SimpleNamespace(trigger="manual", actor_id="alice")
        ),
        run_agent_loop=run_agent_loop,
    )
    pipeline = _ErrorHandlingPipeline(deps=deps)
    captured: list[TurnContext] = []

    orchestrator = TurnOrchestrator(
        TurnOrchestratorDeps(
            turn_pipeline=pipeline,
            transitions=TURN_PIPELINE_TRANSITIONS,
            system_turn_handler=SimpleNamespace(process_message=AsyncMock()),
            scan_meta_triggers_for_turn=MagicMock(side_effect=captured.append),
            schedule_meta_cognition_reflection=MagicMock(),
        )
    )
    return orchestrator, captured


@pytest.mark.asyncio
async def test_state_run_timeout_enters_handle_timeout_state() -> None:
    """state_run raising TimeoutError routes the machine to HANDLE_TIMEOUT, not a crash."""
    orchestrator, captured = _build_error_orchestrator(
        AsyncMock(side_effect=TimeoutError("agent loop timed out"))
    )

    outbound = await orchestrator.process_message(
        InboundMessage(channel="cli", sender_id="alice", chat_id="chat", content="hello"),
        session_key="cli:chat",
        capability_snapshot=MagicMock(),
    )

    # Turn completed without crashing and still produced a response.
    assert outbound is not None
    assert outbound.content == "recovered"
    assert len(captured) == 1
    trace_states = [entry.state for entry in captured[0].trace]
    assert TurnState.HANDLE_TIMEOUT in trace_states
    # state_run captured the timeout into the state context (SubTask A2.1).
    assert captured[0].error is not None
    assert "TimeoutError" in captured[0].error


@pytest.mark.asyncio
async def test_state_run_generic_exception_enters_handle_error_state() -> None:
    """state_run raising a generic Exception routes the machine to HANDLE_ERROR, not a crash."""
    orchestrator, captured = _build_error_orchestrator(
        AsyncMock(side_effect=RuntimeError("agent loop exploded"))
    )

    outbound = await orchestrator.process_message(
        InboundMessage(channel="cli", sender_id="alice", chat_id="chat", content="hello"),
        session_key="cli:chat",
        capability_snapshot=MagicMock(),
    )

    assert outbound is not None
    assert outbound.content == "recovered"
    assert len(captured) == 1
    trace_states = [entry.state for entry in captured[0].trace]
    assert TurnState.HANDLE_ERROR in trace_states
    assert captured[0].error is not None
    assert "RuntimeError" in captured[0].error


@pytest.mark.asyncio
async def test_state_handle_error_records_audit_and_returns_ok(captured_log_records) -> None:
    """state_handle_error records an audit log then returns TurnEvent.OK (terminating recovery)."""
    pipeline = AgentTurnPipeline(deps=SimpleNamespace())
    ctx = TurnContext(
        msg=InboundMessage(channel="cli", sender_id="alice", chat_id="chat", content="hello"),
        session_key="cli:chat",
        state=TurnState.HANDLE_ERROR,
        turn_id="turn:handle-error",
    )
    ctx.error = "RuntimeError: boom"

    event = await pipeline.state_handle_error(ctx)

    assert event == TurnEvent.OK
    error_records = [r for r in captured_log_records if r["level"].name == "ERROR"]
    assert error_records, "expected an ERROR audit log entry from state_handle_error"
    assert any("HANDLE_ERROR" in r["message"] for r in error_records)
    assert any(ctx.turn_id in r["message"] for r in error_records)


@pytest.mark.asyncio
async def test_state_handle_timeout_records_audit_and_returns_ok(captured_log_records) -> None:
    """state_handle_timeout records an audit log then returns TurnEvent.OK (terminating recovery)."""
    pipeline = AgentTurnPipeline(deps=SimpleNamespace())
    ctx = TurnContext(
        msg=InboundMessage(channel="cli", sender_id="alice", chat_id="chat", content="hello"),
        session_key="cli:chat",
        state=TurnState.HANDLE_TIMEOUT,
        turn_id="turn:handle-timeout",
    )
    ctx.error = "TimeoutError: agent loop timed out"

    event = await pipeline.state_handle_timeout(ctx)

    assert event == TurnEvent.OK
    warning_records = [r for r in captured_log_records if r["level"].name == "WARNING"]
    assert warning_records, "expected a WARNING audit log entry from state_handle_timeout"
    assert any("HANDLE_TIMEOUT" in r["message"] for r in warning_records)
    assert any(ctx.turn_id in r["message"] for r in warning_records)
