from pathlib import Path
from unittest.mock import MagicMock

from OpenHome.agent.action_runtime import ActionExecutionResult
from OpenHome.agent.loop import AgentLoop
from OpenHome.bus.queue import MessageBus
from OpenHome.config.schema import Config, DeviceToolsConfig


class _FakeExecutor:
    def submit_typed(self, action):
        return ActionExecutionResult(
            status="dry_run",
            action_id="action_1",
            reason="ok",
            backend_called=True,
            permission_status="allow",
        )


def _config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    return cfg


def _provider():
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return provider


def _lighting_names(loop: AgentLoop) -> list[str]:
    return [name for name in loop.tools.tool_names if name.startswith("openhome_device_lighting_")]


def test_from_config_default_does_not_register_device_tools(tmp_path):
    loop = AgentLoop.from_config(_config(tmp_path), bus=MessageBus(), provider=_provider())

    assert _lighting_names(loop) == []


def test_from_config_dry_run_registers_exactly_three_lighting_tools(tmp_path):
    cfg = _config(tmp_path)
    cfg.tools.device = DeviceToolsConfig(
        enabled=True,
        lighting_enabled=True,
        mode="dry_run",
        backend="fake",
    )

    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())

    assert _lighting_names(loop) == [
        "openhome_device_lighting_set_power",
        "openhome_device_lighting_set_brightness",
        "openhome_device_lighting_set_color_temperature",
    ]


def test_from_config_explicit_executor_takes_precedence(tmp_path):
    cfg = _config(tmp_path)
    cfg.tools.device = DeviceToolsConfig(enabled=False)

    loop = AgentLoop.from_config(
        cfg,
        bus=MessageBus(),
        provider=_provider(),
        device_action_executor=_FakeExecutor(),
    )

    assert len(_lighting_names(loop)) == 3


def test_from_config_real_mode_lighting_client_does_not_register_tools(tmp_path):
    cfg = _config(tmp_path)
    cfg.tools.device = DeviceToolsConfig(
        enabled=True,
        lighting_enabled=True,
        mode="real",
        backend="lighting_client",
    )

    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())

    assert "openhome_device_lighting_set_power" not in loop.tools.tool_names
    assert _lighting_names(loop) == []


def test_from_config_real_mode_with_registry_still_does_not_register_tools(tmp_path):
    cfg = _config(tmp_path)
    cfg.tools.device = DeviceToolsConfig(
        enabled=True,
        lighting_enabled=True,
        mode="real",
        backend="lighting_client",
    )

    loop = AgentLoop.from_config(
        cfg,
        bus=MessageBus(),
        provider=_provider(),
        device_registry=object(),
    )

    assert "openhome_device_lighting_set_power" not in loop.tools.tool_names
    assert _lighting_names(loop) == []
