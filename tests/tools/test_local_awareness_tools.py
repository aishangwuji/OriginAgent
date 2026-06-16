from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from OriginAgent.agent.local_awareness import (
    DeviceMap,
    DiscoveredDevice,
    LocalAwarenessBackend,
    normalize_local_awareness_summary,
)
from OriginAgent.agent.tools.local_awareness import (
    BindDeviceTool,
    CaptureCameraFrameTool,
    CaptureScreenTool,
    DiscoverLanDevicesTool,
    DiscoverLocalDevicesTool,
    InspectMediaTool,
    ListDeviceBindingsTool,
    RecordAudioSampleTool,
    RequestDevicePermissionTool,
    RevokeDeviceBindingTool,
    SpeakTextTool,
)
from OriginAgent.agent.identity import ActorResolver
from OriginAgent.agent.world_state import WorldStateManager
from OriginAgent.config.schema import ToolsConfig
from OriginAgent.session.manager import SessionManager


def _tools_config(**local_overrides):
    local = ToolsConfig.LocalAwarenessConfig(**local_overrides)
    return SimpleNamespace(local_awareness=local)


def _loop(config: ToolsConfig.LocalAwarenessConfig):
    backend = LocalAwarenessBackend()
    return SimpleNamespace(
        tools_config=SimpleNamespace(local_awareness=config),
        _local_awareness_backend=backend,
        _last_local_awareness_summary=normalize_local_awareness_summary(config, backend=backend),
    )


def _service(loop):
    return SimpleNamespace(_loop=loop)


def _loop_with_world(config: ToolsConfig.LocalAwarenessConfig, workspace: Path):
    loop = _loop(config)
    loop.sessions = SessionManager(workspace)
    loop.world_state = WorldStateManager(workspace, loop.sessions)
    return loop


def _request_context(session_key: str = "cli:direct"):
    runtime_context = ActorResolver().resolve_runtime_context(
        channel="cli",
        chat_id="direct",
        sender_id="user-1",
        metadata={"device_id": "device-a"},
        session_key=session_key,
    )
    return SimpleNamespace(session_key=session_key, runtime_context=runtime_context)


@pytest.mark.asyncio
async def test_discover_local_devices_fails_closed_when_disabled(tmp_path: Path) -> None:
    config = _tools_config(enabled=False)
    loop = _loop(config.local_awareness)
    tool = DiscoverLocalDevicesTool(
        workspace=tmp_path,
        config=config,
        backend=loop._local_awareness_backend,
        introspection_service=_service(loop),
    )

    result = await tool.execute()

    assert result == {"status": "disabled", "reason": "local_awareness_disabled"}
    assert loop._last_local_awareness_summary["last_discovery"] == result


@pytest.mark.asyncio
async def test_discover_local_devices_reports_safe_placeholder_capabilities(tmp_path: Path) -> None:
    config = _tools_config(
        enabled=True,
        camera=ToolsConfig.LocalAwarenessCameraConfig(enabled=True),
        audio=ToolsConfig.LocalAwarenessAudioConfig(output_enabled=True),
    )
    loop = _loop(config.local_awareness)
    tool = DiscoverLocalDevicesTool(
        workspace=tmp_path,
        config=config,
        backend=loop._local_awareness_backend,
        introspection_service=_service(loop),
    )

    result = await tool.execute()

    assert result["status"] == "ok"
    assert result["reason"] == "dependency_free_backend_no_hardware_probe"
    assert result["local_devices"] == {
        "cameras": [],
        "microphones": [],
        "speakers": [],
        "screens": [],
    }
    assert result["capabilities"]["camera_capture"] is True
    assert result["capabilities"]["audio_output"] is True
    assert loop._last_local_awareness_summary["camera_enabled"] is True


def test_hardware_discovery_config_defaults_and_aliases() -> None:
    default = ToolsConfig.LocalAwarenessConfig()

    assert default.hardware_discovery.enabled is False
    assert default.hardware_discovery.posture == "strong"
    assert 8123 in default.hardware_discovery.service_ports

    camel = ToolsConfig.LocalAwarenessConfig(
        hardwareDiscovery={
            "enabled": True,
            "allowedCidrs": ["192.168.1.0/30"],
            "maxHosts": 2,
            "timeoutMs": 250,
            "activeProbeEnabled": False,
            "servicePorts": [80, 8123],
        }
    )
    snake = ToolsConfig.LocalAwarenessConfig(
        hardware_discovery={
            "enabled": True,
            "allowed_cidrs": ["10.0.0.0/30"],
            "max_hosts": 3,
        }
    )

    assert camel.hardware_discovery.enabled is True
    assert camel.hardware_discovery.allowed_cidrs == ["192.168.1.0/30"]
    assert camel.hardware_discovery.max_hosts == 2
    assert camel.hardware_discovery.timeout_ms == 250
    assert camel.hardware_discovery.active_probe_enabled is False
    assert camel.hardware_discovery.service_ports == [80, 8123]
    assert snake.hardware_discovery.allowed_cidrs == ["10.0.0.0/30"]
    assert snake.hardware_discovery.max_hosts == 3


def test_normalize_local_awareness_summary_passes_device_map_additively() -> None:
    device_map = DeviceMap(
        devices=[
            DiscoveredDevice(
                device_id="dev_camera",
                kind="camera",
                name="Front Camera",
                protocols=["local_pnp"],
            )
        ]
    ).to_dict()

    summary = normalize_local_awareness_summary(
        None,
        cached={"last_discovery": {"device_map": device_map}},
    )

    assert summary["device_count"] == 1
    assert summary["device_map"]["devices"][0]["device_id"] == "dev_camera"
    assert summary["device_map_summary"]["kind_counts"] == {"camera": 1}


@pytest.mark.asyncio
async def test_lan_discovery_is_local_hostname_only_and_default_disabled(tmp_path: Path) -> None:
    config = _tools_config(enabled=True, lan_discovery_enabled=False)
    loop = _loop(config.local_awareness)
    tool = DiscoverLanDevicesTool(
        workspace=tmp_path,
        config=config,
        backend=loop._local_awareness_backend,
        introspection_service=_service(loop),
    )

    result = await tool.execute()

    assert result == {"status": "disabled", "devices": [], "reason": "lan_discovery_disabled"}


@pytest.mark.asyncio
async def test_lan_discovery_real_backend_is_gated_by_hardware_flag(tmp_path: Path) -> None:
    config = _tools_config(enabled=True, lan_discovery_enabled=True)
    loop = _loop(config.local_awareness)
    tool = DiscoverLanDevicesTool(
        workspace=tmp_path,
        config=config,
        backend=loop._local_awareness_backend,
        introspection_service=_service(loop),
    )

    result = await tool.execute()

    assert result["method"] == "local_hostname_only"
    assert "device_map" not in result


def test_windows_local_hardware_probe_normalizes_mocked_output() -> None:
    payload = {
        "cameras": [{"Name": "USB Camera", "PNPDeviceID": "USB\\VID_CAM"}],
        "microphones": [{"Name": "Array Microphone", "PNPDeviceID": "USB\\VID_MIC"}],
        "speakers": [{"Name": "Realtek Audio"}],
        "screens": [{"Name": "Generic PnP Monitor"}],
        "usb": [],
        "bluetooth": [],
        "network_adapters": [{"Name": "Intel Ethernet", "MACAddress": "aa:bb:cc:dd:ee:ff"}],
    }
    backend = LocalAwarenessBackend(command_runner=lambda *_args, **_kwargs: __import__("json").dumps(payload))
    config = ToolsConfig.LocalAwarenessConfig(
        enabled=True,
        hardwareDiscovery={"enabled": True},
    )

    result = backend._discover_local_devices_real(config=config, hardware=config.hardware_discovery)

    assert result["status"] == "ok"
    assert result["local_devices"]["cameras"][0]["name"] == "USB Camera"
    assert result["device_map"]["device_count"] >= 4
    assert result["device_map"]["summary"]["kind_counts"]["camera"] == 1


def test_lan_discovery_merges_arp_and_tcp_fingerprints() -> None:
    backend = LocalAwarenessBackend(command_runner=lambda *_args, **_kwargs: "")
    config = ToolsConfig.LocalAwarenessConfig(
        enabled=True,
        lan_discovery_enabled=True,
        hardwareDiscovery={
            "enabled": True,
            "allowedCidrs": ["192.168.1.0/30"],
            "maxHosts": 2,
            "activeProbeEnabled": True,
            "servicePorts": [8123],
        },
    )
    backend._discover_from_arp = lambda generated_at, skipped: [  # type: ignore[method-assign]
        DiscoveredDevice(
            device_id="dev_arp",
            ip_addresses=["192.168.1.1"],
            mac_address="aa:bb:cc:dd:ee:ff",
            protocols=["arp"],
            evidence=["arp_table"],
        )
    ]
    backend._discover_from_hostname = lambda generated_at, skipped: []  # type: ignore[method-assign]
    backend._discover_from_ssdp = lambda hardware, generated_at, skipped: []  # type: ignore[method-assign]
    backend._discover_from_mdns = lambda hardware, generated_at, skipped: []  # type: ignore[method-assign]
    backend._active_tcp_probe = lambda targets, hardware, generated_at, skipped: [  # type: ignore[method-assign]
        DiscoveredDevice(
            device_id="dev_tcp",
            kind="home_assistant",
            ip_addresses=["192.168.1.1"],
            protocols=["home_assistant"],
            services=[{"port": 8123, "protocol": "home_assistant", "name": "home_assistant"}],
            evidence=["tcp:8123 open"],
            confidence=0.92,
        )
    ]

    result = backend.discover_lan_devices(config=config)

    assert result["device_count"] == 1
    device = result["devices"][0]
    assert device["kind"] == "home_assistant"
    assert device["mac_address"] == "aa:bb:cc:dd:ee:ff"
    assert "arp" in device["protocols"]
    assert "home_assistant" in device["protocols"]
    assert result["scan_boundaries"]["target_count"] == 2


def test_scan_targets_enforce_private_cidr_and_global_max_hosts() -> None:
    backend = LocalAwarenessBackend(command_runner=lambda *_args, **_kwargs: "")
    config = ToolsConfig.LocalAwarenessConfig(
        hardwareDiscovery={
            "enabled": True,
            "allowedCidrs": ["127.0.0.0/30", "8.8.8.0/30", "192.168.10.0/29"],
            "maxHosts": 3,
        }
    )
    skipped: list[str] = []

    targets = backend._select_scan_targets(config.hardware_discovery, skipped=skipped)

    assert targets == ["192.168.10.1", "192.168.10.2", "192.168.10.3"]
    assert "public_cidr_skipped:8.8.8.0/30" in skipped


@pytest.mark.asyncio
async def test_discovery_updates_world_state_device_events_and_summary(tmp_path: Path) -> None:
    config = _tools_config(enabled=True, lan_discovery_enabled=True)
    loop = _loop_with_world(config.local_awareness, tmp_path)
    backend = LocalAwarenessBackend(command_runner=lambda *_args, **_kwargs: "")
    backend.discover_lan_devices = lambda config: {  # type: ignore[method-assign]
        "status": "ok",
        "devices": [{"device_id": "dev_router", "kind": "router", "ip_addresses": ["192.168.1.1"]}],
        "device_map": {
            "devices": [{"device_id": "dev_router", "kind": "router", "ip_addresses": ["192.168.1.1"]}],
            "device_count": 1,
        },
    }
    tool = DiscoverLanDevicesTool(
        workspace=tmp_path,
        config=config,
        backend=backend,
        introspection_service=_service(loop),
    )
    tool.set_context(_request_context())

    result = await tool.execute()

    assert result["device_events_summary"]["device_event_count"] == 1
    assert loop._last_local_awareness_summary["device_event_count"] == 1
    session = loop.sessions.get_or_create("cli:direct")
    assert loop.world_state.inspect(session)["device_events"][0]["kind"] == "appeared"


@pytest.mark.asyncio
async def test_device_binding_permission_tools_and_capture_gate(tmp_path: Path) -> None:
    config = _tools_config(
        enabled=True,
        camera=ToolsConfig.LocalAwarenessCameraConfig(enabled=True, require_confirmation=False),
    )
    loop = _loop_with_world(config.local_awareness, tmp_path)
    runtime_context = _request_context().runtime_context
    session = loop.sessions.get_or_create("cli:direct")
    loop.world_state.ingest_device_discovery(
        session,
        runtime_context=runtime_context,
        device_map={"devices": [{"device_id": "dev_cam", "kind": "camera", "ip_addresses": ["192.168.1.9"]}]},
    )
    service = _service(loop)
    bind_tool = BindDeviceTool(workspace=tmp_path, config=config, backend=loop._local_awareness_backend, introspection_service=service)
    permission_tool = RequestDevicePermissionTool(workspace=tmp_path, config=config, backend=loop._local_awareness_backend, introspection_service=service)
    list_tool = ListDeviceBindingsTool(workspace=tmp_path, config=config, backend=loop._local_awareness_backend, introspection_service=service)
    capture_tool = CaptureCameraFrameTool(workspace=tmp_path, config=config, backend=loop._local_awareness_backend, introspection_service=service)
    for tool in (bind_tool, permission_tool, list_tool, capture_tool):
        tool.set_context(_request_context())

    blocked = await capture_tool.execute(device_id="dev_cam", attach_to_world=False)
    binding = await bind_tool.execute(device_id="dev_cam", user_label="front door camera", location="entry")
    pending = await permission_tool.execute(device_id="dev_cam", capability="camera")
    granted = await permission_tool.execute(device_id="dev_cam", capability="camera", grant=True, confirmation_id="confirm_cam")
    listed = await list_tool.execute()
    captured = await capture_tool.execute(device_id="dev_cam", attach_to_world=False)

    assert blocked["status"] == "pending_confirmation"
    assert blocked["reason"] == "camera_device_permission_required"
    assert binding["status"] == "ok"
    assert pending["status"] == "pending_confirmation"
    assert granted["permission"]["status"] == "granted"
    assert listed["device_permissions_summary"]["granted_permission_count"] == 1
    assert captured["status"] == "ok"
    assert captured["device_id"] == "dev_cam"


@pytest.mark.asyncio
async def test_revoke_device_binding_removes_capture_permission(tmp_path: Path) -> None:
    config = _tools_config(
        enabled=True,
        camera=ToolsConfig.LocalAwarenessCameraConfig(enabled=True, require_confirmation=False),
    )
    loop = _loop_with_world(config.local_awareness, tmp_path)
    runtime_context = _request_context().runtime_context
    session = loop.sessions.get_or_create("cli:direct")
    loop.world_state.ingest_device_discovery(
        session,
        runtime_context=runtime_context,
        device_map={"devices": [{"device_id": "dev_cam", "kind": "camera", "ip_addresses": ["192.168.1.9"]}]},
    )
    loop.world_state.bind_device(session, runtime_context=runtime_context, device_id="dev_cam", user_label="front")
    loop.world_state.request_device_permission(
        session,
        runtime_context=runtime_context,
        device_id="dev_cam",
        capability="camera",
        status="granted",
    )
    service = _service(loop)
    revoke_tool = RevokeDeviceBindingTool(workspace=tmp_path, config=config, backend=loop._local_awareness_backend, introspection_service=service)
    capture_tool = CaptureCameraFrameTool(workspace=tmp_path, config=config, backend=loop._local_awareness_backend, introspection_service=service)
    revoke_tool.set_context(_request_context())
    capture_tool.set_context(_request_context())

    revoked = await revoke_tool.execute("dev_cam")
    blocked = await capture_tool.execute(device_id="dev_cam", attach_to_world=False)

    assert revoked["status"] == "ok"
    assert blocked["status"] == "pending_confirmation"
    assert blocked["reason"] == "camera_device_permission_required"


@pytest.mark.asyncio
async def test_camera_capture_requires_confirmation_by_default(tmp_path: Path) -> None:
    config = _tools_config(
        enabled=True,
        camera=ToolsConfig.LocalAwarenessCameraConfig(enabled=True),
    )
    loop = _loop(config.local_awareness)
    tool = CaptureCameraFrameTool(
        workspace=tmp_path,
        config=config,
        backend=loop._local_awareness_backend,
        introspection_service=_service(loop),
    )

    result = await tool.execute()

    assert result == {
        "status": "pending_confirmation",
        "reason": "camera_capture_requires_confirmation",
    }
    assert loop._last_local_awareness_summary["last_capture"] == result


@pytest.mark.asyncio
async def test_camera_capture_writes_placeholder_inside_workspace_when_unlocked(tmp_path: Path) -> None:
    config = _tools_config(
        enabled=True,
        camera=ToolsConfig.LocalAwarenessCameraConfig(
            enabled=True,
            require_confirmation=False,
            save_dir="uploads/perception",
        ),
    )
    loop = _loop(config.local_awareness)
    tool = CaptureCameraFrameTool(
        workspace=tmp_path,
        config=config,
        backend=loop._local_awareness_backend,
        introspection_service=_service(loop),
    )

    result = await tool.execute(attach_to_world=False)

    assert result["status"] == "ok"
    assert result["placeholder"] is True
    assert result["media_path"].startswith("uploads/perception/camera_")
    assert result["media_path"].endswith(".png")
    assert (tmp_path / result["media_path"]).is_file()
    assert Path(result["absolute_path"]).is_relative_to(tmp_path)
    assert loop._last_local_awareness_summary["last_capture"]["media_path"] == result["media_path"]


@pytest.mark.asyncio
async def test_screen_capture_uses_screen_parameter_and_placeholder_backend(tmp_path: Path) -> None:
    config = _tools_config(
        enabled=True,
        screen=ToolsConfig.LocalAwarenessScreenConfig(enabled=True, require_confirmation=False),
    )
    loop = _loop(config.local_awareness)
    tool = CaptureScreenTool(
        workspace=tmp_path,
        config=config,
        backend=loop._local_awareness_backend,
        introspection_service=_service(loop),
    )

    result = await tool.execute(screen_id="primary", attach_to_world=False)

    assert tool.parameters["properties"].keys() == {"screen_id", "device_id", "attach_to_world"}
    assert result["status"] == "ok"
    assert result["device_id"] == "primary"
    assert result["media_path"].startswith("uploads/perception/screen_")


@pytest.mark.asyncio
async def test_audio_record_denies_over_limit_before_writing(tmp_path: Path) -> None:
    config = _tools_config(
        enabled=True,
        audio=ToolsConfig.LocalAwarenessAudioConfig(
            input_enabled=True,
            require_confirmation=False,
            max_record_seconds=2,
        ),
    )
    loop = _loop(config.local_awareness)
    tool = RecordAudioSampleTool(
        workspace=tmp_path,
        config=config,
        backend=loop._local_awareness_backend,
        introspection_service=_service(loop),
    )

    result = await tool.execute(seconds=3)

    assert result == {
        "status": "denied",
        "reason": "record_seconds_exceeds_limit",
        "max_seconds": 2,
    }
    assert list((tmp_path / "uploads").glob("**/*")) == []


@pytest.mark.asyncio
async def test_speak_text_is_disabled_by_default_and_dry_run_when_unlocked(tmp_path: Path) -> None:
    disabled = _tools_config(enabled=True)
    disabled_loop = _loop(disabled.local_awareness)
    disabled_tool = SpeakTextTool(
        workspace=tmp_path,
        config=disabled,
        backend=disabled_loop._local_awareness_backend,
        introspection_service=_service(disabled_loop),
    )
    assert await disabled_tool.execute("hello") == {
        "status": "disabled",
        "reason": "audio_output_disabled",
    }

    enabled = _tools_config(
        enabled=True,
        audio=ToolsConfig.LocalAwarenessAudioConfig(
            output_enabled=True,
            require_confirmation=False,
        ),
    )
    enabled_loop = _loop(enabled.local_awareness)
    enabled_tool = SpeakTextTool(
        workspace=tmp_path,
        config=enabled,
        backend=enabled_loop._local_awareness_backend,
        introspection_service=_service(enabled_loop),
    )

    result = await enabled_tool.execute("hello world")

    assert result["status"] == "dry_run"
    assert result["is_real_output"] is False
    assert result["backend_kind"] == "local_awareness_placeholder"


@pytest.mark.asyncio
async def test_inspect_media_records_disabled_result_in_summary(tmp_path: Path) -> None:
    config = _tools_config(enabled=False)
    loop = _loop(config.local_awareness)
    tool = InspectMediaTool(
        workspace=tmp_path,
        config=config,
        backend=loop._local_awareness_backend,
        introspection_service=_service(loop),
    )

    result = await tool.execute("uploads/perception/camera.png")

    assert result == {"status": "disabled", "reason": "local_awareness_disabled"}
    assert loop._last_local_awareness_summary["last_media_inspection"] == result
