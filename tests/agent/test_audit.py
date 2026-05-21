import json
from datetime import datetime, timezone

from OriginAgent.agent.audit import AUDIT_REASON_MAX_CHARS, AuditEvent, AuditLogger
from OriginAgent.agent.memory import MemoryStore, MemoryWorkspaceSnapshot

NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


def read_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_audit_logger_appends_valid_jsonl(tmp_path):
    audit = AuditLogger(tmp_path)

    audit.log_action_decision(
        action_id="action_1",
        actor_id="alice",
        action="turn_on",
        scope="home.living_room.light",
        risk="low",
        trigger="user_initiated",
        decision="dry_run",
        reason="safety checks passed",
        metadata={"payload_keys": ["brightness"]},
        created_at=NOW,
    )

    rows = read_jsonl(tmp_path / "memory" / "audit" / "action_decisions.jsonl")
    assert rows[0]["event_type"] == "action_decision"
    assert rows[0]["action_id"] == "action_1"
    assert rows[0]["metadata"]["payload_keys"] == '["brightness"]'


def test_append_uses_memory_lock_and_fsync(tmp_path, monkeypatch):
    events = []

    class FakeLock:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, exc_type, exc, tb):
            events.append("exit")

    monkeypatch.setattr("OriginAgent.agent.audit.os.fsync", lambda fd: events.append(("fsync", fd)))
    audit = AuditLogger(tmp_path, lock_factory=lambda: FakeLock())

    audit.log_action_decision(
        action_id="action_1",
        decision="denied",
        reason="no",
        created_at=NOW,
    )

    assert events[0] == "enter"
    assert any(item[0] == "fsync" for item in events if isinstance(item, tuple))
    assert events[-1] == "exit"


def test_action_event_redacts_values_and_keeps_only_keys(tmp_path):
    audit = AuditLogger(tmp_path)
    long_reason = "x" * (AUDIT_REASON_MAX_CHARS + 50)

    event = audit.log_action_decision(
        action_id="action_1",
        action="unlock password=supersecretvalue",
        scope="home.entry.lock alice@example.com",
        decision="failed",
        reason=long_reason + " Bearer abcdefghijklmnop",
        metadata={
            "payload_keys": ["note"],
            "backend_result_keys": ["dry_run"],
            "raw_payload": {"token": "secret"},
            "nested": {"token": "secret"},
        },
        created_at=NOW,
    )

    raw = (tmp_path / "memory" / "audit" / "action_decisions.jsonl").read_text(
        encoding="utf-8"
    )
    assert "supersecretvalue" not in raw
    assert "alice@example.com" not in raw
    assert "Bearer abcdefghijklmnop" not in raw
    assert "secret" not in raw
    assert "raw_payload" not in raw
    assert len(event.reason) <= AUDIT_REASON_MAX_CHARS
    assert event.metadata["nested"] == "[REDACTED_COMPLEX_VALUE]"


def test_schema_rejected_action_audit_keeps_parameter_keys_only(tmp_path):
    audit = AuditLogger(tmp_path)

    audit.log_action_decision(
        action_id="action_schema_bad",
        actor_id="alice",
        action="turn_off_all_lights",
        decision="failed",
        reason="invalid typed device action: unsupported action_type turn_off_all_lights",
        metadata={
            "gate_decision": "not_evaluated",
            "schema_validated": False,
            "typed_action_type": "turn_off_all_lights",
            "typed_action_domain": "lighting",
            "parameter_keys": ["brightness"],
        },
        created_at=NOW,
    )

    raw = (tmp_path / "memory" / "audit" / "action_decisions.jsonl").read_text(
        encoding="utf-8"
    )
    row = read_jsonl(tmp_path / "memory" / "audit" / "action_decisions.jsonl")[0]
    assert row["metadata"]["schema_validated"] == "False"
    assert row["metadata"]["parameter_keys"] == '["brightness"]'
    assert "200" not in raw


def test_permission_event_stores_role_domain_without_display_name(tmp_path):
    audit = AuditLogger(tmp_path)

    audit.log_permission_decision(
        action_id="action_1",
        actor_id="partner",
        action="unlock",
        scope="home.entry.lock",
        risk="high",
        trigger="user_initiated",
        permission="confirm_action",
        device_domain="lock",
        decision="ask_admin",
        reason="action requires administrator confirmation",
        actor_role="resident",
        metadata={"display_name": "Partner Person"},
        created_at=NOW,
    )

    rows = read_jsonl(tmp_path / "memory" / "audit" / "permission_decisions.jsonl")
    assert rows[0]["action_id"] == "action_1"
    assert rows[0]["metadata"]["actor_role"] == "resident"
    assert rows[0]["metadata"]["device_domain"] == "lock"
    assert "display_name" not in rows[0]["metadata"]
    assert "Partner Person" not in json.dumps(rows[0], ensure_ascii=False)


def test_confirmation_event_does_not_store_prompt_or_source_excerpt(tmp_path):
    audit = AuditLogger(tmp_path)

    audit.log_confirmation_event(
        confirmation_id="confirmation_1",
        decision="created",
        reason="source_excerpt={raw door log}",
        metadata={
            "prompt": "full prompt",
            "source_excerpt": "raw door log",
            "confirmation_status": "pending",
        },
        created_at=NOW,
    )

    raw = (tmp_path / "memory" / "audit" / "confirmation_events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "full prompt" not in raw
    assert "raw door log" not in raw
    assert "source_excerpt" not in raw
    assert "confirmation_status" in raw


def test_find_helpers_and_explain_action_skip_malformed_lines(tmp_path):
    audit = AuditLogger(tmp_path)
    action = audit.log_action_decision(
        action_id="action_1",
        confirmation_id="confirmation_1",
        decision="pending_confirmation",
        reason="needs confirmation",
        created_at=NOW,
    )
    permission = audit.log_permission_decision(
        action_id="action_1",
        actor_id="alice",
        action="unlock",
        scope="home.entry.lock",
        risk="high",
        trigger="user_initiated",
        permission="confirm_action",
        device_domain="lock",
        decision="allow",
        reason="admin may confirm action",
        actor_role="admin",
        created_at=NOW,
    )
    confirmation = audit.log_confirmation_event(
        confirmation_id="confirmation_1",
        decision="confirmed_once",
        reason="confirmed once",
        created_at=NOW,
    )
    with (tmp_path / "memory" / "audit" / "action_decisions.jsonl").open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write("{not json}\n")

    assert [event.event_id for event in audit.find_by_confirmation_id("confirmation_1")] == [
        action.event_id,
        confirmation.event_id,
    ]
    assert {
        event.event_id
        for event in audit.find_by_action_id("action_1")
    } == {action.event_id, permission.event_id, confirmation.event_id}
    assert {
        event.event_id
        for event in audit.explain_action("action_1")
    } == {action.event_id, permission.event_id, confirmation.event_id}


def test_audit_files_are_not_git_tracked_or_dream_snapshot_tracked(tmp_path):
    store = MemoryStore(tmp_path)
    snapshot = MemoryWorkspaceSnapshot(tmp_path)
    audit_path = "memory/audit/action_decisions.jsonl"

    assert audit_path not in store.git._tracked_files
    assert all(str(path).replace("\\", "/") != audit_path for path in snapshot._TRACKED_FILES)


def test_audit_event_rejects_unknown_event_type():
    try:
        AuditEvent(
            event_id="audit_bad",
            event_type="surprise",
            created_at=NOW.isoformat(),
            actor_id=None,
            action_id=None,
            confirmation_id=None,
            scope=None,
            action=None,
            risk=None,
            trigger=None,
            decision="no",
            reason="no",
        )
    except ValueError as exc:
        assert "invalid audit event type" in str(exc)
    else:
        raise AssertionError("expected invalid audit event type")
