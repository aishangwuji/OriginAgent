"""Tests for EpicMotorProcessor idempotency gap fix (规则12 红线).

Verifies that after motor dispatch succeeds, the idempotency key is recorded
so that retries don't cause duplicate execution.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.action_runtime import ActionIntent, ActionExecutionResult
from OriginAgent.agent.action_safety import ActionDecision
from OriginAgent.agent.epic_motor import EpicActionQueue, EpicMotorCommand, EpicMotorProcessor


NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_command(idempotency_key: str | None = None) -> EpicMotorCommand:
    intent = ActionIntent(
        action="turn_on",
        scope="home.living_room.light",
        trigger="user_initiated",
        risk="low",
        idempotency_key=idempotency_key,
    )
    decision = ActionDecision(
        decision="allow",
        reason="low risk",
        presence_status="unknown",
    )
    return EpicMotorCommand(
        action_id="action_test_001",
        intent=intent,
        decision=decision,
        enqueued_at=NOW,
        ready_at=NOW,
    )


@pytest.mark.asyncio
async def test_motor_dispatch_records_idempotency_key():
    """After successful dispatch, idempotency key must be recorded."""
    queue = EpicActionQueue()
    executor = MagicMock()
    executor._execute_allowed = MagicMock(
        return_value=ActionExecutionResult(
            status="executed",
            action_id="action_test_001",
            reason="ok",
        )
    )
    executor._remember_successful_idempotency = MagicMock()

    processor = EpicMotorProcessor(executor=executor, queue=queue)
    queue.enqueue(_make_command(idempotency_key="idem-motor-1"))

    results = await processor.tick()

    assert len(results) == 1
    assert results[0].status == "executed"
    # 规则12 红线验证：幂等键必须被记忆
    executor._remember_successful_idempotency.assert_called_once()


@pytest.mark.asyncio
async def test_motor_dispatch_failed_does_not_record_idempotency():
    """When dispatch fails (exception), idempotency should NOT be recorded."""
    queue = EpicActionQueue()
    executor = MagicMock()
    executor._execute_allowed = MagicMock(side_effect=RuntimeError("backend down"))
    executor._remember_successful_idempotency = MagicMock()

    processor = EpicMotorProcessor(executor=executor, queue=queue)
    queue.enqueue(_make_command(idempotency_key="idem-motor-fail"))

    results = await processor.tick()

    assert len(results) == 1
    assert results[0].status == "failed"
    # 失败时不应记忆幂等键
    executor._remember_successful_idempotency.assert_not_called()


@pytest.mark.asyncio
async def test_motor_dispatch_dry_run_records_idempotency():
    """Dry-run results should also record idempotency (status in {"executed", "dry_run"})."""
    queue = EpicActionQueue()
    executor = MagicMock()
    executor._execute_allowed = MagicMock(
        return_value=ActionExecutionResult(
            status="dry_run",
            action_id="action_test_002",
            reason="dry run",
        )
    )
    executor._remember_successful_idempotency = MagicMock()

    processor = EpicMotorProcessor(executor=executor, queue=queue)
    queue.enqueue(_make_command(idempotency_key="idem-motor-dry"))

    results = await processor.tick()

    assert len(results) == 1
    assert results[0].status == "dry_run"
    executor._remember_successful_idempotency.assert_called_once()
