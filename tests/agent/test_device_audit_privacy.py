import json
from datetime import datetime, timezone

from OpenHome.agent.action_runtime import SafeActionExecutor
from OpenHome.agent.action_safety import ActionSafetyGate
from OpenHome.agent.audit import AuditLogger
from OpenHome.agent.confirmation import ConfirmationManager
from OpenHome.agent.device_actions import DeviceActionSchemaRegistry, TypedActionPlanner, TypedDeviceAction
from OpenHome.agent.device_backends import DeviceActionExecutor
from OpenHome.agent.device_integrations import RealLightingBackend
from OpenHome.agent.devices import sanitize_device_scope
from OpenHome.agent.facts import FactStore
from OpenHome.agent.permissions import HouseholdActor, PermissionResolver
from OpenHome.agent.presence import PresenceStore

NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
PRIVATE_DEVICE_ID = "private_device_7f3a9c"
SECRET_PAYLOAD_VALUE = "secret_payload_value_9c31"


class RecordingLightingClient:
    def __init__(self):
        self.calls = []

    def set_power(self, device_id: str, power: str):
        self.calls.append(("set_power", device_id, power))
        return {"ok": True, "raw_device_id": device_id}

    def set_brightness(self, device_id: str, brightness: int):
        self.calls.append(("set_brightness", device_id, brightness))
        return {"ok": True}

    def set_color_temperature(self, device_id: str, temperature: str):
        self.calls.append(("set_color_temperature", device_id, temperature))
        return {"ok": True}


def _executor(tmp_path, *, real_mode: bool = True):
    audit = AuditLogger(tmp_path)
    client = RecordingLightingClient()
    backend = RealLightingBackend(client, real_mode=real_mode)
    safe_executor = SafeActionExecutor(
        gate=ActionSafetyGate(PresenceStore(tmp_path), FactStore(tmp_path)),
        confirmation_manager=ConfirmationManager(tmp_path, audit_logger=audit),
        backend=backend,
        permission_resolver=PermissionResolver(
            {"admin_user": HouseholdActor("admin_user", "admin")}
        ),
        audit_logger=audit,
    )
    return (
        DeviceActionExecutor(
            TypedActionPlanner(DeviceActionSchemaRegistry()),
            safe_executor,
            audit_logger=audit,
        ),
        client,
        audit,
    )


def _typed_action(**kwargs) -> TypedDeviceAction:
    values = {
        "action_type": "set_light_power",
        "domain": "lighting",
        "device_id": PRIVATE_DEVICE_ID,
        "room": "living_room",
        "parameters": {"power": "on"},
        "requested_by": "admin_user",
    }
    values.update(kwargs)
    return TypedDeviceAction(**values)


def _raw_action_audit(tmp_path) -> str:
    return (tmp_path / "memory" / "audit" / "action_decisions.jsonl").read_text(
        encoding="utf-8"
    )


def test_sanitize_device_scope_hides_leaf_device_id():
    assert (
        sanitize_device_scope(f"home.living_room.lighting.{PRIVATE_DEVICE_ID}")
        == "home.living_room.lighting.<device>"
    )
    assert sanitize_device_scope(f"home.lighting.{PRIVATE_DEVICE_ID}") == "home.lighting.<device>"
    assert sanitize_device_scope(None) is None
    assert sanitize_device_scope("") is None


def test_action_decision_persistence_sanitizes_scope(tmp_path):
    audit = AuditLogger(tmp_path)

    audit.log_action_decision(
        action_id="action_private",
        actor_id="admin_user",
        action="set_light_power",
        scope=f"home.living_room.lighting.{PRIVATE_DEVICE_ID}",
        risk="low",
        trigger="user_initiated",
        decision="dry_run",
        reason="ok",
        created_at=NOW,
    )

    raw = _raw_action_audit(tmp_path)
    assert PRIVATE_DEVICE_ID not in raw
    assert "home.living_room.lighting.<device>" in raw


def test_schema_failure_audit_hides_device_id_and_parameter_values(tmp_path):
    executor, client, _ = _executor(tmp_path, real_mode=True)

    result = executor.submit_typed(
        _typed_action(parameters={"power": SECRET_PAYLOAD_VALUE}),
        now=NOW,
    )

    raw = _raw_action_audit(tmp_path)
    row = json.loads(raw.splitlines()[0])
    assert result.status == "failed"
    assert client.calls == []
    assert PRIVATE_DEVICE_ID not in raw
    assert SECRET_PAYLOAD_VALUE not in raw
    assert "home.living_room.lighting.<device>" in raw
    assert row["metadata"]["schema_validated"] == "False"
    assert row["metadata"]["schema_error"] == "invalid_parameter"
    assert row["metadata"]["parameter_keys"] == '["power"]'


def test_backend_receives_real_device_id_while_audit_and_explain_are_sanitized(tmp_path):
    executor, client, audit = _executor(tmp_path, real_mode=True)

    result = executor.submit_typed(_typed_action(), now=NOW)

    assert result.status == "executed"
    assert client.calls == [("set_power", PRIVATE_DEVICE_ID, "on")]
    raw = _raw_action_audit(tmp_path)
    explanation = json.dumps(
        [event.to_dict() for event in audit.explain_action(result.action_id)],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert PRIVATE_DEVICE_ID not in raw
    assert PRIVATE_DEVICE_ID not in explanation
    assert "home.living_room.lighting.<device>" in raw
    assert "lighting" in explanation
    assert "executed" in explanation
