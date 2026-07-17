"""Tests for ChannelManager outbound dispatch to inbound-only channels.

Covers the cron-channel discard behavior: cron is an inbound-only channel
(no BaseChannel implementation), so outbound messages addressed to it
must be silently discarded at INFO level rather than logged as WARNING
"Unknown channel".
"""
import asyncio

import pytest
from loguru import logger

from OriginAgent.bus.events import OutboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.channels.manager import ChannelManager
from OriginAgent.config.schema import Config


@pytest.fixture
def config():
    """Create a minimal config for testing (no channels enabled by default)."""
    return Config()


@pytest.fixture
def bus():
    """Create a message bus for testing."""
    return MessageBus()


@pytest.fixture
def manager(config, bus):
    """Create a ChannelManager with no channels registered (cron is not a
    BaseChannel subclass and is not discovered by discover_all).

    The default Config() enables no channel sections, so ``self.channels``
    stays empty — exactly the scenario we want to exercise.
    """
    return ChannelManager(config, bus)


@pytest.fixture
def loguru_sink():
    """Capture loguru records for assertion, removed after test.

    Mirrors the pattern in tests/channels/test_allow_from_validation.py.
    """
    records = []

    def sink(message):
        records.append(message.record)

    handler_id = logger.add(sink, format="{message}", level="DEBUG")
    try:
        yield records
    finally:
        logger.remove(handler_id)


def _record_messages(records, level_name):
    """Return the formatted message strings for records at *level_name*."""
    return [str(r["message"]) for r in records if r["level"].name == level_name]


async def _run_dispatch_until(manager, bus, msg, sink, *, expected_level, expected_fragment, max_rounds=40):
    """Start ``_dispatch_outbound``, publish *msg*, and poll the captured
    loguru records until a record at *expected_level* containing
    *expected_fragment* appears (or *max_rounds* elapse).

    Always cancels the dispatch task before returning.
    """
    await bus.publish_outbound(msg)
    task = asyncio.create_task(manager._dispatch_outbound())
    try:
        for _ in range(max_rounds):
            messages = _record_messages(sink, expected_level)
            if any(expected_fragment in m for m in messages):
                break
            await asyncio.sleep(0.05)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestInboundOnlyChannelDispatch:
    """Outbound messages addressed to inbound-only channels (e.g. cron)
    must be silently discarded at INFO level, not warned about as unknown."""

    @pytest.mark.asyncio
    async def test_inbound_only_channel_discarded_silently(self, manager, bus, loguru_sink):
        """cron is an inbound-only channel: an outbound message addressed to
        it must produce an INFO 'inbound-only channel' log and must NOT
        produce a WARNING 'Unknown channel' log."""
        msg = OutboundMessage(
            channel="cron",
            chat_id="test",
            content="hi",
        )

        await _run_dispatch_until(
            manager, bus, msg, loguru_sink,
            expected_level="INFO",
            expected_fragment="inbound-only channel",
        )

        warnings = _record_messages(loguru_sink, "WARNING")
        infos = _record_messages(loguru_sink, "INFO")

        # cron must not be reported as an unknown channel
        assert not any("Unknown channel: cron" in w for w in warnings), (
            f"cron should be inbound-only, not WARNING-unknown; warnings={warnings!r}"
        )
        # an INFO discard record must have been emitted
        assert any("inbound-only channel" in i or "discarding" in i for i in infos), (
            f"expected INFO 'inbound-only channel' / 'discarding' record; infos={infos!r}"
        )

    @pytest.mark.asyncio
    async def test_truly_unknown_channel_still_warns(self, manager, bus, loguru_sink):
        """A genuinely unknown channel (e.g. 'nonexistent') must still emit
        the WARNING 'Unknown channel' log and must NOT be silently discarded
        as inbound-only."""
        msg = OutboundMessage(
            channel="nonexistent",
            chat_id="test",
            content="hi",
        )

        await _run_dispatch_until(
            manager, bus, msg, loguru_sink,
            expected_level="WARNING",
            expected_fragment="Unknown channel",
        )

        warnings = _record_messages(loguru_sink, "WARNING")
        infos = _record_messages(loguru_sink, "INFO")

        # nonexistent must be reported as an unknown channel
        assert any("Unknown channel: nonexistent" in w for w in warnings), (
            f"expected WARNING 'Unknown channel: nonexistent'; warnings={warnings!r}"
        )
        # no inbound-only discard record for nonexistent
        assert not any("inbound-only channel 'nonexistent'" in i for i in infos), (
            f"nonexistent must not be treated as inbound-only; infos={infos!r}"
        )
