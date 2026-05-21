import json
from datetime import datetime, timedelta, timezone

from OriginAgent.agent.action_runtime import ActionIntent, SafeActionExecutor
from OriginAgent.agent.action_safety import ActionDecision, ActionRequest
from OriginAgent.agent.audit import AuditLogger
from OriginAgent.agent.confirmation import ConfirmationManager
from OriginAgent.domain_packs.smart_home.runtime.permissions import HouseholdActor, PermissionResolver

NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class SequenceGate:
    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.requests = []

    def evaluate(self, request):
        self.requests.append(request)
        if self.decisions:
            return self.decisions.pop(0)
        return ActionDecision(
            decision="allow",
            reason="default allow",
            presence_status="empty",
        )


class CountingBackend:
    def __init__(self, result=None, exc=None, on_execute=None):
        self.result = result if result is not None else {"dry_run": True}
        self.exc = exc
        self.on_execute = on_execute
        self.calls = 0
        self.seen_intents = []

    def execute(self, intent):
        self.calls += 1
        self.seen_intents.append(intent)
        if self.on_execute is not None:
            self.on_execute()
        if self.exc is not None:
            raise self.exc
        return self.result


class FlakyBackend:
    def __init__(self):
        self.calls = 0
        self.seen_intents = []

    def execute(self, intent):
        self.calls += 1
        self.seen_intents.append(intent)
        if self.calls == 1:
            raise RuntimeError("temporary failure")
        return {"dry_run": True}


def decision(value, **kwargs):
    defaults = {
        "decision": value,
        "reason": f"{value} reason",
        "presence_status": "unknown" if value == "ask_confirmation" else "empty",
    }
    defaults.update(kwargs)
    return ActionDecision(**defaults)


def intent(**kwargs):
    defaults = {
        "action": "unlock",
        "scope": "home.entry.lock",
        "trigger": "user_initiated",
        "risk": "high",
        "requested_by": "alice",
        "payload": {"domain": "lock"},
    }
    defaults.update(kwargs)
    return ActionIntent(**defaults)


def permissions(**actors):
    defaults = {"alice": HouseholdActor("alice", "admin")}
    defaults.update(actors)
    return PermissionResolver(defaults)


def executor(tmp_path, gate, backend, permission_resolver=None, audit_logger=None):
    manager = ConfirmationManager(tmp_path, audit_logger=audit_logger)
    return SafeActionExecutor(
        gate=gate,
        confirmation_manager=manager,
        backend=backend,
        permission_resolver=permission_resolver or permissions(),
        audit_logger=audit_logger,
    )


def create_pending(executor, action_intent=None):
    result = executor.submit(action_intent or intent(), now=NOW)
    assert result.status == "pending_confirmation"
    assert result.confirmation_id is not None
    return result.confirmation_id


def create_stored_pending(executor, action_intent):
    confirmation = executor.confirmation_manager.create_from_action_decision(
        action_intent.to_request(),
        decision("ask_confirmation"),
        now=NOW,
        action_payload=action_intent.sanitized().payload,
        idempotency_key=action_intent.idempotency_key,
    )
    assert confirmation is not None
    return confirmation.confirmation_id


def stored_confirmation(executor, confirmation_id):
    confirmation = executor.confirmation_manager.store.get(confirmation_id)
    assert confirmation is not None
    return confirmation


def test_confirmed_once_resumes_dry_run_execution_once(tmp_path):
    gate = SequenceGate(decision("ask_confirmation"), decision("allow", presence_status="empty"))
    backend = CountingBackend({
        "dry_run": True,
        "note": "bob@example.com and sk-proj_abcdefghijklmnopqrstuvwxyz",
    })
    action_intent = intent(
        requires_presence_empty=True,
        uses_facts=["fact_policy"],
        payload={
            "level": 1,
            "token": "secret",
            "mac": "aa:bb",
            "note": "password=supersecretvalue",
            "nested": {"raw_audio": "bytes", "safe": "ok"},
        },
        idempotency_key="resume-1",
    )
    safe_executor = executor(tmp_path, gate, backend)
    confirmation_id = create_pending(safe_executor, action_intent)

    raw_pending = (tmp_path / "memory" / "pending_confirmations.json").read_text(
        encoding="utf-8"
    )
    assert "secret" not in raw_pending
    assert "aa:bb" not in raw_pending
    assert "raw_audio" not in raw_pending
    assert "supersecretvalue" not in raw_pending

    result = safe_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)

    assert result.status == "dry_run"
    assert result.confirmation_id == confirmation_id
    assert backend.calls == 1
    assert len(gate.requests) == 2
    assert isinstance(gate.requests[1], ActionRequest)
    assert not hasattr(gate.requests[1], "payload")
    assert gate.requests[1].requires_presence_empty is True
    assert gate.requests[1].uses_facts == ["fact_policy"]
    assert backend.seen_intents[0].payload == {
        "level": "1",
        "note": "password=[REDACTED_SECRET]",
        "nested": '{"safe":"ok"}',
    }
    assert backend.seen_intents[0].idempotency_key == "resume-1"
    assert "bob@example.com" not in result.backend_result["note"]
    assert "sk-proj_" not in result.backend_result["note"]
    assert stored_confirmation(safe_executor, confirmation_id).consumed_at is not None


def test_rejected_reply_does_not_execute(tmp_path):
    gate = SequenceGate(decision("ask_confirmation"))
    backend = CountingBackend()
    safe_executor = executor(tmp_path, gate, backend)
    confirmation_id = create_pending(safe_executor)

    result = safe_executor.resume_confirmed(confirmation_id, reply="no", now=NOW)

    assert result.status == "denied"
    assert backend.calls == 0
    assert stored_confirmation(safe_executor, confirmation_id).status == "rejected"


def test_expired_confirmation_does_not_execute_and_persists_expired(tmp_path):
    gate = SequenceGate(decision("ask_confirmation"), decision("allow"))
    backend = CountingBackend()
    safe_executor = executor(tmp_path, gate, backend)
    confirmation_id = create_pending(safe_executor)

    result = safe_executor.resume_confirmed(
        confirmation_id,
        reply="yes",
        now=NOW + timedelta(minutes=3),
    )

    assert result.status == "denied"
    assert "expired" in result.reason
    assert backend.calls == 0
    assert stored_confirmation(safe_executor, confirmation_id).status == "expired"


def test_resume_expired_confirmation_logs_expired_confirmation_event(tmp_path):
    audit = AuditLogger(tmp_path)
    gate = SequenceGate(decision("ask_confirmation"), decision("allow"))
    backend = CountingBackend()
    safe_executor = executor(tmp_path, gate, backend, audit_logger=audit)
    confirmation_id = create_pending(safe_executor)

    result = safe_executor.resume_confirmed(
        confirmation_id,
        reply="yes",
        now=NOW + timedelta(minutes=3),
    )

    confirmation_events = audit.find_by_confirmation_id(confirmation_id)
    assert result.status == "denied"
    assert backend.calls == 0
    assert any(event.decision == "expired" for event in confirmation_events)


def test_unclear_reply_does_not_execute(tmp_path):
    gate = SequenceGate(decision("ask_confirmation"))
    backend = CountingBackend()
    safe_executor = executor(tmp_path, gate, backend)
    confirmation_id = create_pending(safe_executor)

    result = safe_executor.resume_confirmed(confirmation_id, reply="maybe", now=NOW)

    assert result.status == "pending_confirmation"
    assert backend.calls == 0
    assert stored_confirmation(safe_executor, confirmation_id).status == "pending"


def test_missing_confirmation_does_not_execute(tmp_path):
    gate = SequenceGate(decision("allow"))
    backend = CountingBackend()
    safe_executor = executor(tmp_path, gate, backend)

    result = safe_executor.resume_confirmed("confirmation_missing", reply="yes", now=NOW)

    assert result.status == "denied"
    assert backend.calls == 0


def test_notify_only_confirmation_cannot_resume(tmp_path):
    gate = SequenceGate(decision("notify_only"))
    backend = CountingBackend()
    safe_executor = executor(tmp_path, gate, backend)
    result = safe_executor.submit(intent(action="notify_user", risk="low"), now=NOW)

    resumed = safe_executor.resume_confirmed(result.confirmation_id, reply="yes", now=NOW)

    assert resumed.status == "denied"
    assert "not executable" in resumed.reason
    assert backend.calls == 0


def test_same_confirmation_cannot_execute_twice(tmp_path):
    gate = SequenceGate(
        decision("ask_confirmation"),
        decision("allow", presence_status="empty"),
        decision("allow", presence_status="empty"),
    )
    backend = CountingBackend()
    safe_executor = executor(tmp_path, gate, backend)
    confirmation_id = create_pending(safe_executor)

    first = safe_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)
    second = safe_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)

    assert first.status == "dry_run"
    assert second.status == "already_executed"
    assert backend.calls == 1


def test_resume_rechecks_gate_and_denies_without_consuming(tmp_path):
    gate = SequenceGate(
        decision("ask_confirmation"),
        decision("deny", reason="presence changed"),
    )
    backend = CountingBackend()
    safe_executor = executor(tmp_path, gate, backend)
    confirmation_id = create_pending(safe_executor)

    result = safe_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)

    assert result.status == "denied"
    assert result.reason == "presence changed"
    assert backend.calls == 0
    assert stored_confirmation(safe_executor, confirmation_id).consumed_at is None


def test_resume_permission_failure_does_not_consume_or_execute(tmp_path):
    gate = SequenceGate(decision("allow", presence_status="empty"))
    backend = CountingBackend()
    safe_executor = executor(
        tmp_path,
        gate,
        backend,
        permission_resolver=permissions(bob=HouseholdActor("bob", "resident")),
    )
    confirmation_id = create_stored_pending(
        safe_executor,
        intent(requested_by="bob", action="unlock", scope="home.entry.lock", risk="high"),
    )

    result = safe_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)

    assert result.status == "ask_admin"
    assert backend.calls == 0
    raw = json.loads(
        (tmp_path / "memory" / "pending_confirmations.json").read_text(encoding="utf-8")
    )
    assert raw["confirmations"][0]["consumed_at"] is None


def test_resume_permission_success_claims_and_executes(tmp_path):
    gate = SequenceGate(decision("ask_confirmation"), decision("allow", presence_status="empty"))
    backend = CountingBackend()
    safe_executor = executor(tmp_path, gate, backend)
    confirmation_id = create_pending(
        safe_executor,
        intent(requested_by="alice", action="unlock", scope="home.entry.lock", risk="high"),
    )

    result = safe_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)

    assert result.status == "dry_run"
    assert backend.calls == 1
    assert stored_confirmation(safe_executor, confirmation_id).consumed_at is not None


def test_resume_success_logs_action_permission_and_consumed_confirmation(tmp_path):
    audit = AuditLogger(tmp_path)
    gate = SequenceGate(decision("ask_confirmation"), decision("allow", presence_status="empty"))
    backend = CountingBackend()
    safe_executor = executor(tmp_path, gate, backend, audit_logger=audit)
    confirmation_id = create_pending(
        safe_executor,
        intent(requested_by="alice", action="unlock", scope="home.entry.lock", risk="high"),
    )

    result = safe_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)

    action_events = audit.find_by_action_id(result.action_id)
    permission_events = [
        event for event in action_events if event.event_type == "permission_decision"
    ]
    confirmation_events = audit.find_by_confirmation_id(confirmation_id)
    assert result.status == "dry_run"
    assert any(event.decision == "dry_run" for event in action_events)
    assert {event.metadata["permission"] for event in permission_events} == {
        "confirm_action",
        "execute_action",
    }
    assert any(event.decision == "consumed" for event in confirmation_events)


def test_resident_resuming_lock_action_asks_admin(tmp_path):
    gate = SequenceGate(decision("allow", presence_status="empty"))
    backend = CountingBackend()
    safe_executor = executor(
        tmp_path,
        gate,
        backend,
        permission_resolver=permissions(bob=HouseholdActor("bob", "resident")),
    )
    confirmation_id = create_stored_pending(
        safe_executor,
        intent(requested_by="bob", action="unlock", scope="home.entry.lock", risk="low"),
    )

    result = safe_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)

    assert result.status == "ask_admin"
    assert backend.calls == 0
    assert stored_confirmation(safe_executor, confirmation_id).consumed_at is None


def test_resume_still_requires_confirmation_without_new_confirmation_or_consumption(tmp_path):
    gate = SequenceGate(
        decision("ask_confirmation"),
        decision("ask_confirmation", reason="still unsafe"),
    )
    backend = CountingBackend()
    safe_executor = executor(tmp_path, gate, backend)
    confirmation_id = create_pending(safe_executor)

    result = safe_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)

    assert result.status == "pending_confirmation_still_required"
    assert result.reason == "still unsafe"
    assert backend.calls == 0
    confirmations = safe_executor.confirmation_manager.store.read_all()
    assert len(confirmations) == 1
    assert confirmations[0].consumed_at is None


def test_consumed_at_is_persisted_before_backend_and_survives_failure(tmp_path):
    gate = SequenceGate(decision("ask_confirmation"), decision("allow"))
    safe_executor = executor(tmp_path, gate, CountingBackend())
    confirmation_id = create_pending(safe_executor)

    def assert_consumed_before_backend():
        assert stored_confirmation(safe_executor, confirmation_id).consumed_at is not None

    backend = CountingBackend(
        exc=RuntimeError("backend failed"),
        on_execute=assert_consumed_before_backend,
    )
    safe_executor.backend = backend

    result = safe_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)

    assert result.status == "failed"
    assert backend.calls == 1
    assert stored_confirmation(safe_executor, confirmation_id).consumed_at is not None

    second = safe_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)

    assert second.status == "already_executed"
    assert backend.calls == 1


def test_successful_idempotency_key_prevents_duplicate_execution(tmp_path):
    gate = SequenceGate(
        decision("ask_confirmation"),
        decision("allow"),
    )
    backend = CountingBackend()
    safe_executor = executor(tmp_path, gate, backend)
    action_intent = intent(idempotency_key="idem-success")
    confirmation_id = create_pending(safe_executor, action_intent)

    first = safe_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)
    second = safe_executor.submit(
        intent(action="turn_on", risk="low", idempotency_key="idem-success"),
        now=NOW,
    )

    assert first.status == "dry_run"
    assert second.status == "already_executed"
    assert backend.calls == 1


def test_successful_idempotency_key_survives_executor_recreation(tmp_path):
    first_backend = CountingBackend()
    first_executor = executor(
        tmp_path,
        SequenceGate(decision("ask_confirmation"), decision("allow")),
        first_backend,
    )
    action_intent = intent(idempotency_key="idem-persisted")
    confirmation_id = create_pending(first_executor, action_intent)

    first = first_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)

    second_backend = CountingBackend()
    second_executor = executor(
        tmp_path,
        SequenceGate(decision("allow")),
        second_backend,
    )
    second = second_executor.submit(
        intent(action="turn_on", risk="low", idempotency_key="idem-persisted"),
        now=NOW,
    )

    assert first.status == "dry_run"
    assert second.status == "already_executed"
    assert first_backend.calls == 1
    assert second_backend.calls == 0


def test_idempotency_replay_logs_action_decision(tmp_path):
    audit = AuditLogger(tmp_path)
    gate = SequenceGate(
        decision("ask_confirmation"),
        decision("allow"),
    )
    backend = CountingBackend()
    safe_executor = executor(tmp_path, gate, backend, audit_logger=audit)
    action_intent = intent(idempotency_key="idem-success")
    confirmation_id = create_pending(safe_executor, action_intent)

    safe_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)
    replay = safe_executor.submit(
        intent(action="turn_on", risk="low", idempotency_key="idem-success"),
        now=NOW,
    )

    replay_events = audit.find_by_action_id(replay.action_id)
    assert replay.status == "already_executed"
    assert replay_events[0].decision == "already_executed"
    assert replay_events[0].metadata["gate_decision"] == "idempotent_replay"
    assert replay_events[0].metadata["idempotency_key_present"] == "True"


def test_failed_backend_does_not_mark_idempotency_key_successful(tmp_path):
    gate = SequenceGate(
        decision("ask_confirmation"),
        decision("allow"),
        decision("allow"),
    )
    backend = FlakyBackend()
    safe_executor = executor(tmp_path, gate, backend)
    confirmation_id = create_pending(safe_executor, intent(idempotency_key="idem-fail"))

    first = safe_executor.resume_confirmed(confirmation_id, reply="yes", now=NOW)
    second = safe_executor.submit(
        intent(action="turn_on", risk="low", idempotency_key="idem-fail"),
        now=NOW,
    )

    assert first.status == "failed"
    assert second.status == "dry_run"
    assert backend.calls == 2


def test_backward_compatible_confirmation_without_snapshot_fields_can_parse(tmp_path):
    manager = ConfirmationManager(tmp_path)
    manager.store.pending_file.parent.mkdir(parents=True, exist_ok=True)
    manager.store.pending_file.write_text(
        json.dumps(
            {
                "confirmations": [
                    {
                        "confirmation_id": "confirmation_old",
                        "kind": "action_confirmation",
                        "status": "pending",
                        "prompt": "Continue?",
                        "action": "unlock",
                        "scope": "home.entry.lock",
                        "trigger": "user_initiated",
                        "risk": "high",
                        "requested_by": None,
                        "decision_reason": "reason",
                        "presence_status": "unknown",
                        "related_fact_ids": [],
                        "created_at": NOW.isoformat(),
                        "expires_at": (NOW + timedelta(minutes=2)).isoformat(),
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )

    confirmation = manager.store.get("confirmation_old")

    assert confirmation.requires_presence_empty is False
    assert confirmation.uses_facts == []
    assert confirmation.action_payload == {}
    assert confirmation.idempotency_key is None
    assert confirmation.consumed_at is None
