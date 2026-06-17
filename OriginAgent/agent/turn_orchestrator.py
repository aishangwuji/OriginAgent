from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from loguru import logger

from OriginAgent.agent.agent_turn_pipeline import StateTraceEntry, TurnContext, TurnState
from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.security.capabilities import CapabilitySnapshot


@dataclass(frozen=True)
class TurnOrchestratorDeps:
    loop: Any


class TurnOrchestrator:
    """Drive non-system turn orchestration via AgentLoop compatibility hooks.

    Required loop attributes:
    - _process_system_message
    - _turn_pipeline
    - _TRANSITIONS
    - _scan_meta_triggers_for_turn
    - _schedule_meta_cognition_reflection
    - _current_meta_turn_id
    """

    def __init__(self, deps: TurnOrchestratorDeps) -> None:
        self._deps = deps

    @property
    def loop(self) -> Any:
        return self._deps.loop

    def _turn_pipeline(self) -> Any:
        pipeline = getattr(self.loop, "_turn_pipeline", None)
        if pipeline is not None:
            return pipeline
        raise AttributeError("turn pipeline is not available")

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
            return await self.loop._process_system_message(
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
        self.loop._current_meta_turn_id = ctx.turn_id
        try:
            pipeline = self._turn_pipeline()
            while ctx.state is not TurnState.DONE:
                handler_name = f"state_{ctx.state.name.lower()}"
                handler = getattr(pipeline, handler_name, None)
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

                next_state = self.loop._TRANSITIONS.get((ctx.state, event))
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
            self.loop._scan_meta_triggers_for_turn(ctx)
            self.loop._schedule_meta_cognition_reflection(ctx)
            return ctx.outbound
        finally:
            self.loop._current_meta_turn_id = None
