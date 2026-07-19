"""D10 — ConfirmationRequest.owner_id 字段与跨会话审批 测试。

验证目标（spec: unify-approval-flow-and-cross-session Task 1）：
1. create_tool_approval 支持 owner_id 参数并写入 ConfirmationRequest
2. list_pending_for_owner 按 owner_id 过滤
3. resolve_user_reply 在 caller_actor_id == request.owner_id 时允许跨 session 审批
4. resolve_user_reply 在 caller_actor_id != request.owner_id 时拒绝并发射审计事件
5. 旧 json（无 owner_id 字段）from_dict 加载时 owner_id 为 None（向后兼容）

关联规则：规则 18（安全边界，红线闭集 P0）、规则 34（验证先行）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from OriginAgent.agent.audit import AuditLogger
from OriginAgent.agent.confirmation import ConfirmationManager, ConfirmationRequest


NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


def test_create_tool_approval_writes_owner_id(tmp_path) -> None:
    """create_tool_approval(owner_id=...) 必须把 owner_id 写入返回的 ConfirmationRequest。"""
    manager = ConfirmationManager(tmp_path)

    confirmation = manager.create_tool_approval(
        tool_name="exec",
        prompt="cron wants exec",
        decision_reason="cron triggered exec",
        requested_by="cron",
        trigger="scheduled",
        risk="high",
        session_key="cron:abc",
        owner_id="tenant:owner",
        idempotency_key="tool_approval:exec:owner_id_write",
        now=NOW,
    )

    assert confirmation.owner_id == "tenant:owner", (
        f"create_tool_approval 应把 owner_id 写入 ConfirmationRequest，"
        f"实际 owner_id={confirmation.owner_id!r}"
    )

    # 持久化后 reload 仍应保留 owner_id（验证 to_dict/from_dict round-trip）
    reloaded = manager.store.read_all()
    assert len(reloaded) == 1
    assert reloaded[0].owner_id == "tenant:owner"


def test_list_pending_for_owner_filters_by_owner_id(tmp_path) -> None:
    """list_pending_for_owner(owner_id) 只返回该 owner 名下的 pending confirmation。"""
    manager = ConfirmationManager(tmp_path)

    # owner=A 创建 2 个 pending
    manager.create_tool_approval(
        tool_name="exec",
        prompt="A wants exec #1",
        decision_reason="A exec 1",
        requested_by="cron",
        trigger="scheduled",
        risk="high",
        session_key="cron:A",
        owner_id="A",
        idempotency_key="tool_approval:exec:A1",
        now=NOW,
    )
    manager.create_tool_approval(
        tool_name="read_file",
        prompt="A wants read_file #2",
        decision_reason="A read 2",
        requested_by="cron",
        trigger="scheduled",
        risk="high",
        session_key="cron:A",
        owner_id="A",
        idempotency_key="tool_approval:read_file:A2",
        now=NOW,
    )

    # owner=B 创建 1 个 pending
    manager.create_tool_approval(
        tool_name="exec",
        prompt="B wants exec",
        decision_reason="B exec",
        requested_by="cron",
        trigger="scheduled",
        risk="high",
        session_key="cron:B",
        owner_id="B",
        idempotency_key="tool_approval:exec:B1",
        now=NOW,
    )

    # 查 owner=A 的 pending，应返回 2 个
    pending_a = manager.list_pending_for_owner("A", now=NOW)
    assert len(pending_a) == 2, (
        f"owner=A 应有 2 个 pending，实际 {len(pending_a)} 个"
    )
    for item in pending_a:
        assert item.owner_id == "A"

    # 查 owner=B 的 pending，应返回 1 个
    pending_b = manager.list_pending_for_owner("B", now=NOW)
    assert len(pending_b) == 1
    assert pending_b[0].owner_id == "B"


def test_resolve_user_reply_allows_cross_session_when_owner_matches(tmp_path) -> None:
    """caller_actor_id == request.owner_id 时允许跨 session 审批。

    场景：cron 触发生成 pending（session_key="cron:abc"），
    owner 在 telegram 交互会话（session_key="telegram:owner"）中审批通过。
    """
    manager = ConfirmationManager(tmp_path)

    # cron 触发生成 pending，owner_id="tenant:owner"
    confirmation = manager.create_tool_approval(
        tool_name="exec",
        prompt="cron wants exec",
        decision_reason="cron triggered exec",
        requested_by="cron",
        trigger="scheduled",
        risk="high",
        session_key="cron:abc",
        owner_id="tenant:owner",
        idempotency_key="tool_approval:exec:cross_session_ok",
        now=NOW,
    )

    # owner 在不同 session 中审批（caller_actor_id 匹配 owner_id）
    result = manager.resolve_user_reply(
        confirmation.confirmation_id,
        "yes",
        caller_actor_id="tenant:owner",
        now=NOW,
    )

    # 应成功审批
    assert result.decision == "confirmed", (
        f"owner 匹配时应允许跨 session 审批，实际 decision={result.decision!r}，"
        f"reason={result.reason!r}"
    )
    assert result.confirmation_id == confirmation.confirmation_id

    # confirmation 状态应更新为 confirmed_once
    reloaded = manager.store.read_all()
    assert len(reloaded) == 1
    assert reloaded[0].status == "confirmed_once"


def test_resolve_user_reply_denies_cross_owner_approval(tmp_path) -> None:
    """caller_actor_id != request.owner_id 时拒绝审批并发射审计事件。

    场景：pending owner_id="tenant:owner"，caller actor_id="tenant:guest"。
    规则 18 安全边界：跨 owner 审批必须被拒绝并审计。
    """
    audit = AuditLogger(tmp_path)
    manager = ConfirmationManager(tmp_path, audit_logger=audit)

    # owner_id="tenant:owner" 的 pending
    confirmation = manager.create_tool_approval(
        tool_name="exec",
        prompt="owner wants exec",
        decision_reason="owner exec",
        requested_by="cron",
        trigger="scheduled",
        risk="high",
        session_key="cron:abc",
        owner_id="tenant:owner",
        idempotency_key="tool_approval:exec:cross_owner_deny",
        now=NOW,
    )

    # guest 尝试审批
    result = manager.resolve_user_reply(
        confirmation.confirmation_id,
        "yes",
        caller_actor_id="tenant:guest",
        now=NOW,
    )

    # 应被拒绝
    assert result.decision == "rejected", (
        f"跨 owner 审批应被拒绝（rejected），实际 decision={result.decision!r}"
    )

    # confirmation 状态应保持 pending（不应被 guest 改变）
    reloaded = manager.store.read_all()
    assert len(reloaded) == 1
    assert reloaded[0].status == "pending", (
        f"被拒绝的跨 owner 审批不应改变 confirmation 状态，"
        f"实际 status={reloaded[0].status!r}"
    )

    # 审计事件中应存在 approval_denied_owner_mismatch（作为 decision 字段或 metadata.denial_reason）
    events = audit.find_by_confirmation_id(confirmation.confirmation_id)
    mismatch_events = [
        event
        for event in events
        if event.decision == "approval_denied_owner_mismatch"
        or event.metadata.get("denial_reason") == "approval_denied_owner_mismatch"
    ]
    assert mismatch_events, (
        f"应发射审计事件 approval_denied_owner_mismatch，"
        f"实际 decisions={[e.decision for e in events]}，"
        f"metadata 集合={[e.metadata for e in events]}"
    )


def test_old_pending_without_owner_id_loads_with_none(tmp_path) -> None:
    """旧 json（无 owner_id 字段）from_dict 加载时 owner_id 应为 None（向后兼容）。"""
    # 构造一个没有 owner_id 字段的旧 json dict
    legacy_raw = {
        "confirmation_id": "confirmation_legacy_abc",
        "kind": "tool_approval",
        "status": "pending",
        "prompt": "legacy pending without owner_id",
        "action": "tool:exec",
        "scope": None,
        "trigger": "scheduled",
        "risk": "high",
        "requested_by": "cron",
        "decision_reason": "legacy",
        "presence_status": "unknown",
        "related_fact_ids": [],
        "created_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "requires_presence_empty": False,
        "uses_facts": [],
        "action_payload": {},
        "idempotency_key": None,
        "consumed_at": None,
        "metadata": {"session_key": "cron:legacy", "tool_name": "exec"},
        # 注意：故意不写 owner_id 字段，模拟 D10 修复前的旧数据
    }

    confirmation = ConfirmationRequest.from_dict(legacy_raw)

    assert confirmation.owner_id is None, (
        f"旧 json 无 owner_id 字段时 from_dict 应返回 None，"
        f"实际 owner_id={confirmation.owner_id!r}"
    )
    assert confirmation.confirmation_id == "confirmation_legacy_abc"
