import json
from OriginAgent.domain_packs.smart_home.runtime.action_safety import SmartHomeActionSafetyGate
from datetime import datetime, timezone

import pytest

from OriginAgent.agent.action_runtime import ActionIntent, SafeActionExecutor

from OriginAgent.agent.audit import AuditLogger
from OriginAgent.agent.confirmation import ConfirmationManager
from OriginAgent.domain_packs.smart_home.runtime.device_actions import (
    DeviceActionSchemaRegistry,
    TypedActionPlanner,
    TypedDeviceAction,
)
from OriginAgent.domain_packs.smart_home.runtime.device_backends import DeviceActionExecutor
from OriginAgent.domain_packs.smart_home.runtime.device_integrations import RealLightingBackend
from OriginAgent.agent.facts import FactStore
from OriginAgent.domain_packs.smart_home.runtime.permissions import HouseholdActor, PermissionResolver
from OriginAgent.domain_packs.smart_home.runtime.presence import PresenceStore

NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
PRIVATE_DEVICE_ID = "private_device_7f3a9c"


class FakeLightingClient:
    def __init__(self):
        self.calls = []

    def set_power(self, device_id: str, power: str):
        self.calls.append(("set_power", device_id, power))
        return {"ok": True, "raw_device_id": device_id}

    def set_brightness(self, device_id: str, brightness: int):
        self.calls.append(("set_brightness", device_id, brightness))
        return {"ok": True, "raw_payload": {"brightness": brightness}}

    def set_color_temperature(self, device_id: str, temperature: str):
        self.calls.append(("set_color_temperature", device_id, temperature))
        return {"ok": True, "raw_client_response": temperature}


def build_chain(tmp_path, *, real_mode: bool):
    audit = AuditLogger(tmp_path)
    client = FakeLightingClient()
    backend = RealLightingBackend(client, real_mode=real_mode)
    safe_executor = SafeActionExecutor(
        gate=SmartHomeActionSafetyGate(PresenceStore(tmp_path), FactStore(tmp_path)),
        confirmation_manager=ConfirmationManager(tmp_path, audit_logger=audit),
        backend=backend,
        permission_resolver=PermissionResolver(
            {
                "admin_user": HouseholdActor("admin_user", "admin"),
                "guest_user": HouseholdActor("guest_user", "guest"),
            }
        ),
        audit_logger=audit,
    )
    executor = DeviceActionExecutor(
        TypedActionPlanner(DeviceActionSchemaRegistry()),
        safe_executor,
        audit_logger=audit,
    )
    return executor, safe_executor, backend, client, audit


def typed_action(**kwargs):
    defaults = {
        "action_type": "set_light_power",
        "domain": "lighting",
        "device_id": "ceiling_light",
        "room": "living_room",
        "parameters": {"power": "on"},
        "requested_by": "admin_user",
    }
    defaults.update(kwargs)
    return TypedDeviceAction(**defaults)


def raw_audit(tmp_path) -> str:
    audit_dir = tmp_path / "memory" / "audit"
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(audit_dir.glob("*.jsonl"))
    )


def audit_rows(tmp_path, filename: str) -> list[dict]:
    path = tmp_path / "memory" / "audit" / filename
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_admin_light_power_dry_run_runs_full_chain_without_client_call(tmp_path):
    executor, _, _, client, audit = build_chain(tmp_path, real_mode=False)

    result = executor.submit_typed(typed_action(), now=NOW)

    assert result.status == "dry_run"
    assert result.backend_called is True
    assert client.calls == []
    explanations = audit.explain_action(result.action_id)
    assert {event.event_type for event in explanations} == {
        "action_decision",
        "permission_decision",
    }
    action_row = audit_rows(tmp_path, "action_decisions.jsonl")[0]
    assert action_row["metadata"]["schema_validated"] == "True"
    assert action_row["metadata"]["backend_called"] == "True"


def test_real_mode_brightness_calls_fake_client_once_without_leaking_raw_response(tmp_path):
    executor, _, _, client, _ = build_chain(tmp_path, real_mode=True)

    result = executor.submit_typed(
        typed_action(
            action_type="set_light_brightness",
            device_id=PRIVATE_DEVICE_ID,
            parameters={"brightness": 50},
        ),
        now=NOW,
    )

    assert result.status == "executed"
    assert result.backend_called is True
    assert client.calls == [("set_brightness", PRIVATE_DEVICE_ID, 50)]
    backend_json = json.dumps(result.backend_result, ensure_ascii=False)
    assert "raw_device_id" not in backend_json
    assert "raw_payload" not in backend_json
    assert "raw_client_response" not in backend_json
    raw = raw_audit(tmp_path)
    assert PRIVATE_DEVICE_ID not in raw
    assert "home.living_room.lighting.<device>" in raw
    assert "raw_device_id" not in raw
    assert "raw_payload" not in raw
    assert "raw_client_response" not in raw
    assert '"brightness":50' not in raw
    assert '"brightness":"50"' not in raw
    assert "token" not in raw.casefold()
    assert "host" not in raw.casefold()
    assert "bearer" not in raw.casefold()
    assert result.is_real_execution is True
    assert result.backend_kind == "real_lighting"
    assert result.physical_target_domain == "lighting"


@pytest.mark.parametrize(
    ("action", "schema_error"),
    [
        (
            typed_action(
                action_type="set_light_brightness",
                parameters={"brightness": 200},
            ),
            "invalid_parameter",
        ),
        (
            typed_action(
                action_type="turn_off_all_lights",
                domain="lighting",
                device_id="all",
                room=None,
                parameters={},
            ),
            "unsupported_action",
        ),
        (
            typed_action(
                action_type="unlock_door",
                domain="lock",
                device_id="front_door",
                room=None,
                parameters={},
            ),
            "disabled_domain",
        ),
    ],
)
def test_schema_failures_stop_before_runtime_boundaries(tmp_path, action, schema_error):
    executor, safe_executor, _, client, _ = build_chain(tmp_path, real_mode=True)

    result = executor.submit_typed(action, now=NOW)

    assert result.status == "failed"
    assert result.backend_called is False
    assert client.calls == []
    assert safe_executor.confirmation_manager.store.read_all() == []
    assert audit_rows(tmp_path, "permission_decisions.jsonl") == []
    action_row = audit_rows(tmp_path, "action_decisions.jsonl")[0]
    assert action_row["metadata"]["schema_validated"] == "False"
    assert action_row["metadata"]["gate_decision"] == "not_evaluated"
    assert action_row["metadata"]["schema_error"] == schema_error


def test_raw_action_intent_direct_to_real_backend_is_rejected(tmp_path):
    _, _, backend, client, _ = build_chain(tmp_path, real_mode=True)

    with pytest.raises(ValueError, match="payload action_type"):
        backend.execute(
            ActionIntent(
                action="set_light_brightness",
                scope="home.living_room.lighting.ceiling_light",
                trigger="user_initiated",
                risk="low",
                requested_by="admin_user",
                payload={},
            )
        )

    assert client.calls == []


def test_guest_low_risk_lighting_action_is_allowed_and_explainable(tmp_path):
    executor, _, _, client, audit = build_chain(tmp_path, real_mode=False)

    result = executor.submit_typed(
        typed_action(
            device_id=PRIVATE_DEVICE_ID,
            parameters={"power": "on"},
            requested_by="guest_user",
        ),
        now=NOW,
    )

    assert result.status == "dry_run"
    assert result.backend_called is True
    assert result.permission_status == "allow"
    assert client.calls == []
    assert PRIVATE_DEVICE_ID not in raw_audit(tmp_path)
    permission_row = audit_rows(tmp_path, "permission_decisions.jsonl")[0]
    assert permission_row["metadata"]["actor_role"] == "guest"
    assert {event.event_type for event in audit.explain_action(result.action_id)} == {
        "action_decision",
        "permission_decision",
    }

