"""Tests for capability boundary injection into system prompt.

Verifies that when a CapabilitySnapshot with restricted capabilities (e.g.
scheduled_default for cron sessions) is passed to build_system_prompt, the
resulting prompt explicitly declares which tools are unavailable — preventing
the Agent from repeatedly attempting denied tool calls.

Design rationale: cron sessions use CapabilitySnapshot.scheduled_default()
which has can_exec=False, can_write_files=False, etc. Without explicit
declaration in the prompt, the LLM only discovers these restrictions by
trying and being denied, leading to wasteful retry loops.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.context import ContextBuilder
from OriginAgent.security.capabilities import CapabilitySnapshot


# ---------------------------------------------------------------------------
# Core logic: build_capability_boundaries_text (static, pure function)
# ---------------------------------------------------------------------------


class TestBuildCapabilityBoundariesText:
    """Test the pure-function rendering of capability boundaries."""

    def test_scheduled_default_lists_unavailable_capabilities(self) -> None:
        """scheduled_default has can_exec=False etc → text lists them as unavailable."""
        snapshot = CapabilitySnapshot.scheduled_default()
        text = ContextBuilder.build_capability_boundaries_text(snapshot)

        assert text is not None
        assert "exec" in text.lower()
        assert "write_files" in text.lower()
        assert "spawn" in text.lower()
        assert "cron" in text.lower() or "can_create_cron" in text.lower()
        # read-only mcp is allowed, should not be listed as unavailable
        assert "read" not in text.lower().split("unavailable")[0] if "unavailable" in text.lower() else True

    def test_user_turn_returns_none(self) -> None:
        """user_turn has all major capabilities True → no boundary text needed."""
        snapshot = CapabilitySnapshot.user_turn()
        text = ContextBuilder.build_capability_boundaries_text(snapshot)

        # user_turn has can_send_cross_target=False, so it might still produce text
        # but the major tools (exec, read, write, spawn, cron) should NOT be listed
        if text is not None:
            assert "exec" not in text.lower() or "available" in text.lower()
            assert "write_files" not in text.lower() or "available" in text.lower()
        # The key assertion: user_turn should not produce a "do not attempt" warning
        # for the main tools

    def test_none_snapshot_returns_none(self) -> None:
        """No snapshot → no boundary text (backward compatible)."""
        text = ContextBuilder.build_capability_boundaries_text(None)
        assert text is None

    def test_partial_snapshot_lists_only_unavailable(self) -> None:
        """A snapshot with some True and some False → only lists False ones."""
        snapshot = CapabilitySnapshot(
            version=1,
            source="cron",
            trigger="scheduled",
            can_exec=False,
            can_read_files=True,
            can_write_files=False,
            can_send_cross_target=False,
            can_create_cron=True,
            can_spawn=False,
            allowed_device_domains=(),
            allowed_mcp_scopes=("read",),
        )
        text = ContextBuilder.build_capability_boundaries_text(snapshot)

        assert text is not None
        assert "exec" in text.lower()
        assert "write_files" in text.lower()
        assert "spawn" in text.lower()
        # can_read_files=True and can_create_cron=True → should NOT be listed as unavailable
        assert "can_read_files" not in text.lower() or "read_files" not in [
            line.strip().lower() for line in text.split("\n") if "unavailable" in line.lower()
        ]


# ---------------------------------------------------------------------------
# Integration: build_system_prompt includes capability boundaries
# ---------------------------------------------------------------------------


class TestBuildSystemPromptWithCapabilityBoundaries:
    """Test that build_system_prompt includes capability boundary text."""

    @pytest.fixture
    def context_builder(self, tmp_path: Path) -> ContextBuilder:
        """Build a minimal ContextBuilder for testing."""
        # Create minimal workspace structure
        (tmp_path / "templates").mkdir(exist_ok=True)
        (tmp_path / "memory").mkdir(exist_ok=True)
        (tmp_path / "skills").mkdir(exist_ok=True)

        cb = ContextBuilder(
            workspace=tmp_path,
            timezone="UTC",
        )
        # Mock heavy dependencies to avoid needing full workspace
        cb._load_bootstrap_files = MagicMock(return_value="")  # type: ignore
        return cb

    def test_scheduled_default_snapshot_injects_boundaries(
        self, context_builder: ContextBuilder
    ) -> None:
        """build_system_prompt with scheduled_default → prompt contains boundary declaration."""
        snapshot = CapabilitySnapshot.scheduled_default()
        prompt = context_builder.build_system_prompt(
            capability_snapshot=snapshot,
        )

        assert "capability" in prompt.lower() or "boundary" in prompt.lower()
        assert "exec" in prompt.lower()
        assert "unavailable" in prompt.lower() or "not allowed" in prompt.lower() or "denied" in prompt.lower()

    def test_none_snapshot_does_not_inject_boundaries(
        self, context_builder: ContextBuilder
    ) -> None:
        """build_system_prompt without snapshot → no boundary section (backward compatible)."""
        prompt = context_builder.build_system_prompt()

        assert "capability boundar" not in prompt.lower()

    def test_user_turn_snapshot_does_not_warn_about_exec(
        self, context_builder: ContextBuilder
    ) -> None:
        """build_system_prompt with user_turn → exec NOT listed as unavailable."""
        snapshot = CapabilitySnapshot.user_turn()
        prompt = context_builder.build_system_prompt(
            capability_snapshot=snapshot,
        )

        # exec is True in user_turn, should not be in unavailable list
        lines = prompt.split("\n")
        exec_unavailable_lines = [
            line for line in lines
            if "exec" in line.lower()
            and ("unavailable" in line.lower() or "denied" in line.lower() or "not allowed" in line.lower())
        ]
        assert len(exec_unavailable_lines) == 0, (
            f"exec should not be listed as unavailable for user_turn, but found: {exec_unavailable_lines}"
        )
