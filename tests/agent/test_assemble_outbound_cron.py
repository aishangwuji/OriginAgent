"""Tests for _assemble_outbound cron-channel suppression (Plan A).

Cron-triggered turns must NOT produce an OutboundMessage to the "cron"
channel — that channel is inbound-only (no implementation class), so any
OutboundMessage sent there is silently discarded. The response content is
instead recovered from the session by on_cron_job (cli/commands.py) and
delivered via job.payload.deliver/to to a real channel (Path B).

These tests lock the suppression behavior:
  - cron channel → returns None (no "Response to cron:cron" log, no waste)
  - non-cron channels → still construct and return OutboundMessage
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.agent_runtime import AgentRuntime, RuntimeDependencies


def _build_runtime() -> AgentRuntime:
    """Build a minimal AgentRuntime for _assemble_outbound tests."""
    tools = MagicMock()
    # tools.get("message") returns None → skip MessageTool short-circuit
    tools.get.return_value = None
    deps = RuntimeDependencies(tools=tools)
    return AgentRuntime(deps)


def _make_msg(channel: str = "cron", chat_id: str = "job-123") -> SimpleNamespace:
    return SimpleNamespace(
        channel=channel,
        sender_id="cron",
        chat_id=chat_id,
        metadata={},
    )


class TestAssembleOutboundCronSuppression:
    """Plan A: cron-channel turns suppress outbound assembly."""

    def test_cron_channel_returns_none(self) -> None:
        """_assemble_outbound must return None when msg.channel == 'cron'.

        This is the core of Plan A: no OutboundMessage is constructed for
        the inbound-only cron channel, eliminating the 'Response to cron:cron'
        log and the wasted OutboundMessage that would be silently discarded.
        """
        runtime = _build_runtime()
        msg = _make_msg(channel="cron")

        result = runtime._assemble_outbound(
            msg=msg,
            final_content="提醒：该喝水了",
            all_msgs=[],
            stop_reason="ok",
            had_injections=True,
            generated_media=[],
        )

        assert result is None

    def test_cron_channel_returns_none_even_without_injections(self) -> None:
        """Suppression must fire regardless of had_injections / stop_reason."""
        runtime = _build_runtime()
        msg = _make_msg(channel="cron")

        result = runtime._assemble_outbound(
            msg=msg,
            final_content="Done",
            all_msgs=[],
            stop_reason="ok",
            had_injections=False,
            generated_media=[],
        )

        assert result is None

    def test_cli_channel_still_returns_outbound(self) -> None:
        """Regression: non-cron channels must still construct OutboundMessage."""
        runtime = _build_runtime()
        msg = _make_msg(channel="cli", chat_id="user-1")

        result = runtime._assemble_outbound(
            msg=msg,
            final_content="Hello!",
            all_msgs=[],
            stop_reason="ok",
            had_injections=False,
            generated_media=[],
        )

        assert result is not None
        assert result.channel == "cli"
        assert result.content == "Hello!"

    def test_telegram_channel_still_returns_outbound(self) -> None:
        """Regression: telegram channel must still construct OutboundMessage."""
        runtime = _build_runtime()
        msg = _make_msg(channel="telegram", chat_id="7715515124")

        result = runtime._assemble_outbound(
            msg=msg,
            final_content="提醒：该喝水了",
            all_msgs=[],
            stop_reason="ok",
            had_injections=False,
            generated_media=[],
        )

        assert result is not None
        assert result.channel == "telegram"
        assert "提醒" in result.content
