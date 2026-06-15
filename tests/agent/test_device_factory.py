from OriginAgent.domain_packs.smart_home.runtime.device_factory import build_device_action_executor
from OriginAgent.domain_packs.smart_home.runtime.device_actions import TypedDeviceAction
from OriginAgent.config.schema import DeviceToolsConfig


def test_default_device_config_returns_none(tmp_path):
    assert build_device_action_executor(workspace=tmp_path, config=DeviceToolsConfig()) is None


def test_device_config_defaults_are_fail_closed():
    config = DeviceToolsConfig()

    assert config.enabled is False
    assert config.lighting_enabled is False
    assert config.mode == "dry_run"
    assert config.backend == "none"


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


def test_real_mode_with_lighting_client_returns_none(tmp_path):
    executor = build_device_action_executor(
        workspace=tmp_path,
        config=DeviceToolsConfig(
            enabled=True,
            lighting_enabled=True,
            mode="real",
            backend="lighting_client",
        ),
    )

    assert executor is None


def test_real_mode_with_lighting_client_and_full_allowlist_returns_executor(tmp_path):
    executor = build_device_action_executor(
        workspace=tmp_path,
        config=DeviceToolsConfig(
            enabled=True,
            lighting_enabled=True,
            mode="real",
            backend="lighting_client",
            real_execution_enabled=True,
            lighting_client_endpoint="http://127.0.0.1:18080",
        ),
    )

    assert executor is not None


def test_invalid_backend_returns_none(tmp_path):
    config = DeviceToolsConfig.model_construct(
        enabled=True,
        lighting_enabled=True,
        mode="dry_run",
        backend="invalid",
    )

    assert build_device_action_executor(workspace=tmp_path, config=config) is None


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
