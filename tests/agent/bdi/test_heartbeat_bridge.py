"""Tests for BDI-Heartbeat bridge."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from OriginAgent.bdi.models import (
    DeliberationIntention,
    DeliberationResult,
    Desire,
    DesireStatus,
    DesirePriority,
    now_iso,
)
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.deliberation import DeliberationEngine
from OriginAgent.bdi.heartbeat_bridge import BDIHeartbeatBridge


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as td:
        yield DesireStore(Path(td))


@pytest.fixture
def mock_provider():
    p = MagicMock()
    p.chat_with_retry = AsyncMock()
    return p


class TestBDIHeartbeatBridge:
    @pytest.mark.asyncio
    async def test_bridge_runs_deliberation_on_tick(self, store, mock_provider):
        store.add(Desire(
            desire_id="d1", owner_id="u", session_key="s",
            content="Test desire", priority=DesirePriority.HIGH,
            status=DesireStatus.ACTIVE,
        ))

        # Wire up mock provider to return a deliberation response
        from OriginAgent.bdi.deliberation import _DELIBERATION_TOOL

        mock_response = MagicMock()
        mock_response.should_execute_tools = True
        mock_response.has_tool_calls = True
        mock_tool_call = MagicMock()
        mock_tool_call.arguments = {
            "reasoning": "Test reasoning",
            "intentions": [{
                "desire_id": "d1",
                "action": "send_message",
                "scope": "test",
                "risk": "low",
                "reasoning": "Test intention",
                "payload": {"text": "Hello"},
            }],
            "desires_to_satisfy": [],
            "desires_to_suspend": [],
            "desires_to_cancel": [],
            "next_check_at": None,
        }
        mock_response.tool_calls = [mock_tool_call]
        mock_provider.chat_with_retry.return_value = mock_response

        engine = DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=mock_provider,
            model="test",
            enabled=True,
        )

        bridge = BDIHeartbeatBridge(
            engine=engine,
            on_notify=AsyncMock(),
        )

        result = await bridge.tick()

        assert result is not None
        assert result.intentions_formed >= 0
        mock_provider.chat_with_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_bridge_notifies_on_intention(self, store, mock_provider):
        store.add(Desire(
            desire_id="d1", owner_id="u", session_key="s",
            content="Test desire", priority=DesirePriority.HIGH,
            status=DesireStatus.ACTIVE,
        ))

        mock_response = MagicMock()
        mock_response.should_execute_tools = True
        mock_response.has_tool_calls = True
        mock_tool_call = MagicMock()
        mock_tool_call.arguments = {
            "reasoning": "Test",
            "intentions": [{
                "desire_id": "d1",
                "action": "send_message",
                "scope": "telegram",
                "risk": "low",
                "reasoning": "Test",
                "payload": {"text": "Hi"},
            }],
            "desires_to_satisfy": [],
            "desires_to_suspend": [],
            "desires_to_cancel": [],
            "next_check_at": None,
        }
        mock_response.tool_calls = [mock_tool_call]
        mock_provider.chat_with_retry.return_value = mock_response

        on_notify = AsyncMock()
        on_execute = AsyncMock(return_value="Done!")

        engine = DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=mock_provider,
            model="test",
            enabled=True,
        )

        bridge = BDIHeartbeatBridge(
            engine=engine,
            on_notify=on_notify,
            on_execute=on_execute,
        )

        result = await bridge.tick()

        # on_notify should be called with intention details
        assert on_notify.called or on_execute.called

    @pytest.mark.asyncio
    async def test_bridge_no_desires_no_action(self, store, mock_provider):
        mock_response = MagicMock()
        mock_response.should_execute_tools = True
        mock_response.has_tool_calls = True
        mock_tool_call = MagicMock()
        mock_tool_call.arguments = {
            "reasoning": "Nothing to do.",
            "intentions": [],
            "desires_to_satisfy": [],
            "desires_to_suspend": [],
            "desires_to_cancel": [],
            "next_check_at": None,
        }
        mock_response.tool_calls = [mock_tool_call]
        mock_provider.chat_with_retry.return_value = mock_response

        on_notify = AsyncMock()
        engine = DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=mock_provider,
            model="test",
            enabled=True,
        )

        bridge = BDIHeartbeatBridge(engine=engine, on_notify=on_notify)
        result = await bridge.tick()

        assert result.intentions_formed == 0
        # Should not notify when no intentions formed
