"""Tests for Tier-2 SQLite stores (memory candidates, scheduler, deliberation, idempotency)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from OriginAgent.agent.tier2_stores_sqlite import (
    DeliberationCyclesSqlite, IdempotencyKeysSqlite,
    MemoryCandidatesSqlite, SchedulerRunsSqlite,
)


def _w(path: Path, lines: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in lines:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ==========================================================================
# MemoryCandidatesSqlite
# ==========================================================================

class TestMemoryCandidatesSqlite:
    def test_migrate_preserves_candidates(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "memory_candidates.jsonl"
        db = tmp_path / "memory" / "memory_candidates.sqlite3"
        _w(j, [
            {"candidate_id": "c1", "kind": "fact", "summary": "test",
             "source_session_key": "s1", "owner_id": "user"},
            {"candidate_id": "c2", "kind": "preference", "summary": "test2",
             "source_session_key": "s2", "owner_id": "user"},
        ])
        store = MemoryCandidatesSqlite(workspace=tmp_path, db_path=db)

        result = store.migrate()
        assert result.ok and result.records_imported == 2

    def test_migrate_is_idempotent(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "memory_candidates.jsonl"
        db = tmp_path / "memory" / "memory_candidates.sqlite3"
        _w(j, [{"candidate_id": "c1", "kind": "fact", "summary": "test",
                "source_session_key": "s1", "owner_id": "user"}])
        store = MemoryCandidatesSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        result = store.migrate()
        assert result.records_imported == 1

    def test_read_all_unfiltered(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "memory_candidates.jsonl"
        db = tmp_path / "memory" / "memory_candidates.sqlite3"
        _w(j, [
            {"candidate_id": "c1", "kind": "fact", "source_session_key": "s1", "owner_id": "u1"},
            {"candidate_id": "c2", "kind": "preference", "source_session_key": "s1", "owner_id": "u1"},
        ])
        store = MemoryCandidatesSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert len(store.read_all()) == 2

    def test_read_all_filter_by_kind(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "memory_candidates.jsonl"
        db = tmp_path / "memory" / "memory_candidates.sqlite3"
        _w(j, [
            {"candidate_id": "c1", "kind": "fact", "source_session_key": "s1", "owner_id": "u1"},
            {"candidate_id": "c2", "kind": "preference", "source_session_key": "s1", "owner_id": "u1"},
        ])
        store = MemoryCandidatesSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert len(store.read_all(kinds=("fact",))) == 1

    def test_read_all_filter_by_owner(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "memory_candidates.jsonl"
        db = tmp_path / "memory" / "memory_candidates.sqlite3"
        _w(j, [
            {"candidate_id": "c1", "kind": "fact", "source_session_key": "s1", "owner_id": "alice"},
            {"candidate_id": "c2", "kind": "fact", "source_session_key": "s1", "owner_id": "bob"},
        ])
        store = MemoryCandidatesSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert len(store.read_all(owner_id="alice")) == 1

    def test_cursor_tracking(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "memory_candidates.jsonl"
        db = tmp_path / "memory" / "memory_candidates.sqlite3"
        _w(j, [])
        store = MemoryCandidatesSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        assert store.get_cursor("consumer-a") == 0
        store.set_cursor("consumer-a", 5)
        assert store.get_cursor("consumer-a") == 5

    def test_empty_store_returns_empty(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "memory_candidates.jsonl"
        db = tmp_path / "memory" / "memory_candidates.sqlite3"
        _w(j, [])
        store = MemoryCandidatesSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert store.read_all() == []


# ==========================================================================
# SchedulerRunsSqlite
# ==========================================================================

class TestSchedulerRunsSqlite:
    def test_migrate_and_query(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "cognitive" / "scheduler_runs.jsonl"
        db = tmp_path / "memory" / "cognitive" / "scheduler_runs.sqlite3"
        _w(j, [
            {"run_id": "r1", "trigger": "cron", "scanned_session_count": 5,
             "created_at": "2026-01-01T00:00:00Z"},
            {"run_id": "r2", "trigger": "manual", "scanned_session_count": 3,
             "created_at": "2026-02-01T00:00:00Z"},
        ])
        store = SchedulerRunsSqlite(workspace=tmp_path, db_path=db)
        result = store.migrate()
        assert result.ok and result.records_imported == 2

        recent = store.recent(limit=1)
        assert len(recent) == 1
        assert recent[0]["run_id"] == "r2"

    def test_idempotent(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "cognitive" / "scheduler_runs.jsonl"
        db = tmp_path / "memory" / "cognitive" / "scheduler_runs.sqlite3"
        _w(j, [{"run_id": "r1"}])
        store = SchedulerRunsSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert store.migrate().records_imported == 1


# ==========================================================================
# DeliberationCyclesSqlite
# ==========================================================================

class TestDeliberationCyclesSqlite:
    def test_migrate_and_query(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "bdi" / "cycles.jsonl"
        db = tmp_path / "memory" / "bdi" / "deliberation_cycles.sqlite3"
        _w(j, [
            {"cycle_id": "cy1", "created_at": "2026-01-01T00:00:00Z"},
            {"cycle_id": "cy2", "created_at": "2026-02-01T00:00:00Z"},
        ])
        store = DeliberationCyclesSqlite(workspace=tmp_path, db_path=db)
        result = store.migrate()
        assert result.ok and result.records_imported == 2

        recent = store.recent(limit=1)
        assert recent[0]["cycle_id"] == "cy2"

    def test_idempotent(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "bdi" / "cycles.jsonl"
        db = tmp_path / "memory" / "bdi" / "deliberation_cycles.sqlite3"
        _w(j, [{"cycle_id": "cy1"}])
        store = DeliberationCyclesSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert store.migrate().records_imported == 1


# ==========================================================================
# IdempotencyKeysSqlite
# ==========================================================================

class TestIdempotencyKeysSqlite:
    def test_migrate_imports_keys(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "action" / "idempotency_keys.jsonl"
        db = tmp_path / "memory" / "action" / "idempotency_keys.sqlite3"
        _w(j, [
            {"idempotency_key": "key-aaa"},
            {"idempotency_key": "key-bbb"},
        ])
        store = IdempotencyKeysSqlite(workspace=tmp_path, db_path=db)
        result = store.migrate()
        assert result.ok and result.records_imported == 2

    def test_load_returns_set(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "action" / "idempotency_keys.jsonl"
        db = tmp_path / "memory" / "action" / "idempotency_keys.sqlite3"
        _w(j, [{"idempotency_key": "key-aaa"}])
        store = IdempotencyKeysSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        keys = store.load()
        assert keys == {"key-aaa"}

    def test_add_and_contains(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "action" / "idempotency_keys.jsonl"
        db = tmp_path / "memory" / "action" / "idempotency_keys.sqlite3"
        _w(j, [])
        store = IdempotencyKeysSqlite(workspace=tmp_path, db_path=db)

        store.add("new-key")
        assert store.contains("new-key") is True
        assert store.contains("missing") is False

    def test_add_duplicate_is_noop(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "action" / "idempotency_keys.jsonl"
        db = tmp_path / "memory" / "action" / "idempotency_keys.sqlite3"
        _w(j, [])
        store = IdempotencyKeysSqlite(workspace=tmp_path, db_path=db)

        store.add("key-1")
        store.add("key-1")
        assert len(store.load()) == 1

    def test_migrate_is_idempotent(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "action" / "idempotency_keys.jsonl"
        db = tmp_path / "memory" / "action" / "idempotency_keys.sqlite3"
        _w(j, [{"idempotency_key": "key-aaa"}])
        store = IdempotencyKeysSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        result = store.migrate()
        assert result.records_imported == 1

    def test_empty_jsonl_is_noop(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "action" / "idempotency_keys.jsonl"
        db = tmp_path / "memory" / "action" / "idempotency_keys.sqlite3"
        _w(j, [])
        store = IdempotencyKeysSqlite(workspace=tmp_path, db_path=db)
        result = store.migrate()
        assert result.ok and result.records_imported == 0
