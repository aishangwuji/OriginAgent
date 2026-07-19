"""方案 B: cron tool_approval 延迟询问测试。

cron 触发时（用户不在线）tool_approval 不应直接阻塞会话，
而应标记为 deferred，等用户下次对话时主动询问。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from OriginAgent.agent.confirmation import ConfirmationManager


def test_cron_tool_approval_marked_deferred_for_user(tmp_path) -> None:
    """方案 B: cron 触发的 tool_approval，metadata 应标记 deferred_for_user=true。"""
    manager = ConfirmationManager(tmp_path)

    confirmation = manager.create_tool_approval(
        tool_name="exec",
        prompt="cron wants to use exec",
        decision_reason="cron triggered exec",
        requested_by="cron",
        trigger="scheduled",  # 关键：cron 触发
        risk="high",
        session_key="tenant:guest",
        metadata={"session_key": "tenant:guest", "tool_name": "exec"},
        idempotency_key="tool_approval:exec:cron1",
        now=datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert confirmation.metadata.get("deferred_for_user") == "true", (
        f"cron tool_approval should be marked deferred_for_user=true, got metadata={confirmation.metadata}"
    )
    assert confirmation.trigger == "scheduled"


def test_user_tool_approval_not_marked_deferred(tmp_path) -> None:
    """方案 B: 用户触发的 tool_approval 不应标记 deferred_for_user（用户就在线）。"""
    manager = ConfirmationManager(tmp_path)

    confirmation = manager.create_tool_approval(
        tool_name="exec",
        prompt="user wants to use exec",
        decision_reason="user triggered exec",
        requested_by="alice",
        trigger="user_initiated",  # 用户触发
        risk="high",
        session_key="tenant:guest",
        metadata={"session_key": "tenant:guest", "tool_name": "exec"},
        idempotency_key="tool_approval:exec:user1",
        now=datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc),
    )

    # 用户触发的 tool_approval 不应标记 deferred_for_user
    assert confirmation.metadata.get("deferred_for_user") != "true", (
        f"user tool_approval should NOT be marked deferred, got metadata={confirmation.metadata}"
    )


def test_list_deferred_tool_approvals_returns_only_cron_deferred(tmp_path) -> None:
    """方案 B: list_deferred_tool_approvals 只返回 cron 触发且 deferred 的 pending approvals。"""
    manager = ConfirmationManager(tmp_path)
    now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)

    # 创建一个 cron deferred approval
    cron_approval = manager.create_tool_approval(
        tool_name="exec",
        prompt="cron wants exec",
        decision_reason="cron exec",
        requested_by="cron",
        trigger="scheduled",
        risk="high",
        session_key="tenant:guest",
        metadata={"session_key": "tenant:guest", "tool_name": "exec"},
        idempotency_key="tool_approval:exec:cron1",
        now=now,
    )

    # 创建一个用户 approval（不应出现在 deferred 列表中）
    manager.create_tool_approval(
        tool_name="read_file",
        prompt="user wants read_file",
        decision_reason="user read_file",
        requested_by="alice",
        trigger="user_initiated",
        risk="high",
        session_key="tenant:guest",
        metadata={"session_key": "tenant:guest", "tool_name": "read_file"},
        idempotency_key="tool_approval:read_file:user1",
        now=now,
    )

    deferred = manager.list_deferred_tool_approvals(session_key="tenant:guest", now=now)

    assert len(deferred) == 1
    assert deferred[0].confirmation_id == cron_approval.confirmation_id
    assert deferred[0].metadata.get("deferred_for_user") == "true"
