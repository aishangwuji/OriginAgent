from OpenHome.agent.device_factory import build_device_action_executor
from OpenHome.agent.device_actions import TypedDeviceAction
from OpenHome.config.schema import DeviceToolsConfig


def test_default_device_config_returns_none(tmp_path):
    assert build_device_action_executor(workspace=tmp_path, config=DeviceToolsConfig()) is None


def test_lighting_dry_run_returns_executor(tmp_path):
    executor = build_device_action_executor(
        workspace=tmp_path,
        config=DeviceToolsConfig(enabled=True, lighting_enabled=True, backend="fake"),
    )

    assert executor is not None


def test_real_mode_without_lighting_client_returns_none(tmp_path):
    executor = build_device_action_executor(
        workspace=tmp_path,
        config=DeviceToolsConfig(
            enabled=True,
            lighting_enabled=True,
            mode="real",
            backend="fake",
        ),
    )

    assert executor is None


def test_factory_executor_runs_lighting_dry_run(tmp_path):
    executor = build_device_action_executor(
        workspace=tmp_path,
        config=DeviceToolsConfig(enabled=True, lighting_enabled=True, backend="fake"),
    )
    assert executor is not None

    result = executor.submit_typed(
        TypedDeviceAction(
            action_type="set_light_power",
            domain="lighting",
            device_id="lamp",
            parameters={"power": "on"},
            requested_by="guest",
            trigger="user_initiated",
        )
    )

    assert result.status == "dry_run"
    assert result.backend_called is True

