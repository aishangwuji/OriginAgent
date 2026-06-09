import json
from OriginAgent.domain_packs.smart_home.runtime.action_safety import SmartHomeActionSafetyGate
from datetime import datetime, timezone

import pytest

from OriginAgent.agent.action_continuity import ActionContinuityInputs, ActionWorldView
from OriginAgent.agent.action_runtime import ActionIntent, SafeActionExecutor
from OriginAgent.agent.action_safety import ActionDecision
from OriginAgent.agent.audit import AuditLogger
from OriginAgent.agent.confirmation import ConfirmationManager
from OriginAgent.agent.identity import RuntimeContext
from OriginAgent.domain_packs.smart_home.runtime.device_actions import DeviceActionSchemaRegistry, TypedActionPlanner, TypedDeviceAction
from OriginAgent.domain_packs.smart_home.runtime.device_backends import DeviceActionExecutor, LowRiskDeviceBackend
from OriginAgent.domain_packs.smart_home.runtime.action_automation import ActionAutomationPreconditionGate
from OriginAgent.domain_packs.smart_home.runtime.devices import DeviceRecord, DeviceRegistry
from OriginAgent.agent.facts import FactStore
from OriginAgent.domain_packs.smart_home.runtime.permissions import HouseholdActor, PermissionResolver
from OriginAgent.domain_packs.smart_home.runtime.presence import PresenceStore

NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


class CountingGate:
    def __init__(self, decision):
        self.decision = decision
        self.requests = []

    def evaluate(self, request):
        self.requests.append(request)
        return self.decision


class CountingBackend:
    def __init__(self, result=None):
        self.result = result if result is not None else {"dry_run": True}
        self.calls = 0
        self.seen_intents = []

    def execute(self, intent):
        self.calls += 1
        self.seen_intents.append(intent)
        return self.result


def decision(value, **kwargs):
    defaults = {
        "decision": value,
        "reason": f"{value} reason",
        "presence_status": "unknown",
    }
    defaults.update(kwargs)
    return ActionDecision(**defaults)


def permissions(**actors):
    defaults = {"alice": HouseholdActor("alice", "admin"), "bob": HouseholdActor("bob", "resident")}
    defaults.update(actors)
    return PermissionResolver(defaults)


def typed_action(**kwargs):
    defaults = {
        "action_type": "set_light_brightness",
        "device_id": "ceiling_light",
        "domain": "lighting",
        "room": "living_room",
        "parameters": {"brightness": 45},
        "requested_by": "alice",
    }
    defaults.update(kwargs)
    return TypedDeviceAction(**defaults)


def continuity_inputs(*, fresh: bool = True, contested: bool = False) -> ActionContinuityInputs:
    runtime_context = RuntimeContext(
        actor_id="alice",
        user_id="alice",
        session_id="cli:home",
        device_id="device-a",
        trigger="automation",
        channel="cli",
        chat_id="home",
        session_key="cli:home",
        source="automation",
        default_scope="session",
    )
    return ActionContinuityInputs(
        runtime_context=runtime_context,
        working_memory={"priority_facts": ["lights should help visibility"], "attention_items": [], "tool_residue": []},
        world_view=ActionWorldView(
            included_summary={"summary_id": "world_1", "focus": ["living room looks dark"]},
            contested_summary={"contested": contested, "items": ["disagreement"] if contested else []},
            freshness={"is_fresh": fresh, "generated_at": NOW.isoformat(), "fresh_until": NOW.isoformat()},
            selection_reasons=["fresh_visible_fallback"],
        ),
        governance_summary={},
        retrieval_hints={},
        pending_confirmations=[],
    )


def device_executor(tmp_path, gate=None, backend=None, audit_logger=None, permission_resolver=None):
    safe_executor = SafeActionExecutor(
        gate=gate or CountingGate(decision("allow")),
        confirmation_manager=ConfirmationManager(tmp_path, audit_logger=audit_logger),
        backend=backend or CountingBackend(),
        permission_resolver=permission_resolver or permissions(),
        audit_logger=audit_logger,
    )
    return DeviceActionExecutor(
        TypedActionPlanner(DeviceActionSchemaRegistry()),
        safe_executor,
        audit_logger=audit_logger,
    )


@pytest.mark.parametrize(
    "intent",
    [
        ActionIntent(
            action="set_light_power",
            scope="home.living_room.lighting.ceiling_light",
            trigger="user_initiated",
            risk="low",
            payload={
                "action_type": "set_light_power",
                "domain": "lighting",
                "device_id": "ceiling_light",
            },
        ),
        ActionIntent(
            action="set_media_volume",
            scope="home.living_room.media.speaker",
            trigger="user_initiated",
            risk="low",
            payload={
                "action_type": "set_media_volume",
                "domain": "media",
                "device_id": "speaker",
            },
        ),
        ActionIntent(
            action="set_temperature",
            scope="home.bedroom.climate.thermostat",
            trigger="user_initiated",
            risk="medium",
            payload={
                "action_type": "set_temperature",
                "domain": "climate",
                "device_id": "thermostat",
            },
        ),
    ],
)
def test_low_risk_backend_accepts_supported_domains(intent):
    result = LowRiskDeviceBackend().execute(intent)

    assert result["dry_run"] is True
    assert result["backend"] == "low_risk_mock"
    assert result["accepted"] is True


def test_low_risk_backend_rejects_raw_action_intent_without_schema_payload():
    intent = ActionIntent(
        action="set_light_brightness",
        scope="home.living_room.lighting.ceiling_light",
        trigger="user_initiated",
        risk="low",
        payload={},
    )

    with pytest.raises(ValueError, match="payload action_type"):
        LowRiskDeviceBackend().execute(intent)


@pytest.mark.parametrize(
    "payload",
    [
        {"domain": "lighting", "device_id": "ceiling_light"},
        {"action_type": "set_light_brightness", "device_id": "ceiling_light"},
        {"action_type": "set_light_brightness", "domain": "lighting"},
    ],
)
def test_low_risk_backend_rejects_missing_schema_markers(payload):
    intent = ActionIntent(
        action="set_light_brightness",
        scope="home.living_room.lighting.ceiling_light",
        trigger="user_initiated",
        risk="low",
        payload=payload,
    )

    with pytest.raises(ValueError):
        LowRiskDeviceBackend().execute(intent)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "action_type": "set_light_power",
            "domain": "lighting",
            "device_id": "ceiling_light",
        },
        {
            "action_type": "set_light_brightness",
            "domain": "media",
            "device_id": "ceiling_light",
        },
        {
            "action_type": "set_light_brightness",
            "domain": "lighting",
            "device_id": "other_light",
        },
    ],
)
def test_low_risk_backend_rejects_schema_marker_mismatch(payload):
    intent = ActionIntent(
        action="set_light_brightness",
        scope="home.living_room.lighting.ceiling_light",
        trigger="user_initiated",
        risk="low",
        payload=payload,
    )

    with pytest.raises(ValueError):
        LowRiskDeviceBackend().execute(intent)


@pytest.mark.parametrize("scope", ["home.entry.lock", "home.security.alarm", "home.camera.front"])
def test_low_risk_backend_rejects_high_risk_scopes(scope):
    intent = ActionIntent(
        action="set_light_power",
        scope=scope,
        trigger="user_initiated",
        risk="low",
        payload={
            "action_type": "set_light_power",
            "domain": "lighting",
            "device_id": "ceiling_light",
        },
    )

    with pytest.raises(ValueError):
        LowRiskDeviceBackend().execute(intent)


def test_low_risk_backend_result_does_not_echo_raw_payload_values():
    intent = ActionIntent(
        action="set_light_brightness",
        scope="home.living_room.lighting.ceiling_light",
        trigger="user_initiated",
        risk="low",
        payload={
            "action_type": "set_light_brightness",
            "domain": "lighting",
            "brightness": "77",
            "device_id": "ceiling_light",
        },
    )

    result = LowRiskDeviceBackend().execute(intent)

    assert "77" not in json.dumps(result, ensure_ascii=False)
    assert "ceiling_light" not in json.dumps(result, ensure_ascii=False)


def test_device_action_executor_submits_valid_action_through_safe_executor(tmp_path):
    backend = CountingBackend({"dry_run": True, "backend": "test"})
    executor = device_executor(tmp_path, backend=backend)

    result = executor.submit_typed(typed_action(parameters={"brightness": 52}), now=NOW)

    assert result.status == "dry_run"
    assert backend.calls == 1
    assert backend.seen_intents[0].action == "set_light_brightness"
    assert backend.seen_intents[0].risk == "low"


def test_low_risk_lighting_action_reaches_mock_backend_when_allowed(tmp_path):
    executor = device_executor(tmp_path, backend=LowRiskDeviceBackend())

    result = executor.submit_typed(typed_action(parameters={"brightness": 30}), now=NOW)

    assert result.status == "dry_run"
    assert result.backend_result["backend"] == "low_risk_mock"
    assert result.backend_result["domain"] == "lighting"
    assert result.backend_result["action"] == "set_light_brightness"


def test_schema_failure_returns_failed_without_gate_backend_or_confirmation(tmp_path):
    audit = AuditLogger(tmp_path)
    gate = CountingGate(decision("allow"))
    backend = CountingBackend()
    executor = device_executor(tmp_path, gate=gate, backend=backend, audit_logger=audit)

    result = executor.submit_typed(
        typed_action(parameters={"brightness": 200}),
        now=NOW,
    )

    assert result.status == "failed"
    assert "invalid typed device action" in result.reason
    assert gate.requests == []
    assert backend.calls == 0
    assert executor.safe_executor.confirmation_manager.store.read_all() == []
    assert not (tmp_path / "memory" / "audit" / "permission_decisions.jsonl").exists()


def test_schema_failure_audit_contains_no_parameter_values(tmp_path):
    audit = AuditLogger(tmp_path)
    executor = device_executor(tmp_path, audit_logger=audit)

    result = executor.submit_typed(
        typed_action(
            action_type="turn_off_all_lights",
            device_id="all",
            room=None,
            parameters={"brightness": 200},
        ),
        now=NOW,
    )

    raw = (tmp_path / "memory" / "audit" / "action_decisions.jsonl").read_text(
        encoding="utf-8"
    )
    row = json.loads(raw.splitlines()[0])
    assert result.status == "failed"
    assert row["decision"] == "failed"
    assert row["metadata"]["gate_decision"] == "not_evaluated"
    assert row["metadata"]["schema_validated"] == "False"
    assert row["metadata"]["typed_action_type"] == "turn_off_all_lights"
    assert row["metadata"]["typed_action_domain"] == "lighting"
    assert row["metadata"]["parameter_keys"] == '["brightness"]'
    assert "200" not in raw


def test_disabled_lock_domain_fails_before_runtime(tmp_path):
    gate = CountingGate(decision("allow"))
    backend = CountingBackend()
    executor = device_executor(tmp_path, gate=gate, backend=backend)

    result = executor.submit_typed(
        typed_action(
            action_type="set_light_power",
            domain="lock",
            device_id="front_door",
            parameters={"power": "on"},
        ),
        now=NOW,
    )

    assert result.status == "failed"
    assert gate.requests == []
    assert backend.calls == 0


def test_medium_climate_action_follows_existing_gate_and_permission_path(tmp_path):
    presence = PresenceStore(tmp_path)
    facts = FactStore(tmp_path)
    backend = LowRiskDeviceBackend()
    safe_executor = SafeActionExecutor(
        gate=SmartHomeActionSafetyGate(presence, facts),
        confirmation_manager=ConfirmationManager(tmp_path),
        backend=backend,
        permission_resolver=permissions(),
    )
    executor = DeviceActionExecutor(
        TypedActionPlanner(DeviceActionSchemaRegistry()),
        safe_executor,
    )

    result = executor.submit_typed(
        typed_action(
            action_type="set_temperature",
            domain="climate",
            device_id="thermostat",
            room="bedroom",
            parameters={"temperature_c": 22.5},
            requested_by="bob",
        ),
        now=NOW,
    )

    assert result.status == "dry_run"
    assert result.decision.decision == "allow"
    assert result.permission_status == "allow"
    assert result.backend_result["domain"] == "climate"


def test_automation_precondition_gate_denies_contested_world_state() -> None:
    gate = ActionAutomationPreconditionGate(
        device_registry=DeviceRegistry(
            [DeviceRecord(device_id="ceiling_light", domain="lighting", room="living_room", device_ref="ceiling_light")]
        ),
    )

    decision_result = gate.evaluate(typed_action(trigger="automation"), continuity_inputs(contested=True))

    assert decision_result.outcome == "denied"
    assert "contested" in decision_result.reason


def test_automation_precondition_gate_audit_marks_fact_check_delegated() -> None:
    gate = ActionAutomationPreconditionGate(
        device_registry=DeviceRegistry(
            [DeviceRecord(device_id="ceiling_light", domain="lighting", room="living_room", device_ref="ceiling_light")]
        ),
    )

    decision_result = gate.evaluate(typed_action(trigger="automation"), continuity_inputs())

    assert decision_result.outcome == "allow"
    assert decision_result.audit["supporting_facts_check"] == "delegated_to_safety_gate"


def test_submit_automation_uses_precondition_gate_before_safe_executor(tmp_path):
    backend = CountingBackend({"dry_run": True, "backend": "test"})
    safe_executor = SafeActionExecutor(
        gate=CountingGate(decision("allow")),
        confirmation_manager=ConfirmationManager(tmp_path),
        backend=backend,
        permission_resolver=permissions(),
    )
    executor = DeviceActionExecutor(
        TypedActionPlanner(DeviceActionSchemaRegistry()),
        safe_executor,
        automation_gate=ActionAutomationPreconditionGate(
            device_registry=DeviceRegistry(
                [
                    DeviceRecord(
                        device_id="ceiling_light",
                        domain="lighting",
                        room="living_room",
                        device_ref="ceiling_light",
                    )
                ]
            ),
        ),
    )

    result, precondition = executor.submit_automation(
        typed_action(trigger="automation"),
        continuity_inputs=continuity_inputs(),
        now=NOW,
    )

    assert precondition.outcome == "allow"
    assert result.status == "dry_run"
    assert backend.calls == 1

