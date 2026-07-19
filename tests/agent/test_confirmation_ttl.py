"""D7 — tool_approval TTL 按 trigger 分级测试。

cron 触发的 tool_approval 使用 1h TTL（默认 3600s，由
``ConfirmationConfig.tool_approval_cron_ttl_seconds`` 控制），
以适应 cron 调度节奏（用户可能不在线，需要更长的待审批窗口）；
用户触发的 tool_approval 仍走 ``confirm_ttl_by_risk``（high=2min）。

规则 19（可回滚）：TTL 通过 config 字段暴露，运行时可通过修改
配置文件动态调整，无需代码改动。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from OriginAgent.agent.confirmation import ConfirmationManager, _ttl_for
from OriginAgent.config.schema import ConfirmationConfig


def test_ttl_for_cron_trigger_returns_1h() -> None:
    """``_ttl_for`` 在 trigger="scheduled" 时返回 1h（来自 tool_approval_cron_ttl_seconds）。"""
    config = ConfirmationConfig()
    ttl = _ttl_for("tool_approval", "high", config, trigger="scheduled")
    assert ttl == timedelta(hours=1), (
        f"cron-triggered tool_approval should use 1h TTL "
        f"(tool_approval_cron_ttl_seconds=3600), got {ttl}"
    )


def test_ttl_for_user_trigger_returns_2min() -> None:
    """``_ttl_for`` 在 trigger="user" 时维持现有行为：high risk=2min（来自 confirm_ttl_by_risk）。"""
    config = ConfirmationConfig()
    ttl = _ttl_for("tool_approval", "high", config, trigger="user")
    assert ttl == timedelta(minutes=2), (
        f"user-triggered tool_approval should use 2min TTL "
        f"(confirm_ttl_by_risk.high=120), got {ttl}"
    )


def test_create_tool_approval_cron_trigger_uses_1h_ttl(tmp_path) -> None:
    """``create_tool_approval`` 在 trigger="scheduled" 时，expires_at - created_at == 1h。"""
    manager = ConfirmationManager(tmp_path)
    base_time = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)

    confirmation = manager.create_tool_approval(
        tool_name="exec",
        prompt="cron wants to use exec",
        decision_reason="cron triggered exec",
        requested_by="cron",
        trigger="scheduled",
        risk="high",
        session_key="tenant:guest",
        metadata={"session_key": "tenant:guest", "tool_name": "exec"},
        idempotency_key="tool_approval:exec:cron_ttl_test",
        now=base_time,
    )

    created_at = datetime.fromisoformat(confirmation.created_at)
    expires_at = datetime.fromisoformat(confirmation.expires_at)
    delta = expires_at - created_at
    assert delta == timedelta(hours=1), (
        f"cron-triggered tool_approval should have 1h TTL, "
        f"created_at={created_at}, expires_at={expires_at}, delta={delta}"
    )


def test_create_tool_approval_user_trigger_uses_2min_ttl(tmp_path) -> None:
    """``create_tool_approval`` 在 trigger="user" 时，expires_at - created_at == 2min。"""
    manager = ConfirmationManager(tmp_path)
    base_time = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)

    confirmation = manager.create_tool_approval(
        tool_name="exec",
        prompt="user wants to use exec",
        decision_reason="user triggered exec",
        requested_by="alice",
        trigger="user",
        risk="high",
        session_key="tenant:guest",
        metadata={"session_key": "tenant:guest", "tool_name": "exec"},
        idempotency_key="tool_approval:exec:user_ttl_test",
        now=base_time,
    )

    created_at = datetime.fromisoformat(confirmation.created_at)
    expires_at = datetime.fromisoformat(confirmation.expires_at)
    delta = expires_at - created_at
    assert delta == timedelta(minutes=2), (
        f"user-triggered tool_approval should have 2min TTL, "
        f"created_at={created_at}, expires_at={expires_at}, delta={delta}"
    )
