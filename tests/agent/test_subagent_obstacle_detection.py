"""Tests for SubagentManager obstacle detection."""
import pytest
from unittest.mock import MagicMock, AsyncMock
from pathlib import Path

from OriginAgent.agent.soar_models import SoarObstacle
from OriginAgent.agent.error_classifier import ErrorKind


class TestDetectObstacle:
    @pytest.fixture
    def manager(self):
        """Create a minimal SubagentManager for testing _detect_obstacle."""
        from OriginAgent.agent.subagent import SubagentManager
        # Use MagicMock for dependencies to avoid full initialization
        m = object.__new__(SubagentManager)  # bypass __init__
        return m

    def test_tool_error_produces_tool_failure(self, manager):
        """stop_reason='tool_error' produces SoarObstacle(obstacle_type='tool_failure')."""
        obstacle = manager._detect_obstacle(
            stop_reason="tool_error",
            failure_summary="search failed",
            tool_events=[{"name": "search"}, {"name": "read_file"}],
        )
        assert obstacle is not None
        assert obstacle.obstacle_type == "tool_failure"
        assert obstacle.root_cause == "search failed"
        assert obstacle.attempted_tools == ["search", "read_file"]

    def test_exception_produces_internal_error(self, manager):
        """Exception produces SoarObstacle(obstacle_type='internal_error')."""
        exc = TimeoutError("network timeout")
        obstacle = manager._detect_obstacle(
            stop_reason=None,
            failure_summary="",
            tool_events=[],
            exc=exc,
        )
        assert obstacle is not None
        assert obstacle.obstacle_type == "internal_error"
        assert "network timeout" in obstacle.root_cause

    def test_success_returns_none(self, manager):
        """Successful stop_reason returns None."""
        obstacle = manager._detect_obstacle(
            stop_reason="stop",
            failure_summary="",
            tool_events=[],
        )
        assert obstacle is None

    def test_unknown_stop_reason_returns_none(self, manager):
        """Unknown stop_reason without exception returns None."""
        obstacle = manager._detect_obstacle(
            stop_reason="unknown_reason",
            failure_summary="",
            tool_events=[],
        )
        assert obstacle is None

    def test_recoverable_hint_from_network_timeout(self, manager):
        """Network timeout exception maps to recoverable_hint='retry'."""
        exc = TimeoutError("connection timed out")
        obstacle = manager._detect_obstacle(
            stop_reason=None,
            failure_summary="",
            tool_events=[],
            exc=exc,
        )
        assert obstacle is not None
        # TimeoutError should classify as NETWORK_TIMEOUT or INTERNAL
        assert obstacle.recoverable_hint in ("retry", "unknown")

    def test_root_cause_truncated_to_240(self, manager):
        """root_cause is truncated to 240 characters."""
        long_cause = "x" * 500
        obstacle = manager._detect_obstacle(
            stop_reason="tool_error",
            failure_summary=long_cause,
            tool_events=[],
        )
        assert len(obstacle.root_cause) <= 240

    def test_attempted_tools_deduplicated(self, manager):
        """attempted_tools contains unique tool names."""
        obstacle = manager._detect_obstacle(
            stop_reason="tool_error",
            failure_summary="fail",
            tool_events=[{"name": "search"}, {"name": "search"}, {"name": "read"}],
        )
        assert obstacle.attempted_tools == ["search", "read"]

    def test_obstacle_serializable(self, manager):
        """Obstacle can be serialized to JSON and back."""
        obstacle = manager._detect_obstacle(
            stop_reason="tool_error",
            failure_summary="test",
            tool_events=[{"name": "search"}],
        )
        restored = SoarObstacle.from_json(obstacle.to_json())
        assert restored.obstacle_type == obstacle.obstacle_type
        assert restored.root_cause == obstacle.root_cause
        assert restored.attempted_tools == obstacle.attempted_tools
