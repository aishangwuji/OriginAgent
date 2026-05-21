from __future__ import annotations

from typing import Any

import pytest

from OriginAgent.agent.tools.audit import InMemoryToolAuditSink, ToolAuditConfig
from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.registry import ToolRegistry
from OriginAgent.agent.tools.shell import ExecTool
from OriginAgent.security.capabilities import CapabilitySnapshot


class EchoTool(Tool):
    name = "echo_helper"
    description = "Echoes a value; no IO."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "additionalProperties": False,
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, text: str = "") -> dict[str, str]:
        return {"echo": text}


class NamedTool(Tool):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": True}

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


@pytest.mark.asyncio
async def test_low_risk_helper_does_not_require_capability_snapshot() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    result = await registry.execute("echo_helper", {"text": "hello"})

    assert result == {"echo": "hello"}


def test_low_risk_helper_prepare_call_without_snapshot_succeeds() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    tool, params, error = registry.prepare_call("echo_helper", {"text": "hello"})

    assert error is None
    assert tool is not None
    assert params == {"text": "hello"}


@pytest.mark.parametrize(
    "tool_name",
    [
        "exec",
        "write_file",
        "edit_file",
        "message",
        "cron",
        "spawn",
        "originagent_device_lighting_set_power",
        "mcp_demo_tool",
    ],
)
def test_boundary_tools_require_capability_snapshot(tool_name: str) -> None:
    registry = ToolRegistry()
    registry.register(NamedTool(tool_name))

    _, _, error = registry.prepare_call(tool_name, {})

    assert error is not None
    assert "capability snapshot" in error.lower()


def test_scheduled_snapshot_denies_exec() -> None:
    registry = ToolRegistry(capability_snapshot=CapabilitySnapshot.scheduled_default())
    registry.register(NamedTool("exec"))

    _, _, error = registry.prepare_call("exec", {})

    assert error is not None
    assert "exec" in error.lower()
    assert "not allowed by the current capability snapshot" in error


def test_system_snapshot_denies_device_tool() -> None:
    registry = ToolRegistry(capability_snapshot=CapabilitySnapshot.system_default())
    registry.register(NamedTool("originagent_device_lighting_set_power"))

    _, _, error = registry.prepare_call("originagent_device_lighting_set_power", {})

    assert error is not None
    assert "Device tools are not allowed" in error


@pytest.mark.parametrize("mode", ["off", "minimal", "security"])
@pytest.mark.asyncio
async def test_audit_mode_does_not_disable_capability_gate(mode: str) -> None:
    registry = ToolRegistry(
        audit_sink=InMemoryToolAuditSink(),
        audit_config=ToolAuditConfig(mode=mode),  # type: ignore[arg-type]
    )
    registry.register(NamedTool("exec"))

    result = await registry.execute("exec", {})

    assert "requires an explicit capability snapshot" in result


@pytest.mark.parametrize("mode", ["off", "minimal", "security"])
@pytest.mark.asyncio
async def test_audit_mode_does_not_expand_capability_gate(mode: str) -> None:
    registry = ToolRegistry(
        audit_sink=InMemoryToolAuditSink(),
        audit_config=ToolAuditConfig(mode=mode),  # type: ignore[arg-type]
    )
    registry.register(EchoTool())

    result = await registry.execute("echo_helper", {"text": "hello"})

    assert result == {"echo": "hello"}


@pytest.mark.asyncio
async def test_local_dev_exec_profile_still_requires_capability_snapshot(tmp_path) -> None:
    registry = ToolRegistry(
        audit_sink=InMemoryToolAuditSink(),
        audit_config=ToolAuditConfig(mode="off"),
    )
    registry.register(
        ExecTool(
            working_dir=str(tmp_path),
            restrict_to_workspace=True,
            sandbox="",
            security_profile="local_dev",
            allow_unsafe_exec=True,
        )
    )

    result = await registry.execute("exec", {"command": "echo ok"})

    assert "requires an explicit capability snapshot" in result
    assert "unsafe-exec" not in result


@pytest.mark.asyncio
async def test_scheduled_snapshot_denies_local_dev_exec_profile(tmp_path) -> None:
    registry = ToolRegistry(capability_snapshot=CapabilitySnapshot.scheduled_default())
    registry.register(
        ExecTool(
            working_dir=str(tmp_path),
            restrict_to_workspace=True,
            sandbox="",
            security_profile="local_dev",
            allow_unsafe_exec=True,
        )
    )

    result = await registry.execute("exec", {"command": "echo ok"})

    assert "not allowed by the current capability snapshot" in result
    assert "unsafe-exec" not in result
