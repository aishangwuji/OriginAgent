from __future__ import annotations

from datetime import datetime, timedelta, timezone

from OriginAgent.agent.confirmation import ConfirmationRequest
from OriginAgent.security.capabilities import CapabilitySnapshot
from OriginAgent.security.capabilities import intersect_capability_snapshots
from OriginAgent.security.grants import CapabilityGrant, CapabilityGrantStore, issue_tool_approval_grant


def _now() -> datetime:
    return datetime(2030, 1, 1, tzinfo=timezone.utc)


def test_grant_default_is_active() -> None:
    grant = CapabilityGrant(
        grant_id="grant-secret-1",
        created_by="admin",
        created_at=_now().isoformat(),
    )

    assert grant.is_active(_now()) is True
    assert grant.is_expired(_now()) is False
    assert grant.is_revoked() is False


def test_grant_past_invalid_or_revoked_is_inactive() -> None:
    past = CapabilityGrant(
        grant_id="past",
        created_by="admin",
        created_at=_now().isoformat(),
        expires_at=(_now() - timedelta(seconds=1)).isoformat(),
    )
    invalid = CapabilityGrant(
        grant_id="invalid",
        created_by="admin",
        created_at=_now().isoformat(),
        expires_at="not-a-date",
    )
    revoked = CapabilityGrant(
        grant_id="revoked",
        created_by="admin",
        created_at=_now().isoformat(),
        revoked_at=_now().isoformat(),
    )

    assert past.is_active(_now()) is False
    assert invalid.is_active(_now()) is False
    assert revoked.is_active(_now()) is False


def test_grant_to_snapshot_maps_capability_fields() -> None:
    grant = CapabilityGrant(
        grant_id="grant-secret-1",
        created_by="admin",
        created_at=_now().isoformat(),
        can_exec=True,
        can_read_files=True,
        can_write_files=True,
        can_send_cross_target=True,
        can_create_cron=True,
        can_spawn=True,
        allowed_device_domains=("lighting",),
        allowed_mcp_scopes=("read", "write"),
    )

    snapshot = grant.to_snapshot(trigger="scheduled")

    assert snapshot == CapabilitySnapshot(
        version=1,
        source="cron",
        trigger="scheduled",
        can_exec=True,
        can_read_files=True,
        can_write_files=True,
        can_send_cross_target=True,
        can_create_cron=True,
        can_spawn=True,
        allowed_device_domains=("lighting",),
        allowed_mcp_scopes=("read", "write"),
    )


def test_grant_to_snapshot_source_is_cron_only_for_c5a() -> None:
    grant = CapabilityGrant(
        grant_id="grant-secret-1",
        created_by="admin",
        created_at=_now().isoformat(),
        can_exec=True,
    )

    snapshot = grant.to_snapshot(trigger="scheduled")

    assert snapshot.source == "cron"
    assert snapshot.trigger == "scheduled"


def test_grant_to_subagent_snapshot_is_separate_from_cron_snapshot() -> None:
    grant = CapabilityGrant(
        grant_id="grant-secret-1",
        created_by="admin",
        created_at=_now().isoformat(),
        can_read_files=True,
    )

    cron_snapshot = grant.to_snapshot(trigger="scheduled")
    subagent_snapshot = grant.to_subagent_snapshot()

    assert cron_snapshot.source == "cron"
    assert cron_snapshot.trigger == "scheduled"
    assert subagent_snapshot.source == "subagent"
    assert subagent_snapshot.trigger == "subagent"
    assert subagent_snapshot.can_read_files is True


def test_intersect_capability_snapshots_is_explicit_and_cannot_expand() -> None:
    left = CapabilitySnapshot(
        version=1,
        source="subagent",
        trigger="subagent",
        can_exec=False,
        can_read_files=True,
        can_write_files=False,
        can_send_cross_target=False,
        can_create_cron=False,
        can_spawn=False,
        allowed_device_domains=("lighting",),
        allowed_mcp_scopes=("read",),
    )
    right = CapabilitySnapshot(
        version=1,
        source="subagent",
        trigger="subagent",
        can_exec=True,
        can_read_files=True,
        can_write_files=True,
        can_send_cross_target=True,
        can_create_cron=True,
        can_spawn=True,
        allowed_device_domains=("climate", "lighting"),
        allowed_mcp_scopes=("read", "write"),
    )

    effective = intersect_capability_snapshots(
        left,
        right,
        source="subagent",
        trigger="subagent",
    )

    assert effective == CapabilitySnapshot(
        version=1,
        source="subagent",
        trigger="subagent",
        can_exec=False,
        can_read_files=True,
        can_write_files=False,
        can_send_cross_target=False,
        can_create_cron=False,
        can_spawn=False,
        allowed_device_domains=("lighting",),
        allowed_mcp_scopes=("read",),
    )


def test_unknown_source_does_not_control_activity_or_snapshot() -> None:
    grant = CapabilityGrant.from_dict(
        {
            "grant_id": "grant-secret-1",
            "created_by": "admin",
            "created_at": _now().isoformat(),
            "source": "future_source",
            "can_exec": True,
        }
    )

    assert grant.source == "future_source"
    assert grant.is_active(_now()) is True
    snapshot = grant.to_snapshot(trigger="scheduled")
    assert snapshot.source == "cron"
    assert snapshot.can_exec is True


def test_grant_store_put_get_list_revoke_and_tuple_persistence(tmp_path) -> None:
    store = CapabilityGrantStore(tmp_path)
    grant = CapabilityGrant(
        grant_id="grant-secret-1",
        created_by="admin",
        created_at=_now().isoformat(),
        allowed_device_domains=("lighting",),
        allowed_mcp_scopes=("read",),
        approval_confirmation_id="confirmation-1",
        session_key="session-1",
        tool_name="originagent_evolution_control",
        purpose="evolution_override",
        metadata={"action_kind": "suppress_signal"},
    )

    store.put(grant)

    fresh = CapabilityGrantStore(tmp_path)
    loaded = fresh.get("grant-secret-1")
    assert loaded == grant
    assert fresh.list_all() == [grant]
    assert fresh.list_active() == [grant]

    assert fresh.revoke("grant-secret-1", revoked_at=_now()) is True
    revoked = fresh.get("grant-secret-1")
    assert revoked is not None
    assert revoked.is_active(_now()) is False
    assert revoked.approval_confirmation_id == "confirmation-1"
    assert revoked.session_key == "session-1"
    assert revoked.tool_name == "originagent_evolution_control"
    assert revoked.purpose == "evolution_override"
    assert revoked.metadata == {"action_kind": "suppress_signal"}
    assert fresh.list_active() == []
    assert fresh.revoke("missing") is False


def test_summary_omits_raw_sensitive_values_and_full_grant_id() -> None:
    grant = CapabilityGrant(
        grant_id="grant-secret-raw-id",
        created_by="admin",
        created_at=_now().isoformat(),
        expires_at=(_now() + timedelta(days=1)).isoformat(),
        can_exec=True,
        can_read_files=True,
        allowed_device_domains=("lighting",),
        allowed_mcp_scopes=("read",),
    )

    summary = grant.summary()
    text = str(summary)

    assert summary["grant_ref"]
    assert summary["enabled_flags_count"] == 2
    assert "grant-secret-raw-id" not in text
    for forbidden in ("prompt", "message", "path", "channel", "device_id", "secret"):
        assert forbidden not in summary


def test_issue_tool_approval_grant_persists_session_scoped_capability(tmp_path) -> None:
    confirmation = ConfirmationRequest(
        confirmation_id="confirmation_exec_1",
        kind="tool_approval",
        status="confirmed_once",
        prompt="approve exec",
        action="tool:exec",
        scope=None,
        trigger="user_initiated",
        risk="high",
        requested_by="alice",
        decision_reason="exec requires approval",
        presence_status="unknown",
        related_fact_ids=[],
        created_at=_now().isoformat(),
        expires_at=(_now() + timedelta(minutes=2)).isoformat(),
        action_payload={"grant_flags": "{\"can_exec\": true}"},
        metadata={"session_key": "websocket:chat-1", "tool_name": "exec"},
    )
    store = CapabilityGrantStore(tmp_path)

    grant = issue_tool_approval_grant(confirmation, store, approved_by="alice", now=_now())

    assert grant.can_exec is True
    assert grant.session_key == "websocket:chat-1"
    assert grant.tool_name == "exec"
    assert store.latest_active_for_confirmation("confirmation_exec_1", now=_now()) == grant


# ─── 方案 C3：持久授权分级 + TTL 参数化 ───────────────────────


def test_issue_tool_approval_grant_with_custom_ttl_for_persistent(tmp_path) -> None:
    """方案 C3: 持久授权使用用户指定的 TTL，而非默认 10 分钟。

    用户回复"以后都这样 3 天"后，grant 的 expires_at 应为 now + 3 天。
    """
    from OriginAgent.security.grants import issue_tool_approval_grant

    confirmation = ConfirmationRequest(
        confirmation_id="confirmation_persistent_read",
        kind="tool_approval",
        status="confirmed_persistent",
        prompt="approve read_file persistently",
        action="tool:read_file",
        scope=None,
        trigger="user_initiated",
        risk="medium",
        requested_by="alice",
        decision_reason="read_file persistent approval",
        presence_status="unknown",
        related_fact_ids=[],
        created_at=_now().isoformat(),
        expires_at=(_now() + timedelta(minutes=2)).isoformat(),
        action_payload={"grant_flags": "{\"can_read_files\": true}"},
        metadata={
            "session_key": "websocket:chat-1",
            "tool_name": "read_file",
            "persistent": "true",
            "ttl_seconds": str(3 * 24 * 3600),  # 用户指定 3 天
        },
    )
    store = CapabilityGrantStore(tmp_path)

    grant = issue_tool_approval_grant(confirmation, store, approved_by="alice", now=_now())

    # 持久授权的 TTL 应为用户指定的 3 天，而非默认 10 分钟
    expected_expires = _now() + timedelta(days=3)
    actual_expires = datetime.fromisoformat(grant.expires_at.replace("Z", "+00:00"))
    delta = abs((actual_expires - expected_expires).total_seconds())
    assert delta < 5, f"Expected expires_at ≈ now+3d, got delta={delta}s"

    # 持久标记应传递到 grant metadata
    assert grant.metadata.get("persistent") == "true"
    assert grant.tool_name == "read_file"


def test_persistent_grant_blocked_for_high_risk_tools(tmp_path) -> None:
    """方案 C3: exec/cron/spawn 不能持久授权（规则 18 安全边界分级）。

    即使回复"以后都这样"，对 exec 也只能获得单次授权（10 分钟 TTL）。
    """
    from OriginAgent.security.grants import issue_tool_approval_grant

    confirmation = ConfirmationRequest(
        confirmation_id="confirmation_exec_persistent_blocked",
        kind="tool_approval",
        status="confirmed_persistent",
        prompt="approve exec persistently",
        action="tool:exec",
        scope=None,
        trigger="user_initiated",
        risk="high",
        requested_by="alice",
        decision_reason="exec persistent attempt",
        presence_status="unknown",
        related_fact_ids=[],
        created_at=_now().isoformat(),
        expires_at=(_now() + timedelta(minutes=2)).isoformat(),
        action_payload={"grant_flags": "{\"can_exec\": true}"},
        metadata={
            "session_key": "websocket:chat-1",
            "tool_name": "exec",
            "persistent": "true",
            "ttl_seconds": str(7 * 24 * 3600),  # 用户想持久 7 天
        },
    )
    store = CapabilityGrantStore(tmp_path)

    grant = issue_tool_approval_grant(confirmation, store, approved_by="alice", now=_now())

    # exec 是高危工具，即使 persistent=true 也只给 10 分钟 TTL
    expected_expires = _now() + timedelta(minutes=10)
    actual_expires = datetime.fromisoformat(grant.expires_at.replace("Z", "+00:00"))
    delta = abs((actual_expires - expected_expires).total_seconds())
    assert delta < 5, f"Expected expires_at ≈ now+10min (high-risk override), got delta={delta}s"

    # 高危工具不标记 persistent
    assert grant.metadata.get("persistent") != "true"
