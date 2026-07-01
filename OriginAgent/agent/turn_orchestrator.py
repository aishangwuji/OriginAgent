from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from loguru import logger

from OriginAgent.agent.agent_turn_pipeline import StateTraceEntry, TurnContext, TurnState
from OriginAgent.agent.system_turn_handler import SystemTurnHandler
from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.security.capabilities import CapabilitySnapshot


@dataclass(frozen=True)
class TurnOrchestratorDeps:
    turn_pipeline: Any
    transitions: dict[tuple[TurnState, str], TurnState]
    system_turn_handler: SystemTurnHandler
    scan_meta_triggers_for_turn: Callable[[TurnContext], None]
    schedule_meta_cognition_reflection: Callable[[TurnContext], None]
    set_current_meta_turn_id: Callable[[str], None]
    clear_current_meta_turn_id: Callable[[], None]


class TurnOrchestrator:
    """Drive turn orchestration using explicit pipeline and system-turn deps."""

    def __init__(self, deps: TurnOrchestratorDeps) -> None:
        self._deps = deps

    async def process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        pending_queue: Any | None = None,
        capability_snapshot: CapabilitySnapshot | None = None,
    ) -> OutboundMessage | None:
        if msg.channel == "system":
            return await self._deps.system_turn_handler.process_message(
                msg,
                session_key=session_key,
                on_progress=on_progress,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
                pending_queue=pending_queue,
                capability_snapshot=capability_snapshot,
            )

        key = session_key or msg.session_key
        ctx = TurnContext(
            msg=msg,
            session=None,
            session_key=key,
            state=TurnState.RESTORE,
            turn_id=f"{key}:{time.time_ns()}",
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            pending_queue=pending_queue,
            capability_snapshot=capability_snapshot,
        )
        self._deps.set_current_meta_turn_id(ctx.turn_id)
        # Start turn-scoped dedup isolation on the meta runtime
        meta_runtime = getattr(self._deps, "meta_cognition_runtime", None)
        if meta_runtime and hasattr(meta_runtime, "start_turn"):
            meta_runtime.start_turn(ctx.turn_id)
        try:
            while ctx.state is not TurnState.DONE:
                handler_name = f"state_{ctx.state.name.lower()}"
                handler = getattr(self._deps.turn_pipeline, handler_name, None)
                if handler is None:
                    raise RuntimeError(f"Missing state handler for {ctx.state}")

                t0 = time.perf_counter()
                try:
                    event = await handler(ctx)
                except Exception:
                    duration = (time.perf_counter() - t0) * 1000
                    ctx.trace.append(
                        StateTraceEntry(
                            state=ctx.state,
                            started_at=t0,
                            duration_ms=duration,
                            event="",
                            error="exception",
                        )
                    )
                    raise

                duration = (time.perf_counter() - t0) * 1000
                ctx.trace.append(
                    StateTraceEntry(
                        state=ctx.state,
                        started_at=t0,
                        duration_ms=duration,
                        event=event,
                    )
                )
                logger.debug(
                    "[turn {}] State {} took {:.1f}ms -> event {}",
                    ctx.turn_id,
                    ctx.state.name,
                    duration,
                    event,
                )

                next_state = self._deps.transitions.get((ctx.state, event))
                if next_state is None:
                    raise RuntimeError(
                        f"[turn {ctx.turn_id}] No transition from {ctx.state} on event {event!r}"
                    )
                ctx.state = next_state

            logger.debug(
                "[turn {}] Turn completed after {} states",
                ctx.turn_id,
                len(ctx.trace),
            )
            self._deps.scan_meta_triggers_for_turn(ctx)
            self._deps.schedule_meta_cognition_reflection(ctx)
            return ctx.outbound
        finally:
            # End turn-scoped dedup isolation
            if meta_runtime and hasattr(meta_runtime, "end_turn"):
                meta_runtime.end_turn(ctx.turn_id)
            self._deps.clear_current_meta_turn_id()
