"""Async message queue for decoupled channel-agent communication."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Protocol

from loguru import logger

from OriginAgent.bus.events import InboundMessage, OutboundMessage

# Default maximum queue depth before backpressure is applied.
_DEFAULT_MAX_QUEUE_SIZE = 500
_DEFAULT_OVERFLOW_TIMEOUT = 5.0


class MessageBusSubscriber(Protocol):
    """Protocol for observability / middleware subscribers."""

    async def on_inbound(self, msg: InboundMessage) -> None: ...
    async def on_outbound(self, msg: OutboundMessage) -> None: ...


class PersistedMessageSink(Protocol):
    """Optional crash-recovery sink for messages."""

    async def persist_inbound(self, msg: InboundMessage) -> None: ...
    async def persist_outbound(self, msg: OutboundMessage) -> None: ...


class MessageBus:
    """Async message bus that decouples chat channels from the agent core.

    Overflow behavior (when queues are full):

    1. Tries ``put_nowait`` (non-blocking fast path).
    2. On ``QueueFull``, blocks up to ``overflow_timeout`` seconds with
       ``put()``, so a slow consumer can catch up.
    3. If the timeout expires, spills to the *persistence sink* if one is
       configured, so the message is not lost.
    4. Only as a last resort increments the drop counter and logs a warning.
    """

    def __init__(
        self,
        maxsize: int = _DEFAULT_MAX_QUEUE_SIZE,
        *,
        persistence: PersistedMessageSink | None = None,
        overflow_timeout: float = _DEFAULT_OVERFLOW_TIMEOUT,
    ):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=maxsize)
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue(maxsize=maxsize)
        self._subscribers: list[MessageBusSubscriber] = []
        self._persistence = persistence
        self._overflow_timeout = overflow_timeout
        self._published_inbound: int = 0
        self._published_outbound: int = 0
        self._dropped_inbound: int = 0
        self._dropped_outbound: int = 0
        self._persisted_inbound: int = 0
        self._persisted_outbound: int = 0
        self._started_at: float = time.monotonic()

    # -- subscriber management -------------------------------------------------

    def subscribe(self, subscriber: MessageBusSubscriber) -> None:
        """Register a middleware subscriber that observes every published message."""
        self._subscribers.append(subscriber)

    # -- inbound ---------------------------------------------------------------

    async def publish_inbound(self, msg: InboundMessage) -> bool:
        """Publish a message from a channel to the agent.

        Returns True if the message was accepted (enqueued or persisted).
        Returns False only when the message was definitively dropped.
        """
        self._published_inbound += 1
        for sub in self._subscribers:
            with _suppress_log("subscriber on_inbound failed"):
                await sub.on_inbound(msg)

        # Phase 1: non-blocking fast path
        try:
            self.inbound.put_nowait(msg)
            return True
        except asyncio.QueueFull:
            pass

        # Phase 2: block with timeout
        try:
            await asyncio.wait_for(
                self.inbound.put(msg),
                timeout=self._overflow_timeout,
            )
            return True
        except asyncio.TimeoutError:
            pass

        # Phase 3: spill to persistence sink
        if self._persistence is not None:
            with _suppress_log("persist_inbound failed"):
                await self._persistence.persist_inbound(msg)
                self._persisted_inbound += 1
                return True

        # Last resort: count the drop
        self._dropped_inbound += 1
        logger.warning(
            "MessageBus inbound queue full ({} items, max {}); "
            "message dropped after {}s timeout. "
            "In total {} message(s) dropped this session.",
            self.inbound.qsize(),
            self.inbound.maxsize,
            self._overflow_timeout,
            self._dropped_inbound,
        )
        return False

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    # -- outbound --------------------------------------------------------------

    async def publish_outbound(self, msg: OutboundMessage) -> bool:
        """Publish a response from the agent to channels.

        Returns True if the message was accepted (enqueued or persisted).
        Returns False only when the message was definitively dropped.
        """
        self._published_outbound += 1
        for sub in self._subscribers:
            with _suppress_log("subscriber on_outbound failed"):
                await sub.on_outbound(msg)

        # Phase 1: non-blocking fast path
        try:
            self.outbound.put_nowait(msg)
            return True
        except asyncio.QueueFull:
            pass

        # Phase 2: block with timeout
        try:
            await asyncio.wait_for(
                self.outbound.put(msg),
                timeout=self._overflow_timeout,
            )
            return True
        except asyncio.TimeoutError:
            pass

        # Phase 3: spill to persistence sink
        if self._persistence is not None:
            with _suppress_log("persist_outbound failed"):
                await self._persistence.persist_outbound(msg)
                self._persisted_outbound += 1
                return True

        # Last resort: count the drop
        self._dropped_outbound += 1
        logger.warning(
            "MessageBus outbound queue full ({} items, max {}); "
            "message dropped after {}s timeout. "
            "In total {} message(s) dropped this session.",
            self.outbound.qsize(),
            self.outbound.maxsize,
            self._overflow_timeout,
            self._dropped_outbound,
        )
        return False

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
            "dropped_outbound": self._dropped_outbound,
            "persisted_inbound": self._persisted_inbound,
            "persisted_outbound": self._persisted_outbound,
            "inbound_queue_depth": self.inbound.qsize(),
            "inbound_queue_max": self.inbound.maxsize,
            "outbound_queue_depth": self.outbound.qsize(),
            "outbound_queue_max": self.outbound.maxsize,
            "overflow_timeout": self._overflow_timeout,
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
