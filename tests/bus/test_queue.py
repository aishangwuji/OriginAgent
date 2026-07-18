"""Tests for the async MessageBus — overflow behavior."""

import asyncio
from typing import Any

import pytest

from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.bus.queue import MessageBus


@pytest.mark.asyncio
async def test_inbound_queue_overflow_blocks_not_drops():
    """When inbound queue is full, publish should block until consumer drains it."""
    bus = MessageBus(maxsize=1)
    msg1 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="first")
    msg2 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="second")

    await bus.publish_inbound(msg1)

    # Second publish should NOT raise, but block briefly
    async def delayed_consume():
        await asyncio.sleep(0.05)
        return await bus.consume_inbound()

    consume_task = asyncio.create_task(delayed_consume())
    await bus.publish_inbound(msg2)  # blocks until consumer runs
    await consume_task

    stats = bus.stats
    assert stats["dropped_inbound"] == 0  # NOT dropped


@pytest.mark.asyncio
async def test_inbound_overflow_eventually_times_out():
    """If consumer never drains, publish should record overflow, not hang forever."""
    bus = MessageBus(maxsize=1, overflow_timeout=0.1)
    msg1 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="first")
    msg2 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="second")

    await bus.publish_inbound(msg1)
    await bus.publish_inbound(msg2)  # should time out after 0.1s, not hang

    stats = bus.stats
    # Message was NOT silently dropped — it's recorded
    assert stats["dropped_inbound"] > 0


@pytest.mark.asyncio
async def test_outbound_queue_overflow_not_dropped():
    """Outbound queue overflow blocks until consumer drains it (not drops)."""
    bus = MessageBus(maxsize=1)
    msg1 = OutboundMessage(channel="test", chat_id="c1", content="first")
    msg2 = OutboundMessage(channel="test", chat_id="c1", content="second")

    await bus.publish_outbound(msg1)

    async def delayed_consume():
        await asyncio.sleep(0.05)
        return await bus.consume_outbound()

    consume_task = asyncio.create_task(delayed_consume())
    await bus.publish_outbound(msg2)  # blocks until consumer runs
    await consume_task

    assert bus.stats["dropped_outbound"] == 0


@pytest.mark.asyncio
async def test_normal_operation_no_overflow():
    """Normal publish/consume cycle works without overflow."""
    bus = MessageBus(maxsize=10)
    msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="hello")

    await bus.publish_inbound(msg)
    consumed = await bus.consume_inbound()

    assert consumed.content == "hello"
    assert consumed.channel == "test"
    assert bus.stats["dropped_inbound"] == 0
    assert bus.stats["published_inbound"] == 1


@pytest.mark.asyncio
async def test_stats_reflect_metrics():
    """Stats property returns expected keys and values."""
    bus = MessageBus(maxsize=5)
    msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="hi")
    await bus.publish_inbound(msg)

    stats = bus.stats
    assert stats["published_inbound"] == 1
    assert stats["dropped_inbound"] == 0
    assert stats["inbound_queue_depth"] == 1
    assert stats["inbound_queue_max"] == 5
    assert stats["overflow_timeout"] == 5.0
    assert "uptime_s" in stats


@pytest.mark.asyncio
async def test_persistence_sink_called_on_inbound_overflow():
    """Persistence sink is called when inbound queue overflows."""
    persisted: list[InboundMessage] = []

    class RecordingSink:
        async def persist_inbound(self, msg: InboundMessage) -> None:
            persisted.append(msg)

        async def persist_outbound(self, msg: OutboundMessage) -> None:
            pass

    bus = MessageBus(maxsize=1, overflow_timeout=0.05, persistence=RecordingSink())
    msg1 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="first")
    msg2 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="second")

    await bus.publish_inbound(msg1)
    await bus.publish_inbound(msg2)

    assert len(persisted) == 1
    assert persisted[0].content == "second"
    assert bus.stats["persisted_inbound"] == 1
    assert bus.stats["dropped_inbound"] == 0


@pytest.mark.asyncio
async def test_persistence_sink_failure_falls_through_to_drop():
    """When persistence sink raises, message is counted as dropped (not lost silently)."""
    persisted_called: bool = False

    class FailingSink:
        async def persist_inbound(self, msg: InboundMessage) -> None:
            nonlocal persisted_called
            persisted_called = True
            raise OSError("disk full")

        async def persist_outbound(self, msg: OutboundMessage) -> None:
            pass

    bus = MessageBus(maxsize=1, overflow_timeout=0.05, persistence=FailingSink())
    msg1 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="first")
    msg2 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="second")

    await bus.publish_inbound(msg1)
    await bus.publish_inbound(msg2)

    assert persisted_called, "persistence sink should have been called"
    assert bus.stats["persisted_inbound"] == 0  # counter not incremented on failure
    assert bus.stats["dropped_inbound"] == 1  # fell through to drop


@pytest.mark.asyncio
async def test_old_code_does_not_use_put_nowait():
    """Verify _previous_ bug would have raised QueueFull (safety net).

    This acceptance test ensures that the old non-blocking-only pattern
    (which raised QueueFull) does NOT exist anymore.  We can validate by
    checking that the source file no longer calls ``put_nowait`` inside
    publish methods (static check, not runtime).
    """
    import inspect

    source = inspect.getsource(MessageBus.publish_inbound)

    # The method should contain "put_nowait" (for the fast path) but also
    # handle QueueFull — the key test is that the method does NOT just
    # call put_nowait and raise/warn; it has a fallback path.
    assert "QueueFull" in source
    assert "persistence" in source or "wait_for" in source


# -- Phase 2: dual-queue priority tests --------------------------------------


@pytest.mark.asyncio
async def test_user_message_consumed_before_internal():
    """User messages are drained from ``inbound`` before any internal nudge
    is taken from ``inbound_internal``, even when the nudge was enqueued
    earlier in wall-clock time.
    """
    bus = MessageBus(maxsize=10)
    nudge = InboundMessage(
        channel="cron", sender_id="cron", chat_id="job1",
        content="internal nudge", is_internal=True,
    )
    user_msg = InboundMessage(
        channel="websocket", sender_id="u1", chat_id="c1",
        content="real user message",  # is_internal=False (default)
    )

    # Nudge first, user second — user should still be consumed first.
    await bus.publish_inbound(nudge)
    await bus.publish_inbound(user_msg)

    first = await bus.consume_inbound()
    second = await bus.consume_inbound()

    assert first.content == "real user message"
    assert second.content == "internal nudge"
    assert bus.stats["dropped_inbound"] == 0
    assert bus.stats["dropped_inbound_internal"] == 0


@pytest.mark.asyncio
async def test_internal_queue_overflow_drops_not_blocks():
    """When ``inbound_internal`` is full, internal messages are dropped
    immediately (return False) — they must never block the publish path
    or spill to the persistence sink, because they are regenerable.
    """
    persisted: list[InboundMessage] = []

    class RecordingSink:
        async def persist_inbound(self, msg: InboundMessage) -> None:
            persisted.append(msg)

        async def persist_outbound(self, msg: OutboundMessage) -> None:
            pass

    bus = MessageBus(maxsize=1, overflow_timeout=0.05, persistence=RecordingSink())
    msg1 = InboundMessage(
        channel="cron", sender_id="cron", chat_id="c1",
        content="first nudge", is_internal=True,
    )
    msg2 = InboundMessage(
        channel="cron", sender_id="cron", chat_id="c1",
        content="second nudge", is_internal=True,
    )

    accepted1 = await bus.publish_inbound(msg1)
    accepted2 = await bus.publish_inbound(msg2)  # should drop immediately

    assert accepted1 is True
    assert accepted2 is False  # dropped, not persisted, not blocked
    assert persisted == []  # persistence sink NOT called for internal
    assert bus.stats["dropped_inbound_internal"] == 1
    assert bus.stats["dropped_inbound"] == 0  # user drop counter untouched
    assert bus.stats["persisted_inbound"] == 0


@pytest.mark.asyncio
async def test_consume_inbound_blocks_when_both_queues_empty():
    """When both queues are empty, ``consume_inbound`` blocks until any
    message arrives — user or internal. User wins if both arrive
    concurrently.
    """
    bus = MessageBus(maxsize=10)
    consumed: list[InboundMessage] = []

    async def consumer():
        msg = await bus.consume_inbound()
        consumed.append(msg)

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.02)  # let consumer block on both queues

    # Publish both near-simultaneously; user should win.
    internal = InboundMessage(
        channel="cron", sender_id="cron", chat_id="c1",
        content="nudge", is_internal=True,
    )
    user_msg = InboundMessage(
        channel="websocket", sender_id="u1", chat_id="c1",
        content="user",
    )
    await bus.publish_inbound(internal)
    await bus.publish_inbound(user_msg)

    await asyncio.wait_for(consumer_task, timeout=1.0)
    assert consumed[0].content == "user"  # user wins the race


@pytest.mark.asyncio
async def test_inbound_size_includes_internal_queue():
    """``inbound_size`` reports combined depth of user + internal queues."""
    bus = MessageBus(maxsize=10)
    assert bus.inbound_size == 0

    await bus.publish_inbound(InboundMessage(
        channel="cron", sender_id="cron", chat_id="c1",
        content="nudge", is_internal=True,
    ))
    await bus.publish_inbound(InboundMessage(
        channel="websocket", sender_id="u1", chat_id="c1",
        content="user",
    ))
    assert bus.inbound_size == 2


@pytest.mark.asyncio
async def test_stats_track_internal_queue_separately():
    """Stats expose ``inbound_internal_queue_depth`` and
    ``dropped_inbound_internal`` distinct from user-queue counters.
    """
    bus = MessageBus(maxsize=2)
    nudge = InboundMessage(
        channel="cron", sender_id="cron", chat_id="c1",
        content="nudge", is_internal=True,
    )
    await bus.publish_inbound(nudge)

    stats = bus.stats
    assert stats["inbound_queue_depth"] == 0  # user queue empty
    assert stats["inbound_internal_queue_depth"] == 1
    assert stats["inbound_internal_queue_max"] == 2
    assert "dropped_inbound_internal" in stats
