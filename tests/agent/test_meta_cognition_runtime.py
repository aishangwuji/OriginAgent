from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.introspection.service import RuntimeIntrospectionService
from OriginAgent.agent.meta_cognition_evolution_bridge import bridge_patterns_to_signals
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
from OriginAgent.agent.meta_cognition_patterns import consolidate_error_patterns
from OriginAgent.agent.meta_cognition_reflector import MetaCognitionReflector
from OriginAgent.agent.meta_cognition_runtime import MetaCognitionRuntime
from OriginAgent.agent.meta_cognition_triggers import (
    build_task_completion_trigger,
    build_tool_failure_trigger,
    build_user_correction_trigger,
    latest_assistant_message,
)
from OriginAgent.memory.candidates import GovernedMemoryWriter


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
        "structured_reflection_enabled": False,
        "working_memory_bridge_enabled": False,
        "memory_candidate_bridge_enabled": False,
        "memory_candidate_min_confidence": 0.85,
        "pattern_consolidation_enabled": False,
        "evolution_bridge_enabled": False,
        "pattern_window_days": 14,
        "pattern_window_max_reflections": 200,
        "pattern_min_frequency": 3,
        "pattern_min_distinct_turns": 2,
        "pattern_max_example_refs": 5,
        "signal_max_evidence_refs": 6,
        "max_signal_upserts_per_turn": 1,
        "allowed_evolution_target_types": ("workflow_candidate", "skill_candidate"),
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

    journal = ThoughtJournalEntry(
        entry_id="j1",
        session_key="cli:direct",
        trigger_type="tool_failure",
        task_reference="turn-1",
        strategy_summary="try tool first",
        assumptions=["repo is present"],
        evidence_refs=["tool:grep"],
        confidence=0.4,
        expected_outcome="find file",
        actual_outcome="tool failed",
        mismatch_summary="grep missing",
        suggested_next_action="fallback to rg",
        summary="j",
    )
    reflection = ReflectionRecord(
        reflection_id="r1",
        session_key="cli:direct",
        source_entry_ids=["j1"],
        reflection_kind="error_review",
        outcome_class="partial",
        root_cause_hypotheses=["tool unavailable"],
        what_worked=["error detected"],
        what_failed=["tool unavailable"],
        learned_rule_candidate={
            "kind": "task_pattern",
            "summary": "Prefer rg over grep in this workspace",
            "confidence": 0.9,
            "scope_hint": "session",
            "sensitivity": "low",
            "supporting_refs": ["tool:grep"],
        },
        confidence=0.8,
        retention_hint="candidate",
        summary="r",
    )
    confidence = ConfidenceTrace(
        trace_id="c1",
        session_key="cli:direct",
        subject_type="plan",
        subject_reference="turn-1",
        initial_confidence=None,
        final_confidence=0.6,
        change_reason="initial_record",
        evidence_refs=["tool:grep"],
        summary="c",
    )
    pattern = ErrorPattern(
        pattern_id="p1",
        pattern_key="pk1",
        owner_id="user-1",
        summary="p",
        candidate_target_type="skill_candidate",
    )
    seed = EvolutionSeed(
        seed_id="e1",
        pattern_id="p1",
        pattern_key="pk1",
        owner_id="user-1",
        change_target_type="skill_candidate",
        target_key="meta.skill.answer_quality.user_correction.preference.pk1",
        title="Meta skill candidate",
        summary="e",
    )

    assert ThoughtJournalEntry.from_json(journal.to_json()) == journal
    assert ReflectionRecord.from_json(reflection.to_json()) == reflection
    assert ConfidenceTrace.from_json(confidence.to_json()) == confidence
    assert ErrorPattern.from_json(pattern.to_json()) == pattern
    assert EvolutionSeed.from_json(seed.to_json()) == seed

    legacy = ThoughtJournalEntry.from_json(
        {
            "entry_id": "legacy-journal",
            "session_key": "cli:legacy",
            "summary": "legacy summary",
            "payload": {"legacy": True},
        }
    )
    assert legacy.entry_id == "legacy-journal"
    assert legacy.summary == "legacy summary"
    assert legacy.trigger_type == ""
    assert pattern.owner_id == "user-1"
    assert seed.owner_id == "user-1"


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


def test_meta_cognition_runtime_take_accepted_triggers_for_turn(tmp_path: Path) -> None:
    runtime = MetaCognitionRuntime(
        config=_config(max_accepted_triggers_per_turn=2),
        audit=JsonlMetaCognitionAuditLedger(tmp_path),
    )
    trigger = MetaTrigger(
        trigger_id="accepted",
        session_key="cli:direct",
        trigger_type="tool_failure",
        source_type="tool_execution_observer",
        source_reference="grep:accepted",
    )
    result = runtime.record_trigger(trigger, turn_id="turn-7")
    assert result.accepted is True
    accepted = runtime.take_accepted_triggers_for_turn("turn-7")
    assert [item.trigger_id for item in accepted] == ["accepted"]
    assert runtime.take_accepted_triggers_for_turn("turn-7") == []


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
    assert summary["recent_journals"] == []
    assert summary["working_memory_bridge"]["enabled"] is False
    assert summary["recent_patterns"] == []
    assert summary["recent_evolution_seeds"] == []
    assert summary["uncertainty_stats"]["threshold"] == 0.5

def _reflector(
    tmp_path: Path,
    *,
    config: SimpleNamespace | None = None,
    router: Any | None = None,
    working_memory: Any | None = None,
):
    session = SimpleNamespace(
        key="cli:direct",
        metadata={"goal_state": {"status": "completed", "objective": "finish task"}},
    )
    sessions = SimpleNamespace(get_or_create=lambda key: session)
    wm = working_memory or SimpleNamespace(
        inspect=lambda session, identity=None: {
            "attention_items": [],
            "pending_questions": [],
        },
        load=lambda session, identity=None: SimpleNamespace(attention_items=[], pending_questions=[]),
        append_attention_item=MagicMock(),
        append_pending_question=MagicMock(),
    )
    return MetaCognitionReflector(
        workspace=tmp_path,
        config=config or _config(),
        audit=JsonlMetaCognitionAuditLedger(tmp_path),
        auxiliary_router=router,
        provider=MagicMock(),
        model="test-model",
        sessions=sessions,
        working_memory=wm,
        context_config=SimpleNamespace(world_attention_max_items=3),
    )


@pytest.mark.asyncio
async def test_meta_cognition_reflector_writes_minimal_journal_without_llm(tmp_path: Path) -> None:
    reflector = _reflector(tmp_path, config=_config(structured_reflection_enabled=False))
    trigger = MetaTrigger(
        trigger_id="mc_trigger_1",
        session_key="cli:direct",
        trigger_type="user_correction",
        source_type="turn_end_scan",
        source_reference="user_correction:1",
        evidence_refs=["user_message"],
    )

    result = await reflector.reflect_turn(
        session_key="cli:direct",
        turn_id="turn-1",
        turn_snapshot={"user_message": "不是这样", "assistant_final_content": "ok"},
        accepted_triggers=[trigger],
        runtime_context=SimpleNamespace(identity=None, user_id="user-1"),
    )

    assert result.status == "ok"
    assert result.reason == "minimal_only"
    journals = reflector.audit.recent_journals(limit=10)
    assert len(journals) == 1
    assert journals[0]["trigger_type"] == "user_correction"


@pytest.mark.asyncio
async def test_meta_cognition_reflector_policy_denied_stays_journal_only(tmp_path: Path) -> None:
    reflector = _reflector(tmp_path, config=_config(structured_reflection_enabled=True))
    trigger = MetaTrigger(
        trigger_id="mc_trigger_policy",
        session_key="cli:direct",
        trigger_type="tool_failure",
        source_type="tool_execution_observer",
        source_reference="tool:1",
        payload={"status": "policy_denied"},
    )

    result = await reflector.reflect_turn(
        session_key="cli:direct",
        turn_id="turn-2",
        turn_snapshot={"user_message": "do x", "assistant_final_content": "blocked"},
        accepted_triggers=[trigger],
        runtime_context=SimpleNamespace(identity=None, user_id="user-1"),
    )

    assert result.status == "ok"
    assert result.reason == "journal_only"
    assert reflector.audit.recent_reflections(limit=10) == []


@pytest.mark.asyncio
async def test_meta_cognition_reflector_llm_error_keeps_minimal_journal(tmp_path: Path) -> None:
    router = MagicMock()
    router.call_llm = AsyncMock(return_value=SimpleNamespace(finish_reason="error", content="boom"))
    reflector = _reflector(
        tmp_path,
        config=_config(structured_reflection_enabled=True),
        router=router,
    )
    trigger = MetaTrigger(
        trigger_id="mc_trigger_err",
        session_key="cli:direct",
        trigger_type="task_completion",
        source_type="complete_goal",
        source_reference="complete_goal",
    )

    result = await reflector.reflect_turn(
        session_key="cli:direct",
        turn_id="turn-3",
        turn_snapshot={"user_message": "done", "assistant_final_content": "done"},
        accepted_triggers=[trigger],
        runtime_context=SimpleNamespace(identity=None, user_id="user-1"),
    )

    assert result.status == "error"
    journals = reflector.audit.recent_journals(limit=10)
    assert len(journals) == 1
    assert reflector.audit.recent_reflections(limit=10) == []


@pytest.mark.asyncio
async def test_meta_cognition_reflector_writes_candidate_queue_for_high_confidence_rule(tmp_path: Path) -> None:
    router = MagicMock()
    router.call_llm = AsyncMock(
        return_value=SimpleNamespace(
            finish_reason="stop",
            content="""
```json
{
  "journal_enrichment": {
    "summary": "user corrected preference",
    "strategy_summary": "track explicit correction",
    "assumptions": ["correction is explicit"],
    "confidence": 0.8,
    "expected_outcome": "align response",
    "actual_outcome": "correction received",
    "mismatch_summary": "preference was missed",
    "suggested_next_action": "prefer concise updates"
  },
  "reflection": {
    "summary": "stable preference detected",
    "reflection_kind": "correction_review",
    "outcome_class": "partial",
    "root_cause_hypotheses": ["style preference not remembered"],
    "what_worked": ["explicit correction captured"],
    "what_failed": ["preference not applied"],
    "learned_rule_candidate": {
      "kind": "preference",
      "summary": "User prefers concise updates",
      "confidence": 0.93,
      "scope_hint": "user",
      "sensitivity": "low",
      "supporting_refs": ["user_message"]
    },
    "confidence": 0.88,
    "retention_hint": "candidate"
  },
  "confidence_trace": {
    "summary": "confidence recorded",
    "subject_type": "rule",
    "subject_reference": "preference",
    "initial_confidence": null,
    "final_confidence": 0.88,
    "change_reason": "initial_record",
    "evidence_refs": ["user_message"]
  }
}
```""",
        )
    )
    working_memory = SimpleNamespace(
        inspect=lambda session, identity=None: {"attention_items": [], "pending_questions": []},
        load=lambda session, identity=None: SimpleNamespace(attention_items=[], pending_questions=[]),
        append_attention_item=MagicMock(),
        append_pending_question=MagicMock(),
    )
    reflector = _reflector(
        tmp_path,
        config=_config(
            structured_reflection_enabled=True,
            working_memory_bridge_enabled=True,
            memory_candidate_bridge_enabled=True,
            memory_candidate_min_confidence=0.8,
        ),
        router=router,
        working_memory=working_memory,
    )
    trigger = MetaTrigger(
        trigger_id="mc_trigger_pref",
        session_key="cli:direct",
        trigger_type="user_correction",
        source_type="turn_end_scan",
        source_reference="user_correction:pref",
        evidence_refs=["user_message"],
    )

    result = await reflector.reflect_turn(
        session_key="cli:direct",
        turn_id="turn-4",
        turn_snapshot={"user_message": "以后请简洁一点", "assistant_final_content": "好"},
        accepted_triggers=[trigger],
        runtime_context=SimpleNamespace(identity=None, user_id="user-1"),
    )

    assert result.status == "ok"
    reflection_row = reflector.audit.recent_reflections(limit=10)[0]
    trace_row = reflector.audit.recent_confidence_traces(limit=10)[0]
    assert reflection_row["retention_hint"] == "candidate"
    assert reflection_row["payload"]["uncertainty_score"] < 0.5
    assert trace_row["payload"]["uncertainty_score"] == reflection_row["payload"]["uncertainty_score"]
    writer = GovernedMemoryWriter(tmp_path)
    candidates = writer.read_all()
    assert len(candidates) == 1
    assert candidates[0].kind == "preference"
    assert candidates[0].metadata["origin"] == "meta_cognition"
    assert working_memory.append_attention_item.call_count == 1
    assert working_memory.append_pending_question.call_count == 1


def test_meta_cognition_reflector_skips_attention_when_fast_path_ref_present(tmp_path: Path) -> None:
    working_memory = SimpleNamespace(
        inspect=lambda session, identity=None: {"attention_items": [], "pending_questions": []},
        load=lambda session, identity=None: SimpleNamespace(attention_items=[], pending_questions=[]),
        append_attention_item=MagicMock(),
        append_pending_question=MagicMock(),
    )
    reflector = _reflector(
        tmp_path,
        config=_config(working_memory_bridge_enabled=True),
        working_memory=working_memory,
    )
    reflection = ReflectionRecord(
        reflection_id="r1",
        session_key="cli:direct",
        what_failed=["user_correction: concise please"],
        summary="summary",
        payload={"source_reference": "user_correction:pref"},
    )

    reflector._bridge_to_working_memory(
        session=object(),
        reflection=reflection,
        runtime_context=SimpleNamespace(identity=None, meta_cognition_fast_path_refs={"user_correction:pref"}),
    )

    assert working_memory.append_attention_item.call_count == 0


def test_meta_cognition_summary_exposes_fast_path_decision_counts(tmp_path: Path) -> None:
    from OriginAgent.agent.introspection.service import RuntimeIntrospectionService

    loop = SimpleNamespace(
        _meta_cognition_runtime=SimpleNamespace(
            summary=lambda: {
                "contract_version": "meta_cognition.v1.freeze",
                "enabled": True,
                "trigger_collection_enabled": True,
                "runtime_status": {},
                "recent_triggers": [],
                "recent_decisions": [],
                "decision_counts": {},
                "suppression_reason_counts": {},
            }
        ),
        _meta_cognition_reflector=None,
        _meta_coordinator=SimpleNamespace(
            summary={
                "fast_path_decision_counts": {
                    "fast_path_working_memory_written": 1,
                    "fast_path_duplicate_skipped": 2,
                }
            },
            last_artifacts={},
        ),
    )
    service = RuntimeIntrospectionService(
        loop=loop,
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=[]),
        sessions=object(),
        pending_queues={},
    )

    summary = service.meta_cognition_summary()

    assert summary["fast_path_decision_counts"] == {
        "fast_path_working_memory_written": 1,
        "fast_path_duplicate_skipped": 2,
    }


def test_consolidate_error_patterns_merges_repeated_reflections() -> None:
    reflections = [
        ReflectionRecord(
            reflection_id=f"r{i}",
            session_key="cli:direct",
            created_at=f"2026-06-13T00:00:0{i}+00:00",
            source_entry_ids=[f"j{i}"],
            reflection_kind="error_review",
            outcome_class="incorrect",
            root_cause_hypotheses=["Tool retry strategy missing"],
            what_failed=["Tool retry strategy missing"],
            learned_rule_candidate={
                "kind": "task_pattern",
                "summary": "Retry grep-like tool calls with rg fallback",
                "confidence": 0.9,
                "scope_hint": "session",
                "sensitivity": "low",
                "supporting_refs": [f"tool:grep:{i}"],
            },
            payload={
                "owner_id": "user-1",
                "trigger_contexts": [{"trigger_type": "tool_failure", "status": "error"}],
                "evidence_refs": [f"tool:grep:{i}"],
            },
        )
        for i in range(1, 4)
    ]
    result = consolidate_error_patterns(
        reflections=reflections,
        config=_config(
            pattern_consolidation_enabled=True,
            pattern_min_frequency=3,
            pattern_min_distinct_turns=2,
        ),
        owner_id="user-1",
        current_turn_reflection_ids={"r3"},
        now=datetime(2026, 6, 14, tzinfo=timezone.utc),
    )
    assert len(result.patterns) == 1
    pattern = result.patterns[0]
    assert pattern.frequency == 3
    assert pattern.candidate_target_type == "workflow_candidate"
    assert pattern.severity == "high"
    assert pattern.recency_score > 0.0
    assert pattern.pattern_score > 0.0
    assert result.eligible_patterns[0].pattern_id == pattern.pattern_id


def test_bridge_patterns_to_signals_builds_signal_evidence_sources(tmp_path: Path) -> None:
    pattern = ErrorPattern(
        pattern_id="meta_pattern_1",
        pattern_key="abcdef1234567890",
        owner_id="user-1",
        created_at="2026-06-13T00:00:00+00:00",
        updated_at="2026-06-13T00:10:00+00:00",
        source_reflection_ids=["r1", "r2", "r3"],
        source_entry_ids=["j1", "j2"],
        source_session_keys=["cli:direct"],
        trigger_types=["user_correction"],
        capability_domain="answer_quality",
        severity="high",
        frequency=3,
        distinct_turn_count=3,
        example_refs=["meta:reflection:r1"],
        candidate_target_type="skill_candidate",
        summary="User corrections show repeated answer-quality mismatch",
    )
    results = bridge_patterns_to_signals(
        workspace=tmp_path,
        patterns=[pattern],
        config=_config(
            evolution_bridge_enabled=True,
            allowed_evolution_target_types=("skill_candidate",),
        ),
        artifact_lookup={
            "meta:pattern:meta_pattern_1": {
                "created_at": "2026-06-13T00:10:00+00:00",
                "summary": "pattern summary",
            },
            "meta:reflection:r1": {
                "session_key": "cli:direct",
                "created_at": "2026-06-13T00:00:00+00:00",
                "summary": "reflection one",
            },
            "meta:reflection:r2": {
                "session_key": "cli:direct",
                "created_at": "2026-06-13T00:01:00+00:00",
                "summary": "reflection two",
            },
            "meta:journal:j1": {
                "session_key": "cli:direct",
                "created_at": "2026-06-13T00:00:00+00:00",
                "summary": "journal one",
            },
        },
    )
    assert results[0].decision == "queued"
    signal_rows = GovernedMemoryWriter(tmp_path).read_all()
    assert signal_rows == []
    from OriginAgent.agent.evolution import OpportunitySignalStore

    signal = OpportunitySignalStore(tmp_path).read_all()[0]
    assert signal.kind == "skill_candidate"
    assert signal.evidence_sources[0]["preview"] != ""
    assert signal.summary.startswith("Origin: meta_cognition")


@pytest.mark.asyncio
async def test_meta_cognition_reflector_generates_patterns_and_seeds(tmp_path: Path) -> None:
    router = MagicMock()
    router.call_llm = AsyncMock(
        return_value=SimpleNamespace(
            finish_reason="stop",
            content="""
```json
{
  "journal_enrichment": {"summary": "tool failure observed"},
  "reflection": {
    "summary": "Repeated fallback issue",
    "reflection_kind": "error_review",
    "outcome_class": "incorrect",
    "root_cause_hypotheses": ["Tool retry strategy missing"],
    "what_worked": ["Failure captured"],
    "what_failed": ["Tool retry strategy missing"],
    "learned_rule_candidate": {
      "kind": "task_pattern",
      "summary": "Retry grep-like tool calls with rg fallback",
      "confidence": 0.92,
      "scope_hint": "session",
      "sensitivity": "low",
      "supporting_refs": ["tool:grep"]
    },
    "confidence": 0.9,
    "retention_hint": "candidate"
  },
  "confidence_trace": null
}
```""",
        )
    )
    reflector = _reflector(
        tmp_path,
        config=_config(
            structured_reflection_enabled=True,
            pattern_consolidation_enabled=True,
            evolution_bridge_enabled=True,
            allowed_evolution_target_types=("workflow_candidate", "skill_candidate"),
            pattern_min_frequency=3,
            pattern_min_distinct_turns=2,
        ),
        router=router,
    )
    runtime_context = SimpleNamespace(identity=None, user_id="user-1")
    trigger = MetaTrigger(
        trigger_id="mc_trigger_pattern",
        session_key="cli:direct",
        trigger_type="tool_failure",
        source_type="tool_execution_observer",
        source_reference="grep:pattern",
        evidence_refs=["tool:grep"],
        payload={"status": "error"},
    )
    for index in range(3):
        result = await reflector.reflect_turn(
            session_key="cli:direct",
            turn_id=f"turn-pattern-{index}",
            turn_snapshot={"user_message": "retry", "assistant_final_content": "done"},
            accepted_triggers=[trigger],
            runtime_context=runtime_context,
        )
        assert result.status == "ok"
    patterns = reflector.audit.recent_patterns(limit=10)
    seeds = reflector.audit.recent_evolution_seeds(limit=10)
    assert len(patterns) >= 1
    assert len(seeds) >= 1
    assert seeds[-1]["change_target_type"] == "workflow_candidate"


@pytest.mark.asyncio
async def test_meta_cognition_reflector_records_uncertainty_without_trace(tmp_path: Path) -> None:
    router = MagicMock()
    router.call_llm = AsyncMock(
        return_value=SimpleNamespace(
            finish_reason="stop",
            content="""
```json
{
  "journal_enrichment": {"summary": "tool failure observed"},
  "reflection": {
    "summary": "",
    "reflection_kind": "error_review",
    "outcome_class": "incorrect",
    "root_cause_hypotheses": [],
    "what_worked": [],
    "what_failed": [],
    "learned_rule_candidate": null,
    "confidence": 0.2,
    "retention_hint": "candidate"
  },
  "confidence_trace": null
}
```""",
        )
    )
    reflector = _reflector(
        tmp_path,
        config=_config(structured_reflection_enabled=True),
        router=router,
    )
    trigger = MetaTrigger(
        trigger_id="mc_trigger_uq",
        session_key="cli:direct",
        trigger_type="tool_failure",
        source_type="tool_execution_observer",
        source_reference="grep:uq",
        evidence_refs=["tool:grep"],
        payload={"status": "error"},
    )

    result = await reflector.reflect_turn(
        session_key="cli:direct",
        turn_id="turn-uq",
        turn_snapshot={"user_message": "retry", "assistant_final_content": "done"},
        accepted_triggers=[trigger],
        runtime_context=SimpleNamespace(identity=None, user_id="user-1"),
    )

    assert result.status == "ok"
    reflection_row = reflector.audit.recent_reflections(limit=10)[0]
    assert reflection_row["payload"]["uncertainty_score"] >= 0.5
    assert "missing_summary" in reflection_row["payload"]["uncertainty_reason_codes"]
    assert reflector.audit.recent_confidence_traces(limit=10) == []


@pytest.mark.asyncio
async def test_meta_cognition_prompt_includes_historical_similar_sections(tmp_path: Path) -> None:
    reflector = _reflector(tmp_path, config=_config(structured_reflection_enabled=True))
    reflector.audit.append_pattern(ErrorPattern(
        pattern_id="meta_pattern_hist",
        pattern_key="pk_hist",
        owner_id="user-1",
        updated_at="2026-06-13T00:10:00+00:00",
        trigger_types=["tool_failure"],
        capability_domain="tool/grep",
        severity="high",
        frequency=3,
        distinct_turn_count=3,
        recency_score=1.0,
        pattern_score=0.91,
        candidate_target_type="workflow_candidate",
        summary="Retry grep-like tool calls with rg fallback",
    ))
    reflector.audit.append_reflection(ReflectionRecord(
        reflection_id="hist_reflection",
        session_key="cli:direct",
        created_at="2026-06-13T00:09:00+00:00",
        source_entry_ids=["j1"],
        reflection_kind="error_review",
        outcome_class="incorrect",
        root_cause_hypotheses=["Tool retry strategy missing"],
        what_failed=["Tool retry strategy missing"],
        confidence=0.9,
        summary="Repeated fallback issue",
        payload={"owner_id": "user-1", "trigger_types": ["tool_failure"]},
    ))
    session = reflector.sessions.get_or_create("cli:direct")
    prompt = reflector._build_prompt(
        session=session,
        turn_id="turn-historical",
        accepted_triggers=[
            MetaTrigger(
                trigger_id="mc_trigger_hist",
                session_key="cli:direct",
                trigger_type="tool_failure",
                source_type="tool_execution_observer",
                source_reference="grep:pattern",
                evidence_refs=["tool:grep"],
            )
        ],
        turn_snapshot={
            "user_message": "retry grep",
            "assistant_final_content": "done",
            "previous_assistant_message": "try grep",
        },
        runtime_context=SimpleNamespace(identity=None, user_id="user-1"),
        historical_context=reflector._retrieve_historical_context(
            accepted_triggers=[
                MetaTrigger(
                    trigger_id="mc_trigger_hist",
                    session_key="cli:direct",
                    trigger_type="tool_failure",
                    source_type="tool_execution_observer",
                    source_reference="grep:pattern",
                    evidence_refs=["tool:grep"],
                )
            ],
            turn_snapshot={
                "user_message": "retry grep",
                "assistant_final_content": "done",
                "previous_assistant_message": "try grep",
            },
            owner_id="user-1",
        ),
    )

    assert "## Historical Similar Patterns" in prompt
    assert "## Historical Similar Reflections" in prompt
    assert "Retry grep-like tool calls with rg fallback" in prompt
    assert "Repeated fallback issue" in prompt


# ── _load_json_payload empty/blank handling ───────────────────────


def test_load_json_payload_empty_string_returns_none() -> None:
    assert MetaCognitionReflector._load_json_payload("") is None


def test_load_json_payload_whitespace_returns_none() -> None:
    assert MetaCognitionReflector._load_json_payload("   ") is None


def test_load_json_payload_backtick_only_returns_none() -> None:
    assert MetaCognitionReflector._load_json_payload("```") is None


def test_load_json_payload_backtick_empty_body_returns_none() -> None:
    assert MetaCognitionReflector._load_json_payload("```\n   \n```") is None


@pytest.mark.asyncio
async def test_reflect_turn_empty_llm_response_does_not_crash(tmp_path: Path) -> None:
    """LLM returns finish_reason=stop with empty content — should not crash."""
    router = MagicMock()
    router.call_llm = AsyncMock(
        return_value=SimpleNamespace(finish_reason="stop", content=""),
    )
    reflector = _reflector(
        tmp_path,
        config=_config(structured_reflection_enabled=True),
        router=router,
    )
    trigger = MetaTrigger(
        trigger_id="mc_empty",
        session_key="cli:direct",
        trigger_type="tool_failure",
        source_type="tool_execution_observer",
        source_reference="grep:empty",
        evidence_refs=["tool:grep"],
        payload={"status": "error"},
    )
    result = await reflector.reflect_turn(
        session_key="cli:direct",
        turn_id="turn-empty",
        turn_snapshot={"user_message": "test", "assistant_final_content": "test"},
        accepted_triggers=[trigger],
        runtime_context=SimpleNamespace(identity=None, user_id="user-1"),
    )
    assert result.status == "error"
