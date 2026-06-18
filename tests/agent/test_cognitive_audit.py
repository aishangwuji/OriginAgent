from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from OriginAgent.agent.active_intents import ActiveIntentRecord, JsonlActiveIntentLedger
from OriginAgent.agent.cognitive_audit import JsonlCognitiveAuditLedger
from OriginAgent.agent.cognitive_events import CognitiveDecision, CognitiveEvent
from OriginAgent.agent.cognitive_scheduler import JsonlCognitiveSchedulerLedger, CognitiveSchedulerRun
from OriginAgent.agent.introspection.service import RuntimeIntrospectionService


def test_cognitive_event_round_trips_json() -> None:
    event = CognitiveEvent(
        event_id="evt-1",
        session_key="cli:direct",
        event_type="goal_nudge",
        source_type="goal_state",
        source_reference="goal:active",
        summary="Resume the active goal",
        priority="high",
        payload={"goal_id": "g-1"},
    )

    restored = CognitiveEvent.from_json(event.to_json())

    assert restored == event


def test_cognitive_decision_round_trips_json() -> None:
    decision = CognitiveDecision(
        decision_id="dec-1",
        event_id="evt-1",
        session_key="cli:direct",
        action="suppress",
        outcome="suppressed",
        suppression_reason="session_cooldown",
        cooldown_key="goal:cli:direct",
        written_to_working_memory=False,
        published_internal_event=False,
        payload={"attempt": 1},
    )

    restored = CognitiveDecision.from_json(decision.to_json())

    assert restored == decision


def test_cognitive_event_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        CognitiveEvent(
            event_id="evt-1",
            session_key="cli:direct",
            event_type="unknown_type",  # type: ignore[arg-type]
            source_type="goal_state",
            source_reference="goal:active",
        )


def test_cognitive_decision_rejects_unknown_outcome() -> None:
    with pytest.raises(ValueError):
        CognitiveDecision(
            decision_id="dec-1",
            event_id="evt-1",
            session_key="cli:direct",
            action="emit",
            outcome="unknown",  # type: ignore[arg-type]
        )


def test_cognitive_audit_ledger_appends_and_reads_recent_records(tmp_path) -> None:
    ledger = JsonlCognitiveAuditLedger(tmp_path)
    event = CognitiveEvent(
        event_id="evt-1",
        session_key="cli:direct",
        event_type="scheduled_reminder",
        source_type="reminder_store",
        source_reference="reminder:r-1",
        summary="Reminder is due",
    )
    decision = CognitiveDecision(
        decision_id="dec-1",
        event_id="evt-1",
        session_key="cli:direct",
        action="emit",
        outcome="emitted",
        written_to_working_memory=True,
        published_internal_event=True,
    )

    ledger.append_event(event)
    ledger.append_decision(decision)

    events = ledger.recent_events()
    decisions = ledger.recent_decisions()

    assert len(events) == 1
    assert len(decisions) == 1
    assert events[0]["event_id"] == "evt-1"
    assert decisions[0]["decision_id"] == "dec-1"


def test_cognitive_audit_ledger_skips_bad_json_lines(tmp_path) -> None:
    root = tmp_path / "memory" / "cognitive"
    root.mkdir(parents=True)
    events_path = root / "events.jsonl"
    events_path.write_text('{"event_id":"ok"}\n{bad json\n', encoding="utf-8")

    ledger = JsonlCognitiveAuditLedger(tmp_path)

    events = ledger.recent_events()

    assert events == [{"event_id": "ok"}]


class _Registry:
    tool_names = ["read_file"]


class _Sessions:
    def list_sessions(self):
        return [{"key": "cli:direct"}]


class _Loop:
    _cognitive_loop_enabled = True
    _active_intent_config = type("_Config", (), {"enabled": False})()
    cognitive_scheduler = None


def test_runtime_introspection_includes_cognition_summary(tmp_path) -> None:
    ledger = JsonlCognitiveAuditLedger(tmp_path)
    scheduler_ledger = JsonlCognitiveSchedulerLedger(tmp_path)
    ledger.append_event(
        CognitiveEvent(
            event_id="evt-1",
            session_key="cli:direct",
            event_type="foresight_nudge",
            source_type="nearline",
            source_reference="foresight:f-1",
            summary="Potential next-step foresight",
        )
    )
    ledger.append_decision(
        CognitiveDecision(
            decision_id="dec-1",
            event_id="evt-1",
            session_key="cli:direct",
            action="suppress",
            outcome="suppressed",
            suppression_reason="intent_cooldown",
        )
    )
    scheduler_ledger.append(
        CognitiveSchedulerRun(
            run_id="run-1",
            trigger="cron",
            scheduled_job_id="cognitive_scheduler",
            scanned_session_count=1,
            decision_count=1,
            suppressed_count=1,
        )
    )
    service = RuntimeIntrospectionService(
        loop=_Loop(),
        workspace=tmp_path,
        registry=_Registry(),
        sessions=_Sessions(),
        pending_queues={},
    )

    summary = service.cognition_summary()
    loop_summary = service.current_loop_summary()

    assert summary["enabled"] is True
    assert summary["messaging_enabled"] is False
    assert summary["event_count"] == 1
    assert summary["decision_count"] == 1
    assert summary["scheduler_run_count"] == 1
    assert summary["outcome_counts"]["suppressed"] == 1
    assert summary["suppression_reason_counts"]["intent_cooldown"] == 1
    assert summary["latest_scheduler_run"]["trigger"] == "cron"
    assert loop_summary["cognition"]["latest_event"]["event_type"] == "foresight_nudge"


def test_cognitive_audit_records_are_jsonl_on_disk(tmp_path) -> None:
    ledger = JsonlCognitiveAuditLedger(tmp_path)
    ledger.append_event(
        CognitiveEvent(
            event_id="evt-1",
            session_key="cli:direct",
            event_type="goal_nudge",
            source_type="goal_state",
            source_reference="goal:g-1",
        )
    )

    path = tmp_path / "memory" / "cognitive" / "events.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()

    assert len(lines) == 1
    assert json.loads(lines[0])["event_type"] == "goal_nudge"


def test_cognitive_decision_payload_can_stand_alone_without_active_intent_ledger(tmp_path) -> None:
    cognitive_ledger = JsonlCognitiveAuditLedger(tmp_path)
    decision = CognitiveDecision(
        decision_id="dec-1",
        event_id="goal_nudge:cli:test:goal-1",
        session_key="cli:test",
        action="suppress",
        outcome="suppressed",
        suppression_reason="session_cooldown",
        cooldown_key="goal_nudge:cli:test:goal-1",
        payload={
            "event_type": "goal_nudge",
            "source_type": "goal_state",
            "source_reference": "goal-1",
            "intent_id": "goal_nudge:cli:test:goal-1",
            "summary": "Resume the active goal",
        },
    )

    cognitive_ledger.append_decision(decision)

    row = cognitive_ledger.recent_decisions()[0]

    assert row["payload"]["event_type"] == "goal_nudge"
    assert row["payload"]["source_type"] == "goal_state"
    assert row["payload"]["source_reference"] == "goal-1"
    assert row["payload"]["intent_id"] == "goal_nudge:cli:test:goal-1"
    assert row["payload"]["summary"] == "Resume the active goal"


def test_legacy_active_intent_ledger_remains_readable_for_migration(tmp_path) -> None:
    active_intent_ledger = JsonlActiveIntentLedger(tmp_path)
    record = ActiveIntentRecord(
        timestamp="2026-06-13T00:00:00+00:00",
        session_key="cli:test",
        intent_type="goal_nudge",
        intent_id="goal_nudge:cli:test:goal-1",
        source_type="goal_state",
        source_reference="goal-1",
        outcome="emitted",
        summary="Resume the active goal",
    )

    active_intent_ledger.append(record)

    rows = active_intent_ledger.recent()

    assert len(rows) == 1
    assert rows[0]["intent_id"] == "goal_nudge:cli:test:goal-1"


def test_fallback_path_has_event_and_decision_audit_without_scheduler_run(tmp_path) -> None:
    cognitive_ledger = JsonlCognitiveAuditLedger(tmp_path)
    scheduler_ledger = JsonlCognitiveSchedulerLedger(tmp_path)
    before_runs = scheduler_ledger.summary(limit=20)["scheduler_run_count"]
    event = CognitiveEvent(
        event_id="goal_nudge:cli:test:goal-1",
        session_key="cli:test",
        event_type="goal_nudge",
        source_type="goal_state",
        source_reference="goal-1",
        summary="Resume the active goal",
    )
    decision = CognitiveDecision(
        decision_id="decision:goal_nudge:cli:test:goal-1",
        event_id=event.event_id,
        session_key="cli:test",
        action="suppress",
        outcome="suppressed",
        suppression_reason="intent_cooldown",
        cooldown_key=event.event_id,
    )

    cognitive_ledger.append_event(event)
    cognitive_ledger.append_decision(decision)

    assert cognitive_ledger.recent_events()[-1]["event_id"] == event.event_id
    assert cognitive_ledger.recent_decisions()[-1]["decision_id"] == decision.decision_id
    assert scheduler_ledger.summary(limit=20)["scheduler_run_count"] == before_runs


def test_latest_scan_counts_can_be_reconciled_with_recent_audit_and_scheduler_rows(tmp_path) -> None:
    cognitive_ledger = JsonlCognitiveAuditLedger(tmp_path)
    scheduler_ledger = JsonlCognitiveSchedulerLedger(tmp_path)
    created_at = datetime.now(timezone.utc).isoformat()
    event_a = CognitiveEvent(
        event_id="goal_nudge:cli:test:goal-1",
        session_key="cli:test",
        event_type="goal_nudge",
        source_type="goal_state",
        source_reference="goal-1",
        created_at=created_at,
    )
    event_b = CognitiveEvent(
        event_id="reminder:r-1",
        session_key="cli:test",
        event_type="scheduled_reminder",
        source_type="reminder_store",
        source_reference="r-1",
        created_at=created_at,
    )
    decision_a = CognitiveDecision(
        decision_id="decision:goal_nudge:cli:test:goal-1",
        event_id=event_a.event_id,
        session_key="cli:test",
        action="emit",
        outcome="emitted",
        cooldown_key=event_a.event_id,
        created_at=created_at,
    )
    decision_b = CognitiveDecision(
        decision_id="decision:reminder:r-1",
        event_id=event_b.event_id,
        session_key="cli:test",
        action="suppress",
        outcome="suppressed",
        suppression_reason="session_cooldown",
        cooldown_key=event_b.event_id,
        created_at=created_at,
    )
    run = CognitiveSchedulerRun(
        run_id="manual:cognitive_scheduler:2026-06-13T00:00:00+00:00",
        trigger="manual",
        scanned_session_count=1,
        decision_count=2,
        emitted_count=1,
        suppressed_count=1,
        payload={"session_keys": ["cli:test"]},
        created_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
    )

    for item in (event_a, event_b):
        cognitive_ledger.append_event(item)
    for item in (decision_a, decision_b):
        cognitive_ledger.append_decision(item)
    scheduler_ledger.append(run)

    event_rows = cognitive_ledger.recent_events(limit=10)
    decision_rows = cognitive_ledger.recent_decisions(limit=10)
    summary = scheduler_ledger.summary(limit=10)
    latest_scan_like = {
        "decision_count": len(decision_rows),
        "emitted_count": sum(1 for item in decision_rows if item["outcome"] == "emitted"),
        "suppressed_count": sum(1 for item in decision_rows if item["outcome"] == "suppressed"),
        "event_types": [item["event_type"] for item in event_rows],
    }

    assert latest_scan_like["decision_count"] == 2
    assert latest_scan_like["emitted_count"] == 1
    assert latest_scan_like["suppressed_count"] == 1
    assert latest_scan_like["event_types"] == ["goal_nudge", "scheduled_reminder"]
    assert summary["latest_scheduler_run"]["decision_count"] == 2
    assert summary["latest_scheduler_run"]["emitted_count"] == 1
    assert summary["latest_scheduler_run"]["suppressed_count"] == 1
