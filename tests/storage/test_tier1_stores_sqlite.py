"""Tests for Tier-1 append-only SQLite stores (cognitive, meta, thought, world, evolution)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from OriginAgent.agent.cognitive_audit_sqlite import CognitiveEventsSqlite, CognitiveDecisionsSqlite
from OriginAgent.agent.meta_cognition_audit_sqlite import (
    MetaConfidenceTracesSqlite, MetaDecisionsSqlite, MetaEvolutionSeedsSqlite,
    MetaJournalsSqlite, MetaPatternsSqlite, MetaReflectionsSqlite, MetaTriggersSqlite,
)
from OriginAgent.agent.tier1_stores_sqlite import (
    CausalEdgesSqlite, EvolutionConfigPatchesSqlite, EvolutionDependenciesSqlite,
    EvolutionHealthHistorySqlite, EvolutionOutcomesSqlite, EvolutionSandboxCacheSqlite,
    EvolutionTrialLogsSqlite, MetaProgrammingCompilationsSqlite,
    SimulationFeedbackSqlite, SimulationTracesSqlite,
    ThoughtFramesSqlite, ThoughtJournalsSqlite,
)


def _w(path: Path, lines: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in lines:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ==========================================================================
# Parametrized generic test for any append-only store
# ==========================================================================

STORE_SPECS = [
    # (class, jsonl_relpath, db_relpath, sample_record)
    ("CognitiveEvents", CognitiveEventsSqlite,
     "memory/cognitive/events.jsonl", "memory/cognitive/cognitive_events.sqlite3",
     {"event_id": "e1", "session_key": "s1", "event_type": "test"}),
    ("CognitiveDecisions", CognitiveDecisionsSqlite,
     "memory/cognitive/decisions.jsonl", "memory/cognitive/cognitive_decisions.sqlite3",
     {"event_id": "d1", "session_key": "s1"}),
    # Meta-cognition (7 stores)
    ("MetaTriggers", MetaTriggersSqlite,
     "memory/meta_cognition/triggers.jsonl", "memory/meta_cognition/meta_triggers.sqlite3",
     {"trigger_id": "t1", "session_key": "s1"}),
    ("MetaDecisions", MetaDecisionsSqlite,
     "memory/meta_cognition/decisions.jsonl", "memory/meta_cognition/meta_decisions.sqlite3",
     {"trigger_id": "t1", "created_at": "2026-01-01T00:00:00Z"}),
    ("MetaJournals", MetaJournalsSqlite,
     "memory/meta_cognition/journals.jsonl", "memory/meta_cognition/meta_journals.sqlite3",
     {"entry_id": "j1", "session_key": "s1"}),
    ("MetaReflections", MetaReflectionsSqlite,
     "memory/meta_cognition/reflections.jsonl", "memory/meta_cognition/meta_reflections.sqlite3",
     {"reflection_id": "r1", "session_key": "s1"}),
    ("MetaConfidenceTraces", MetaConfidenceTracesSqlite,
     "memory/meta_cognition/confidence_traces.jsonl", "memory/meta_cognition/meta_confidence_traces.sqlite3",
     {"trace_id": "c1", "session_key": "s1"}),
    ("MetaPatterns", MetaPatternsSqlite,
     "memory/meta_cognition/patterns.jsonl", "memory/meta_cognition/meta_patterns.sqlite3",
     {"pattern_id": "p1", "session_key": "s1"}),
    ("MetaEvolutionSeeds", MetaEvolutionSeedsSqlite,
     "memory/meta_cognition/evolution_seeds.jsonl", "memory/meta_cognition/meta_evolution_seeds.sqlite3",
     {"seed_id": "s1", "session_key": "s1"}),
    # Thought
    ("ThoughtFrames", ThoughtFramesSqlite,
     "memory/thought_substrate/frames.jsonl", "memory/thought_substrate/thought_frames.sqlite3",
     {"frame_id": "f1", "session_key": "s1"}),
    ("ThoughtJournals", ThoughtJournalsSqlite,
     "memory/thought_substrate/journals.jsonl", "memory/thought_substrate/thought_journals.sqlite3",
     {"entry_id": "j1", "session_key": "s1"}),
    # World
    ("CausalEdges", CausalEdgesSqlite,
     "memory/world_simulator/causal_edges.jsonl", "memory/world_simulator/causal_edges.sqlite3",
     {"edge_id": "e1"}),
    ("SimulationTraces", SimulationTracesSqlite,
     "memory/world_simulator/simulation_traces.jsonl", "memory/world_simulator/simulation_traces.sqlite3",
     {"trace_id": "t1"}),
    ("SimulationFeedback", SimulationFeedbackSqlite,
     "memory/world_simulator/simulation_feedback.jsonl", "memory/world_simulator/simulation_feedback.sqlite3",
     {"feedback_id": "f1"}),
    # Evolution
    ("EvolutionSandboxCache", EvolutionSandboxCacheSqlite,
     "memory/evolution_sandbox_cache.jsonl", "memory/evolution_sandbox_cache.sqlite3",
     {"cache_key": "k1"}),
    ("EvolutionOutcomes", EvolutionOutcomesSqlite,
     "memory/evolution_outcomes.jsonl", "memory/evolution_outcomes.sqlite3",
     {"event_id": "e1"}),
    ("EvolutionDependencies", EvolutionDependenciesSqlite,
     "memory/evolution_dependencies.jsonl", "memory/evolution_dependencies.sqlite3",
     {"dep_id": "d1"}),
    ("EvolutionHealthHistory", EvolutionHealthHistorySqlite,
     "memory/evolution_health_history.jsonl", "memory/evolution_health_history.sqlite3",
     {"snapshot_id": "s1"}),
    ("EvolutionTrialLogs", EvolutionTrialLogsSqlite,
     "memory/evolution_trial_logs.jsonl", "memory/evolution_trial_logs.sqlite3",
     {"trial_id": "t1"}),
    ("EvolutionConfigPatches", EvolutionConfigPatchesSqlite,
     "memory/evolution_config_patches.jsonl", "memory/evolution_config_patches.sqlite3",
     {"patch_id": "p1"}),
    ("MetaProgrammingCompilations", MetaProgrammingCompilationsSqlite,
     "memory/meta_programming_compilations.jsonl", "memory/meta_programming_compilations.sqlite3",
     {"compilation_id": "c1"}),
]


@pytest.mark.parametrize("name,cls,jsonl_rel,db_rel,sample", STORE_SPECS)
def test_migrate_preserves_record(name, cls, jsonl_rel, db_rel, sample, tmp_path: Path) -> None:
    jsonl = tmp_path / jsonl_rel
    db = tmp_path / db_rel
    _w(jsonl, [sample])
    store = cls(workspace=tmp_path, db_path=db)

    result = store.migrate()

    assert result.ok, f"{name}: {result.error}"
    assert result.records_imported == 1


@pytest.mark.parametrize("name,cls,jsonl_rel,db_rel,sample", STORE_SPECS)
def test_migrate_is_idempotent(name, cls, jsonl_rel, db_rel, sample, tmp_path: Path) -> None:
    jsonl = tmp_path / jsonl_rel
    db = tmp_path / db_rel
    _w(jsonl, [sample])
    store = cls(workspace=tmp_path, db_path=db)

    store.migrate()
    result = store.migrate()

    assert result.ok, f"{name}: {result.error}"
    assert result.records_imported == 1  # INSERT OR IGNORE


@pytest.mark.parametrize("name,cls,jsonl_rel,db_rel,sample", STORE_SPECS)
def test_empty_jsonl_is_noop(name, cls, jsonl_rel, db_rel, sample, tmp_path: Path) -> None:
    jsonl = tmp_path / jsonl_rel
    db = tmp_path / db_rel
    _w(jsonl, [])
    store = cls(workspace=tmp_path, db_path=db)

    result = store.migrate()

    assert result.ok
    assert result.records_imported == 0


@pytest.mark.parametrize("name,cls,jsonl_rel,db_rel,sample", STORE_SPECS)
def test_invalid_lines_are_skipped(name, cls, jsonl_rel, db_rel, sample, tmp_path: Path) -> None:
    jsonl = tmp_path / jsonl_rel
    db = tmp_path / db_rel
    _w(jsonl, [{}, sample])  # empty dict should fail validation
    store = cls(workspace=tmp_path, db_path=db)

    result = store.migrate()

    assert result.records_imported == 1
    assert result.records_skipped == 1


# ==========================================================================
# Store-specific query tests
# ==========================================================================

class TestCognitiveQueries:
    def test_events_by_session(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "cognitive" / "events.jsonl"
        db = tmp_path / "memory" / "cognitive" / "cognitive_events.sqlite3"
        _w(j, [{"event_id": "e1", "session_key": "sess-a"},
               {"event_id": "e2", "session_key": "sess-b"}])
        store = CognitiveEventsSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert len(store.by_session("sess-a")) == 1

    def test_events_recent(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "cognitive" / "events.jsonl"
        db = tmp_path / "memory" / "cognitive" / "cognitive_events.sqlite3"
        _w(j, [{"event_id": "e1", "session_key": "s1", "created_at": "2026-01-01T00:00:00Z"},
               {"event_id": "e2", "session_key": "s2", "created_at": "2026-02-01T00:00:00Z"}])
        store = CognitiveEventsSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        recent = store.recent(limit=1)
        assert len(recent) == 1
        assert recent[0]["event_id"] == "e2"

    def test_decisions_recent(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "cognitive" / "decisions.jsonl"
        db = tmp_path / "memory" / "cognitive" / "cognitive_decisions.sqlite3"
        _w(j, [{"event_id": "d1", "outcome": "ok"}])
        store = CognitiveDecisionsSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert len(store.recent()) == 1


class TestThoughtQueries:
    def test_frames_by_session(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "thought_substrate" / "frames.jsonl"
        db = tmp_path / "memory" / "thought_substrate" / "thought_frames.sqlite3"
        _w(j, [{"frame_id": "f1", "session_key": "sess-a"},
               {"frame_id": "f2", "session_key": "sess-b"}])
        store = ThoughtFramesSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert len(store.by_session("sess-a")) == 1

    def test_journals_recent(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "thought_substrate" / "journals.jsonl"
        db = tmp_path / "memory" / "thought_substrate" / "thought_journals.sqlite3"
        _w(j, [{"entry_id": "j1", "session_key": "s1"}])
        store = ThoughtJournalsSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert len(store.recent()) == 1


class TestMetaQueries:
    def test_meta_triggers_recent(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "meta_cognition" / "triggers.jsonl"
        db = tmp_path / "memory" / "meta_cognition" / "meta_triggers.sqlite3"
        _w(j, [{"trigger_id": "t1", "session_key": "s1"}])
        store = MetaTriggersSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert len(store.recent()) == 1
