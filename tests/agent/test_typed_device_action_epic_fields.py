"""验证 TypedDeviceAction / ActionIntent 的 Epic 字段（duration_ms、requires_parallel）
在默认值、显式赋值、planner 透传以及 schema 校验等场景下的行为保持一致。
"""

from OriginAgent.agent.action_runtime import ActionIntent
from OriginAgent.domain_packs.smart_home.runtime.device_actions import (
    DeviceActionSchemaRegistry,
    TypedActionPlanner,
    TypedDeviceAction,
)


def test_typed_device_action_default_values():
    # 不传 Epic 字段时应保持向后兼容的默认值，避免破坏既有调用方
    action = TypedDeviceAction(
        action_type="set_light_power",
        device_id="light_1",
        domain="lighting",
        parameters={"power": "on"},
    )

    assert action.duration_ms == 0
    assert action.requires_parallel is False


def test_typed_device_action_explicit_parallel_fields():
    # 显式传入 Epic 字段时必须原样保留，供下游调度器使用
    action = TypedDeviceAction(
        action_type="set_light_power",
        device_id="light_1",
        domain="lighting",
        parameters={"power": "on"},
        duration_ms=300,
        requires_parallel=True,
    )

    assert action.duration_ms == 300
    assert action.requires_parallel is True


def test_planner_to_intent_propagates_epic_fields():
    # planner.to_intent 必须把 Epic 字段透传到 ActionIntent，否则调度信息会丢失
    registry = DeviceActionSchemaRegistry()
    planner = TypedActionPlanner(registry)
    action = TypedDeviceAction(
        action_type="set_light_power",
        device_id="light_1",
        domain="lighting",
        parameters={"power": "on"},
        duration_ms=300,
        requires_parallel=True,
    )

    intent = planner.to_intent(action)

    assert isinstance(intent, ActionIntent)
    assert intent.duration_ms == 300
    assert intent.requires_parallel is True


def test_schema_validate_preserves_epic_fields():
    # registry.validate 用 dataclasses.replace 重建对象，需确认 Epic 字段不被丢弃
    registry = DeviceActionSchemaRegistry()
    action = TypedDeviceAction(
        action_type="set_light_power",
        device_id="light_1",
        domain="lighting",
        parameters={"power": "on"},
        duration_ms=250,
        requires_parallel=True,
    )

    validated = registry.validate(action)

    assert validated.duration_ms == 250
    assert validated.requires_parallel is True


def test_action_intent_default_values():
    # ActionIntent 不传 Epic 字段时也应回退到默认值，保持向后兼容
    intent = ActionIntent(action="x", scope="y", trigger="z", risk="low")

    assert intent.duration_ms == 0
    assert intent.requires_parallel is False
