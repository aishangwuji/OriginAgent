"""Tests for cron reminder_note denial warning (Improvement C).

When a cron session has recent PolicyDeniedError tool results, the reminder_note
should append a "do not retry" instruction so the Agent doesn't repeatedly
attempt denied tools.

Scope:
    - Test ``_scan_recent_policy_denials`` — scans session messages for denied
      tool names.
    - Test ``_build_cron_reminder_note`` — builds reminder note with optional
      denial warning.
"""

from __future__ import annotations

from typing import Any


# ─── Helper to build tool result messages ──────────────────────────────────


def _tool_result_msg(
    tool_name: str,
    content: str,
    tool_call_id: str = "call_1",
) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": tool_name,
        "content": content,
    }


def _user_msg(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def _assistant_msg(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


_DENIAL_EXEC = (
    "Error: PolicyDeniedError: tool 'exec' is not allowed by the current "
    "capability snapshot (this is a hard policy boundary, not a transient failure; "
    "do not retry)"
)

_DENIAL_WRITE_FILES = (
    "Error: PolicyDeniedError: tool 'write_files' is not allowed by the current "
    "capability snapshot"
)

_DENIAL_CREATE_CRON = (
    "Error: PolicyDeniedError: tool 'create_cron' is not allowed by the current "
    "capability snapshot"
)

_SUCCESS_EXEC = "Command output: hello world"


# ─── Test class 1: _scan_recent_policy_denials ────────────────────────────


class TestScanRecentPolicyDenials:
    """Test _scan_recent_policy_denials extracts denied tool names from messages."""

    def test_scan_returns_denied_tool_names(self) -> None:
        """Messages with PolicyDeniedError tool results → returns tool names."""
        from OriginAgent.cli.commands import _scan_recent_policy_denials

        messages = [
            _user_msg("reminder"),
            _tool_result_msg("exec", _DENIAL_EXEC),
        ]

        result = _scan_recent_policy_denials(messages)

        assert result == ["exec"]

    def test_scan_deduplicates_tool_names(self) -> None:
        """Multiple denials of same tool → returns once."""
        from OriginAgent.cli.commands import _scan_recent_policy_denials

        messages = [
            _tool_result_msg("exec", _DENIAL_EXEC, tool_call_id="call_1"),
            _tool_result_msg("exec", _DENIAL_EXEC, tool_call_id="call_2"),
            _tool_result_msg("exec", _DENIAL_EXEC, tool_call_id="call_3"),
        ]

        result = _scan_recent_policy_denials(messages)

        assert result == ["exec"]

    def test_scan_returns_multiple_distinct_tools(self) -> None:
        """Denials of different tools → returns all, most-recent-first."""
        from OriginAgent.cli.commands import _scan_recent_policy_denials

        messages = [
            _tool_result_msg("exec", _DENIAL_EXEC, tool_call_id="call_1"),
            _tool_result_msg("write_files", _DENIAL_WRITE_FILES, tool_call_id="call_2"),
        ]

        result = _scan_recent_policy_denials(messages)

        # Most recent first (reversed scan)
        assert "exec" in result
        assert "write_files" in result
        assert result[0] == "write_files"  # last in list = most recent

    def test_scan_ignores_non_tool_messages(self) -> None:
        """User/assistant messages → ignored."""
        from OriginAgent.cli.commands import _scan_recent_policy_denials

        messages = [
            _user_msg("Error: PolicyDeniedError: tool 'exec' is not allowed"),
            _assistant_msg("Error: PolicyDeniedError: tool 'exec' is not allowed"),
        ]

        result = _scan_recent_policy_denials(messages)

        assert result == []

    def test_scan_ignores_non_denial_tool_results(self) -> None:
        """Successful tool results → ignored."""
        from OriginAgent.cli.commands import _scan_recent_policy_denials

        messages = [
            _tool_result_msg("exec", _SUCCESS_EXEC),
            _tool_result_msg("read_file", "file content here"),
        ]

        result = _scan_recent_policy_denials(messages)

        assert result == []

    def test_scan_respects_max_messages(self) -> None:
        """Only scans last N messages."""
        from OriginAgent.cli.commands import _scan_recent_policy_denials

        # Build 20 messages: first 5 are denials, last 15 are non-denial.
        # With max_messages=10, only the last 10 (all non-denial) are scanned.
        messages: list[dict[str, Any]] = []
        for i in range(5):
            messages.append(_tool_result_msg("exec", _DENIAL_EXEC, tool_call_id=f"call_{i}"))
        for i in range(15):
            messages.append(_tool_result_msg("read_file", "content", tool_call_id=f"ok_{i}"))

        result = _scan_recent_policy_denials(messages, max_messages=10)

        # Only last 10 messages scanned, all are read_file (non-denial)
        assert result == []

    def test_scan_empty_messages(self) -> None:
        """Empty list → returns []."""
        from OriginAgent.cli.commands import _scan_recent_policy_denials

        result = _scan_recent_policy_denials([])

        assert result == []

    def test_scan_no_denials(self) -> None:
        """Messages without denials → returns []."""
        from OriginAgent.cli.commands import _scan_recent_policy_denials

        messages = [
            _user_msg("hello"),
            _assistant_msg("hi there"),
            _tool_result_msg("read_file", "content"),
        ]

        result = _scan_recent_policy_denials(messages)

        assert result == []

    def test_scan_detects_various_denial_phrases(self) -> None:
        """Detects different PolicyDeniedError message formats."""
        from OriginAgent.cli.commands import _scan_recent_policy_denials

        messages = [
            _tool_result_msg(
                "read_file",
                "Error: PolicyDeniedError: Path /foo is protected runtime state "
                "and cannot be read by generic file tools (this is a hard policy "
                "boundary, not a transient failure)",
                tool_call_id="call_1",
            ),
            _tool_result_msg(
                "write_file",
                "Error: PolicyDeniedError: path traversal detected (this is a "
                "hard policy boundary, not a transient failure)",
                tool_call_id="call_2",
            ),
        ]

        result = _scan_recent_policy_denials(messages)

        assert "read_file" in result
        assert "write_file" in result


# ─── Test class 2: _build_cron_reminder_note ──────────────────────────────


class TestBuildCronReminderNote:
    """Test _build_cron_reminder_note constructs the reminder with denial warning."""

    def test_note_without_denials(self) -> None:
        """No denied tools → standard reminder note, no warning."""
        from OriginAgent.cli.commands import _build_cron_reminder_note

        note = _build_cron_reminder_note("Time to take medication", denied_tools=[])

        assert "Time to take medication" in note
        assert "Capability Boundary Reminder" not in note
        assert "do not retry" not in note.lower()

    def test_note_with_denials_includes_warning(self) -> None:
        """Denied tools → reminder note includes denial warning."""
        from OriginAgent.cli.commands import _build_cron_reminder_note

        note = _build_cron_reminder_note(
            "Time to take medication",
            denied_tools=["exec", "write_files"],
        )

        assert "Time to take medication" in note
        assert "Capability Boundary Reminder" in note
        assert "exec" in note
        assert "write_files" in note
        assert "do not" in note.lower()

    def test_note_with_single_denial(self) -> None:
        """Single denied tool → warning lists that tool."""
        from OriginAgent.cli.commands import _build_cron_reminder_note

        note = _build_cron_reminder_note("Check emails", denied_tools=["exec"])

        assert "Capability Boundary Reminder" in note
        assert "exec" in note
        assert "do not" in note.lower()

    def test_note_preserves_base_message(self) -> None:
        """Base reminder message is preserved when denial warning is added."""
        from OriginAgent.cli.commands import _build_cron_reminder_note

        base_message = "Standup meeting in 5 minutes"
        note = _build_cron_reminder_note(base_message, denied_tools=["create_cron"])

        # Base message should appear before the warning
        base_idx = note.index(base_message)
        warning_idx = note.index("Capability Boundary Reminder")
        assert base_idx < warning_idx

    def test_note_with_none_denied_tools(self) -> None:
        """denied_tools=None → no warning (same as empty list)."""
        from OriginAgent.cli.commands import _build_cron_reminder_note

        note = _build_cron_reminder_note("Reminder", denied_tools=None)

        assert "Capability Boundary Reminder" not in note
