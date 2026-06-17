from __future__ import annotations

import asyncio
import dataclasses
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from loguru import logger

from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.session.goal_state import goal_state_ws_blob
from OriginAgent.utils.webui_titles import maybe_generate_webui_title_after_turn


@dataclass(frozen=True)
class MessageDispatcherDeps:
    loop: Any


class MessageDispatcher:
    def __init__(self, deps: MessageDispatcherDeps) -> None:
        self._deps = deps

    @property
    def loop(self) -> Any:
        return self._deps.loop

    async def run_forever(self) -> None:
        while self.loop._running:
            try:
                msg = await asyncio.wait_for(self.loop.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                self.loop.auto_compact.check_expired(
                    self.loop._schedule_background,
                    active_session_keys=self.loop._pending_queues.keys(),
                )
                continue
            except asyncio.CancelledError:
                if not self.loop._running or asyncio.current_task().cancelling():
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
                        "Pending queue full for session {}, falling back to queued task",
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
        pending = asyncio.Queue(maxsize=20)
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
                    await self.loop.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="Sorry, I encountered an error.",
                        )
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
