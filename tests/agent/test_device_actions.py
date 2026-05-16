import pytest

from OpenHome.agent.action_runtime import ActionIntent
from OpenHome.agent.device_actions import (
    DeviceActionSchemaRegistry,
    InvalidActionParameterError,
    TypedActionPlanner,
    TypedDeviceAction,
    UnsupportedActionError,
    UnsupportedDomainError,
)


def action(**kwargs):
    defaults = {
        "action_type": "set_light_brightness",
        "device_id": "ceiling_light",
        "domain": "lighting",
        "room": "living_room",
        "parameters": {"brightness": 40},
        "requested_by": "alice",
    }
    defaults.update(kwargs)
    return TypedDeviceAction(**defaults)


def registry():
    return DeviceActionSchemaRegistry()


def test_valid_lighting_brightness_action_validates():
    validated = registry().validate(action(parameters={"brightness": 75}))

    assert validated.action_type == "set_light_brightness"
    assert validated.domain == "lighting"
    assert validated.parameters == {"brightness": 75}


@pytest.mark.parametrize("brightness", [-1, 101])
def test_brightness_outside_range_rejected(brightness):
    with pytest.raises(InvalidActionParameterError):
        registry().validate(action(parameters={"brightness": brightness}))


def test_unknown_domain_rejected():
    with pytest.raises(UnsupportedDomainError):
        registry().validate(action(domain="garden"))


@pytest.mark.parametrize("domain", ["lock", "security", "camera", "gas", "presence", "appliance"])
def test_disabled_domains_rejected_in_phase_6(domain):
    with pytest.raises(UnsupportedDomainError):
        registry().validate(action(domain=domain))


@pytest.mark.parametrize("temperature", [15.9, 30.1])
def test_climate_temperature_outside_range_rejected(temperature):
    with pytest.raises(InvalidActionParameterError):
        registry().validate(
            action(
                action_type="set_temperature",
                domain="climate",
                device_id="thermostat",
                parameters={"temperature_c": temperature},
            )
        )


def test_media_volume_above_100_rejected():
    with pytest.raises(InvalidActionParameterError):
        registry().validate(
            action(
                action_type="set_media_volume",
                domain="media",
                device_id="speaker",
                parameters={"volume": 101},
            )
        )


def test_registry_inferrs_risk_from_action_and_domain():
    rules = registry()

    low_light = rules.infer_risk(action())
    medium_media = rules.infer_risk(
        action(
            action_type="set_media_volume",
            domain="media",
            device_id="speaker",
            parameters={"volume": 71},
        )
    )
    medium_climate = rules.infer_risk(
        action(
            action_type="set_hvac_mode",
            domain="climate",
            device_id="thermostat",
            parameters={"mode": "heat"},
        )
    )

    assert low_light == "low"
    assert medium_media == "medium"
    assert medium_climate == "medium"


def test_registry_inferrs_scope_deterministically():
    rules = registry()

    assert rules.infer_scope(action()) == "home.living_room.lighting.ceiling_light"
    assert rules.infer_scope(action(room=None)) == "home.lighting.ceiling_light"


def test_planner_converts_typed_action_to_action_intent():
    planner = TypedActionPlanner(registry())

    intent = planner.to_intent(action(parameters={"brightness": 55}, idempotency_key="idem-1"))

    assert isinstance(intent, ActionIntent)
    assert intent.action == "set_light_brightness"
    assert intent.scope == "home.living_room.lighting.ceiling_light"
    assert intent.risk == "low"
    assert intent.trigger == "user_initiated"
    assert intent.requested_by == "alice"
    assert intent.requires_presence_empty is False
    assert intent.payload == {
        "device_id": "ceiling_light",
        "domain": "lighting",
        "action_type": "set_light_brightness",
        "brightness": 55,
    }
    assert intent.idempotency_key == "idem-1"


def test_caller_cannot_override_registry_risk_with_parameters():
    with pytest.raises(InvalidActionParameterError):
        registry().validate(action(parameters={"brightness": 55, "risk": "low"}))


def test_domain_action_mismatch_rejected():
    with pytest.raises(UnsupportedActionError):
        registry().validate(action(domain="climate"))


def test_turn_off_all_lights_is_not_supported_in_phase_6():
    with pytest.raises(UnsupportedActionError):
        registry().validate(
            action(
                action_type="turn_off_all_lights",
                device_id="all",
                domain="lighting",
                room=None,
                parameters={},
            )
        )
