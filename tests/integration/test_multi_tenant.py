"""Multi-tenant integration tests — one agent, multiple family members."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OriginAgent.identity.tenant import Tenant, TenantRegistry
from OriginAgent.identity.resolver import IdentityResolver
from OriginAgent.config.schema import TenantChannelBinding, TenantConfig, TenantsConfig


def _make_tenants_config() -> TenantsConfig:
    return TenantsConfig(
        tenants=[
            TenantConfig(
                tenant_id="dad",
                display_name="爸爸",
                bindings=[TenantChannelBinding(
                    channel="telegram", sender_id="tg-dad-001", label="手机"
                )],
                bdi_enabled=True,
                permissions={"exec": True, "device_control": True},
            ),
            TenantConfig(
                tenant_id="mom",
                display_name="妈妈",
                bindings=[TenantChannelBinding(
                    channel="websocket", sender_id="ws-mom-001", label="手机App"
                )],
                bdi_enabled=True,
                permissions={"exec": False, "device_control": True},
            ),
        ],
        guest_tenant_enabled=True,
    )


class TestTenantResolution:
    def test_dad_on_telegram_resolves_to_dad(self, tmp_path: Path) -> None:
        registry = TenantRegistry(tmp_path, _make_tenants_config())
        resolver = IdentityResolver(registry)
        tenant = resolver.resolve("telegram", "tg-dad-001")
        assert tenant.tenant_id == "dad"
        assert tenant.display_name == "爸爸"
        assert tenant.unified_session_key == "tenant:dad"

    def test_mom_on_websocket_resolves_to_mom(self, tmp_path: Path) -> None:
        registry = TenantRegistry(tmp_path, _make_tenants_config())
        resolver = IdentityResolver(registry)
        tenant = resolver.resolve("websocket", "ws-mom-001")
        assert tenant.tenant_id == "mom"
        assert tenant.unified_session_key == "tenant:mom"

    def test_unknown_sender_gets_guest(self, tmp_path: Path) -> None:
        registry = TenantRegistry(tmp_path, _make_tenants_config())
        resolver = IdentityResolver(registry)
        tenant = resolver.resolve("telegram", "unknown-stranger")
        assert tenant.tenant_id == "guest"
        assert tenant.bdi_enabled is False

    def test_two_tenants_have_different_workspaces(self, tmp_path: Path) -> None:
        registry = TenantRegistry(tmp_path, _make_tenants_config())
        dad = registry.get("dad")
        mom = registry.get("mom")
        assert dad is not None and mom is not None
        assert dad.workspace_dir != mom.workspace_dir
        assert dad.workspace_dir.name == "dad"
        assert mom.workspace_dir.name == "mom"


class TestTenantIsolation:
    def test_same_tenant_two_channels_same_session_key(self, tmp_path: Path) -> None:
        """Dad on Telegram AND WebUI = same unified session."""
        registry = TenantRegistry(tmp_path, _make_tenants_config())
        # Register a second channel for dad
        registry.register_binding("websocket", "ws-dad-webui", "dad")
        dad1 = registry.lookup("telegram", "tg-dad-001")
        dad2 = registry.lookup("websocket", "ws-dad-webui")
        assert dad1 is not None and dad2 is not None
        assert dad1.tenant_id == dad2.tenant_id == "dad"
        assert dad1.unified_session_key == dad2.unified_session_key

    def test_claim_pairing_binds_sender_to_tenant(self, tmp_path: Path) -> None:
        registry = TenantRegistry(tmp_path, _make_tenants_config())
        # Simulate: unknown sender paired, then says "I'm mom"
        tenant = registry.claim_pairing("websocket", "ws-new-device", "mom")
        assert tenant.tenant_id == "mom"
        # Now lookup works
        found = registry.lookup("websocket", "ws-new-device")
        assert found is not None and found.tenant_id == "mom"
