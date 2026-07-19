"""REST API tests for /api/approvals endpoints.

Task 3 (方向 C-1) 测试 — 验证审批 REST API 端点（spec: unify-approval-flow-and-cross-session）：
1. ``GET /api/approvals`` — 列出当前 owner 的 pending（expired 不返回）
2. ``POST /api/approvals/{id}/approve`` — 审批通过并返回 grant_id
3. ``POST /api/approvals/{id}/reject`` — 审批拒绝
4. 跨 owner 审批返回 403 + 审计事件 ``approval_denied_owner_mismatch``

关联规则：规则 18（安全边界，红线闭集 P0）、规则 34（验证先行）。

测试策略：注入真实的 ``ConfirmationManager`` + ``CapabilityGrantStore`` + ``AuditLogger``
指向 ``tmp_path``，验证 handler → manager → store → audit 全链路集成行为，
而非 mock manager 的返回值（避免 mock 漂离真实契约）。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import quote

from OriginAgent.agent.audit import AuditLogger
from OriginAgent.agent.confirmation import ConfirmationManager
from OriginAgent.channels.websocket import WebSocketChannel
from OriginAgent.config.loader import save_config
from OriginAgent.config.schema import Config
from OriginAgent.security.grants import CapabilityGrantStore


# 与 test_confirmation_owner_id.py 对齐的固定时间锚点，避免 TTL 计算受墙钟漂移影响
NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


def _make_channel(
    tmp_path: Path, monkeypatch
) -> tuple[WebSocketChannel, ConfirmationManager, AuditLogger, CapabilityGrantStore]:
    """构造隔离的 WebSocketChannel + 真实 ConfirmationManager。

    生产环境 handler 从 ``config.workspace_path`` 构造 ConfirmationManager；
    测试通过 ``_confirmation_manager_provider`` / ``_grant_store_provider``
    注入指向 ``tmp_path`` 的实例，确保 pending_confirmations.json 与
    capability_grants.json 落在测试隔离目录，不污染真实 workspace。
    """
    config_path = tmp_path / "config.json"
    config = Config()
    save_config(config, config_path)
    monkeypatch.setattr("OriginAgent.config.loader._current_config_path", config_path)

    audit = AuditLogger(tmp_path)
    manager = ConfirmationManager(tmp_path, audit_logger=audit)
    grant_store = CapabilityGrantStore(tmp_path)

    bus = MagicMock()
    bus.publish_inbound = MagicMock()
    channel = WebSocketChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
            "host": "127.0.0.1",
            "port": 29901,
            "path": "/ws",
            "websocketRequiresToken": False,
        },
        bus,
    )
    # 直接登记一个多用途 API token（与 test_tenants_api.py 同模式）
    channel._gateway_auth._api_tokens["tok"] = time.monotonic() + 300
    # 注入测试专用的 manager / grant_store（生产由 handler 从 config 构造）
    channel._confirmation_manager_provider = lambda: manager
    channel._grant_store_provider = lambda: grant_store
    return channel, manager, audit, grant_store


def _request(path: str, owner_id: str | None = None) -> MagicMock:
    """构造最小化的 WsRequest mock。

    若提供 ``owner_id``，追加为 ``?owner_id=...`` query 参数——这是
    Task 3 的 caller_actor_id 解析路径（见 rest_api.py 中假设显式化说明）。
    """
    req = MagicMock()
    final_path = path
    if owner_id is not None:
        sep = "&" if "?" in path else "?"
        final_path = f"{path}{sep}owner_id={quote(owner_id, safe='')}"
    req.path = final_path
    req.headers = {"Authorization": "Bearer tok"}
    return req


def _body(response: Any) -> dict:
    return json.loads(response.body.decode("utf-8"))


class TestGetApprovalsList:
    def test_get_approvals_returns_owner_pending(self, tmp_path: Path, monkeypatch) -> None:
        """GET /api/approvals 返回当前 owner 的未过期 pending。

        场景：owner=A 有 2 个 pending（1 个未过期 + 1 个已过期），
        断言只返回 1 个未过期的，expired 不在列表中。
        """
        channel, manager, _, _ = _make_channel(tmp_path, monkeypatch)

        # 未过期 pending（NOW 创建，TTL 1h，NOW 时刻仍有效）
        manager.create_tool_approval(
            tool_name="exec",
            prompt="A wants exec (active)",
            decision_reason="A exec active",
            requested_by="cron",
            trigger="scheduled",
            risk="high",
            session_key="cron:A",
            owner_id="A",
            idempotency_key="tool_approval:exec:A_active",
            now=NOW,
        )
        # 已过期 pending（2h 前创建，TTL 1h，NOW 时刻已过期 1h）
        manager.create_tool_approval(
            tool_name="read_file",
            prompt="A wants read_file (expired)",
            decision_reason="A read expired",
            requested_by="cron",
            trigger="scheduled",
            risk="high",
            session_key="cron:A",
            owner_id="A",
            idempotency_key="tool_approval:read_file:A_expired",
            now=NOW - timedelta(hours=2),
        )

        # D6 lazy expire：list_pending_for_owner 内部会调 expire_old，但用墙钟
        # 时间判断（真实当前时间是 2026-07-19，测试固定 NOW=2026-07-20 是未来）。
        # 这里显式用 NOW 触发 lazy 清理，让过期 pending 的 status 落盘为
        # "expired"，后续 handler 调 list_pending_for_owner 时它会被 status
        # 过滤掉（不会被 un-expire 回 pending）。这验证的是"已 expired 的
        # pending 不在 list 中"这一契约，而非 D6 lazy 触发时机本身。
        manager.expire_old(now=NOW)

        resp = channel._rest_api._handle_approvals_list(
            _request("/api/approvals", owner_id="A")
        )

        assert resp.status_code == 200, (
            f"GET /api/approvals 应返回 200，实际 {resp.status_code}"
        )
        data = _body(resp)
        assert "approvals" in data
        assert len(data["approvals"]) == 1, (
            f"应返回 1 个未过期 pending（expired 不返回），实际 {len(data['approvals'])} 个"
        )
        approval = data["approvals"][0]
        assert approval["owner_id"] == "A"
        assert approval["metadata"]["tool_name"] == "exec"
        assert approval["confirmation_id"].startswith("confirmation_")
        assert approval["status"] == "pending"


class TestPostApprove:
    def test_post_approve_creates_grant_and_updates_status(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """POST /api/approvals/{id}/approve 审批通过并返回 grant_id。

        断言：
        - 返回 200 + grant_id
        - confirmation 状态变为 confirmed_once
        - grant 被写入 grant_store
        """
        channel, manager, _, grant_store = _make_channel(tmp_path, monkeypatch)

        confirmation = manager.create_tool_approval(
            tool_name="exec",
            prompt="A wants exec",
            decision_reason="A exec",
            requested_by="cron",
            trigger="scheduled",
            risk="high",
            session_key="cron:A",
            owner_id="A",
            idempotency_key="tool_approval:exec:A_approve_test",
            now=NOW,
        )

        path_id = quote(confirmation.confirmation_id, safe="")
        resp = channel._rest_api._handle_approval_action(
            _request(f"/api/approvals/{path_id}/approve", owner_id="A"),
            confirmation.confirmation_id,
            "approve",
        )

        assert resp.status_code == 200, (
            f"approve 应返回 200，实际 {resp.status_code}，body={resp.body!r}"
        )
        data = _body(resp)
        assert data["decision"] == "confirmed", (
            f"decision 应为 confirmed，实际 {data.get('decision')!r}"
        )
        assert data["confirmation_id"] == confirmation.confirmation_id
        grant_id = data.get("grant_id")
        assert grant_id and grant_id.startswith("grant_"), (
            f"应返回有效的 grant_id，实际 {grant_id!r}"
        )

        # confirmation 状态应已变为 confirmed_once
        reloaded = manager.store.get(confirmation.confirmation_id)
        assert reloaded is not None
        assert reloaded.status == "confirmed_once", (
            f"confirmation 状态应为 confirmed_once，实际 {reloaded.status!r}"
        )

        # grant 应已写入 grant_store
        grant = grant_store.get(grant_id)
        assert grant is not None, (
            f"grant {grant_id} 应已写入 grant_store"
        )
        assert grant.approval_confirmation_id == confirmation.confirmation_id


class TestPostReject:
    def test_post_reject_updates_status(self, tmp_path: Path, monkeypatch) -> None:
        """POST /api/approvals/{id}/reject 审批拒绝。

        断言：
        - 返回 200
        - confirmation 状态变为 rejected
        """
        channel, manager, _, _ = _make_channel(tmp_path, monkeypatch)

        confirmation = manager.create_tool_approval(
            tool_name="exec",
            prompt="A wants exec",
            decision_reason="A exec",
            requested_by="cron",
            trigger="scheduled",
            risk="high",
            session_key="cron:A",
            owner_id="A",
            idempotency_key="tool_approval:exec:A_reject_test",
            now=NOW,
        )

        path_id = quote(confirmation.confirmation_id, safe="")
        resp = channel._rest_api._handle_approval_action(
            _request(f"/api/approvals/{path_id}/reject", owner_id="A"),
            confirmation.confirmation_id,
            "reject",
        )

        assert resp.status_code == 200, (
            f"reject 应返回 200，实际 {resp.status_code}，body={resp.body!r}"
        )
        data = _body(resp)
        assert data["decision"] == "rejected", (
            f"decision 应为 rejected，实际 {data.get('decision')!r}"
        )

        # confirmation 状态应已变为 rejected
        reloaded = manager.store.get(confirmation.confirmation_id)
        assert reloaded is not None
        assert reloaded.status == "rejected", (
            f"confirmation 状态应为 rejected，实际 {reloaded.status!r}"
        )


class TestCrossOwnerApproval:
    def test_cross_owner_approval_returns_403(self, tmp_path: Path, monkeypatch) -> None:
        """跨 owner 审批返回 403 + 审计事件 approval_denied_owner_mismatch。

        场景：pending owner_id="tenant:owner"，caller（通过 ?owner_id=）
        声明为 "tenant:guest"。规则 18 安全边界：跨 owner 审批必须被拒绝。

        断言：
        - 返回 403
        - 响应 body 包含 approval_denied_owner_mismatch 错误标识
        - confirmation 状态保持 pending（不应被 guest 改变）
        - 审计日志中存在 approval_denied_owner_mismatch 事件
        """
        channel, manager, audit, _ = _make_channel(tmp_path, monkeypatch)

        confirmation = manager.create_tool_approval(
            tool_name="exec",
            prompt="owner wants exec",
            decision_reason="owner exec",
            requested_by="cron",
            trigger="scheduled",
            risk="high",
            session_key="cron:owner",
            owner_id="tenant:owner",
            idempotency_key="tool_approval:exec:cross_owner_403",
            now=NOW,
        )

        path_id = quote(confirmation.confirmation_id, safe="")
        # caller 声明为 tenant:guest，与 pending 的 owner_id=tenant:owner 不匹配
        resp = channel._rest_api._handle_approval_action(
            _request(f"/api/approvals/{path_id}/approve", owner_id="tenant:guest"),
            confirmation.confirmation_id,
            "approve",
        )

        assert resp.status_code == 403, (
            f"跨 owner 审批应返回 403，实际 {resp.status_code}"
        )
        data = _body(resp)
        assert data.get("error") == "approval_denied_owner_mismatch", (
            f"响应应包含 error=approval_denied_owner_mismatch，实际 {data!r}"
        )

        # confirmation 状态应保持 pending（guest 无权改变）
        reloaded = manager.store.get(confirmation.confirmation_id)
        assert reloaded is not None
        assert reloaded.status == "pending", (
            f"被拒绝的跨 owner 审批不应改变 confirmation 状态，"
            f"实际 status={reloaded.status!r}"
        )

        # 审计事件：approval_denied_owner_mismatch 应已发射
        events = audit.find_by_confirmation_id(confirmation.confirmation_id)
        mismatch_events = [
            event
            for event in events
            if event.decision == "approval_denied_owner_mismatch"
            or event.metadata.get("denial_reason") == "approval_denied_owner_mismatch"
        ]
        assert mismatch_events, (
            f"应发射审计事件 approval_denied_owner_mismatch，"
            f"实际 decisions={[e.decision for e in events]}"
        )


class TestAuth:
    def test_unauthorized_without_token(self, tmp_path: Path, monkeypatch) -> None:
        """无 token 调用审批端点返回 401（规则 18 安全边界）。"""
        channel, _, _, _ = _make_channel(tmp_path, monkeypatch)
        req = MagicMock()
        req.path = "/api/approvals"
        req.headers = {}  # 无 Authorization header
        resp = channel._rest_api._handle_approvals_list(req)
        assert resp.status_code == 401
