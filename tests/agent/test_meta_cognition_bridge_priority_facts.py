"""Tests for MetaCognitionReflector bridge: learned_rule_candidate → priority_facts.

Improvement B: high-confidence actionable learned rules (constraint / task_pattern)
are written to working_memory.priority_facts, which does NOT decay (30min). This
prevents the Agent from repeating the same mistake within a session — the rule
persists across turns as a hard constraint.

Scope:
    - Test the new ``WorkingMemoryManager.append_priority_fact`` method.
    - Test ``_bridge_to_working_memory`` extension that writes
      ``learned_rule_candidate`` to ``priority_facts`` when filters pass.
    - Test filter boundaries: retention_hint, confidence, kind, sensitivity.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.meta_cognition_audit import JsonlMetaCognitionAuditLedger
from OriginAgent.agent.meta_cognition_models import ReflectionRecord
from OriginAgent.agent.meta_cognition_reflector import MetaCognitionReflector
from OriginAgent.agent.working_memory import (
    WORKING_MEMORY_METADATA_KEY,
    WorkingMemoryManager,
    WorkingMemorySnapshot,
)
from OriginAgent.session.manager import Session, SessionManager
from OriginAgent.utils.tracing import log_event


# ─── Test fixtures ─────────────────────────────────────────────────────────


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
        "working_memory_bridge_enabled": True,
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


def _make_reflector(
    tmp_path: Path,
    *,
    session: SimpleNamespace,
    working_memory: Any,
    sessions: Any | None = None,
) -> MetaCognitionReflector:
    sessions = sessions or SimpleNamespace(get_or_create=lambda key: session)
    return MetaCognitionReflector(
        workspace=tmp_path,
        config=_config(),
        audit=JsonlMetaCognitionAuditLedger(tmp_path),
        auxiliary_router=None,
        provider=MagicMock(),
        model="test-model",
        sessions=sessions,
        working_memory=working_memory,
        context_config=SimpleNamespace(world_attention_max_items=3),
    )


def _make_reflection(
    *,
    learned_rule_candidate: dict[str, Any] | None = None,
    retention_hint: str = "candidate",
    what_failed: list[str] | None = None,
    summary: str = "",
) -> ReflectionRecord:
    return ReflectionRecord(
        reflection_id="ref_test_001",
        session_key="cli:direct",
        created_at="2026-07-17T12:00:00+00:00",
        source_entry_ids=["entry_1"],
        reflection_kind="user_correction",
        outcome_class="failure",
        root_cause_hypotheses=[],
        what_worked=[],
        what_failed=what_failed or [],
        learned_rule_candidate=learned_rule_candidate,
        confidence=0.9,
        retention_hint=retention_hint,
        summary=summary,
        payload={"trigger_types": ["user_correction"]},
    )


def _mock_working_memory(snapshot: WorkingMemorySnapshot | None = None) -> MagicMock:
    """Create a mock working_memory that returns the given snapshot on load()."""
    wm = MagicMock()
    wm.load.return_value = snapshot or WorkingMemorySnapshot(session_key="cli:direct")
    return wm


# ─── Test class 1: WorkingMemoryManager.append_priority_fact ──────────────


class TestAppendPriorityFact:
    """Test the new append_priority_fact method on WorkingMemoryManager."""

    def test_append_priority_fact_adds_to_snapshot(self, tmp_path: Path) -> None:
        """append_priority_fact writes the fact to priority_facts list."""
        session = Session(key="cli:direct", metadata={})
        sessions = MagicMock(spec=SessionManager)
        sessions.get_or_create.return_value = session
        wm = WorkingMemoryManager(sessions)

        wm.append_priority_fact(session, "do not retry denied exec in cron sessions")

        snapshot = wm.load(session)
        assert "do not retry denied exec in cron sessions" in snapshot.priority_facts

    def test_append_priority_fact_preserves_existing(self, tmp_path: Path) -> None:
        """append_priority_fact preserves existing priority_facts."""
        session = Session(
            key="cli:direct",
            metadata={
                WORKING_MEMORY_METADATA_KEY: WorkingMemorySnapshot(
                    session_key="cli:direct",
                    priority_facts=["existing rule"],
                ).to_json()
            },
        )
        sessions = MagicMock(spec=SessionManager)
        sessions.get_or_create.return_value = session
        wm = WorkingMemoryManager(sessions)

        wm.append_priority_fact(session, "new rule")

        snapshot = wm.load(session)
        assert "existing rule" in snapshot.priority_facts
        assert "new rule" in snapshot.priority_facts

    def test_append_priority_fact_empty_string_noop(self, tmp_path: Path) -> None:
        """append_priority_fact with empty string does not add."""
        session = Session(key="cli:direct", metadata={})
        sessions = MagicMock(spec=SessionManager)
        sessions.get_or_create.return_value = session
        wm = WorkingMemoryManager(sessions)

        wm.append_priority_fact(session, "")

        snapshot = wm.load(session)
        assert snapshot.priority_facts == []

    def test_append_priority_fact_truncates_long_text(self, tmp_path: Path) -> None:
        """append_priority_fact truncates text exceeding max_chars."""
        session = Session(key="cli:direct", metadata={})
        sessions = MagicMock(spec=SessionManager)
        sessions.get_or_create.return_value = session
        wm = WorkingMemoryManager(sessions)

        long_text = "x" * 300
        wm.append_priority_fact(session, long_text)

        snapshot = wm.load(session)
        assert len(snapshot.priority_facts) == 1
        # _normalize_items truncates to 240 chars + "..."
        assert snapshot.priority_facts[0].endswith("...")
        assert len(snapshot.priority_facts[0]) <= 244  # 240 + "..."


# ─── Test class 2: bridge writes learned_rule to priority_facts ───────────


class TestBridgeWritesLearnedRuleToPriorityFacts:
    """Test _bridge_to_working_memory writes learned_rule_candidate to priority_facts."""

    def test_bridge_writes_constraint_rule_to_priority_facts(self, tmp_path: Path) -> None:
        """High-confidence constraint rule with retention=candidate → priority_facts."""
        snapshot = WorkingMemorySnapshot(session_key="cli:direct")
        wm = _mock_working_memory(snapshot)
        session = SimpleNamespace(key="cli:direct", metadata={})
        reflector = _make_reflector(tmp_path, session=session, working_memory=wm)

        reflection = _make_reflection(
            learned_rule_candidate={
                "kind": "constraint",
                "summary": "do not retry denied exec in cron sessions",
                "confidence": 0.9,
                "scope_hint": "session",
                "sensitivity": "low",
                "supporting_refs": [],
            },
            retention_hint="candidate",
            what_failed=["attempted exec in cron session, was denied"],
            summary="Agent retried denied exec tool 3 times in cron session",
        )

        reflector._bridge_to_working_memory(session, reflection, runtime_context=None)

        # Verify append_priority_fact was called with the learned rule
        wm.append_priority_fact.assert_called_once()
        call_args = wm.append_priority_fact.call_args
        assert call_args.args[0] is session
        fact_text = call_args.args[1]
        assert "do not retry denied exec in cron sessions" in fact_text
        assert "constraint" in fact_text.lower() or "learned_rule" in fact_text.lower()

    def test_bridge_writes_task_pattern_to_priority_facts(self, tmp_path: Path) -> None:
        """High-confidence task_pattern rule with retention=candidate → priority_facts."""
        snapshot = WorkingMemorySnapshot(session_key="cli:direct")
        wm = _mock_working_memory(snapshot)
        session = SimpleNamespace(key="cli:direct", metadata={})
        reflector = _make_reflector(tmp_path, session=session, working_memory=wm)

        reflection = _make_reflection(
            learned_rule_candidate={
                "kind": "task_pattern",
                "summary": "use close_episode once per turn to finalize",
                "confidence": 0.88,
                "scope_hint": "session",
                "sensitivity": "low",
                "supporting_refs": [],
            },
            retention_hint="candidate",
        )

        reflector._bridge_to_working_memory(session, reflection, runtime_context=None)

        wm.append_priority_fact.assert_called_once()
        fact_text = wm.append_priority_fact.call_args.args[1]
        assert "use close_episode once per turn" in fact_text

    def test_bridge_skips_when_retention_not_candidate(self, tmp_path: Path) -> None:
        """retention_hint=discard → no write to priority_facts."""
        snapshot = WorkingMemorySnapshot(session_key="cli:direct")
        wm = _mock_working_memory(snapshot)
        session = SimpleNamespace(key="cli:direct", metadata={})
        reflector = _make_reflector(tmp_path, session=session, working_memory=wm)

        reflection = _make_reflection(
            learned_rule_candidate={
                "kind": "constraint",
                "summary": "some rule",
                "confidence": 0.9,
                "scope_hint": "session",
                "sensitivity": "low",
                "supporting_refs": [],
            },
            retention_hint="discard",
        )

        reflector._bridge_to_working_memory(session, reflection, runtime_context=None)

        wm.append_priority_fact.assert_not_called()

    def test_bridge_skips_when_confidence_below_threshold(self, tmp_path: Path) -> None:
        """confidence < 0.85 → no write to priority_facts."""
        snapshot = WorkingMemorySnapshot(session_key="cli:direct")
        wm = _mock_working_memory(snapshot)
        session = SimpleNamespace(key="cli:direct", metadata={})
        reflector = _make_reflector(tmp_path, session=session, working_memory=wm)

        reflection = _make_reflection(
            learned_rule_candidate={
                "kind": "constraint",
                "summary": "low confidence rule",
                "confidence": 0.7,
                "scope_hint": "session",
                "sensitivity": "low",
                "supporting_refs": [],
            },
            retention_hint="candidate",
        )

        reflector._bridge_to_working_memory(session, reflection, runtime_context=None)

        wm.append_priority_fact.assert_not_called()

    def test_bridge_skips_when_kind_is_preference(self, tmp_path: Path) -> None:
        """kind=preference → no write to priority_facts (goes to memory_candidates instead)."""
        snapshot = WorkingMemorySnapshot(session_key="cli:direct")
        wm = _mock_working_memory(snapshot)
        session = SimpleNamespace(key="cli:direct", metadata={})
        reflector = _make_reflector(tmp_path, session=session, working_memory=wm)

        reflection = _make_reflection(
            learned_rule_candidate={
                "kind": "preference",
                "summary": "user prefers concise responses",
                "confidence": 0.9,
                "scope_hint": "user",
                "sensitivity": "low",
                "supporting_refs": [],
            },
            retention_hint="candidate",
        )

        reflector._bridge_to_working_memory(session, reflection, runtime_context=None)

        wm.append_priority_fact.assert_not_called()

    def test_bridge_skips_when_kind_is_fact(self, tmp_path: Path) -> None:
        """kind=fact → no write to priority_facts (goes to memory_candidates instead)."""
        snapshot = WorkingMemorySnapshot(session_key="cli:direct")
        wm = _mock_working_memory(snapshot)
        session = SimpleNamespace(key="cli:direct", metadata={})
        reflector = _make_reflector(tmp_path, session=session, working_memory=wm)

        reflection = _make_reflection(
            learned_rule_candidate={
                "kind": "fact",
                "summary": "user has a cat named mimi",
                "confidence": 0.95,
                "scope_hint": "user",
                "sensitivity": "low",
                "supporting_refs": [],
            },
            retention_hint="candidate",
        )

        reflector._bridge_to_working_memory(session, reflection, runtime_context=None)

        wm.append_priority_fact.assert_not_called()

    def test_bridge_skips_when_no_candidate(self, tmp_path: Path) -> None:
        """learned_rule_candidate=None → no write to priority_facts."""
        snapshot = WorkingMemorySnapshot(session_key="cli:direct")
        wm = _mock_working_memory(snapshot)
        session = SimpleNamespace(key="cli:direct", metadata={})
        reflector = _make_reflector(tmp_path, session=session, working_memory=wm)

        reflection = _make_reflection(
            learned_rule_candidate=None,
            retention_hint="candidate",
            what_failed=["something failed"],
        )

        reflector._bridge_to_working_memory(session, reflection, runtime_context=None)

        wm.append_priority_fact.assert_not_called()

    def test_bridge_skips_when_sensitivity_review_only(self, tmp_path: Path) -> None:
        """sensitivity=review-only → no write to priority_facts (needs human review)."""
        snapshot = WorkingMemorySnapshot(session_key="cli:direct")
        wm = _mock_working_memory(snapshot)
        session = SimpleNamespace(key="cli:direct", metadata={})
        reflector = _make_reflector(tmp_path, session=session, working_memory=wm)

        reflection = _make_reflection(
            learned_rule_candidate={
                "kind": "constraint",
                "summary": "sensitive rule needing review",
                "confidence": 0.9,
                "scope_hint": "session",
                "sensitivity": "review-only",
                "supporting_refs": [],
            },
            retention_hint="candidate",
        )

        reflector._bridge_to_working_memory(session, reflection, runtime_context=None)

        wm.append_priority_fact.assert_not_called()

    def test_bridge_writes_priority_fact_alongside_attention(self, tmp_path: Path) -> None:
        """When both what_failed and learned_rule are present, both are written.

        This verifies the new priority_facts bridge is additive — it does not
        replace the existing attention_items / pending_questions bridges.
        """
        snapshot = WorkingMemorySnapshot(session_key="cli:direct")
        wm = _mock_working_memory(snapshot)
        session = SimpleNamespace(key="cli:direct", metadata={})
        reflector = _make_reflector(tmp_path, session=session, working_memory=wm)

        reflection = _make_reflection(
            learned_rule_candidate={
                "kind": "constraint",
                "summary": "do not retry denied tools",
                "confidence": 0.92,
                "scope_hint": "session",
                "sensitivity": "low",
                "supporting_refs": [],
            },
            retention_hint="candidate",
            what_failed=["attempted denied tool"],
            summary="Agent retried denied tool",
        )

        reflector._bridge_to_working_memory(session, reflection, runtime_context=None)

        # Both attention_item and priority_fact should be written
        wm.append_attention_item.assert_called_once()
        wm.append_priority_fact.assert_called_once()
