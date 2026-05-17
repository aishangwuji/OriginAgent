from __future__ import annotations

from datetime import datetime, timedelta, timezone

from OpenHome.security.capabilities import CapabilitySnapshot
from OpenHome.security.grants import CapabilityGrant, CapabilityGrantStore


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


def test_grant_store_put_get_list_revoke_and_tuple_persistence(tmp_path) -> None:
    store = CapabilityGrantStore(tmp_path)
    grant = CapabilityGrant(
        grant_id="grant-secret-1",
        created_by="admin",
        created_at=_now().isoformat(),
        allowed_device_domains=("lighting",),
        allowed_mcp_scopes=("read",),
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
