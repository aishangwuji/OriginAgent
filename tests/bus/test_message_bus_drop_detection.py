"""Tests for MessageBus drop detection."""
import asyncio
import pytest
from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.bus.queue import MessageBus


def make_inbound() -> InboundMessage:
    return InboundMessage(
        channel="test",
        sender_id="u1",
        content="hello",
        chat_id="c1",
    )


def make_outbound() -> OutboundMessage:
    return OutboundMessage(
        channel="test",
        content="response",
        chat_id="c1",
    )


@pytest.mark.asyncio
async def test_publish_inbound_returns_false_when_dropped():
    """When queue is full with no persistence, publish_inbound returns False."""
    bus = MessageBus(maxsize=1, overflow_timeout=0.01)

    # Fill the queue
    assert await bus.publish_inbound(make_inbound()) is True
    # Consumer does not drain — queue stays full

    # Next publish should fail (no persistence sink)
    result = await bus.publish_inbound(make_inbound())
    assert result is False


@pytest.mark.asyncio
async def test_publish_outbound_returns_false_when_dropped():
    """When outbound queue is full with no persistence, publish_outbound returns False."""
    bus = MessageBus(maxsize=1, overflow_timeout=0.01)

    assert await bus.publish_outbound(make_outbound()) is True
    result = await bus.publish_outbound(make_outbound())
    assert result is False


@pytest.mark.asyncio
async def test_publish_succeeds_when_queue_has_room():
    """Normal publish returns True."""
    bus = MessageBus(maxsize=10)
    result = await bus.publish_inbound(make_inbound())
    assert result is True


@pytest.mark.asyncio
async def test_persisted_message_counts_as_success():
    """When persistence sink is configured, spill to disk counts as success."""
    persisted: list[InboundMessage] = []

    class FakeSink:
        async def persist_inbound(self, msg: InboundMessage) -> None:
            persisted.append(msg)

        async def persist_outbound(self, msg: OutboundMessage) -> None:
            persisted.append(msg)

    bus = MessageBus(maxsize=1, overflow_timeout=0.01, persistence=FakeSink())
    assert await bus.publish_inbound(make_inbound()) is True
    # Queue is full, should spill to persistence
    result = await bus.publish_inbound(make_inbound())
    assert result is True
    assert len(persisted) == 1
