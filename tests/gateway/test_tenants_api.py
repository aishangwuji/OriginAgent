"""REST API tests for /api/tenants CRUD.

Mirrors the test pattern in tests/gateway/test_settings_update.py —
isolates config via monkeypatch on _current_config_path and asserts the
on-disk config reflects each mutation.

Note: the WebUI is served over the WebSocket handshake port, which only
accepts GET. Write operations therefore pass their payload through the
?config=<json> query parameter (same pattern as /api/settings/mcp/upsert).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import quote

from OriginAgent.channels.websocket import WebSocketChannel
from OriginAgent.config.loader import get_config_path, load_config, save_config
from OriginAgent.config.schema import Config, TenantChannelBinding, TenantConfig, TenantsConfig


def _make_channel(tmp_path: Path, monkeypatch) -> WebSocketChannel:
    """WebSocketChannel wired to an isolated config file (no live registry).

    The _tenant_registry_provider is intentionally NOT set so the handler
    exercises the reconstruction fallback: each request loads config from
    disk, builds a TenantRegistry from gateway.tenants, mutates it, and
    persists via save_config. This is the contract the test asserts.
    """
    config_path = tmp_path / "config.json"
    config = Config()
    save_config(config, config_path)
    monkeypatch.setattr("OriginAgent.config.loader._current_config_path", config_path)

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
    channel._gateway_auth._api_tokens["tok"] = time.monotonic() + 300
    return channel


def _request(path: str, config: dict | None = None) -> MagicMock:
    """Minimal duck-typed WsRequest.

    If ``config`` is provided, it is URL-encoded as ?config=<json> on the path,
    matching the GET-based write API used elsewhere (see
    _handle_settings_mcp_upsert).
    """
    req = MagicMock()
    final_path = path
    if config is not None:
        encoded = quote(json.dumps(config, ensure_ascii=False), safe="")
        final_path = f"{path}?config={encoded}"
    req.path = final_path
    req.headers = {"Authorization": "Bearer tok"}
    return req


def _body(response: Any) -> dict:
    return json.loads(response.body.decode("utf-8"))


class TestListTenants:
    def test_list_tenants_empty(self, tmp_path: Path, monkeypatch) -> None:
        channel = _make_channel(tmp_path, monkeypatch)
        resp = channel._rest_api._handle_tenants_list(_request("/api/tenants"))
        assert resp.status_code == 200
        data = _body(resp)
        assert data["tenants"] == []
        # Default Config() enables guest tenant (matches schema default).
        assert data["guestTenantEnabled"] is True
        assert data["defaultTenantId"] == ""

    def test_list_tenants_with_data(self, tmp_path: Path, monkeypatch) -> None:
        channel = _make_channel(tmp_path, monkeypatch)
        # Seed config with one tenant.
        config = load_config(get_config_path())
        config.gateway.tenants = TenantsConfig(
            tenants=[
                TenantConfig(
                    tenant_id="dad",
                    display_name="爸爸",
                    bindings=[
                        TenantChannelBinding(channel="telegram", sender_id="12345", label="手机"),
                    ],
                    bdi_enabled=True,
                    claimable_by_pairing=False,
                    permissions={"exec": False, "write_files": False, "device_control": True},
                ),
            ],
            default_tenant_id="dad",
            guest_tenant_enabled=True,
        )
        save_config(config, get_config_path())

        resp = channel._rest_api._handle_tenants_list(_request("/api/tenants"))
        assert resp.status_code == 200
        data = _body(resp)
        assert len(data["tenants"]) == 1
        t = data["tenants"][0]
        assert t["tenantId"] == "dad"
        assert t["displayName"] == "爸爸"
        assert t["bindings"][0] == {
            "channel": "telegram", "senderId": "12345", "label": "手机",
        }
        assert t["bdiEnabled"] is True
        assert t["claimableByPairing"] is False
        assert t["permissions"]["deviceControl"] is True
        assert data["defaultTenantId"] == "dad"


class TestCreateTenant:
    def test_create_tenant_success(self, tmp_path: Path, monkeypatch) -> None:
        channel = _make_channel(tmp_path, monkeypatch)
        config = {
            "tenant_id": "dad",
            "display_name": "爸爸",
            "bindings": [{"channel": "telegram", "sender_id": "12345", "label": "手机"}],
            "bdi_enabled": True,
            "claimable_by_pairing": False,
        }
        resp = channel._rest_api._handle_tenants_create(
            _request("/api/tenants/create", config=config)
        )
        assert resp.status_code == 201
        # Persisted: reload config from disk and check the tenant is there.
        cfg = load_config(get_config_path())
        assert len(cfg.gateway.tenants.tenants) == 1
        assert cfg.gateway.tenants.tenants[0].tenant_id == "dad"
        # No .tmp file left behind (rule 19).
        assert not (tmp_path / "config.json.tmp").exists()

    def test_create_tenant_duplicate_409(self, tmp_path: Path, monkeypatch) -> None:
        channel = _make_channel(tmp_path, monkeypatch)
        config = {"tenant_id": "dad", "display_name": "爸爸"}
        # First create succeeds.
        resp1 = channel._rest_api._handle_tenants_create(
            _request("/api/tenants/create", config=config)
        )
        assert resp1.status_code == 201
        # Second create of the same id must be 409 (rule 12 — idempotent reject).
        resp2 = channel._rest_api._handle_tenants_create(
            _request("/api/tenants/create", config=config)
        )
        assert resp2.status_code == 409
        assert b"already exists" in resp2.body

    def test_create_tenant_no_config_400(self, tmp_path: Path, monkeypatch) -> None:
        channel = _make_channel(tmp_path, monkeypatch)
        resp = channel._rest_api._handle_tenants_create(
            _request("/api/tenants/create")  # no ?config=
        )
        assert resp.status_code == 400
        assert b"config is required" in resp.body


class TestUpdateTenant:
    def test_update_tenant_success(self, tmp_path: Path, monkeypatch) -> None:
        channel = _make_channel(tmp_path, monkeypatch)
        # Seed with one tenant.
        channel._rest_api._handle_tenants_create(
            _request("/api/tenants/create", config={
                "tenant_id": "dad", "display_name": "爸爸",
            })
        )
        # Update with new display_name and a binding.
        update_config = {
            "tenant_id": "dad",
            "display_name": "爸爸Pro",
            "bindings": [{"channel": "telegram", "sender_id": "999", "label": "新手机"}],
        }
        path_id = quote("dad", safe="")
        resp2 = channel._rest_api._handle_tenants_update(
            _request(f"/api/tenants/{path_id}/update", config=update_config),
            "dad",
        )
        assert resp2.status_code == 200
        # Reload config: the tenant's display_name must be updated.
        cfg = load_config(get_config_path())
        dad = cfg.gateway.tenants.tenants[0]
        assert dad.display_name == "爸爸Pro"
        assert dad.bindings[0].sender_id == "999"

    def test_update_tenant_not_found_404(self, tmp_path: Path, monkeypatch) -> None:
        channel = _make_channel(tmp_path, monkeypatch)
        path_id = quote("ghost", safe="")
        resp = channel._rest_api._handle_tenants_update(
            _request(f"/api/tenants/{path_id}/update",
                     config={"tenant_id": "ghost", "display_name": "幽灵"}),
            "ghost",
        )
        assert resp.status_code == 404


class TestDeleteTenant:
    def test_delete_tenant_success(self, tmp_path: Path, monkeypatch) -> None:
        channel = _make_channel(tmp_path, monkeypatch)
        channel._rest_api._handle_tenants_create(
            _request("/api/tenants/create", config={
                "tenant_id": "dad", "display_name": "爸爸",
            })
        )
        path_id = quote("dad", safe="")
        resp = channel._rest_api._handle_tenants_delete(
            _request(f"/api/tenants/{path_id}/delete"),
            "dad",
        )
        assert resp.status_code == 200
        # Persisted: tenant must be gone from config.
        cfg = load_config(get_config_path())
        assert len(cfg.gateway.tenants.tenants) == 0

    def test_delete_tenant_with_bindings_cascades(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        channel = _make_channel(tmp_path, monkeypatch)
        # Create a tenant WITH a binding — delete must cascade (unbind,
        # remove session file, remove workspace dir) rather than be refused.
        channel._rest_api._handle_tenants_create(
            _request("/api/tenants/create", config={
                "tenant_id": "dad",
                "display_name": "爸爸",
                "bindings": [
                    {"channel": "telegram", "sender_id": "12345", "label": "手机"}
                ],
            })
        )
        # Simulate the runtime creating the tenant's session file + workspace
        # so we can assert they get cascaded away.
        workspace_root = channel._rest_api._load_config().workspace_path
        session_file = workspace_root / "sessions" / "tenant_dad.jsonl"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text("{}", encoding="utf-8")
        workspace_dir = workspace_root / "tenants" / "dad"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "memory.json").write_text("{}", encoding="utf-8")

        path_id = quote("dad", safe="")
        resp = channel._rest_api._handle_tenants_delete(
            _request(f"/api/tenants/{path_id}/delete"),
            "dad",
        )
        assert resp.status_code == 200
        # Cascade: tenant + bindings removed from config.
        cfg = load_config(get_config_path())
        assert not any(t.tenant_id == "dad" for t in cfg.gateway.tenants.tenants)
        # Cascade: session file removed.
        assert not session_file.exists()
        # Cascade: workspace directory removed.
        assert not workspace_dir.exists()

    def test_delete_guest_rejected(self, tmp_path: Path, monkeypatch) -> None:
        channel = _make_channel(tmp_path, monkeypatch)
        path_id = quote("guest", safe="")
        resp = channel._rest_api._handle_tenants_delete(
            _request(f"/api/tenants/{path_id}/delete"),
            "guest",
        )
        assert resp.status_code == 400
        assert b"guest" in resp.body

    def test_delete_unknown_404(self, tmp_path: Path, monkeypatch) -> None:
        channel = _make_channel(tmp_path, monkeypatch)
        path_id = quote("ghost", safe="")
        resp = channel._rest_api._handle_tenants_delete(
            _request(f"/api/tenants/{path_id}/delete"),
            "ghost",
        )
        assert resp.status_code == 404


class TestAuth:
    def test_unauthorized_without_token(self, tmp_path: Path, monkeypatch) -> None:
        channel = _make_channel(tmp_path, monkeypatch)
        req = MagicMock()
        req.path = "/api/tenants"
        req.headers = {}  # no Authorization header
        resp = channel._rest_api._handle_tenants_list(req)
        assert resp.status_code == 401
