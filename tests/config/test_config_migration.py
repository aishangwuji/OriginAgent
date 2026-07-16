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


def test_robot_g1_config_defaults_disabled_and_parses_aliases(tmp_path) -> None:
    default_config_path = tmp_path / "default-config.json"
    default_config_path.write_text("{}", encoding="utf-8")

    default_config = load_config(default_config_path)

    assert default_config.agents.defaults.robot_g1.enabled is False
    assert default_config.agents.defaults.robot_g1.mcp_endpoint is None
    assert default_config.agents.defaults.robot_g1.tool_timeout_seconds == 30
    assert default_config.agents.defaults.robot_g1.perception_enabled is False
    assert default_config.agents.defaults.robot_g1.perception_mode == "disabled"

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "robotG1": {
                            "enabled": True,
                            "mcpEndpoint": "http://192.168.123.164:18791/sse",
                            "toolTimeoutSeconds": 45,
                            "perceptionEnabled": True,
                            "perceptionMode": "on_demand",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.agents.defaults.robot_g1.enabled is True
    assert config.agents.defaults.robot_g1.mcp_endpoint == "http://192.168.123.164:18791/sse"
    assert config.agents.defaults.robot_g1.tool_timeout_seconds == 45
    assert config.agents.defaults.robot_g1.perception_enabled is True
    assert config.agents.defaults.robot_g1.perception_mode == "on_demand"


def test_local_awareness_config_defaults_disabled_and_parses_aliases(tmp_path) -> None:
    default_config_path = tmp_path / "default-config.json"
    default_config_path.write_text("{}", encoding="utf-8")

    default_config = load_config(default_config_path)

    assert default_config.tools.local_awareness.enabled is True
    assert default_config.tools.local_awareness.device_discovery_enabled is True
    assert default_config.tools.local_awareness.lan_discovery_enabled is True
    assert default_config.tools.local_awareness.camera.enabled is False
    assert default_config.tools.local_awareness.screen.enabled is False
    assert default_config.tools.local_awareness.audio.input_enabled is False
    assert default_config.tools.local_awareness.audio.output_enabled is False
    assert default_config.tools.local_awareness.media_inspection.enabled is True

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "tools": {
                    "localAwareness": {
                        "enabled": True,
                        "lanDiscoveryEnabled": True,
                        "camera": {
                            "enabled": True,
                            "requireConfirmation": False,
                            "saveDir": "uploads/local",
                            "deviceId": "camera-1",
                        },
                        "screen": {
                            "enabled": True,
                            "screenId": "main",
                        },
                        "audio": {
                            "inputEnabled": True,
                            "outputEnabled": True,
                            "maxRecordSeconds": 7,
                            "voice": "local",
                        },
                        "mediaInspection": {
                            "enabled": False,
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    local_awareness = config.tools.local_awareness

    assert local_awareness.enabled is True
    assert local_awareness.lan_discovery_enabled is True
    assert local_awareness.camera.enabled is True
    assert local_awareness.camera.require_confirmation is False
    assert local_awareness.camera.save_dir == "uploads/local"
    assert local_awareness.camera.device_id == "camera-1"
    assert local_awareness.screen.enabled is True
    assert local_awareness.screen.screen_id == "main"
    assert local_awareness.audio.input_enabled is True
    assert local_awareness.audio.output_enabled is True
    assert local_awareness.audio.max_record_seconds == 7
    assert local_awareness.audio.voice == "local"
    assert local_awareness.media_inspection.enabled is False


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


# ── config_version mechanism (spec 3.15, rule 2) ──────────────────────────────


def test_migrate_config_adds_config_version_to_unversioned_dict() -> None:
    """An unversioned (v0) config dict must gain config_version=1 after migration."""
    from OriginAgent.config.loader import _migrate_config

    result = _migrate_config({"tools": {}})
    assert result["config_version"] == 1


def test_migrate_config_v0_migrates_legacy_my_tool_keys() -> None:
    """v0 configs must still run the myEnabled/mySet → my.{enable,allowSet} migration."""
    from OriginAgent.config.loader import _migrate_config

    result = _migrate_config({"tools": {"myEnabled": False, "mySet": True}})
    assert result["config_version"] == 1
    assert result["tools"]["my"] == {"enable": False, "allowSet": True}
    assert "myEnabled" not in result["tools"]
    assert "mySet" not in result["tools"]


def test_migrate_config_v1_does_not_re_run_migrations() -> None:
    """A config already at config_version=1 must not be re-migrated (rule 2).

    The migration chain only runs forward from the declared version. If a user
    explicitly writes a v1 config that still contains legacy keys, those keys
    are left untouched — v1 means 'already shaped by v0→v1'. This preserves
    version semantics so re-saving an already-migrated config is a no-op.
    """
    from OriginAgent.config.loader import _migrate_config

    result = _migrate_config(
        {"config_version": 1, "tools": {"myEnabled": False, "mySet": True}}
    )
    assert result["config_version"] == 1
    assert "myEnabled" in result["tools"]
    assert "mySet" in result["tools"]
    assert "my" not in result["tools"]


def test_default_config_has_config_version_1() -> None:
    """A freshly constructed Config must declare config_version=1 (rule 2)."""
    from OriginAgent.config.schema import Config

    assert Config().config_version == 1


def test_load_config_backfills_config_version_for_legacy_file(tmp_path) -> None:
    """Loading a legacy (unversioned) config file must yield config_version=1."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"tools": {"myEnabled": False}}),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.config_version == 1
    assert config.tools.my.enable is False

