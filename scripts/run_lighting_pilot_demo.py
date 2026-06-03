from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from OriginAgent.agent.action_runtime import ActionIntent, SafeActionExecutor
from OriginAgent.agent.action_safety import ActionSafetyGate
from OriginAgent.agent.audit import AuditLogger
from OriginAgent.agent.confirmation import ConfirmationManager
from OriginAgent.agent.device_actions import (
    DeviceActionSchemaRegistry,
    TypedActionPlanner,
    TypedDeviceAction,
)
from OriginAgent.agent.device_backends import DeviceActionExecutor
from OriginAgent.agent.device_integrations import RealLightingBackend
from OriginAgent.agent.facts import FactStore
from OriginAgent.agent.permissions import HouseholdActor, PermissionResolver
from OriginAgent.agent.presence import PresenceStore


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


@dataclass
class Harness:
    workspace: Path
    audit: AuditLogger
    client: FakeLightingClient
    backend: RealLightingBackend
    executor: DeviceActionExecutor


def build_harness(workspace: Path, *, real_mode: bool) -> Harness:
    audit = AuditLogger(workspace)
    client = FakeLightingClient()
    backend = RealLightingBackend(client, real_mode=real_mode)
    safe_executor = SafeActionExecutor(
        gate=ActionSafetyGate(PresenceStore(workspace), FactStore(workspace)),
        confirmation_manager=ConfirmationManager(workspace, audit_logger=audit),
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
    return Harness(
        workspace=workspace,
        audit=audit,
        client=client,
        backend=backend,
        executor=executor,
    )


def light_action(**kwargs) -> TypedDeviceAction:
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


def print_result(name: str, expected_status: str, harness: Harness, action: TypedDeviceAction) -> None:
    before_calls = list(harness.client.calls)
    result = harness.executor.submit_typed(action)
    events = harness.audit.explain_action(result.action_id)
    passed = result.status == expected_status
    print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    print(f"  status={result.status}")
    print(f"  backend_called={result.backend_called}")
    print(f"  permission_status={result.permission_status}")
    print(f"  fake_client_calls={harness.client.calls[len(before_calls):]}")
    print("  explanation:")
    for event in events:
        metadata = event.metadata
        details = []
        detail = metadata.get("gate_decision") or metadata.get("permission")
        if detail:
            details.append(detail)
        schema = metadata.get("schema_validated")
        if schema is not None:
            details.append(f"schema_validated={schema}")
        suffix = " ".join(details)
        print(f"    - {event.event_type}: {event.decision}{(' ' + suffix) if suffix else ''}")


def print_raw_backend_rejection(harness: Harness) -> None:
    try:
        harness.backend.execute(
            ActionIntent(
                action="set_light_brightness",
                scope="home.living_room.lighting.ceiling_light",
                trigger="user_initiated",
                risk="low",
                requested_by="admin_user",
                payload={},
            )
        )
    except ValueError as exc:
        print("[PASS] raw ActionIntent direct backend rejection")
        print(f"  rejected={exc}")
    else:
        print("[FAIL] raw ActionIntent direct backend rejection")
        print("  rejected=False")


def main() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="originagent-lighting-pilot-"))
    dry_run_harness = build_harness(workspace / "dry-run", real_mode=False)
    real_mode_harness = build_harness(workspace / "real-mode", real_mode=True)

    print_result(
        "admin light power dry-run",
        "dry_run",
        dry_run_harness,
        light_action(),
    )
    print_result(
        "admin brightness fake real backend",
        "executed",
        real_mode_harness,
        light_action(
            action_type="set_light_brightness",
            parameters={"brightness": 50},
        ),
    )
    print_result(
        "invalid brightness rejected",
        "failed",
        dry_run_harness,
        light_action(
            action_type="set_light_brightness",
            parameters={"brightness": 200},
        ),
    )
    print_result(
        "turn_off_all_lights rejected",
        "failed",
        dry_run_harness,
        light_action(
            action_type="turn_off_all_lights",
            domain="lighting",
            device_id="all",
            room=None,
            parameters={},
        ),
    )
    print_result(
        "lock domain rejected",
        "failed",
        dry_run_harness,
        light_action(
            action_type="unlock_door",
            domain="lock",
            device_id="front_door",
            room=None,
            parameters={},
        ),
    )
    print_result(
        "guest low-risk light dry-run",
        "dry_run",
        dry_run_harness,
        light_action(
            device_id="lamp",
            parameters={"power": "on"},
            requested_by="guest_user",
        ),
    )
    print_raw_backend_rejection(real_mode_harness)
    print(f"audit_workspace={workspace}")
    for path in sorted(workspace.glob("**/memory/audit/*.jsonl")):
        print(f"audit_file={path}")


if __name__ == "__main__":
    main()
