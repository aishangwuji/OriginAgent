import json
from datetime import datetime, timezone

import pytest

from OpenHome.agent.action_runtime import ActionIntent, SafeActionExecutor
from OpenHome.agent.action_safety import ActionDecision
from OpenHome.agent.audit import AuditLogger
from OpenHome.agent.confirmation import ConfirmationManager
from OpenHome.agent.device_actions import (
    DeviceActionSchemaRegistry,
    TypedActionPlanner,
    TypedDeviceAction,
)
from OpenHome.agent.device_backends import DeviceActionExecutor
from OpenHome.agent.device_integrations import RealLightingBackend
from OpenHome.agent.permissions import HouseholdActor, PermissionResolver

NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


class CountingGate:
    def __init__(self, decision):
        self.decision = decision
        self.requests = []

    def evaluate(self, request):
        self.requests.append(request)
        return self.decision


class FakeLightingClient:
    def __init__(self, *, exc=None):
        self.exc = exc
        self.calls = []

    def set_power(self, device_id, power):
        self._record("set_power", device_id, power)
        return {"client_endpoint": "client-endpoint", "client_auth": "client-auth", "device_id": device_id}

    def set_brightness(self, device_id, brightness):
        self._record("set_brightness", device_id, brightness)
        return {"client_endpoint": "client-endpoint", "raw_payload": {"brightness": brightness}}

    def set_color_temperature(self, device_id, temperature):
        self._record("set_color_temperature", device_id, temperature)
        return {"raw_client_response": temperature}

    def _record(self, operation, device_id, value):
        self.calls.append((operation, device_id, value))
        if self.exc is not None:
            raise self.exc


def decision(value):
    return ActionDecision(
        decision=value,
        reason=f"{value} reason",
        presence_status="unknown",
    )


def permissions():
    return PermissionResolver({"alice": HouseholdActor("alice", "admin")})


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


def intent(**kwargs):
    defaults = {
        "action": "set_light_brightness",
        "scope": "home.living_room.lighting.ceiling_light",
        "trigger": "user_initiated",
        "risk": "low",
        "requested_by": "alice",
        "payload": {
            "action_type": "set_light_brightness",
            "domain": "lighting",
            "device_id": "ceiling_light",
            "brightness": "45",
        },
    }
    defaults.update(kwargs)
    return ActionIntent(**defaults)


def safe_executor(tmp_path, backend, *, audit_logger=None, gate=None):
    return SafeActionExecutor(
        gate=gate or CountingGate(decision("allow")),
        confirmation_manager=ConfirmationManager(tmp_path, audit_logger=audit_logger),
        backend=backend,
        permission_resolver=permissions(),
        audit_logger=audit_logger,
    )


def device_executor(tmp_path, backend, *, audit_logger=None, gate=None):
    return DeviceActionExecutor(
        TypedActionPlanner(DeviceActionSchemaRegistry()),
        safe_executor(tmp_path, backend, audit_logger=audit_logger, gate=gate),
        audit_logger=audit_logger,
    )


def raw_action_audit(tmp_path):
    return (tmp_path / "memory" / "audit" / "action_decisions.jsonl").read_text(
        encoding="utf-8"
    )


def raw_audit(tmp_path):
    audit_dir = tmp_path / "memory" / "audit"
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(audit_dir.glob("*.jsonl"))
    )


def test_real_lighting_backend_default_dry_run_does_not_call_client():
    client = FakeLightingClient()

    result = RealLightingBackend(client).execute(intent())

    assert client.calls == []
    assert result == {
        "backend": "real_lighting",
        "accepted": True,
        "dry_run": True,
        "device_id_present": True,
        "operation": "set_brightness",
    }
    assert "ceiling_light" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(
    ("action_type", "parameters", "expected_call"),
    [
        ("set_light_power", {"power": "on"}, ("set_power", "ceiling_light", "on")),
        ("set_light_brightness", {"brightness": 72}, ("set_brightness", "ceiling_light", 72)),
        (
            "set_light_color_temperature",
            {"temperature": "cool"},
            ("set_color_temperature", "ceiling_light", "cool"),
        ),
    ],
)
def test_real_mode_calls_fake_lighting_client_exactly_once(
    tmp_path,
    action_type,
    parameters,
    expected_call,
):
    client = FakeLightingClient()
    executor = device_executor(tmp_path, RealLightingBackend(client, real_mode=True))

    result = executor.submit_typed(
        typed_action(action_type=action_type, parameters=parameters),
        now=NOW,
    )

    assert result.status == "executed"
    assert client.calls == [expected_call]
    assert result.backend_result["backend"] == "real_lighting"
    assert result.backend_result["device_id_present"] == "True"
    assert "ceiling_light" not in json.dumps(result.backend_result, ensure_ascii=False)


@pytest.mark.parametrize(
    "bad_intent",
    [
        intent(scope="home.living_room.media.ceiling_light"),
        intent(payload={"action_type": "set_light_brightness", "domain": "media", "device_id": "ceiling_light", "brightness": "45"}),
    ],
)
def test_real_lighting_backend_rejects_non_lighting_scope_or_domain(bad_intent):
    with pytest.raises(ValueError):
        RealLightingBackend(FakeLightingClient(), real_mode=True).execute(bad_intent)


@pytest.mark.parametrize(
    "payload",
    [
        {"domain": "lighting", "device_id": "ceiling_light", "brightness": "45"},
        {"action_type": "set_light_brightness", "device_id": "ceiling_light", "brightness": "45"},
        {"action_type": "set_light_brightness", "domain": "lighting", "brightness": "45"},
    ],
)
def test_real_lighting_backend_rejects_missing_schema_markers(payload):
    with pytest.raises(ValueError):
        RealLightingBackend(FakeLightingClient(), real_mode=True).execute(intent(payload=payload))


@pytest.mark.parametrize(
    "payload",
    [
        {
            "action_type": "set_light_power",
            "domain": "lighting",
            "device_id": "ceiling_light",
            "brightness": "45",
        },
        {
            "action_type": "set_light_brightness",
            "domain": "media",
            "device_id": "ceiling_light",
            "brightness": "45",
        },
        {
            "action_type": "set_light_brightness",
            "domain": "lighting",
            "device_id": "other_light",
            "brightness": "45",
        },
    ],
)
def test_real_lighting_backend_rejects_schema_marker_mismatch(payload):
    with pytest.raises(ValueError):
        RealLightingBackend(FakeLightingClient(), real_mode=True).execute(intent(payload=payload))


def test_real_lighting_backend_rejects_turn_off_all_lights():
    with pytest.raises(ValueError, match="unsupported lighting action"):
        RealLightingBackend(FakeLightingClient(), real_mode=True).execute(
            intent(
                action="turn_off_all_lights",
                scope="home.lighting.all",
                payload={
                    "action_type": "turn_off_all_lights",
                    "domain": "lighting",
                    "device_id": "all",
                },
            )
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "action_type": "set_light_brightness",
            "domain": "lighting",
            "device_id": "ceiling_light",
            "brightness": "101",
        },
        {
            "action_type": "set_light_brightness",
            "domain": "lighting",
            "device_id": "ceiling_light",
            "brightness": "45.5",
        },
        {
            "action_type": "set_light_color_temperature",
            "domain": "lighting",
            "device_id": "ceiling_light",
            "temperature": "blue",
        },
    ],
)
def test_real_lighting_backend_rechecks_payload_parameters(payload):
    action = payload["action_type"]

    with pytest.raises(ValueError):
        RealLightingBackend(FakeLightingClient(), real_mode=True).execute(
            intent(action=action, payload=payload)
        )


def test_client_exception_returns_failed_through_safe_action_executor(tmp_path):
    client = FakeLightingClient(exc=RuntimeError("client execution failed"))
    executor = safe_executor(tmp_path, RealLightingBackend(client, real_mode=True))

    result = executor.submit(intent(), now=NOW)

    assert client.calls == [("set_brightness", "ceiling_light", 45)]
    assert result.status == "failed"
    assert result.backend_called is True
    assert result.backend_result == {}
    assert "client execution failed" in result.reason


def test_audit_does_not_contain_device_or_payload_or_client_details(tmp_path):
    audit = AuditLogger(tmp_path)
    client = FakeLightingClient()
    executor = device_executor(
        tmp_path,
        RealLightingBackend(client, real_mode=True),
        audit_logger=audit,
    )

    result = executor.submit_typed(
        typed_action(parameters={"brightness": 88}),
        now=NOW,
    )

    action_raw = raw_action_audit(tmp_path)
    row = json.loads(action_raw.splitlines()[0])
    raw = raw_audit(tmp_path)
    assert result.status == "executed"
    assert row["metadata"]["backend_called"] == "True"
    assert row["metadata"]["backend_result_keys"] == (
        '["accepted","backend","device_id_present","dry_run","operation"]'
    )
    assert "client-auth" not in raw
    assert "client-endpoint" not in raw
    assert "ceiling_light" not in raw
    assert '"brightness":"88"' not in raw
    assert '"brightness":88' not in raw
    assert "raw_payload" not in raw
    assert "raw_client_response" not in raw


def test_valid_typed_lighting_action_executes_full_device_action_path(tmp_path):
    client = FakeLightingClient()
    gate = CountingGate(decision("allow"))
    executor = device_executor(
        tmp_path,
        RealLightingBackend(client, real_mode=True),
        gate=gate,
    )

    result = executor.submit_typed(typed_action(parameters={"brightness": 64}), now=NOW)

    assert result.status == "executed"
    assert len(gate.requests) == 1
    assert client.calls == [("set_brightness", "ceiling_light", 64)]
    assert result.permission_status == "allow"


def test_direct_raw_action_intent_without_typed_payload_is_rejected(tmp_path):
    client = FakeLightingClient()
    executor = safe_executor(tmp_path, RealLightingBackend(client, real_mode=True))

    result = executor.submit(
        intent(payload={}),
        now=NOW,
    )

    assert result.status == "failed"
    assert client.calls == []
    assert result.backend_called is True
