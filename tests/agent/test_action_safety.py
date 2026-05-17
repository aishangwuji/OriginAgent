import pytest

from OpenHome.agent.action_safety import ActionRequest, ActionSafetyGate
from OpenHome.agent.facts import FactStore
from OpenHome.agent.presence import PresenceStore
from OpenHome.agent.presence_adapters import (
    MotionAdapter,
    MotionEvent,
    PhoneGeofenceAdapter,
    PhoneGeofenceEvent,
    WifiDeviceEvent,
    WifiPresenceAdapter,
)
from OpenHome.agent.presence_signals import PresenceSignalIngestor


@pytest.fixture
def stores(tmp_path):
    presence = PresenceStore(tmp_path)
    facts = FactStore(tmp_path)
    return presence, facts


def gate(stores):
    presence, facts = stores
    return ActionSafetyGate(presence, facts)


def request(**kwargs):
    defaults = {
        "action": "turn_on",
        "scope": "home.living_room.light",
        "trigger": "user_initiated",
        "risk": "low",
    }
    defaults.update(kwargs)
    return ActionRequest(**defaults)


def test_low_risk_user_action_without_facts_allows(stores):
    decision = gate(stores).evaluate(request())

    assert decision.decision == "allow"


@pytest.mark.parametrize("trigger", ["scheduled", "system", "subagent"])
def test_pending_fact_in_uses_facts_blocks_non_user_triggers(stores, trigger):
    _, facts = stores
    fact = facts.upsert_fact(
        "Do not unlock front door automatically",
        category="policy",
        scope="home.entry.lock",
    )

    decision = gate(stores).evaluate(
        request(
            scope="home.entry.lock",
            trigger=trigger,
            risk="medium",
            uses_facts=[fact.fact_id],
        )
    )

    assert decision.decision == "deny"
    assert decision.pending_facts == [fact.fact_id]


def test_pending_fact_in_uses_facts_on_user_action_asks_confirmation(stores):
    _, facts = stores
    fact = facts.upsert_fact(
        "Do not unlock front door automatically",
        category="policy",
        scope="home.entry.lock",
    )

    decision = gate(stores).evaluate(
        request(scope="home.entry.lock", risk="medium", uses_facts=[fact.fact_id])
    )

    assert decision.decision == "ask_confirmation"
    assert decision.pending_facts == [fact.fact_id]


def test_missing_fact_id_is_not_authorizing(stores):
    user_decision = gate(stores).evaluate(request(uses_facts=["fact_missing"]))
    scheduled_decision = gate(stores).evaluate(
        request(trigger="scheduled", uses_facts=["fact_missing"])
    )

    assert user_decision.decision == "ask_confirmation"
    assert user_decision.pending_facts == ["fact_missing"]
    assert scheduled_decision.decision == "deny"


def test_unrelated_pending_fact_does_not_block_low_risk_user_action(stores):
    _, facts = stores
    facts.upsert_fact(
        "Confirm before changing locks",
        category="policy",
        scope="home.entry.lock",
    )

    decision = gate(stores).evaluate(request(scope="home.living_room.light"))

    assert decision.decision == "allow"


@pytest.mark.parametrize("trigger", ["scheduled", "system", "subagent"])
def test_high_risk_non_user_action_denied(stores, trigger):
    decision = gate(stores).evaluate(
        request(action="unlock", scope="home.entry.lock", trigger=trigger, risk="high")
    )

    assert decision.decision == "deny"


def test_high_risk_user_with_unknown_occupancy_asks_confirmation(stores):
    decision = gate(stores).evaluate(
        request(action="unlock", scope="home.entry.lock", risk="high")
    )

    assert decision.decision == "ask_confirmation"
    assert decision.presence_status == "unknown"


@pytest.mark.parametrize("trigger", ["scheduled", "system", "subagent"])
def test_medium_risk_non_user_with_unknown_occupancy_denied(stores, trigger):
    decision = gate(stores).evaluate(
        request(
            action="set_temperature",
            scope="home.hvac",
            trigger=trigger,
            risk="medium",
        )
    )

    assert decision.decision == "deny"
    assert decision.reason == "medium-risk non-user action denied with unknown occupancy"


def test_medium_risk_non_user_can_allow_when_occupancy_known_and_no_pending_facts(stores):
    presence, _ = stores
    presence.upsert_presence(
        "alice",
        role="resident",
        status="home",
        source="manual",
        confidence=0.9,
    )

    decision = gate(stores).evaluate(
        request(
            action="set_temperature",
            scope="home.hvac",
            trigger="scheduled",
            risk="medium",
        )
    )

    assert decision.decision == "allow"
    assert decision.presence_status == "occupied"


def test_requires_presence_empty_blocks_occupied_and_unknown(stores):
    presence, _ = stores

    unknown = gate(stores).evaluate(request(requires_presence_empty=True))
    presence.upsert_presence(
        "alice",
        role="resident",
        status="home",
        source="manual",
        confidence=0.95,
    )
    occupied = gate(stores).evaluate(request(requires_presence_empty=True))

    assert unknown.decision == "ask_confirmation"
    assert occupied.decision == "deny"


def test_requires_presence_empty_allows_empty_to_continue(stores):
    presence, _ = stores
    presence.upsert_presence(
        "alice",
        role="resident",
        status="away",
        source="manual",
        confidence=0.95,
    )

    decision = gate(stores).evaluate(request(requires_presence_empty=True))

    assert decision.decision == "allow"
    assert decision.presence_status == "empty"


def test_active_low_risk_fact_can_support_allow(stores):
    _, facts = stores
    fact = facts.upsert_fact(
        "Living room lights may turn on from user commands",
        category="preference",
        scope="home.living_room.light",
    )

    decision = gate(stores).evaluate(request(uses_facts=[fact.fact_id]))

    assert decision.decision == "allow"
    assert decision.supporting_facts == [fact.fact_id]


def test_deprecated_and_contradicted_facts_are_ignored_when_not_used(stores):
    _, facts = stores
    deprecated = facts.upsert_fact("Old rule", scope="home.living_room.light")
    contradicted = facts.upsert_fact("Contradicted rule", scope="home.living_room.light")
    records = facts.read_all()
    for record in records:
        if record.fact_id == deprecated.fact_id:
            record.status = "deprecated"
        if record.fact_id == contradicted.fact_id:
            record.status = "contradicted"
    facts._write_records_unlocked(records)

    decision = gate(stores).evaluate(request())

    assert decision.decision == "allow"


def test_active_policy_or_safety_fact_does_not_allow_high_risk_autonomous(stores):
    _, facts = stores
    fact = facts.upsert_fact(
        "Trusted automation may unlock the door",
        category="policy",
        scope="home.entry.lock",
        status="active",
        requires_confirmation=False,
    )

    decision = gate(stores).evaluate(
        request(
            action="unlock",
            scope="home.entry.lock",
            trigger="autonomous",
            risk="high",
            uses_facts=[fact.fact_id],
        )
    )

    assert decision.decision == "deny"
    assert decision.reason == "invalid trigger"
    assert decision.supporting_facts == []


@pytest.mark.parametrize(
    ("risk", "trigger", "expected"),
    [
        ("medium", "user_initiated", "deny"),
        ("high", "user_initiated", "deny"),
        ("low", "scheduled", "deny"),
        ("low", "user_initiated", "allow"),
    ],
)
def test_fact_read_failure_fail_closed_except_low_risk_user_without_constraints(
    stores, monkeypatch, risk, trigger, expected,
):
    _, facts = stores

    def fail():
        raise OSError("cannot read facts")

    monkeypatch.setattr(facts, "read_all", fail)

    decision = gate(stores).evaluate(request(risk=risk, trigger=trigger))

    assert decision.decision == expected


def test_invalid_trigger_or_risk_denied(stores):
    assert gate(stores).evaluate(request(trigger="timer")).decision == "deny"
    assert gate(stores).evaluate(request(trigger="autonomous")).reason == "invalid trigger"
    assert gate(stores).evaluate(request(risk="extreme")).decision == "deny"


def test_gate_decisions_do_not_include_notify_only(stores):
    seen = {
        gate(stores).evaluate(
            request(action="unlock", scope="home.entry.lock", risk="high", trigger="user_initiated")
        ).decision,
        gate(stores).evaluate(
            request(action="unlock", scope="home.entry.lock", risk="high", trigger="scheduled")
        ).decision,
        gate(stores).evaluate(request(trigger="autonomous")).decision,
        gate(stores).evaluate(request()).decision,
    }

    assert seen <= {"allow", "deny", "ask_confirmation"}
    assert "notify_only" not in seen


def test_motion_signal_unknown_occupancy_makes_high_risk_user_ask_confirmation(stores):
    presence, _ = stores
    PresenceSignalIngestor(presence).ingest(
        MotionAdapter.from_event(MotionEvent(zone="home.entry", active=True))
    )

    decision = gate(stores).evaluate(
        request(action="unlock", scope="home.entry.lock", risk="high")
    )

    assert decision.decision == "ask_confirmation"
    assert decision.presence_status == "unknown"


def test_motion_signal_unknown_occupancy_makes_medium_scheduled_deny(stores):
    presence, _ = stores
    PresenceSignalIngestor(presence).ingest(
        MotionAdapter.from_event(MotionEvent(zone="home.entry", active=True))
    )

    decision = gate(stores).evaluate(
        request(
            action="set_temperature",
            scope="home.hvac",
            trigger="scheduled",
            risk="medium",
        )
    )

    assert decision.decision == "deny"
    assert decision.presence_status == "unknown"


def test_wifi_home_signal_blocks_requires_presence_empty_action(stores):
    presence, _ = stores
    PresenceSignalIngestor(presence).ingest(
        WifiPresenceAdapter.from_event(
            WifiDeviceEvent(
                registered_device_id="device_alice_phone",
                person_id="alice",
                online=True,
            )
        )
    )

    decision = gate(stores).evaluate(
        request(trigger="scheduled", risk="low", requires_presence_empty=True)
    )

    assert decision.decision == "deny"
    assert decision.presence_status == "occupied"


def test_phone_geofence_away_signals_can_drive_low_risk_scheduled_allow(stores):
    presence, _ = stores
    ingestor = PresenceSignalIngestor(presence)
    ingestor.ingest(
        PhoneGeofenceAdapter.from_event(
            PhoneGeofenceEvent(person_id="alice", state="outside_home", role="admin")
        )
    )
    ingestor.ingest(
        PhoneGeofenceAdapter.from_event(
            PhoneGeofenceEvent(person_id="bob", state="outside_home", role="resident")
        )
    )

    decision = gate(stores).evaluate(
        request(
            action="turn_off_light",
            scope="home.living_room.light",
            trigger="scheduled",
            risk="low",
        )
    )

    assert presence.resolve_occupancy().status == "empty"
    assert decision.decision == "allow"
    assert decision.presence_status == "empty"
