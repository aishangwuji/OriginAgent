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


def test_list_tool_approvals_lazy_expires_old_pending(tmp_path) -> None:
    """方向 D 修复：``list_tool_approvals`` 必须在读取前调用
    ``expire_old`` 把过期 pending 标记为 ``expired``。

    修复前根因：``ConfirmationManager.expire_old`` 从未被任何 caller 调用
    （D6 缺陷），导致 ``pending_confirmations.json`` 堆积 status=pending
    但 expires_at < now 的"假 pending"——功能上虽被 ``_is_expired`` 过滤，
    但 status 字段不更新，让用户误以为有 N 个待审批其实早已过期。

    Rule 7（状态变化审计）+ rule 12（幂等）：过期 confirmation 必须在
    状态字段上正确反映，避免基于 status 的下游消费者误判。
    """
    manager = ConfirmationManager(tmp_path)
    base_time = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)

    # 创建一个 high-risk cron 触发的 tool_approval（D7 后 TTL=1h）
    manager.create_tool_approval(
        tool_name="exec",
        prompt="cron wants exec",
        decision_reason="cron exec",
        requested_by="cron",
        trigger="scheduled",
        risk="high",
        session_key="tenant:guest",
        metadata={"session_key": "tenant:guest", "tool_name": "exec"},
        idempotency_key="tool_approval:exec:lazy_expire_1",
        now=base_time,
    )

    # 验证创建后 status=pending
    all_before = manager.store.read_all()
    assert len(all_before) == 1
    assert all_before[0].status == "pending"

    # 时间前进 2 小时（远超 1 小时 TTL；D7 后 cron 触发的 tool_approval TTL=1h）
    later_time = base_time + timedelta(hours=2)

    # 调用 list_tool_approvals —— 修复后应触发 lazy expire_old
    result = manager.list_tool_approvals(now=later_time)

    # 功能正确性（修复前后均成立）：过期 confirmation 不应返回
    assert result == [], "Expired confirmation should not be returned"

    # 状态字段正确性（修复后才成立）：status 必须从 pending 变为 expired
    all_after = manager.store.read_all()
    assert len(all_after) == 1
    assert all_after[0].status == "expired", (
        f"After list_tool_approvals call, expired confirmation status should be "
        f"'expired' (lazy expire_old fix), got status={all_after[0].status!r}. "
        f"This is the D6 root cause: stale status='pending' makes users think "
        f"there are pending approvals when in fact they already expired."
    )
