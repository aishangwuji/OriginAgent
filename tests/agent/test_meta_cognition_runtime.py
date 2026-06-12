from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from OriginAgent.agent.introspection.service import RuntimeIntrospectionService
from OriginAgent.agent.meta_cognition_audit import JsonlMetaCognitionAuditLedger
from OriginAgent.agent.meta_cognition_models import (
    ConfidenceTrace,
    ErrorPattern,
    EvolutionSeed,
    MetaTrigger,
    RecordTriggerResult,
    ReflectionRecord,
    ThoughtJournalEntry,
)
from OriginAgent.agent.meta_cognition_runtime import MetaCognitionRuntime
from OriginAgent.agent.meta_cognition_triggers import (
    build_task_completion_trigger,
    build_tool_failure_trigger,
    build_user_correction_trigger,
    latest_assistant_message,
)


def _config(**overrides):
    base = {
        "enabled": True,
        "trigger_collection_enabled": True,
        "max_accepted_triggers_per_turn": 2,
        "session_cooldown_seconds": 0,
        "trigger_type_cooldown_seconds": 0,
        "queue_max_items": 10,
        "capture_user_correction_explicit_only": True,
        "capture_task_completion_from_complete_goal_only": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_meta_cognition_models_roundtrip() -> None:
    trigger = MetaTrigger(
        trigger_id="t1",
        session_key="cli:direct",
        trigger_type="tool_failure",
        source_type="tool_execution_observer",
        source_reference="grep:abcd1234",
    )
    assert MetaTrigger.from_json(trigger.to_json()) == trigger

    result = RecordTriggerResult(accepted=True, decision="accepted")
    assert RecordTriggerResult.from_json(result.to_json()) == result

    journal = ThoughtJournalEntry(entry_id="j1", session_key="cli:direct", summary="j")
    reflection = ReflectionRecord(reflection_id="r1", session_key="cli:direct", summary="r")
    confidence = ConfidenceTrace(trace_id="c1", session_key="cli:direct", summary="c")
    pattern = ErrorPattern(pattern_id="p1", session_key="cli:direct", summary="p")
    seed = EvolutionSeed(seed_id="e1", session_key="cli:direct", summary="e")

    assert ThoughtJournalEntry.from_json(journal.to_json()) == journal
    assert ReflectionRecord.from_json(reflection.to_json()) == reflection
    assert ConfidenceTrace.from_json(confidence.to_json()) == confidence
    assert ErrorPattern.from_json(pattern.to_json()) == pattern
    assert EvolutionSeed.from_json(seed.to_json()) == seed


def test_meta_cognition_runtime_accepts_and_suppresses(tmp_path: Path) -> None:
    runtime = MetaCognitionRuntime(
        config=_config(max_accepted_triggers_per_turn=1),
        audit=JsonlMetaCognitionAuditLedger(tmp_path),
    )
    trigger_a = MetaTrigger(
        trigger_id="a",
        session_key="cli:direct",
        trigger_type="tool_failure",
        source_type="tool_execution_observer",
        source_reference="grep:1",
        created_at="2026-06-12T00:00:00+00:00",
    )
    accepted = runtime.record_trigger(trigger_a, turn_id="turn-1")
    assert accepted.accepted is True
    assert accepted.decision == "accepted"

    duplicate = runtime.record_trigger(trigger_a, turn_id="turn-1")
    assert duplicate.accepted is False
    assert duplicate.decision == "suppressed_duplicate"

    trigger_b = MetaTrigger(
        trigger_id="b",
        session_key="cli:direct",
        trigger_type="user_correction",
        source_type="turn_end_scan",
        source_reference="user_correction:2",
        created_at="2026-06-12T00:00:01+00:00",
    )
    limited = runtime.record_trigger(trigger_b, turn_id="turn-1")
    assert limited.accepted is False
    assert limited.decision == "suppressed_turn_limit"


def test_meta_cognition_runtime_respects_disabled_state(tmp_path: Path) -> None:
    runtime = MetaCognitionRuntime(
        config=_config(enabled=False, trigger_collection_enabled=False),
        audit=JsonlMetaCognitionAuditLedger(tmp_path),
    )
    trigger = MetaTrigger(
        trigger_id="a",
        session_key="cli:direct",
        trigger_type="tool_failure",
        source_type="tool_execution_observer",
        source_reference="grep:1",
    )
    result = runtime.record_trigger(trigger, turn_id="turn-1")
    assert result.accepted is False
    assert result.decision == "dropped_runtime_disabled"


def test_build_tool_failure_trigger_filters_status() -> None:
    trigger = build_tool_failure_trigger(
        session_key="cli:direct",
        tool_name="grep",
        params={"pattern": "TODO"},
        status="error",
        error_kind="RuntimeError",
    )
    assert trigger is not None
    assert trigger.trigger_type == "tool_failure"
    assert trigger.source_reference.startswith("grep:")

    assert build_tool_failure_trigger(
        session_key="cli:direct",
        tool_name="grep",
        params={"pattern": "TODO"},
        status="validation_error",
    ) is None


def test_build_task_completion_trigger_requires_completed_goal() -> None:
    assert build_task_completion_trigger(
        session_key="cli:direct",
        session_metadata={"goal_state": {"status": "active"}},
        params={"recap": "done"},
    ) is None

    trigger = build_task_completion_trigger(
        session_key="cli:direct",
        session_metadata={"goal_state": {"status": "completed"}},
        params={"recap": "done"},
    )
    assert trigger is not None
    assert trigger.trigger_type == "task_completion"
    assert trigger.payload["recap_present"] is True


def test_build_user_correction_trigger_matches_explicit_patterns_only() -> None:
    trigger = build_user_correction_trigger(
        session_key="cli:direct",
        user_message="不是这个问题，你理解错了",
        last_assistant_message="Earlier answer",
    )
    assert trigger is not None
    assert trigger.trigger_type == "user_correction"

    assert build_user_correction_trigger(
        session_key="cli:direct",
        user_message="继续",
        last_assistant_message="Earlier answer",
    ) is None

    assert build_user_correction_trigger(
        session_key="cli:direct",
        user_message="还有吗",
        last_assistant_message="Earlier answer",
    ) is None


def test_latest_assistant_message_returns_latest_text() -> None:
    assert latest_assistant_message(
        [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "assistant", "content": "a2"},
        ]
    ) == "a2"


def test_meta_cognition_summary_is_independent_from_continuity(tmp_path: Path) -> None:
    runtime = MetaCognitionRuntime(
        config=_config(enabled=False, trigger_collection_enabled=False),
        audit=JsonlMetaCognitionAuditLedger(tmp_path),
    )
    loop = SimpleNamespace(
        _meta_cognition_runtime=runtime,
        _last_meta_cognition_summary={},
        _last_meta_trigger_scan=[],
    )
    service = RuntimeIntrospectionService(
        loop=loop,
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=[]),
        sessions=SimpleNamespace(),
        pending_queues={},
    )
    summary = service.meta_cognition_summary()
    assert summary["contract_version"] == "meta_cognition.v1.freeze"
    assert summary["enabled"] is False
    assert summary["trigger_collection_enabled"] is False
    assert summary["recent_triggers"] == []
