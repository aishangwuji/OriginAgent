from __future__ import annotations

from types import SimpleNamespace

import pytest

from OpenHome.agent.action_runtime import ActionExecutionResult
from OpenHome.agent.device_actions import TypedDeviceAction
from OpenHome.agent.tools.device import (
    LightingSetBrightnessTool,
    LightingSetColorTemperatureTool,
    LightingSetPowerTool,
    lighting_tools,
)


class FakeExecutor:
    def __init__(self, status: str = "executed"):
        self.actions: list[TypedDeviceAction] = []
        self.status = status

    def submit_typed(self, action: TypedDeviceAction):
        self.actions.append(action)
        return ActionExecutionResult(
            status=self.status,
            action_id="action_123",
            reason="done",
            confirmation_id=None,
            backend_result={"raw_payload": "secret"},
            backend_called=True,
            permission_status="allow",
        )


@pytest.mark.asyncio
async def test_lighting_set_power_constructs_typed_action():
    executor = FakeExecutor()
    tool = LightingSetPowerTool(executor)  # type: ignore[arg-type]
    tool.set_context("alice", "user_initiated")

    result = await tool.execute(device_id="lamp", room="living_room", power="on")

    assert result["status"] == "success"
    assert result["execution_status"] == "executed"
    action = executor.actions[0]
    assert action == TypedDeviceAction(
        action_type="set_light_power",
        domain="lighting",
        device_id="lamp",
        room="living_room",
        parameters={"power": "on"},
        requested_by="alice",
        trigger="user_initiated",
    )


@pytest.mark.asyncio
async def test_lighting_tool_fails_closed_without_actor_context():
    executor = FakeExecutor()
    tool = LightingSetBrightnessTool(executor)  # type: ignore[arg-type]
    tool.set_context("", "user_initiated")

    with pytest.raises(PermissionError, match="actor context"):
        await tool.execute(device_id="lamp", brightness=50)

    assert executor.actions == []


@pytest.mark.asyncio
async def test_lighting_tool_fails_closed_without_trigger_context():
    executor = FakeExecutor()
    tool = LightingSetBrightnessTool(executor)  # type: ignore[arg-type]
    tool.set_context("alice", "")

    with pytest.raises(PermissionError, match="trigger context"):
        await tool.execute(device_id="lamp", brightness=50)

    assert executor.actions == []


def test_brightness_schema_rejects_injected_fields():
    tool = LightingSetBrightnessTool(FakeExecutor())  # type: ignore[arg-type]

    errors = tool.validate_params(
        {
            "device_id": "lamp",
            "brightness": 50,
            "risk": "low",
            "scope": "home.lighting.lamp",
            "requested_by": "mallory",
            "backend": "direct",
        }
    )

    assert any("unexpected property risk" in error for error in errors)
    assert any("unexpected property backend" in error for error in errors)


@pytest.mark.asyncio
async def test_scheduled_trigger_is_preserved():
    executor = FakeExecutor()
    tool = LightingSetColorTemperatureTool(executor)  # type: ignore[arg-type]
    tool.set_context("alice", "scheduled")

    await tool.execute(device_id="lamp", temperature="warm")

    assert executor.actions[0].trigger == "scheduled"
    assert executor.actions[0].parameters == {"temperature": "warm"}


@pytest.mark.asyncio
async def test_tool_result_does_not_include_backend_raw_response():
    tool = LightingSetBrightnessTool(FakeExecutor())  # type: ignore[arg-type]
    tool.set_context("alice", "user_initiated")

    result = await tool.execute(device_id="lamp", brightness=50)

    assert "backend_result" not in result
    assert "raw_payload" not in str(result)


def test_lighting_tools_register_exactly_three_names():
    tools = lighting_tools(SimpleNamespace(submit_typed=lambda action: None))  # type: ignore[arg-type]

    assert [tool.name for tool in tools] == [
        "openhome_device_lighting_set_power",
        "openhome_device_lighting_set_brightness",
        "openhome_device_lighting_set_color_temperature",
    ]
