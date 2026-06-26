"""Async message queue for decoupled channel-agent communication."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Protocol

from loguru import logger

from OriginAgent.bus.events import InboundMessage, OutboundMessage

# Default maximum queue depth before backpressure is applied.
_DEFAULT_MAX_QUEUE_SIZE = 500


class MessageBusSubscriber(Protocol):
    """Protocol for observability / middleware subscribers."""

    async def on_inbound(self, msg: InboundMessage) -> None: ...
    async def on_outbound(self, msg: OutboundMessage) -> None: ...


class PersistedMessageSink(Protocol):
    """Optional crash-recovery sink for messages."""

    async def persist_inbound(self, msg: InboundMessage) -> None: ...
    async def persist_outbound(self, msg: OutboundMessage) -> None: ...


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.

    The bus supports bounded queues (with backpressure), subscriber-based
    observability middleware, and an optional persistence sink for crash
    recovery.
    """

    def __init__(
        self,
        maxsize: int = _DEFAULT_MAX_QUEUE_SIZE,
        *,
        persistence: PersistedMessageSink | None = None,
    ):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=maxsize)
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue(maxsize=maxsize)
        self._subscribers: list[MessageBusSubscriber] = []
        self._persistence = persistence
        self._published_inbound: int = 0
        self._published_outbound: int = 0
        self._dropped_inbound: int = 0
        self._started_at: float = time.monotonic()

    # -- subscriber management -------------------------------------------------

    def subscribe(self, subscriber: MessageBusSubscriber) -> None:
        """Register a middleware subscriber that observes every published message."""
        self._subscribers.append(subscriber)

    # -- inbound ---------------------------------------------------------------

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent.

        If the queue is full, the message is dropped and logged as a warning.
        """
        self._published_inbound += 1
        for sub in self._subscribers:
            with _suppress_log("subscriber on_inbound failed"):
                await sub.on_inbound(msg)
        if self._persistence is not None:
            with _suppress_log("persist_inbound failed"):
                await self._persistence.persist_inbound(msg)
        try:
            self.inbound.put_nowait(msg)
        except asyncio.QueueFull:
            self._dropped_inbound += 1
            logger.warning(
                "MessageBus inbound queue full ({}/{}); dropping message",
                self.inbound.qsize(),
                self.inbound.maxsize,
            )

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    # -- outbound --------------------------------------------------------------

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        self._published_outbound += 1
        for sub in self._subscribers:
            with _suppress_log("subscriber on_outbound failed"):
                await sub.on_outbound(msg)
        if self._persistence is not None:
            with _suppress_log("persist_outbound failed"):
                await self._persistence.persist_outbound(msg)
        try:
            self.outbound.put_nowait(msg)
        except asyncio.QueueFull:
            logger.warning(
                "MessageBus outbound queue full ({}/{}); dropping message",
                self.outbound.qsize(),
                self.outbound.maxsize,
            )

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    # -- observability ---------------------------------------------------------

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()

    @property
    def stats(self) -> dict[str, Any]:
        """Return a snapshot of bus metrics for introspection."""
        return {
            "published_inbound": self._published_inbound,
            "published_outbound": self._published_outbound,
            "dropped_inbound": self._dropped_inbound,
            "inbound_queue_depth": self.inbound.qsize(),
            "inbound_queue_max": self.inbound.maxsize,
            "outbound_queue_depth": self.outbound.qsize(),
            "outbound_queue_max": self.outbound.maxsize,
            "uptime_s": round(time.monotonic() - self._started_at, 1),
        }


# -- helpers -------------------------------------------------------------------


from contextlib import suppress as _suppress_ctx


def _suppress_log(context: str):
    """Suppress exceptions in subscriber / persistence callbacks and log them."""
    return _suppress_log_wrapper(context)


class _suppress_log_wrapper:
    def __init__(self, context: str):
        self._context = context

    def __enter__(self):
        return _suppress_ctx()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None and exc_val is not None:
            logger.warning("MessageBus: {}: {}", self._context, exc_val)
        return True
