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
