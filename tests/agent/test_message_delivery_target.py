"""Tests for MessageTool delivery target mechanism (proactive cron notification).

In cron/scheduled sessions, the inbound channel is "cron" (inbound-only, so
OutboundMessages are discarded). The Agent cannot send to the user's real
channel (e.g. Telegram) because ``can_send_cross_target=False`` in the
scheduled capability snapshot.

The delivery target mechanism (``set_delivery_target``) solves this:
``on_cron_job`` sets the delivery target to ``job.payload.to`` before running
the Agent. When the Agent calls ``message(content="...")`` without explicit
channel/chat_id, it sends to the delivery target — no cross-target check
needed. This enables proactive mid-turn notifications ("阿珍，邮件收件箱有
新东西") that actually reach the user via Telegram.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.tools.message import MessageTool
from OriginAgent.bus.events import OutboundMessage
from OriginAgent.security.capabilities import CapabilitySnapshot


class _CapturingCallback:
    """Mock send_callback that captures the last OutboundMessage."""

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    async def __call__(self, msg: OutboundMessage) -> None:
        self.sent.append(msg)


def _make_message_tool(
    *,
    default_channel: str = "cron",
    default_chat_id: str = "job-123",
    snapshot: CapabilitySnapshot | None = None,
) -> tuple[MessageTool, _CapturingCallback]:
    """Create a MessageTool wired with a capturing send_callback."""
    callback = _CapturingCallback()
    tool = MessageTool(send_callback=callback)
    tool.set_context(default_channel, default_chat_id)
    if snapshot is not None:
        tool.set_capability_snapshot(snapshot)
    return tool, callback


# ─── set_delivery_target / reset_delivery_target ──────────────────────────


class TestSetDeliveryTarget:
    """Test the set_delivery_target / reset_delivery_target lifecycle."""

    def test_set_delivery_target_returns_tokens(self) -> None:
        tool, _ = _make_message_tool()
        tokens = tool.set_delivery_target("telegram", "user-456")
        assert tokens is not None
        assert isinstance(tokens, tuple)
        assert len(tokens) == 2

    def test_set_delivery_target_empty_returns_none(self) -> None:
        tool, _ = _make_message_tool()
        assert tool.set_delivery_target("", "") is None
        assert tool.set_delivery_target("telegram", "") is None
        assert tool.set_delivery_target("", "user-456") is None

    def test_reset_delivery_target_restores_previous(self) -> None:
        tool, _ = _make_message_tool()
        tokens = tool.set_delivery_target("telegram", "user-456")
        assert tool._delivery_channel_var.get() == "telegram"
        assert tool._delivery_chat_id_var.get() == "user-456"
        tool.reset_delivery_target(tokens)
        assert tool._delivery_channel_var.get() == ""
        assert tool._delivery_chat_id_var.get() == ""

    def test_reset_delivery_target_none_is_noop(self) -> None:
        tool, _ = _make_message_tool()
        tool.reset_delivery_target(None)  # should not raise


# ─── execute() uses delivery target ───────────────────────────────────────


class TestExecuteWithDeliveryTarget:
    """Test that execute() routes to the delivery target when set."""

    @pytest.mark.asyncio
    async def test_no_explicit_target_uses_delivery_target(self) -> None:
        """When no channel/chat_id specified, send to delivery target."""
        tool, cb = _make_message_tool(
            snapshot=CapabilitySnapshot.scheduled_default(),
        )
        tokens = tool.set_delivery_target("telegram", "user-456")
        try:
            result = await tool.execute(content="邮件收件箱有新东西")
            assert "sent" in result.lower()
            assert len(cb.sent) == 1
            assert cb.sent[0].channel == "telegram"
            assert cb.sent[0].chat_id == "user-456"
            assert "邮件收件箱有新东西" in cb.sent[0].content
        finally:
            tool.reset_delivery_target(tokens)

    @pytest.mark.asyncio
    async def test_explicit_delivery_target_no_cross_target_error(self) -> None:
        """Explicitly targeting the delivery target doesn't raise cross-target error."""
        tool, cb = _make_message_tool(
            snapshot=CapabilitySnapshot.scheduled_default(),
        )
        tokens = tool.set_delivery_target("telegram", "user-456")
        try:
            result = await tool.execute(
                content="hello",
                channel="telegram",
                chat_id="user-456",
            )
            assert "sent" in result.lower()
            assert len(cb.sent) == 1
        finally:
            tool.reset_delivery_target(tokens)

    @pytest.mark.asyncio
    async def test_delivery_target_sets_sent_in_turn(self) -> None:
        """Sending to delivery target sets _sent_in_turn=True (skips Path B)."""
        tool, cb = _make_message_tool(
            snapshot=CapabilitySnapshot.scheduled_default(),
        )
        tool.start_turn()
        tokens = tool.set_delivery_target("telegram", "user-456")
        try:
            await tool.execute(content="提醒")
            assert tool._sent_in_turn is True
        finally:
            tool.reset_delivery_target(tokens)

    @pytest.mark.asyncio
    async def test_cross_target_still_denied_without_delivery_target(self) -> None:
        """Without delivery target set, cross-target send is denied by policy."""
        from OriginAgent.security.policy import PolicyDeniedError

        tool, _ = _make_message_tool(
            snapshot=CapabilitySnapshot.scheduled_default(),
        )
        with pytest.raises(PolicyDeniedError):
            await tool.execute(
                content="hello",
                channel="telegram",
                chat_id="user-456",
            )

    @pytest.mark.asyncio
    async def test_cross_target_to_other_channels_still_denied(self) -> None:
        """Delivery target only allows the specific target, not all channels."""
        from OriginAgent.security.policy import PolicyDeniedError

        tool, _ = _make_message_tool(
            snapshot=CapabilitySnapshot.scheduled_default(),
        )
        tokens = tool.set_delivery_target("telegram", "user-456")
        try:
            # Sending to a DIFFERENT target should still be denied
            with pytest.raises(PolicyDeniedError):
                await tool.execute(
                    content="spam",
                    channel="whatsapp",
                    chat_id="other-user",
                )
        finally:
            tool.reset_delivery_target(tokens)

    @pytest.mark.asyncio
    async def test_no_delivery_target_falls_back_to_default(self) -> None:
        """Without delivery target, message goes to default (cron) channel."""
        tool, cb = _make_message_tool(
            default_channel="cron",
            default_chat_id="job-123",
            snapshot=CapabilitySnapshot.scheduled_default(),
        )
        # No delivery target set — should send to default "cron" channel
        result = await tool.execute(content="test")
        assert "sent" in result.lower()
        assert cb.sent[0].channel == "cron"
        assert cb.sent[0].chat_id == "job-123"
