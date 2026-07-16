"""Tests for settings_update env-var resolution and requires_restart flags.

Task B7 — fixes the opaque effect path when users enter ``${VAR}`` references
via the REST settings surface. These tests assert the contract BEFORE the
implementation is expected to satisfy it (rule 34: verification first).
"""

import json
import time
from unittest.mock import MagicMock
from urllib.parse import quote

from OriginAgent.channels.websocket import WebSocketChannel
from OriginAgent.config.loader import get_config_path, load_config, save_config
from OriginAgent.config.schema import Config


def _make_channel(tmp_path, monkeypatch) -> WebSocketChannel:
    """Build a WebSocketChannel wired to an isolated config file."""
    config_path = tmp_path / "config.json"
    config = Config()
    config.providers.openai.api_key = "existing-key"
    save_config(config, config_path)
    monkeypatch.setattr("OriginAgent.config.loader._current_config_path", config_path)

    bus = MagicMock()
    bus.publish_inbound = MagicMock()
    channel = WebSocketChannel(
        {
            "enabled": True,
            "allowFrom": ["*"],
            "host": "127.0.0.1",
            "port": 29900,
            "path": "/ws",
            "websocketRequiresToken": False,
        },
        bus,
    )
    channel._gateway_auth._api_tokens["tok"] = time.monotonic() + 300
    return channel


def _request(path: str) -> MagicMock:
    """Minimal duck-typed request: check_api_token reads headers + path."""
    req = MagicMock()
    req.path = path
    req.headers = {"Authorization": "Bearer tok"}
    return req


def _body(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


class TestSettingsUpdateEnvResolution:
    """B7.1: ``${VAR}`` references entered via settings_update are resolved."""

    def test_env_var_ref_accepted_when_set_and_template_preserved_on_disk(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("MY_KEY", "real-secret")
        channel = _make_channel(tmp_path, monkeypatch)

        api_key_ref = quote("${MY_KEY}", safe="")
        response = channel._rest_api._handle_settings_provider_update(
            _request(
                f"/api/settings/provider/update?provider=openai&api_key={api_key_ref}"
            )
        )

        assert response.status_code == 200
        # The ${VAR} template must be preserved on disk — never the resolved
        # secret (rule 18 + test_save_preserves_templates). The env ref is
        # validated at request time so users get immediate feedback.
        assert load_config(get_config_path()).providers.openai.api_key == "${MY_KEY}"

    def test_env_var_ref_rejected_when_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MY_KEY", raising=False)
        channel = _make_channel(tmp_path, monkeypatch)

        api_key_ref = quote("${MY_KEY}", safe="")
        response = channel._rest_api._handle_settings_provider_update(
            _request(
                f"/api/settings/provider/update?provider=openai&api_key={api_key_ref}"
            )
        )

        assert response.status_code == 400
        assert b"MY_KEY" in response.body


class TestSettingsUpdateRequiresRestart:
    """B7.2: items that cannot take effect at runtime flag requires_restart."""

    def test_provider_config_requires_restart(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_KEY", "real-secret")
        channel = _make_channel(tmp_path, monkeypatch)

        api_key_ref = quote("${MY_KEY}", safe="")
        response = channel._rest_api._handle_settings_provider_update(
            _request(
                f"/api/settings/provider/update?provider=openai&api_key={api_key_ref}"
            )
        )

        assert response.status_code == 200
        assert _body(response)["requires_restart"] is True

    def test_runtime_toggle_does_not_require_restart(self, tmp_path, monkeypatch):
        channel = _make_channel(tmp_path, monkeypatch)

        response = channel._rest_api._handle_settings_learning_background_review_update(
            _request("/api/settings/learning/background-review/update?enabled=true")
        )

        assert response.status_code == 200
        assert _body(response)["requires_restart"] is False
