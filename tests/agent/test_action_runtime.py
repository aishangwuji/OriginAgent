import json
from datetime import datetime, timezone

from OriginAgent.agent.action_runtime import (
    ActionIntent,
    DryRunActionBackend,
    SafeActionExecutor,
    sanitize_action_payload,
)
from OriginAgent.agent.action_safety import ActionDecision, ActionRequest, ActionSafetyGate
from OriginAgent.agent.audit import AuditLogger
from OriginAgent.agent.confirmation import ConfirmationManager
from OriginAgent.agent.device_actions import DeviceActionSchemaRegistry, TypedActionPlanner, TypedDeviceAction
from OriginAgent.agent.device_backends import DeviceActionExecutor, LowRiskDeviceBackend
from OriginAgent.agent.facts import FactStore
from OriginAgent.agent.permissions import HouseholdActor, PermissionResolver
from OriginAgent.agent.presence import PresenceStore
from OriginAgent.agent.presence_adapters import (
    MotionAdapter,
    MotionEvent,
    PhoneGeofenceAdapter,
    PhoneGeofenceEvent,
    WifiDeviceEvent,
    WifiPresenceAdapter,
)
from OriginAgent.agent.presence_signals import PresenceSignalIngestor

NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)


class FakeGate:
    def __init__(self, decision):
        self.decision = decision
        self.requests = []

    def evaluate(self, request):
        self.requests.append(request)
        return self.decision


class CountingBackend:
    def __init__(self, result=None, exc=None):
        self.result = result if result is not None else {"dry_run": True}
        self.exc = exc
        self.calls = 0
        self.seen_intents = []

    def execute(self, intent):
        self.calls += 1
        self.seen_intents.append(intent)
        if self.exc is not None:
            raise self.exc
        return self.result


class FailingConfirmationManager:
    def create_from_action_decision(
        self,
        request,
        decision,
        *,
        now=None,
        metadata=None,
        action_payload=None,
        idempotency_key=None,
    ):
        raise OSError("token=supersecretvalue")


class FailingAuditLogger:
    def log_action_decision(self, **kwargs):
        raise OSError("disk full")

    def log_permission_decision(self, **kwargs):
        raise OSError("disk full")


def decision(value, **kwargs):
    defaults = {
        "decision": value,
        "reason": f"{value} reason",
        "presence_status": "unknown",
    }
    defaults.update(kwargs)
    return ActionDecision(**defaults)


def intent(**kwargs):
    defaults = {
        "action": "turn_on",
        "scope": "home.living_room.light",
        "trigger": "user_initiated",
        "risk": "low",
        "requested_by": "alice",
    }
    defaults.update(kwargs)
    return ActionIntent(**defaults)


def permissions(**actors):
    defaults = {"alice": HouseholdActor("alice", "admin")}
    defaults.update(actors)
    return PermissionResolver(defaults)


def real_executor(tmp_path, backend=None, permission_resolver=None):
    presence = PresenceStore(tmp_path)
    facts = FactStore(tmp_path)
    return (
        SafeActionExecutor(
            gate=ActionSafetyGate(presence, facts),
            confirmation_manager=ConfirmationManager(tmp_path),
            backend=backend or DryRunActionBackend(),
            permission_resolver=permission_resolver or permissions(),
        ),
        presence,
        facts,
    )


def fake_executor(tmp_path, action_decision, backend=None, permission_resolver=None):
    backend = backend or CountingBackend()
    return (
        SafeActionExecutor(
            gate=FakeGate(action_decision),
            confirmation_manager=ConfirmationManager(tmp_path),
            backend=backend,
            permission_resolver=permission_resolver or permissions(),
        ),
        backend,
    )


def test_allow_calls_backend_exactly_once(tmp_path):
    backend = CountingBackend({"dry_run": "true", "ok": "yes"})
    executor, _ = fake_executor(tmp_path, decision("allow"), backend)

    result = executor.submit(intent(), now=NOW)

    assert backend.calls == 1
    assert result.status == "dry_run"
    assert result.backend_result["ok"] == "yes"
    assert executor.records[0].result_status == "dry_run"
    assert executor.records[0].executed_at == NOW.isoformat()


def test_gate_not_allow_never_calls_backend(tmp_path):
    cases = [
        (decision("ask_confirmation"), "pending_confirmation"),
        (decision("notify_only"), "notified"),
        (decision("deny"), "denied"),
        (decision("surprise"), "failed"),
    ]

    for index, (action_decision, expected_status) in enumerate(cases):
        backend = CountingBackend()
        executor, _ = fake_executor(tmp_path / f"case_{index}", action_decision, backend)

        result = executor.submit(intent(), now=NOW)

        assert result.status == expected_status
        assert backend.calls == 0


def test_low_risk_user_action_executes_through_dry_run_backend(tmp_path):
    executor, _, _ = real_executor(tmp_path)

    result = executor.submit(
        intent(payload={"brightness": 50}, idempotency_key="idem-1"),
        now=NOW,
    )

    assert result.status == "dry_run"
    assert result.decision.decision == "allow"
    assert result.backend_result["dry_run"] == "True"
    assert result.backend_result["action"] == "turn_on"
    assert result.backend_result["payload"] == '{"brightness":"50"}'
    assert result.backend_result["idempotency_key"] == "idem-1"


def test_submit_allow_logs_action_and_permission_decisions(tmp_path):
    audit = AuditLogger(tmp_path)
    backend = CountingBackend({"dry_run": True, "ok": "yes"})
    executor = SafeActionExecutor(
        gate=FakeGate(decision("allow", presence_status="empty")),
        confirmation_manager=ConfirmationManager(tmp_path),
        backend=backend,
        permission_resolver=permissions(),
        audit_logger=audit,
    )

    result = executor.submit(
        intent(payload={"brightness": 50}, idempotency_key="idem-1"),
        now=NOW,
    )

    action_rows = json.loads(
        (tmp_path / "memory" / "audit" / "action_decisions.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()[0]
    )
    permission_rows = json.loads(
        (tmp_path / "memory" / "audit" / "permission_decisions.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()[0]
    )
    assert result.status == "dry_run"
    assert action_rows["action_id"] == result.action_id
    assert action_rows["decision"] == "dry_run"
    assert action_rows["metadata"]["gate_decision"] == "allow"
    assert action_rows["metadata"]["backend_called"] == "True"
    assert action_rows["metadata"]["payload_keys"] == '["brightness"]'
    assert action_rows["metadata"]["backend_result_keys"] == '["dry_run","ok"]'
    assert action_rows["metadata"]["idempotency_key_present"] == "True"
    assert permission_rows["action_id"] == result.action_id
    assert permission_rows["metadata"]["permission"] == "execute_action"
    assert permission_rows["decision"] == "allow"


def test_valid_typed_action_logs_schema_metadata_without_parameter_values(tmp_path):
    audit = AuditLogger(tmp_path)
    executor = SafeActionExecutor(
        gate=FakeGate(decision("allow", presence_status="empty")),
        confirmation_manager=ConfirmationManager(tmp_path, audit_logger=audit),
        backend=LowRiskDeviceBackend(),
        permission_resolver=permissions(),
        audit_logger=audit,
    )
    typed_executor = DeviceActionExecutor(
        TypedActionPlanner(DeviceActionSchemaRegistry()),
        executor,
    )

    result = typed_executor.submit_typed(
        TypedDeviceAction(
            action_type="set_light_brightness",
            device_id="ceiling_light",
            domain="lighting",
            room="living_room",
            parameters={"brightness": 88},
            requested_by="alice",
        ),
        now=NOW,
    )

    raw = (tmp_path / "memory" / "audit" / "action_decisions.jsonl").read_text(
        encoding="utf-8"
    )
    row = json.loads(raw.splitlines()[0])
    assert result.status == "dry_run"
    assert row["metadata"]["schema_validated"] == "True"
    assert row["metadata"]["typed_action_type"] == "set_light_brightness"
    assert row["metadata"]["typed_action_domain"] == "lighting"
    assert row["metadata"]["payload_keys"] == '["action_type","brightness","device_id","domain"]'
    assert "88" not in json.dumps(row["metadata"], sort_keys=True)
    assert "88" not in json.dumps(row["action"], sort_keys=True)


def test_ask_confirmation_creates_pending_confirmation_without_backend_call(tmp_path):
    backend = CountingBackend()
    executor, _ = fake_executor(tmp_path, decision("ask_confirmation"), backend)

    result = executor.submit(
        intent(action="unlock", scope="home.entry.lock", risk="high"),
        now=NOW,
    )

    assert backend.calls == 0
    assert result.status == "pending_confirmation"
    assert result.confirmation_id is not None
    pending = executor.confirmation_manager.store.read_all()
    assert pending[0].confirmation_id == result.confirmation_id
    assert pending[0].action == "unlock"


def test_submit_ask_confirmation_logs_action_and_permission_decisions(tmp_path):
    audit = AuditLogger(tmp_path)
    backend = CountingBackend()
    manager = ConfirmationManager(tmp_path, audit_logger=audit)
    executor = SafeActionExecutor(
        gate=FakeGate(decision("ask_confirmation")),
        confirmation_manager=manager,
        backend=backend,
        permission_resolver=permissions(),
        audit_logger=audit,
    )

    result = executor.submit(
        intent(action="unlock", scope="home.entry.lock", risk="high"),
        now=NOW,
    )

    action_row = json.loads(
        (tmp_path / "memory" / "audit" / "action_decisions.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()[0]
    )
    permission_row = json.loads(
        (tmp_path / "memory" / "audit" / "permission_decisions.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()[0]
    )
    assert result.status == "pending_confirmation"
    assert action_row["confirmation_id"] == result.confirmation_id
    assert action_row["metadata"]["permission_status"] == "allow"
    assert permission_row["action_id"] == result.action_id
    assert permission_row["metadata"]["permission"] == "confirm_action"


def test_ask_confirmation_creation_failure_returns_failed_without_backend_call(tmp_path):
    backend = CountingBackend()
    executor = SafeActionExecutor(
        gate=FakeGate(decision("ask_confirmation")),
        confirmation_manager=FailingConfirmationManager(),
        backend=backend,
        permission_resolver=permissions(),
    )

    result = executor.submit(intent(action="unlock", risk="high"), now=NOW)

    assert result.status == "failed"
    assert "supersecretvalue" not in result.reason
    assert backend.calls == 0
    assert executor.records[0].result_status == "failed"


def test_notify_only_creates_non_executable_notification_without_backend_call(tmp_path):
    backend = CountingBackend()
    executor, _ = fake_executor(tmp_path, decision("notify_only"), backend)

    result = executor.submit(intent(action="notify_user"), now=NOW)

    assert backend.calls == 0
    assert result.status == "notified"
    assert result.confirmation_id is not None
    notifications = executor.confirmation_manager.store.read_all()
    assert notifications[0].confirmation_id == result.confirmation_id


def test_notify_only_creation_failure_returns_failed_without_backend_call(tmp_path):
    backend = CountingBackend()
    executor = SafeActionExecutor(
        gate=FakeGate(decision("notify_only")),
        confirmation_manager=FailingConfirmationManager(),
        backend=backend,
    )

    result = executor.submit(intent(action="notify_user"), now=NOW)

    assert result.status == "failed"
    assert "supersecretvalue" not in result.reason
    assert backend.calls == 0
    assert executor.records[0].result_status == "failed"


def test_deny_creates_no_executable_confirmation_and_no_backend_call(tmp_path):
    backend = CountingBackend()
    executor, _ = fake_executor(tmp_path, decision("deny"), backend)

    result = executor.submit(intent(), now=NOW)

    assert result.status == "denied"
    assert backend.calls == 0
    assert executor.confirmation_manager.list_action_confirmations() == []


def test_gate_allow_still_asks_admin_if_actor_lacks_execute_permission(tmp_path):
    backend = CountingBackend()
    executor, _ = fake_executor(
        tmp_path,
        decision("allow"),
        backend,
        permission_resolver=permissions(bob=HouseholdActor("bob", "resident")),
    )

    result = executor.submit(
        intent(
            action="unlock",
            scope="home.entry.lock",
            risk="low",
            requested_by="bob",
        ),
        now=NOW,
    )

    assert result.status == "ask_admin"
    assert backend.calls == 0


def test_permission_ask_admin_logs_permission_decision(tmp_path):
    audit = AuditLogger(tmp_path)
    backend = CountingBackend()
    executor = SafeActionExecutor(
        gate=FakeGate(decision("allow")),
        confirmation_manager=ConfirmationManager(tmp_path),
        backend=backend,
        permission_resolver=permissions(bob=HouseholdActor("bob", "resident")),
        audit_logger=audit,
    )

    result = executor.submit(
        intent(
            action="unlock",
            scope="home.entry.lock",
            risk="low",
            requested_by="bob",
        ),
        now=NOW,
    )

    permission_row = json.loads(
        (tmp_path / "memory" / "audit" / "permission_decisions.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()[0]
    )
    action_row = json.loads(
        (tmp_path / "memory" / "audit" / "action_decisions.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()[0]
    )
    assert result.status == "ask_admin"
    assert permission_row["action_id"] == result.action_id
    assert permission_row["decision"] == "ask_admin"
    assert permission_row["metadata"]["actor_role"] == "resident"
    assert permission_row["metadata"]["device_domain"] == "lock"
    assert action_row["metadata"]["permission_status"] == "ask_admin"


def test_audit_write_failure_does_not_change_submit_result(tmp_path):
    backend = CountingBackend({"dry_run": True})
    executor = SafeActionExecutor(
        gate=FakeGate(decision("allow")),
        confirmation_manager=ConfirmationManager(tmp_path),
        backend=backend,
        permission_resolver=permissions(),
        audit_logger=FailingAuditLogger(),
    )

    result = executor.submit(intent(), now=NOW)

    assert result.status == "dry_run"
    assert backend.calls == 1


def test_resident_low_risk_non_high_risk_user_action_executes(tmp_path):
    backend = CountingBackend()
    executor, _ = fake_executor(
        tmp_path,
        decision("allow"),
        backend,
        permission_resolver=permissions(bob=HouseholdActor("bob", "resident")),
    )

    result = executor.submit(
        intent(
            action="turn_on",
            scope="home.living_room.light",
            risk="low",
            requested_by="bob",
        ),
        now=NOW,
    )

    assert result.status == "dry_run"
    assert backend.calls == 1


def test_guest_low_risk_non_high_risk_user_action_executes(tmp_path):
    backend = CountingBackend()
    executor, _ = fake_executor(
        tmp_path,
        decision("allow"),
        backend,
        permission_resolver=permissions(guest=HouseholdActor("guest", "guest")),
    )

    result = executor.submit(
        intent(
            action="turn_on",
            scope="home.living_room.light",
            risk="low",
            requested_by="guest",
        ),
        now=NOW,
    )

    assert result.status == "dry_run"
    assert backend.calls == 1


def test_requested_by_none_high_risk_creates_no_confirmation_or_backend_call(tmp_path):
    backend = CountingBackend()
    executor, _ = fake_executor(
        tmp_path,
        decision("ask_confirmation"),
        backend,
        permission_resolver=permissions(),
    )

    result = executor.submit(
        intent(
            action="unlock",
            scope="home.entry.lock",
            risk="high",
            requested_by=None,
        ),
        now=NOW,
    )

    assert result.status in {"ask_admin", "denied"}
    assert backend.calls == 0
    assert executor.confirmation_manager.store.read_all() == []


def test_low_risk_high_domain_still_requires_admin(tmp_path):
    backend = CountingBackend()
    executor, _ = fake_executor(
        tmp_path,
        decision("allow"),
        backend,
        permission_resolver=permissions(bob=HouseholdActor("bob", "resident")),
    )

    result = executor.submit(
        intent(
            action="unlock",
            scope="home.entry.lock",
            risk="low",
            requested_by="bob",
        ),
        now=NOW,
    )

    assert result.status == "ask_admin"
    assert backend.calls == 0


def test_admin_high_risk_confirmation_path_can_create_confirmation(tmp_path):
    backend = CountingBackend()
    executor, _ = fake_executor(tmp_path, decision("ask_confirmation"), backend)

    result = executor.submit(
        intent(
            action="unlock",
            scope="home.entry.lock",
            risk="high",
            requested_by="alice",
        ),
        now=NOW,
    )

    assert result.status == "pending_confirmation"
    assert result.confirmation_id is not None
    assert backend.calls == 0


def test_high_risk_scheduled_action_is_denied_without_backend_call(tmp_path):
    backend = CountingBackend()
    executor, _, _ = real_executor(tmp_path, backend)

    result = executor.submit(
        intent(action="unlock", scope="home.entry.lock", trigger="scheduled", risk="high"),
        now=NOW,
    )

    assert result.status == "denied"
    assert result.decision.decision == "deny"
    assert backend.calls == 0


def test_medium_scheduled_unknown_presence_is_denied_without_backend_call(tmp_path):
    backend = CountingBackend()
    executor, _, _ = real_executor(tmp_path, backend)

    result = executor.submit(
        intent(action="set_temperature", scope="home.hvac", trigger="scheduled", risk="medium"),
        now=NOW,
    )

    assert result.status == "denied"
    assert result.decision.reason == "medium-risk non-user action denied with unknown occupancy"
    assert backend.calls == 0


def test_requires_presence_empty_violation_prevents_backend_call(tmp_path):
    backend = CountingBackend()
    executor, presence, _ = real_executor(tmp_path, backend)
    presence.upsert_presence(
        "alice",
        role="resident",
        status="home",
        source="manual",
        confidence=0.95,
    )

    result = executor.submit(
        intent(trigger="scheduled", risk="low", requires_presence_empty=True),
        now=NOW,
    )

    assert result.status == "denied"
    assert result.decision.reason == "presence is occupied"
    assert backend.calls == 0


def test_backend_exception_returns_failed_result(tmp_path):
    backend = CountingBackend(exc=RuntimeError("token=supersecretvalue"))
    executor, _ = fake_executor(tmp_path, decision("allow"), backend)

    result = executor.submit(intent(), now=NOW)

    assert backend.calls == 1
    assert result.status == "failed"
    assert "supersecretvalue" not in result.reason
    assert result.backend_result == {}
    assert executor.records[0].result_status == "failed"


def test_payload_sanitizer_runs_before_backend_sees_intent(tmp_path):
    backend = CountingBackend({"dry_run": True})
    executor, _ = fake_executor(tmp_path, decision("allow"), backend)

    executor.submit(
        intent(
            payload={
                "mac": "aa:bb",
                "ip": "192.0.2.10",
                "token": "secret-token",
                "note": "alice@example.com used Bearer abcdefghijklmnop",
                "nested": {"raw_audio": "bytes", "safe": "ok"},
            }
        ),
        now=NOW,
    )

    seen_payload = backend.seen_intents[0].payload
    assert "mac" not in seen_payload
    assert "ip" not in seen_payload
    assert "token" not in seen_payload
    assert "alice@example.com" not in seen_payload["note"]
    assert "Bearer abcdefghijklmnop" not in seen_payload["note"]
    assert seen_payload["nested"] == '{"safe":"ok"}'


def test_backend_result_is_sanitized_after_backend_returns(tmp_path):
    backend = CountingBackend({
        "dry_run": True,
        "mac": "aa:bb",
        "token": "secret-token",
        "note": "bob@example.com and sk-proj_abcdefghijklmnopqrstuvwxyz",
    })
    executor, _ = fake_executor(tmp_path, decision("allow"), backend)

    result = executor.submit(intent(), now=NOW)

    assert "mac" not in result.backend_result
    assert "token" not in result.backend_result
    assert "bob@example.com" not in result.backend_result["note"]
    assert "sk-proj_" not in result.backend_result["note"]


def test_action_intent_to_request_excludes_payload_and_preserves_gate_fields():
    action_intent = intent(
        requested_by="alice",
        requires_presence_empty=True,
        uses_facts=["fact_one"],
        payload={"brightness": 50},
        idempotency_key="idem-1",
    )

    request = action_intent.to_request()

    assert isinstance(request, ActionRequest)
    assert not hasattr(request, "payload")
    assert request.action == action_intent.action
    assert request.scope == action_intent.scope
    assert request.trigger == action_intent.trigger
    assert request.risk == action_intent.risk
    assert request.requested_by == "alice"
    assert request.requires_presence_empty is True
    assert request.uses_facts == ["fact_one"]
    assert action_intent.idempotency_key == "idem-1"


def test_records_are_in_memory_only_and_no_action_log_is_created(tmp_path):
    executor, _, _ = real_executor(tmp_path)

    result = executor.submit(intent(), now=NOW)

    assert len(executor.records) == 1
    assert executor.records[0].action_id == result.action_id
    assert executor.records[0].intent.payload == {}
    assert not (tmp_path / "memory" / "action_decisions.jsonl").exists()


def test_sanitize_action_payload_removes_sensitive_fields_and_redacts_values():
    sanitized = sanitize_action_payload({
        "raw_video": "bytes",
        "api_key": "secret",
        "safe": "ok",
        "comment": "password=supersecretvalue ghp_abcdefghijklmnopqrstuvwxyz123",
    })

    assert sanitized == {
        "safe": "ok",
        "comment": "password=[REDACTED_SECRET] [REDACTED_SECRET]",
    }


def test_motion_signal_to_medium_scheduled_action_is_denied(tmp_path):
    backend = CountingBackend()
    executor, presence, _ = real_executor(tmp_path, backend)
    PresenceSignalIngestor(presence).ingest(
        MotionAdapter.from_event(MotionEvent(zone="home.entry", active=True))
    )

    result = executor.submit(
        intent(action="set_temperature", scope="home.hvac", trigger="scheduled", risk="medium"),
        now=NOW,
    )

    assert result.status == "denied"
    assert result.decision.presence_status == "unknown"
    assert backend.calls == 0


def test_wifi_home_to_requires_presence_empty_action_is_denied(tmp_path):
    backend = CountingBackend()
    executor, presence, _ = real_executor(tmp_path, backend)
    PresenceSignalIngestor(presence).ingest(
        WifiPresenceAdapter.from_event(
            WifiDeviceEvent(
                registered_device_id="device_alice_phone",
                person_id="alice",
                online=True,
            )
        )
    )

    result = executor.submit(
        intent(trigger="scheduled", risk="low", requires_presence_empty=True),
        now=NOW,
    )

    assert result.status == "denied"
    assert result.decision.presence_status == "occupied"
    assert backend.calls == 0


def test_all_residents_away_allows_low_risk_scheduled_dry_run(tmp_path):
    backend = CountingBackend({"dry_run": True})
    executor, presence, _ = real_executor(tmp_path, backend)
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

    result = executor.submit(
        intent(action="turn_off_light", trigger="scheduled", risk="low"),
        now=NOW,
    )

    assert presence.resolve_occupancy().status == "empty"
    assert result.status == "dry_run"
    assert result.decision.presence_status == "empty"
    assert backend.calls == 1


def test_high_risk_user_unknown_presence_creates_pending_confirmation(tmp_path):
    backend = CountingBackend()
    executor, _, _ = real_executor(tmp_path, backend)

    result = executor.submit(
        intent(action="unlock", scope="home.entry.lock", risk="high"),
        now=NOW,
    )

    assert result.status == "pending_confirmation"
    assert result.confirmation_id is not None
    assert backend.calls == 0
