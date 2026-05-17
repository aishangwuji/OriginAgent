from __future__ import annotations

import json

from OpenHome.agent.tools.filesystem import ReadFileTool
from OpenHome.agent.tools.base import Tool
from OpenHome.agent.tools.registry import ToolRegistry
from OpenHome.config.loader import load_config
from OpenHome.config.profiles import apply_runtime_profile, build_runtime_profile_defaults
from OpenHome.config.schema import Config, DeviceToolsConfig, ExecToolConfig, ToolAuditConfig


class _NamedTool(Tool):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(self) -> str:
        return "ok"


def test_default_runtime_profile_preserves_current_defaults() -> None:
    cfg = Config()

    assert cfg.runtime.profile == "default"
    assert apply_runtime_profile(cfg) == cfg


def test_profile_defaults_are_serializable_and_conservative() -> None:
    for profile in ("household_safe", "local_dev", "automation"):
        cfg = build_runtime_profile_defaults(profile)  # type: ignore[arg-type]
        data = cfg.model_dump(mode="json", by_alias=True)

        assert data["runtime"]["profile"] == profile
        json.dumps(data)
        assert cfg.tools.audit.mode == "minimal"
        assert cfg.tools.device.enabled is False
        assert cfg.tools.device.mode == "dry_run"
        assert cfg.tools.exec.allow_unsafe_exec is False


def test_local_dev_profile_does_not_default_to_unsafe_exec() -> None:
    cfg = build_runtime_profile_defaults("local_dev")

    assert cfg.tools.exec.profile == "local_dev"
    assert cfg.tools.exec.allow_unsafe_exec is False


def test_automation_profile_does_not_default_to_high_power_grants() -> None:
    cfg = build_runtime_profile_defaults("automation")

    assert cfg.tools.exec.profile == "secure"
    assert cfg.tools.exec.allow_unsafe_exec is False
    assert cfg.tools.device.enabled is False


def test_profile_application_does_not_override_explicit_values() -> None:
    cfg = Config()
    cfg.runtime.profile = "local_dev"
    cfg.tools.exec = ExecToolConfig(profile="disabled")
    cfg.tools.audit = ToolAuditConfig(mode="off")
    cfg.tools.device = DeviceToolsConfig(enabled=True, lighting_enabled=True, backend="fake")

    applied = apply_runtime_profile(cfg)

    assert applied.tools.exec.profile == "disabled"
    assert applied.tools.audit.mode == "off"
    assert applied.tools.device.enabled is True
    assert applied.tools.device.backend == "fake"


def test_loader_applies_profile_defaults_without_unsafe_exec(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"runtime": {"profile": "local_dev"}}),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg.runtime.profile == "local_dev"
    assert cfg.tools.exec.profile == "local_dev"
    assert cfg.tools.exec.allow_unsafe_exec is False


def test_agent_loop_from_config_applies_profile_defaults(tmp_path) -> None:
    from unittest.mock import MagicMock

    from OpenHome.agent.loop import AgentLoop
    from OpenHome.bus.queue import MessageBus

    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    cfg.runtime.profile = "local_dev"
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096

    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=provider)

    assert loop.exec_config.profile == "local_dev"
    assert loop.exec_config.allow_unsafe_exec is False


def test_profile_does_not_bypass_capability_gate() -> None:
    cfg = apply_runtime_profile(Config(runtime={"profile": "local_dev"}))  # type: ignore[arg-type]
    registry = ToolRegistry()
    registry.register(_NamedTool("exec"))

    assert cfg.tools.exec.profile == "local_dev"
    _, _, error = registry.prepare_call("exec", {})
    assert error is not None
    assert "capability snapshot" in error.lower()


async def test_profile_does_not_bypass_protected_path_policy(tmp_path) -> None:
    cfg = apply_runtime_profile(Config(runtime={"profile": "local_dev"}))  # type: ignore[arg-type]
    protected = tmp_path / "memory" / "security" / "capability_grants.json"
    protected.parent.mkdir(parents=True)
    protected.write_text("{}", encoding="utf-8")

    assert cfg.tools.exec.allow_unsafe_exec is False
    result = await ReadFileTool(workspace=tmp_path).execute(str(protected))
    assert "protected runtime state" in result
