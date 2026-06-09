from __future__ import annotations

import json

import pytest

from OriginAgent.agent.cognitive_audit import JsonlCognitiveAuditLedger
from OriginAgent.agent.cognitive_events import CognitiveDecision, CognitiveEvent
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


def test_runtime_introspection_includes_cognition_summary(tmp_path) -> None:
    ledger = JsonlCognitiveAuditLedger(tmp_path)
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
    assert summary["event_count"] == 1
    assert summary["decision_count"] == 1
    assert summary["outcome_counts"]["suppressed"] == 1
    assert summary["suppression_reason_counts"]["intent_cooldown"] == 1
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
