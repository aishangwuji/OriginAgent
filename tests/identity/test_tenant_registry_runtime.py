"""Runtime mutation tests for TenantRegistry.

Verifies add/update/delete/to_config — the WebUI admin surface (rule 34:
assertions written before inspecting the implementation's exact branching).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from OriginAgent.config.schema import (
    TenantChannelBinding,
    TenantConfig,
    TenantsConfig,
)
from OriginAgent.identity.tenant import TenantRegistry


def _tc(
    tenant_id: str = "dad",
    display_name: str = "爸爸",
    bindings: list[tuple[str, str, str]] | None = None,
    bdi_enabled: bool = True,
    claimable: bool = False,
) -> TenantConfig:
    return TenantConfig(
        tenant_id=tenant_id,
        display_name=display_name,
        bindings=[
            TenantChannelBinding(channel=c, sender_id=s, label=lab)
            for (c, s, lab) in (bindings or [])
        ],
        bdi_enabled=bdi_enabled,
        claimable_by_pairing=claimable,
        permissions={"exec": False, "write_files": False, "device_control": True},
    )


class TestAddTenant:
    def test_add_tenant_registers_in_lookup(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, TenantsConfig())
        tc = _tc("dad", "爸爸", [("telegram", "12345", "手机")])
        tenant = reg.add_tenant(tc)
        assert tenant.tenant_id == "dad"
        # lookup by (channel, sender_id) must resolve to the new tenant
        resolved = reg.lookup("telegram", "12345")
        assert resolved is not None
        assert resolved.tenant_id == "dad"

    def test_add_tenant_duplicate_raises_value_error(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, TenantsConfig())
        reg.add_tenant(_tc("dad"))
        with pytest.raises(ValueError, match="already exists"):
            reg.add_tenant(_tc("dad"))

    def test_add_tenant_appears_in_all(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, TenantsConfig())
        reg.add_tenant(_tc("dad"))
        reg.add_tenant(_tc("mom"))
        ids = {t.tenant_id for t in reg.all() if t.tenant_id != "guest"}
        assert ids == {"dad", "mom"}


class TestUpdateTenant:
    def test_update_tenant_replaces_bindings(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, TenantsConfig())
        reg.add_tenant(_tc("dad", bindings=[("telegram", "111", "旧手机")]))
        # update with new binding set
        new_tc = _tc("dad", "爸爸Pro", [("telegram", "222", "新手机")])
        updated = reg.update_tenant("dad", new_tc)
        assert updated.display_name == "爸爸Pro"
        # stale (telegram, 111) must NOT resolve any more (rule 7 — no zombie mappings)
        assert reg.lookup("telegram", "111") is None or reg.lookup("telegram", "111").tenant_id != "dad"
        # new (telegram, 222) must resolve to dad
        assert reg.lookup("telegram", "222").tenant_id == "dad"

    def test_update_tenant_unknown_raises_key_error(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, TenantsConfig())
        with pytest.raises(KeyError, match="Unknown tenant"):
            reg.update_tenant("ghost", _tc("ghost"))

    def test_update_tenant_canonicalises_to_path_id(self, tmp_path: Path) -> None:
        """PATCH /api/tenants/{id} keeps the path id as source of truth."""
        reg = TenantRegistry(tmp_path, TenantsConfig())
        reg.add_tenant(_tc("dad"))
        # Body says tenant_id="mom" but path says "dad" — implementation must
        # treat path id as authoritative so the registry doesn't fork.
        mismatched = _tc("mom", display_name="妈妈")
        updated = reg.update_tenant("dad", mismatched)
        assert updated.tenant_id == "dad"
        assert updated.display_name == "妈妈"
        assert reg.get("dad") is not None
        assert reg.get("mom") is None


class TestDeleteTenant:
    def test_delete_tenant_removes_from_lookup(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, TenantsConfig())
        reg.add_tenant(_tc("dad"))
        assert reg.delete_tenant("dad") is True
        assert reg.get("dad") is None

    def test_delete_tenant_with_bindings_cascades(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, TenantsConfig())
        reg.add_tenant(_tc("dad", bindings=[("telegram", "111", "手机")]))
        # Binding must be resolvable before delete.
        assert reg.lookup("telegram", "111").tenant_id == "dad"
        # Delete cascades: tenant + bindings + session file + workspace dir.
        assert reg.delete_tenant("dad") is True
        # Tenant gone from registry.
        assert reg.get("dad") is None
        # Binding mapping also cleared — lookup now falls back to guest (or None).
        resolved = reg.lookup("telegram", "111")
        assert resolved is None or resolved.tenant_id != "dad"
        # Session file cascade-removed.
        assert not (tmp_path / "sessions" / "tenant_dad.jsonl").exists()
        # Workspace directory cascade-removed.
        assert not (tmp_path / "tenants" / "dad").exists()

    def test_delete_guest_rejected(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, TenantsConfig(guest_tenant_enabled=True))
        with pytest.raises(ValueError, match="guest"):
            reg.delete_tenant("guest")

    def test_delete_unknown_raises_key_error(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, TenantsConfig())
        with pytest.raises(KeyError, match="Unknown tenant"):
            reg.delete_tenant("ghost")


class TestToConfigRoundtrip:
    def test_roundtrip_preserves_tenants_and_bindings(self, tmp_path: Path) -> None:
        cfg = TenantsConfig(
            tenants=[
                _tc("dad", "爸爸", [("telegram", "111", "手机")]),
                _tc("mom", "妈妈", [("websocket", "222", "手表")]),
            ],
            default_tenant_id="dad",
            guest_tenant_enabled=True,
        )
        reg = TenantRegistry(tmp_path, cfg)
        out = reg.to_config()
        assert out.default_tenant_id == "dad"
        assert out.guest_tenant_enabled is True
        assert {t.tenant_id for t in out.tenants} == {"dad", "mom"}
        # bindings survive the round-trip
        dad = next(t for t in out.tenants if t.tenant_id == "dad")
        assert dad.bindings[0].channel == "telegram"
        assert dad.bindings[0].sender_id == "111"

    def test_roundtrip_after_add_reflects_new_tenant(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, TenantsConfig())
        reg.add_tenant(_tc("dad", bindings=[("telegram", "111", "手机")]))
        out = reg.to_config()
        assert len(out.tenants) == 1
        assert out.tenants[0].tenant_id == "dad"
