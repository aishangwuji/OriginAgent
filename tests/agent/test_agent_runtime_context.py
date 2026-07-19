"""Tests for ``snapshot_for_trigger`` payload_snapshot passthrough (spec P0-1).

Verifies that:
1. When ``payload_snapshot`` is non-empty, the returned ``CapabilitySnapshot``
   is reconstructed from it (overriding trigger-based selection).
2. When ``payload_snapshot`` is None/empty, behavior is unchanged (existing
   trigger-based mapping, including ``scheduled_default()`` for ``"scheduled"``).
3. When ``payload_snapshot`` is non-empty but invalid (missing required
   capability fields), the function tolerates it (no exception) and falls
   back to ``scheduled_default()``, logging a warning.

These tests are written BEFORE the implementation (rule 34: verify-first).
"""
from __future__ import annotations

import logging

import pytest

from OriginAgent.agent.agent_runtime_context import snapshot_for_trigger
from OriginAgent.security.capabilities import CapabilitySnapshot


def test_snapshot_for_trigger_uses_payload_snapshot_when_provided() -> None:
    """When ``payload_snapshot`` is non-empty, the returned snapshot MUST be
    reconstructed from it, overriding the trigger-based selection.

    For ``trigger="scheduled"`` (which normally returns ``scheduled_default()``
    with all False), providing ``payload_snapshot={"can_exec": True, ...}``
    MUST yield a snapshot with ``can_exec=True``.
    """
    payload = {
        "can_exec": True,
        "can_read_files": True,
        "can_write_files": True,
        "can_create_cron": True,
        "can_spawn": True,
        "can_send_cross_target": True,
    }
    snapshot = snapshot_for_trigger("scheduled", payload_snapshot=payload)

    assert isinstance(snapshot, CapabilitySnapshot)
    assert snapshot.can_exec is True
    assert snapshot.can_read_files is True
    assert snapshot.can_write_files is True
    assert snapshot.can_create_cron is True
    assert snapshot.can_spawn is True
    assert snapshot.can_send_cross_target is True


def test_snapshot_for_trigger_falls_back_when_payload_snapshot_none() -> None:
    """When ``payload_snapshot`` is None, behavior MUST be unchanged.

    For ``trigger="scheduled"``, the result MUST equal
    ``CapabilitySnapshot.scheduled_default()`` (all False).
    """
    snapshot = snapshot_for_trigger("scheduled", payload_snapshot=None)

    expected = CapabilitySnapshot.scheduled_default()
    assert snapshot == expected
    assert snapshot.can_exec is False
    assert snapshot.can_read_files is False
    assert snapshot.can_write_files is False
    assert snapshot.can_create_cron is False
    assert snapshot.can_spawn is False


def test_snapshot_for_trigger_falls_back_when_payload_snapshot_empty_dict() -> None:
    """When ``payload_snapshot`` is an empty dict, behavior MUST be unchanged
    (treated as None — backward compat for callers that always pass a dict).
    """
    snapshot = snapshot_for_trigger("scheduled", payload_snapshot={})

    expected = CapabilitySnapshot.scheduled_default()
    assert snapshot == expected


def test_snapshot_for_trigger_tolerates_invalid_payload_snapshot(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When ``payload_snapshot`` is non-empty but missing required capability
    fields, the function SHALL:
    - NOT raise an exception
    - Fall back to ``scheduled_default()``
    - Log a warning

    This is the fail-safe behavior required by spec (rule 14: assertion that
    invalid input does not raise; rule 18: security boundary — invalid input
    must not silently grant capabilities).
    """
    invalid_payload = {"invalid_field": True}  # no recognized capability keys

    # Must not raise
    snapshot = snapshot_for_trigger("scheduled", payload_snapshot=invalid_payload)

    # Must fall back to scheduled_default() (all False — fail-safe, no
    # capability granted from invalid input)
    expected = CapabilitySnapshot.scheduled_default()
    assert snapshot == expected
    assert snapshot.can_exec is False
    assert snapshot.can_read_files is False

    # Must log a warning
    with caplog.at_level(logging.WARNING, logger="OriginAgent.agent.agent_runtime_context"):
        snapshot_for_trigger("scheduled", payload_snapshot=invalid_payload)
    assert any(
        "payload_snapshot" in record.message.lower() or "capability" in record.message.lower()
        for record in caplog.records
    ), f"Expected a warning log about invalid payload_snapshot, got: {[r.message for r in caplog.records]}"


def test_snapshot_for_trigger_payload_snapshot_overrides_user_trigger() -> None:
    """When ``payload_snapshot`` is non-empty, it MUST override even non-scheduled
    triggers (e.g. ``"user_initiated"`` normally returns ``user_turn()`` with
    can_exec=True; an explicit ``payload_snapshot={"can_exec": False}`` MUST
    yield ``can_exec=False``).

    This confirms payload_snapshot takes precedence over trigger-based selection
    for ALL triggers, not just ``"scheduled"``.
    """
    snapshot = snapshot_for_trigger(
        "user_initiated",
        payload_snapshot={"can_exec": False, "can_read_files": False},
    )

    assert snapshot.can_exec is False
    assert snapshot.can_read_files is False
