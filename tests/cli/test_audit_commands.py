"""Tests for ``originagent audit`` CLI commands.

These commands expose the AuditLogger query interfaces for post-hoc
investigation. See ``OriginAgent/agent/audit.py`` for the write-only
positioning of audit logs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from OriginAgent.agent.audit import AuditLogger
from OriginAgent.cli.commands import app

runner = CliRunner()

NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 5, 16, 12, 5, 0, tzinfo=timezone.utc)
EVEN_LATER = datetime(2026, 5, 16, 12, 10, 0, tzinfo=timezone.utc)


def _seed_audit_events(workspace: Path) -> tuple[str, str, list[str]]:
    """Write three audit events into the workspace.

    Returns ``(action_id, confirmation_id, [event_id_1, event_id_2, event_id_3])``
    ordered by ascending ``created_at``.
    """
    audit = AuditLogger(workspace)
    action_id = "action_test_1"
    confirmation_id = "confirmation_test_1"
    e1 = audit.log_action_decision(
        action_id=action_id,
        confirmation_id=confirmation_id,
        actor_id="alice",
        action="turn_on",
        scope="home.living_room.light",
        risk="low",
        trigger="user_initiated",
        decision="dry_run",
        reason="safety checks passed",
        metadata={"payload_keys": ["brightness"]},
        created_at=NOW,
    )
    e2 = audit.log_permission_decision(
        action_id=action_id,
        actor_id="alice",
        action="turn_on",
        scope="home.living_room.light",
        risk="low",
        trigger="user_initiated",
        permission="auto_allow",
        device_domain="light",
        decision="allow",
        reason="low risk action",
        actor_role="resident",
        created_at=LATER,
    )
    e3 = audit.log_confirmation_event(
        confirmation_id=confirmation_id,
        action_id=action_id,
        decision="created",
        reason="confirmation created",
        created_at=EVEN_LATER,
    )
    return action_id, confirmation_id, [e1.event_id, e2.event_id, e3.event_id]


def test_audit_query_by_action_id_returns_events(tmp_path: Path) -> None:
    action_id, _, _ = _seed_audit_events(tmp_path)

    result = runner.invoke(
        app,
        ["audit", "query", "--action-id", action_id, "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert action_id in result.stdout
    # Decision from log_action_decision
    assert "dry_run" in result.stdout
    # Decision from log_permission_decision
    assert "allow" in result.stdout
    # Action description
    assert "turn_on" in result.stdout


def test_audit_query_unknown_action_id_returns_clear_message(tmp_path: Path) -> None:
    _seed_audit_events(tmp_path)

    result = runner.invoke(
        app,
        ["audit", "query", "--action-id", "nonexistent_action", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 0
    stdout_lower = result.stdout.lower()
    assert "no audit events" in stdout_lower or "not found" in stdout_lower


def test_audit_list_with_limit_returns_recent_events(tmp_path: Path) -> None:
    _, _, _ = _seed_audit_events(tmp_path)

    result = runner.invoke(
        app,
        ["audit", "list", "--limit", "2", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 0
    # Limit 2 means the 2 most recent events (e2 and e3) should be shown.
    # Rich Table truncates long fields (event_id, event_type) in narrow
    # terminals, so assert on short, unambiguous decision values instead.
    assert "allow" in result.stdout        # e2 decision (permission_decision)
    assert "created" in result.stdout      # e3 decision (confirmation_event)
    # e1 decision (dry_run) must NOT appear — the oldest event is excluded.
    assert "dry_run" not in result.stdout


def test_audit_list_empty_workspace_returns_clear_message(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["audit", "list", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 0
    stdout_lower = result.stdout.lower()
    assert "no audit events" in stdout_lower
