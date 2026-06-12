import json
import socket
from unittest.mock import patch

from OriginAgent.config.loader import load_config, save_config
from OriginAgent.security.network import validate_url_target


def _fake_resolve(host: str, results: list[str]):
    """Return a getaddrinfo mock that maps the given host to fake IP results."""
    def _resolver(hostname, port, family=0, type_=0):
        if hostname == host:
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in results]
        raise socket.gaierror(f"cannot resolve {hostname}")
    return _resolver


def test_load_config_keeps_max_tokens_and_ignores_legacy_memory_window(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "maxTokens": 1234,
                        "memoryWindow": 42,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.agents.defaults.max_tokens == 1234
    assert config.agents.defaults.context_window_tokens == 65_536
    assert not hasattr(config.agents.defaults, "memory_window")


def test_save_config_writes_context_window_tokens_but_not_memory_window(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "maxTokens": 2222,
                        "memoryWindow": 30,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    save_config(config, config_path)
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    defaults = saved["agents"]["defaults"]

    assert defaults["maxTokens"] == 2222
    assert defaults["contextWindowTokens"] == 65_536
    assert "memoryWindow" not in defaults


def test_onboard_does_not_crash_with_legacy_memory_window(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "maxTokens": 3333,
                        "memoryWindow": 50,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("OriginAgent.config.loader.get_config_path", lambda: config_path)
    monkeypatch.setattr("OriginAgent.cli.commands.get_workspace_path", lambda _workspace=None: workspace)

    from typer.testing import CliRunner
    from OriginAgent.cli.commands import app
    runner = CliRunner()
    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0


def test_onboard_refresh_backfills_missing_channel_fields(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    config_path.write_text(
        json.dumps(
            {
                "channels": {
                    "qq": {
                        "enabled": False,
                        "appId": "",
                        "secret": "",
                        "allowFrom": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("OriginAgent.config.loader.get_config_path", lambda: config_path)
    monkeypatch.setattr("OriginAgent.cli.commands.get_workspace_path", lambda _workspace=None: workspace)
    monkeypatch.setattr(
        "OriginAgent.channels.registry.discover_all",
        lambda: {
            "qq": SimpleNamespace(
                default_config=lambda: {
                    "enabled": False,
                    "appId": "",
                    "secret": "",
                    "allowFrom": [],
                    "msgFormat": "plain",
                }
            )
        },
    )

    from typer.testing import CliRunner
    from OriginAgent.cli.commands import app
    runner = CliRunner()
    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["channels"]["qq"]["msgFormat"] == "plain"


def test_load_config_migrates_legacy_my_tool_keys(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "tools": {
                    "myEnabled": False,
                    "mySet": True,
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.tools.my.enable is False
    assert config.tools.my.allow_set is True


def test_load_config_parses_large_model_presets_without_changing_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "model": "primary",
                        "provider": "custom",
                    }
                },
                "providers": {
                    "custom": {"apiKey": "x"},
                },
                "modelPresets": {
                    "large-128k": {
                        "model": "fallback-model",
                        "provider": "custom",
                        "contextWindowTokens": 131072,
                    },
                    "large-200k": {
                        "model": "fallback-model",
                        "provider": "custom",
                        "contextWindowTokens": 200000,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.agents.defaults.context_window_tokens == 65_536
    assert config.model_presets["large-128k"].context_window_tokens == 131072
    assert config.model_presets["large-200k"].context_window_tokens == 200000


def test_save_config_rewrites_legacy_my_tool_keys(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "tools": {
                    "myEnabled": False,
                    "mySet": True,
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    save_config(config, config_path)
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    tools = saved["tools"]
    assert "myEnabled" not in tools
    assert "mySet" not in tools
    assert tools["my"] == {"enable": False, "allowSet": True}


def test_new_my_tool_keys_take_precedence_over_legacy(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "tools": {
                    "myEnabled": False,
                    "mySet": False,
                    "my": {"enable": True, "allowSet": True},
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.tools.my.enable is True
    assert config.tools.my.allow_set is True


def test_load_config_resets_ssrf_whitelist_when_next_config_is_empty(tmp_path) -> None:
    whitelisted = tmp_path / "whitelisted.json"
    whitelisted.write_text(
        json.dumps({"tools": {"ssrfWhitelist": ["100.64.0.0/10"]}}),
        encoding="utf-8",
    )
    defaulted = tmp_path / "defaulted.json"
    defaulted.write_text(json.dumps({}), encoding="utf-8")

    load_config(whitelisted)
    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve("ts.local", ["100.100.1.1"])):
        ok, err = validate_url_target("http://ts.local/api")
        assert ok, err

    load_config(defaulted)
    with patch("OriginAgent.security.network.socket.getaddrinfo", _fake_resolve("ts.local", ["100.100.1.1"])):
        ok, _ = validate_url_target("http://ts.local/api")
        assert not ok


def test_domain_packs_config_accepts_camel_case(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "domainPacks": {
                            "enabled": False,
                            "active": ["research"],
                            "disabled": ["smart_home"],
                            "maxCapabilityChars": 1234,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.agents.defaults.domain_packs.enabled is False
    assert config.agents.defaults.domain_packs.active == ["research"]
    assert config.agents.defaults.domain_packs.disabled == ["smart_home"]
    assert config.agents.defaults.domain_packs.max_capability_chars == 1234
    dumped = config.model_dump(mode="json", by_alias=True)
    assert dumped["agents"]["defaults"]["domainPacks"]["maxCapabilityChars"] == 1234
