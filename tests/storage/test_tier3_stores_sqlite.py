"""Tests for Tier-3 SQLite stores (FactRelation, ReviewProposal, ReviewProposalEvent)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from OriginAgent.agent.tier3_stores_sqlite import (
    FactRelationStoreSqlite, ReviewProposalEventStoreSqlite, ReviewProposalStoreSqlite,
)


def _w(path: Path, lines: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in lines:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ==========================================================================
# FactRelationStoreSqlite
# ==========================================================================

class TestFactRelationStoreSqlite:
    def test_migrate_preserves_relations(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "fact_relations.jsonl"
        db = tmp_path / "memory" / "fact_relations.sqlite3"
        _w(j, [
            {"relation_id": "r1", "source_fact_id": "f1", "target_fact_id": "f2", "relation_type": "related_to"},
            {"relation_id": "r2", "source_fact_id": "f2", "target_fact_id": "f3", "relation_type": "supersedes"},
        ])
        store = FactRelationStoreSqlite(workspace=tmp_path, db_path=db)
        result = store.migrate()
        assert result.ok and result.records_imported == 2

    def test_migrate_is_idempotent(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "fact_relations.jsonl"
        db = tmp_path / "memory" / "fact_relations.sqlite3"
        _w(j, [{"relation_id": "r1", "source_fact_id": "f1", "target_fact_id": "f2"}])
        store = FactRelationStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert store.migrate().records_imported == 1

    def test_read_all(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "fact_relations.jsonl"
        db = tmp_path / "memory" / "fact_relations.sqlite3"
        _w(j, [
            {"relation_id": "r1", "source_fact_id": "f1", "target_fact_id": "f2"},
            {"relation_id": "r2", "source_fact_id": "f3", "target_fact_id": "f4"},
        ])
        store = FactRelationStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert len(store.read_all()) == 2

    def test_upsert_replaces_same_edge(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "fact_relations.jsonl"
        db = tmp_path / "memory" / "fact_relations.sqlite3"
        _w(j, [{"relation_id": "r1", "source_fact_id": "f1", "target_fact_id": "f2", "relation_type": "related_to", "confidence": 0.5}])
        store = FactRelationStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        # Same composite key → replace
        store.upsert({"relation_id": "r1", "source_fact_id": "f1", "target_fact_id": "f2", "relation_type": "related_to", "confidence": 0.9})
        all_records = store.read_all()
        assert len(all_records) == 1
        assert all_records[0]["confidence"] == 0.9

    def test_reverse_supersedes_index(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "fact_relations.jsonl"
        db = tmp_path / "memory" / "fact_relations.sqlite3"
        _w(j, [
            {"relation_id": "r1", "source_fact_id": "newer", "target_fact_id": "older", "relation_type": "supersedes", "status": "active"},
        ])
        store = FactRelationStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        idx = store.reverse_supersedes_index()
        assert "older" in idx
        assert "newer" in idx["older"]

    def test_related_fact_ids_graph_traversal(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "fact_relations.jsonl"
        db = tmp_path / "memory" / "fact_relations.sqlite3"
        _w(j, [
            {"relation_id": "r1", "source_fact_id": "a", "target_fact_id": "b", "relation_type": "related_to", "status": "active"},
            {"relation_id": "r2", "source_fact_id": "b", "target_fact_id": "c", "relation_type": "related_to", "status": "active"},
        ])
        store = FactRelationStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        related = store.related_fact_ids("a", depth=2)
        assert related == {"b", "c"}


# ==========================================================================
# ReviewProposalStoreSqlite
# ==========================================================================

class TestReviewProposalStoreSqlite:
    def test_migrate_and_list(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "review_proposals.jsonl"
        db = tmp_path / "memory" / "review_proposals.sqlite3"
        _w(j, [
            {"id": "p1", "title": "Proposal 1", "proposal_type": "fact", "status": "pending"},
            {"id": "p2", "title": "Proposal 2", "proposal_type": "skill", "status": "applied"},
        ])
        store = ReviewProposalStoreSqlite(workspace=tmp_path, db_path=db)
        result = store.migrate()
        assert result.ok and result.records_imported == 2
        assert len(store.list_records()) == 2

    def test_list_filtered_by_status(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "review_proposals.jsonl"
        db = tmp_path / "memory" / "review_proposals.sqlite3"
        _w(j, [
            {"id": "p1", "status": "pending"},
            {"id": "p2", "status": "applied"},
        ])
        store = ReviewProposalStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert len(store.list_records(status="pending")) == 1

    def test_get_by_id(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "review_proposals.jsonl"
        db = tmp_path / "memory" / "review_proposals.sqlite3"
        _w(j, [{"id": "target", "title": "Found"}])
        store = ReviewProposalStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert store.get("target")["title"] == "Found"
        assert store.get("nonexistent") is None

    def test_update_status(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "review_proposals.jsonl"
        db = tmp_path / "memory" / "review_proposals.sqlite3"
        _w(j, [{"id": "p1", "status": "pending"}])
        store = ReviewProposalStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        assert store.update_status("p1", "applied") is True
        assert store.get("p1")["status"] == "applied"

    def test_stats(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "review_proposals.jsonl"
        db = tmp_path / "memory" / "review_proposals.sqlite3"
        _w(j, [
            {"id": "p1", "status": "pending", "proposal_type": "fact"},
            {"id": "p2", "status": "applied", "proposal_type": "fact"},
        ])
        store = ReviewProposalStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        s = store.stats()
        assert s["proposal_count"] == 2
        assert s["pending_count"] == 1

    def test_idempotent(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "review_proposals.jsonl"
        db = tmp_path / "memory" / "review_proposals.sqlite3"
        _w(j, [{"id": "p1"}])
        store = ReviewProposalStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert store.migrate().records_imported == 1


# ==========================================================================
# ReviewProposalEventStoreSqlite
# ==========================================================================

class TestReviewProposalEventStoreSqlite:
    def test_migrate_and_query(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "review_proposal_events.jsonl"
        db = tmp_path / "memory" / "review_proposal_events.sqlite3"
        _w(j, [
            {"event_id": "e1", "proposal_id": "p1", "status": "reviewed", "created_at": "2026-01-01T00:00:00Z"},
            {"event_id": "e2", "proposal_id": "p1", "status": "applied", "created_at": "2026-01-02T00:00:00Z"},
        ])
        store = ReviewProposalEventStoreSqlite(workspace=tmp_path, db_path=db)
        result = store.migrate()
        assert result.ok and result.records_imported == 2

        events = store.for_proposal("p1")
        assert len(events) == 2

        latest = store.latest_for_proposal("p1")
        assert latest["status"] == "applied"

    def test_idempotent(self, tmp_path: Path) -> None:
        j = tmp_path / "memory" / "review_proposal_events.jsonl"
        db = tmp_path / "memory" / "review_proposal_events.sqlite3"
        _w(j, [{"event_id": "e1", "proposal_id": "p1"}])
        store = ReviewProposalEventStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()
        assert store.migrate().records_imported == 1
