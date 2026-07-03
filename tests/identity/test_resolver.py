from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from OriginAgent.identity.tenant import Tenant, TenantRegistry
from OriginAgent.identity.resolver import IdentityResolver
from OriginAgent.config.schema import TenantChannelBinding, TenantConfig, TenantsConfig


def _make_config() -> TenantsConfig:
    return TenantsConfig(
        tenants=[
            TenantConfig(tenant_id="dad", display_name="爸爸", bindings=[
                TenantChannelBinding(channel="telegram", sender_id="tg-1", label="手机"),
            ]),
            TenantConfig(tenant_id="mom", display_name="妈妈", bindings=[
                TenantChannelBinding(channel="websocket", sender_id="ws-1", label="手表"),
            ]),
        ],
        guest_tenant_enabled=True,
    )


class TestTenantRegistry:
    def test_lookup_by_channel_sender(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        t = reg.lookup("telegram", "tg-1")
        assert t is not None and t.tenant_id == "dad"

    def test_unknown_sender_returns_guest(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        t = reg.lookup("telegram", "stranger")
        assert t is not None and t.tenant_id == "guest"
        assert t.bdi_enabled is False

    def test_register_binding_updates_registry(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        reg.register_binding("telegram", "new-device", "mom")
        t = reg.lookup("telegram", "new-device")
        assert t is not None and t.tenant_id == "mom"

    def test_multi_tenant_isolation(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        assert reg.get("dad").workspace_dir.name == "dad"
        assert reg.get("mom").workspace_dir.name == "mom"
        assert reg.get("dad").workspace_dir != reg.get("mom").workspace_dir

    def test_empty_config_no_tenants_no_guest(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, TenantsConfig(tenants=[], guest_tenant_enabled=False))
        assert len(reg.all()) == 0

    def test_register_binding_unknown_tenant_raises(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        with pytest.raises(KeyError):
            reg.register_binding("telegram", "x", "nobody")


class TestIdentityResolver:
    def test_resolve_known_sender(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        resolver = IdentityResolver(reg)
        t = resolver.resolve("telegram", "tg-1")
        assert t.tenant_id == "dad"

    def test_resolve_falls_back_to_guest(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        resolver = IdentityResolver(reg)
        t = resolver.resolve("discord", "stranger-999")
        assert t.tenant_id == "guest"

    def test_speaker_plugin_called_when_sample_provided(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        mock_plugin = MagicMock()
        mock_recognize = MagicMock()
        mock_recognize.recognize = MagicMock(return_value=None)
        mock_plugin.recognize = mock_recognize.recognize

        resolver = IdentityResolver(reg, speaker_plugin=mock_plugin, speaker_threshold=0.7)
        resolver.resolve("mic_array", "", speaker_sample=b"audio-data")
        mock_recognize.recognize.assert_called_once_with(b"audio-data")

    def test_speaker_plugin_below_threshold_falls_back(self, tmp_path: Path) -> None:
        from OriginAgent.identity.speaker_plugin import RecognitionResult

        reg = TenantRegistry(tmp_path, _make_config())
        mock_plugin = MagicMock()
        mock_plugin.recognize = MagicMock(return_value=RecognitionResult(
            tenant_id="dad", confidence=0.3, method="vocalprint", metadata={}
        ))
        resolver = IdentityResolver(reg, speaker_plugin=mock_plugin, speaker_threshold=0.7)
        t = resolver.resolve("mic_array", "", speaker_sample=b"audio")
        assert t.tenant_id == "guest"  # below threshold → guest
