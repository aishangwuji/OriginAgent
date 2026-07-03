"""Minimum closed-loop integration test: single session, single turn.

This test is the ANCHOR.  If it fails, the core agent loop is broken.
It uses a mock LLM provider to avoid network calls, and disables all
optional subsystems (evolution, subagents, meta-cognition, cron).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.bus.queue import MessageBusSubscriber
from OriginAgent.providers.base import LLMResponse


class _OutboundCollector(MessageBusSubscriber):
    """Collects outbound messages published on the bus."""

    def __init__(self) -> None:
        self.messages: list[OutboundMessage] = []

    async def on_inbound(self, msg: InboundMessage) -> None:
        pass

    async def on_outbound(self, msg: OutboundMessage) -> None:
        self.messages.append(msg)


def _make_loop(tmp_path):
    """Create a minimal AgentLoop with mocked subsystems."""
    from OriginAgent.agent.identity import ActorResolver
    from OriginAgent.agent.loop import AgentLoop
    from OriginAgent.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    # Build a proper mock session that the turn pipeline can work with.
    mock_session = MagicMock()
    mock_session.messages = []
    mock_session.last_consolidated = 0
    mock_session.active_episode = None
    mock_session.key = "test:test-chat-1"
    mock_session.metadata = {}
    mock_session.updated_at = 0.0
    mock_session.get_history.return_value = []

    with (
        patch("OriginAgent.agent.loop.ContextBuilder"),
        patch("OriginAgent.agent.loop.SessionManager") as MockSessions,
        patch("OriginAgent.agent.loop.SubagentManager") as MockSubMgr,
    ):
        MockSessions.return_value.get_or_create.return_value = mock_session
        MockSubMgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=tmp_path,
            actor_resolver=ActorResolver(),
        )
    return loop


@pytest.mark.asyncio
async def test_single_session_single_turn_completes(tmp_path):
    """One session, one user message, one LLM response -- end to end.

    If this test fails, the core turn pipeline is broken.  No excuses.
    """
    loop = _make_loop(tmp_path)

    # Mock LLM: one call returns a text response, no tool calls
    async def chat_with_retry(*, messages, **kwargs):
        return LLMResponse(
            content="Hello! I received your message.",
            tool_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    loop.provider.chat_with_retry = chat_with_retry

    # Subscribe to outbound messages on the bus
    collector = _OutboundCollector()
    loop.bus.subscribe(collector)

    # Send a user message through dispatch
    msg = InboundMessage(
        channel="test",
        sender_id="test-user",
        chat_id="test-chat-1",
        content="Hello, agent!",
    )

    await loop._dispatch(msg)

    # Allow any async side-effects to settle
    await asyncio.sleep(0.1)

    # Assert: an outbound response was emitted
    assert len(collector.messages) >= 1, (
        f"Expected at least 1 outbound response, got {len(collector.messages)}. "
        f"Messages: {collector.messages}"
    )

    # Assert: the response contains the LLM's text
    outbound = collector.messages[0]
    assert outbound.channel == "test"
    assert outbound.chat_id == "test-chat-1"
    content = outbound.content
    if isinstance(content, list):
        content = " ".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    assert "Hello" in str(content), (
        f"Expected response to contain greeting, got: {content}"
    )
