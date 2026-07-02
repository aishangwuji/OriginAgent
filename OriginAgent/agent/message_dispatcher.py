from __future__ import annotations

import asyncio
import dataclasses
import sys
import time
from contextlib import nullcontext, AbstractContextManager
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from loguru import logger

from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.agent.error_classifier import classify_exception, user_facing_message
from OriginAgent.session.goal_state import goal_state_ws_blob
from OriginAgent.utils.webui_titles import maybe_generate_webui_title_after_turn


@runtime_checkable
class DispatcherAwareLoop(Protocol):
    """The subset of AgentLoop that MessageDispatcher depends on (D6).

    Turns implicit friend-class coupling into an explicit, type-checked contract.
    """

    _running: bool
    bus: Any
    auto_compact: Any
    commands: Any
    _pending_queues: dict[str, asyncio.Queue[InboundMessage]]
    _active_tasks: dict[str, list[asyncio.Task[Any]]]
    _session_locks: dict[str, asyncio.Lock]
    _concurrency_gate: AbstractContextManager[Any] | None
    sessions: Any
    provider: Any
    model: str

    async def _process_message(self, msg: InboundMessage) -> Any: ...
    async def _dispatch_command_inline(self, msg: InboundMessage, key: str, raw: str, handler: Callable[..., Any]) -> None: ...
    async def _dispatch(self, msg: InboundMessage) -> None: ...
    async def _schedule_background(self, coro: Awaitable[Any]) -> None: ...
    def _effective_session_key(self, msg: InboundMessage) -> str: ...
    def _restore_runtime_checkpoint(self, session: Any) -> bool: ...
    def _clear_pending_user_turn(self, session: Any) -> None: ...
    def expire_stale_sessions(self) -> int: ...


@dataclass(frozen=True)
class MessageDispatcherDeps:
    loop: DispatcherAwareLoop


class MessageDispatcher:
    """Front-end inbound dispatcher for AgentLoop.

    `run_forever()` is a long-lived consume loop and tolerates transient
    cancellation-like bus errors unless the loop is actually stopping.
    `dispatch_message()` handles a single turn task and must re-raise
    `CancelledError` after checkpoint restoration.
    """

    PENDING_QUEUE_MAXSIZE = 20
    _MAX_TRANSIENT_CANCELS = 5
    _STALE_EXPIRY_INTERVAL = 300  # seconds between expire_stale() checks

    def __init__(self, deps: MessageDispatcherDeps) -> None:
        self._deps = deps
        self._last_expiry_check: float = 0.0

    @property
    def loop(self) -> Any:
        return self._deps.loop

    async def run_forever(self) -> None:
        transient_cancel_count = 0
        while self.loop._running:
            try:
                msg = await asyncio.wait_for(self.loop.bus.consume_inbound(), timeout=1.0)
                transient_cancel_count = 0
            except asyncio.TimeoutError:
                self.loop.auto_compact.check_expired(
                    self.loop._schedule_background,
                    active_session_keys=self.loop._pending_queues.keys(),
                )
                now = time.monotonic()
                if now - self._last_expiry_check >= self._STALE_EXPIRY_INTERVAL:
                    self.loop.expire_stale_sessions()
                    self._last_expiry_check = now
                continue
            except asyncio.CancelledError:
                if not self.loop._running or asyncio.current_task().cancelling():
                    raise
                transient_cancel_count += 1
                if transient_cancel_count >= self._MAX_TRANSIENT_CANCELS:
                    logger.error(
                        "Inbound consume cancelled {} consecutive time(s); aborting dispatcher loop",
                        transient_cancel_count,
                    )
                    raise
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            raw = msg.content.strip()
            effective_key = self.loop._effective_session_key(msg)
            if self.loop.commands.is_priority(raw):
                await self.loop._dispatch_command_inline(
                    msg, effective_key, raw, self.loop.commands.dispatch_priority
                )
                continue
            if effective_key in self.loop._pending_queues:
                if self.loop.commands.is_dispatchable_command(raw):
                    await self.loop._dispatch_command_inline(
                        msg, effective_key, raw, self.loop.commands.dispatch
                    )
                    continue
                pending_msg = msg
                if effective_key != msg.session_key:
                    pending_msg = dataclasses.replace(
                        msg,
                        session_key_override=effective_key,
                    )
                try:
                    self.loop._pending_queues[effective_key].put_nowait(pending_msg)
                except asyncio.QueueFull:
                    logger.warning(
                        "Pending queue full for session {}, falling back to a new dispatch task",
                        effective_key,
                    )
                else:
                    logger.info(
                        "Routed follow-up message to pending queue for session {}",
                        effective_key,
                    )
                    continue

            task = asyncio.create_task(self.loop._dispatch(msg))
            self.loop._active_tasks.setdefault(effective_key, []).append(task)
            task.add_done_callback(
                lambda t, k=effective_key: self.loop._active_tasks.get(k, [])
                and self.loop._active_tasks[k].remove(t)
                if t in self.loop._active_tasks.get(k, [])
                else None
            )

    async def dispatch_message(self, msg: InboundMessage) -> None:
        session_key = self.loop._effective_session_key(msg)
        if session_key != msg.session_key:
            msg = dataclasses.replace(msg, session_key_override=session_key)
        lock = self.loop._session_locks.setdefault(session_key, asyncio.Lock())
        gate = self.loop._concurrency_gate or nullcontext()
        pending = asyncio.Queue(maxsize=self.PENDING_QUEUE_MAXSIZE)
        self.loop._pending_queues[session_key] = pending

        try:
            async with lock, gate:
                try:
                    on_stream = on_stream_end = None
                    if msg.metadata.get("_wants_stream"):
                        stream_base_id = f"{msg.session_key}:{time.time_ns()}"
                        stream_segment = 0

                        def _current_stream_id() -> str:
                            return f"{stream_base_id}:{stream_segment}"

                        async def on_stream(delta: str) -> None:
                            meta = dict(msg.metadata or {})
                            meta["_stream_delta"] = True
                            meta["_stream_id"] = _current_stream_id()
                            await self.loop.bus.publish_outbound(
                                OutboundMessage(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    content=delta,
                                    metadata=meta,
                                )
                            )

                        async def on_stream_end(*, resuming: bool = False) -> None:
                            nonlocal stream_segment
                            meta = dict(msg.metadata or {})
                            meta["_stream_end"] = True
                            meta["_resuming"] = resuming
                            meta["_stream_id"] = _current_stream_id()
                            await self.loop.bus.publish_outbound(
                                OutboundMessage(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    content="",
                                    metadata=meta,
                                )
                            )
                            stream_segment += 1

                    response = await self.loop._process_message(
                        msg,
                        on_stream=on_stream,
                        on_stream_end=on_stream_end,
                        pending_queue=pending,
                    )
                    if response is not None:
                        await self.loop.bus.publish_outbound(response)
                    elif msg.channel == "cli":
                        await self.loop.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="",
                                metadata=msg.metadata or {},
                            )
                        )
                    if msg.channel == "websocket":
                        await self.loop.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="",
                                metadata={
                                    **msg.metadata,
                                    "_turn_end": True,
                                    "latency_ms": msg.metadata.get("webui_turn_latency_ms"),
                                    "goal_state": goal_state_ws_blob(
                                        self.loop.sessions.get_or_create(session_key).metadata
                                    ),
                                },
                            )
                        )
                        if msg.metadata.get("webui") is True:
                            async def _generate_title_and_notify() -> None:
                                generated = await maybe_generate_webui_title_after_turn(
                                    channel=msg.channel,
                                    metadata=msg.metadata,
                                    sessions=self.loop.sessions,
                                    session_key=session_key,
                                    provider=self.loop.provider,
                                    model=self.loop.model,
                                )
                                if generated:
                                    await self.loop.bus.publish_outbound(
                                        OutboundMessage(
                                            channel=msg.channel,
                                            chat_id=msg.chat_id,
                                            content="",
                                            metadata={**msg.metadata, "_session_updated": True},
                                        )
                                    )

                            self.loop._schedule_background(_generate_title_and_notify())
                except asyncio.CancelledError:
                    logger.info("Task cancelled for session {}", session_key)
                    try:
                        key = self.loop._effective_session_key(msg)
                        session = self.loop.sessions.get_or_create(key)
                        if self.loop._restore_runtime_checkpoint(session):
                            self.loop._clear_pending_user_turn(session)
                            self.loop.sessions.save(session)
                            logger.info(
                                "Restored partial context for cancelled session {}",
                                key,
                            )
                    except Exception:
                        logger.debug(
                            "Could not restore checkpoint for cancelled session {}",
                            session_key,
                            exc_info=True,
                        )
                    raise
                except Exception:
                    logger.exception("Error processing message for session {}", session_key)
                    classified = classify_exception(sys.exc_info()[1] if sys.exc_info()[1] else Exception("unknown"))
                    error_content = user_facing_message(classified)
                    ok = await self.loop.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=error_content,
                        )
                    )
                    if not ok:
                        logger.error(
                            "CRITICAL: Failed to deliver error response to session {} — "
                            "outbound queue full and no persistence",
                            session_key,
                        )
        finally:
            queue = self.loop._pending_queues.pop(session_key, None)
            if queue is not None:
                leftover = 0
                while True:
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    await self.loop.bus.publish_inbound(item)
                    leftover += 1
                if leftover:
                    logger.info(
                        "Re-published {} leftover message(s) to bus for session {}",
                        leftover,
                        session_key,
                    )
