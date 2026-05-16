import pytest

from OpenHome.agent.permissions import (
    HouseholdActor,
    PermissionRequest,
    PermissionResolver,
    infer_device_domain,
)


def request(**kwargs):
    defaults = {
        "actor_id": "alice",
        "action": "turn_on",
        "scope": "home.living_room.light",
        "risk": "low",
        "trigger": "user_initiated",
        "permission": "execute_action",
        "device_domain": "lighting",
    }
    defaults.update(kwargs)
    return PermissionRequest(**defaults)


def resolver():
    return PermissionResolver(
        actors={
            "admin": HouseholdActor("admin", "admin"),
            "resident": HouseholdActor("resident", "resident"),
            "guest": HouseholdActor("guest", "guest"),
            "child": HouseholdActor("child", "child"),
            "elder": HouseholdActor("elder", "elder"),
            "service": HouseholdActor("service", "service_person"),
        }
    )


def test_invalid_role_rejected():
    with pytest.raises(ValueError):
        HouseholdActor("bad", "owner")


@pytest.mark.parametrize("actor_id", [None, "", "missing"])
def test_missing_actor_resolves_unknown(actor_id):
    actor = resolver().resolve_actor(actor_id)

    assert actor.role == "unknown"


@pytest.mark.parametrize("domain", ["lock", "security", "camera", "gas", "presence"])
def test_admin_can_execute_and_confirm_high_risk_domains(domain):
    permissions = resolver()

    execute = permissions.evaluate(
        request(actor_id="admin", permission="execute_action", risk="high", device_domain=domain)
    )
    confirm = permissions.evaluate(
        request(actor_id="admin", permission="confirm_action", risk="high", device_domain=domain)
    )

    assert execute.decision == "allow"
    assert confirm.decision == "allow"


@pytest.mark.parametrize("actor_id", ["resident", "elder"])
def test_resident_and_elder_can_low_medium_non_high_risk(actor_id):
    permissions = resolver()

    execute = permissions.evaluate(
        request(actor_id=actor_id, risk="medium", permission="execute_action")
    )
    confirm = permissions.evaluate(
        request(actor_id=actor_id, risk="medium", permission="confirm_action")
    )

    assert execute.decision == "allow"
    assert confirm.decision == "allow"


@pytest.mark.parametrize("actor_id", ["resident", "elder"])
@pytest.mark.parametrize("domain", ["lock", "security", "camera", "gas", "presence"])
def test_high_risk_domain_overrides_low_risk_for_resident_and_elder(actor_id, domain):
    permissions = resolver()

    execute = permissions.evaluate(
        request(actor_id=actor_id, risk="low", permission="execute_action", device_domain=domain)
    )
    confirm = permissions.evaluate(
        request(actor_id=actor_id, risk="low", permission="confirm_action", device_domain=domain)
    )

    assert execute.decision == "ask_admin"
    assert confirm.decision == "ask_admin"


@pytest.mark.parametrize("actor_id", ["guest", "child", None])
def test_guest_child_unknown_cannot_confirm_pending_actions(actor_id):
    decision = resolver().evaluate(
        request(actor_id=actor_id, permission="confirm_action")
    )

    assert decision.decision == "deny"


@pytest.mark.parametrize("actor_id", ["guest", "child", None])
def test_guest_child_unknown_can_execute_low_risk_user_non_high_risk(actor_id):
    decision = resolver().evaluate(
        request(actor_id=actor_id, permission="execute_action")
    )

    assert decision.decision == "allow"


def test_service_person_default_limited_to_low_risk_non_high_execute():
    permissions = resolver()

    low = permissions.evaluate(
        request(actor_id="service", permission="execute_action")
    )
    confirm = permissions.evaluate(
        request(actor_id="service", permission="confirm_action")
    )
    high_domain = permissions.evaluate(
        request(
            actor_id="service",
            permission="execute_action",
            risk="low",
            device_domain="lock",
        )
    )

    assert low.decision == "allow"
    assert confirm.decision == "deny"
    assert high_domain.decision == "ask_admin"


@pytest.mark.parametrize(
    ("scope", "action", "expected"),
    [
        ("home.entry.lock", "set_state", "lock"),
        ("home.entry", "unlock", "lock"),
        ("home.security", "arm_away", "security"),
        ("home.living_room.camera", "stream", "camera"),
        ("home.kitchen", "turn_on_gas", "gas"),
        ("home.presence", "mark_home", "presence"),
        ("home.living_room.light", "turn_on", "lighting"),
        ("home.hvac", "set_temperature", "climate"),
        ("home.living_room.speaker", "play_music", "media"),
        ("home.kitchen.appliance", "start", "appliance"),
        ("home.unknown", "do_thing", "general"),
    ],
)
def test_infer_device_domain(scope, action, expected):
    assert infer_device_domain(scope, action) == expected


def test_high_risk_domain_detection_has_priority():
    assert infer_device_domain("home.entry.lock.light", "turn_on") == "lock"
