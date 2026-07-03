"""Tests for JSONL -> SQLite migration template and follower stores."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from OriginAgent.storage.jsonl_migration import (
    AppendOnlyMigrator,
    MigrationResult,
    ReadModifyWriteMigrator,
)
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect


# ===========================================================================
# Test helpers
# ===========================================================================


def _write_jsonl(path: Path, lines: list[dict[str, Any]]) -> None:
    """Write *lines* as JSONL to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for record in lines:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ===========================================================================
# Test concretions
# ===========================================================================


class _SimpleAppendOnly(AppendOnlyMigrator):
    """Concrete AppendOnlyMigrator used for testing the base class logic."""

    def table_ddl(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS test_events (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}'
            ) STRICT;
        """

    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("id") and line.get("name"))

    def insert_row(self, conn, line: dict[str, Any]) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO test_events (id, name, payload_json) VALUES (?, ?, ?)",
            (line["id"], line["name"], json.dumps(line, ensure_ascii=False)),
        )


class _SimpleReadModifyWrite(ReadModifyWriteMigrator):
    """Concrete ReadModifyWriteMigrator used for testing the base class logic."""

    def table_ddl(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS test_items (
                item_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}'
            ) STRICT;
        """

    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("item_id"))

    def upsert_row(self, conn, line: dict[str, Any]) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO test_items (item_id, label, payload_json) VALUES (?, ?, ?)",
            (line["item_id"], line.get("label", ""), json.dumps(line, ensure_ascii=False)),
        )


# ===========================================================================
# AppendOnlyMigrator tests
# ===========================================================================


class TestAppendOnlyMigration:
    def test_empty_jsonl_produces_empty_sqlite(self, tmp_path: Path) -> None:
        """No source file -> zero records imported."""
        jsonl = tmp_path / "data.jsonl"
        db = tmp_path / "store.sqlite3"
        migrator = _SimpleAppendOnly(workspace=tmp_path, db_path=db, jsonl_path=jsonl)

        result = migrator.migrate()

        assert result.ok
        assert result.records_imported == 0
        assert result.records_skipped == 0

    def test_migration_preserves_all_records(self, tmp_path: Path) -> None:
        """All valid records are imported into SQLite."""
        jsonl = tmp_path / "data.jsonl"
        db = tmp_path / "store.sqlite3"
        records = [
            {"id": "a1", "name": "alpha", "value": 10},
            {"id": "b2", "name": "beta", "value": 20},
            {"id": "c3", "name": "gamma", "value": 30},
        ]
        _write_jsonl(jsonl, records)
        migrator = _SimpleAppendOnly(workspace=tmp_path, db_path=db, jsonl_path=jsonl)

        result = migrator.migrate()

        assert result.ok
        assert result.records_imported == 3
        assert result.records_skipped == 0
        conn = sqlite_connect(db)
        try:
            rows = conn.execute(
                "SELECT id, name FROM test_events ORDER BY id"
            ).fetchall()
            assert [tuple(r) for r in rows] == [("a1", "alpha"), ("b2", "beta"), ("c3", "gamma")]
        finally:
            conn.close()

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        """Running migrate() twice does not duplicate records in the DB."""
        jsonl = tmp_path / "data.jsonl"
        db = tmp_path / "store.sqlite3"
        records = [{"id": "x1", "name": "first"}]
        _write_jsonl(jsonl, records)
        migrator = _SimpleAppendOnly(workspace=tmp_path, db_path=db, jsonl_path=jsonl)

        migrator.migrate()
        migrator.migrate()

        # INSERT OR IGNORE prevents duplicates at the DB level
        conn = sqlite_connect(db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM test_events").fetchone()[0]
            assert count == 1
        finally:
            conn.close()

    def test_corrupt_lines_are_skipped(self, tmp_path: Path) -> None:
        """Invalid JSON and invalid rows are counted as skipped."""
        jsonl = tmp_path / "data.jsonl"
        db = tmp_path / "store.sqlite3"
        jsonl.parent.mkdir(parents=True, exist_ok=True)
        with open(jsonl, "w", encoding="utf-8") as f:
            f.write('{"id": "ok", "name": "good"}\n')
            f.write("not-json\n")
            f.write('{"id": "missing-name"}\n')  # fails validate_line
            f.write('{"id": "ok2", "name": "good2"}\n')
        migrator = _SimpleAppendOnly(workspace=tmp_path, db_path=db, jsonl_path=jsonl)

        result = migrator.migrate()

        assert result.ok
        assert result.records_imported == 2
        assert result.records_skipped == 2

    def test_missing_jsonl_file_is_noop(self, tmp_path: Path) -> None:
        """Non-existent JSONL path is handled gracefully."""
        jsonl = tmp_path / "nonexistent.jsonl"
        db = tmp_path / "store.sqlite3"
        migrator = _SimpleAppendOnly(workspace=tmp_path, db_path=db, jsonl_path=jsonl)

        result = migrator.migrate()

        assert result.ok
        assert result.records_imported == 0
        assert result.records_skipped == 0


# ===========================================================================
# ReadModifyWriteMigrator tests
# ===========================================================================


class TestReadModifyWriteMigration:
    def test_empty_jsonl_produces_empty_sqlite(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "items.jsonl"
        db = tmp_path / "items.sqlite3"
        migrator = _SimpleReadModifyWrite(workspace=tmp_path, db_path=db, jsonl_path=jsonl)

        result = migrator.migrate()

        assert result.ok
        assert result.records_imported == 0

    def test_upsert_replaces_existing(self, tmp_path: Path) -> None:
        """INSERT OR REPLACE updates an existing row with the same key."""
        jsonl = tmp_path / "items.jsonl"
        db = tmp_path / "items.sqlite3"
        _write_jsonl(jsonl, [{"item_id": "i1", "label": "old"}])
        migrator = _SimpleReadModifyWrite(workspace=tmp_path, db_path=db, jsonl_path=jsonl)
        migrator.migrate()

        # Write a second file with updated label and re-migrate
        _write_jsonl(jsonl, [{"item_id": "i1", "label": "new"}, {"item_id": "i2", "label": "extra"}])
        result = migrator.migrate()

        assert result.records_imported == 2
        conn = sqlite_connect(db)
        try:
            rows = conn.execute(
                "SELECT item_id, label FROM test_items ORDER BY item_id"
            ).fetchall()
            assert [tuple(r) for r in rows] == [("i1", "new"), ("i2", "extra")]
        finally:
            conn.close()

    def test_skips_invalid_lines(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "items.jsonl"
        db = tmp_path / "items.sqlite3"
        jsonl.parent.mkdir(parents=True, exist_ok=True)
        with open(jsonl, "w", encoding="utf-8") as f:
            f.write('{"item_id": "v1", "label": "valid"}\n')
            f.write("bad-json\n")
            f.write('{"label": "no-id"}\n')
        migrator = _SimpleReadModifyWrite(workspace=tmp_path, db_path=db, jsonl_path=jsonl)

        result = migrator.migrate()

        assert result.records_imported == 1
        assert result.records_skipped == 2


# ===========================================================================
# FactEventStoreSqlite tests
# ===========================================================================


class TestFactEventStoreSqlite:
    def test_events_for_fact_returns_chronological(self, tmp_path: Path) -> None:
        """Events are returned newest-first for a given fact_id."""
        from OriginAgent.agent.facts import FactEventStoreSqlite

        jsonl = tmp_path / "memory" / "audit" / "fact_events.jsonl"
        db = tmp_path / "memory" / "fact_events.sqlite3"
        store = FactEventStoreSqlite(workspace=tmp_path, db_path=db)

        # Write three events for the same fact_id
        _write_jsonl(jsonl, [
            {"event_id": "e1", "fact_id": "f1", "event_type": "create", "created_at": "2025-01-03T00:00:00"},
            {"event_id": "e2", "fact_id": "f1", "event_type": "update", "created_at": "2025-01-02T00:00:00"},
            {"event_id": "e3", "fact_id": "f1", "event_type": "reinforce", "created_at": "2025-01-01T00:00:00"},
        ])
        store.migrate()

        events = store.events_for_fact("f1")
        assert len(events) == 3
        # Newest first (ORDER BY created_at DESC)
        assert events[0]["event_type"] == "create"
        assert events[1]["event_type"] == "update"
        assert events[2]["event_type"] == "reinforce"

    def test_insert_ignore_prevents_duplicates(self, tmp_path: Path) -> None:
        """INSERT OR IGNORE skips duplicate event_id."""
        from OriginAgent.agent.facts import FactEventStoreSqlite

        jsonl = tmp_path / "memory" / "audit" / "fact_events.jsonl"
        db = tmp_path / "memory" / "fact_events.sqlite3"
        store = FactEventStoreSqlite(workspace=tmp_path, db_path=db)

        _write_jsonl(jsonl, [
            {"event_id": "e1", "fact_id": "f1", "event_type": "create"},
            {"event_id": "e1", "fact_id": "f1", "event_type": "duplicate"},
        ])
        store.migrate()

        count = store.count_events()
        assert count == 1  # only the first row survives

    def test_empty_store_returns_empty_list(self, tmp_path: Path) -> None:
        """events_for_fact on an empty store returns []."""
        from OriginAgent.agent.facts import FactEventStoreSqlite

        store = FactEventStoreSqlite(workspace=tmp_path)
        events = store.events_for_fact("nonexistent")
        assert events == []


# ===========================================================================
# DesireStoreSqlite tests
# ===========================================================================


class TestDesireStoreSqlite:
    def test_crud_operations(self, tmp_path: Path) -> None:
        """Add, get, update, and list_all work correctly."""
        from OriginAgent.bdi.desire_store import DesireStoreSqlite

        store = DesireStoreSqlite(workspace=tmp_path)

        # Add
        store.add({
            "desire_id": "d1",
            "owner_id": "user",
            "session_key": "s1",
            "content": "test desire",
            "status": "pending",
            "priority": 50,
        })
        store.add({
            "desire_id": "d2",
            "owner_id": "user",
            "session_key": "s1",
            "content": "high priority desire",
            "status": "active",
            "priority": 80,
        })

        # Get
        d1 = store.get("d1")
        assert d1 is not None
        assert d1["desire_id"] == "d1"
        assert d1["content"] == "test desire"

        # List all (ordered by priority DESC, created_at ASC)
        all_items = store.list_all()
        assert len(all_items) == 2
        assert all_items[0]["desire_id"] == "d2"  # higher priority first
        assert all_items[1]["desire_id"] == "d1"

        # Update
        updated = store.update("d1", status="active", last_reasoning="promoted")
        assert updated is not None
        assert updated["status"] == "active"

    def test_atomic_update_preserves_consistency(self, tmp_path: Path) -> None:
        """An update does not corrupt other rows."""
        from OriginAgent.bdi.desire_store import DesireStoreSqlite

        store = DesireStoreSqlite(workspace=tmp_path)
        store.add({"desire_id": "d1", "owner_id": "u1", "content": "first"})
        store.add({"desire_id": "d2", "owner_id": "u2", "content": "second"})

        updated = store.update("d1", status="active")
        assert updated is not None
        assert updated["desire_id"] == "d1"

        # d2 is untouched
        d2 = store.get("d2")
        assert d2 is not None
        assert d2["content"] == "second"
        assert d2["owner_id"] == "u2"

    def test_add_duplicate_raises(self, tmp_path: Path) -> None:
        """Adding a desire_id that already exists raises ValueError."""
        from OriginAgent.bdi.desire_store import DesireStoreSqlite

        store = DesireStoreSqlite(workspace=tmp_path)
        store.add({"desire_id": "d1", "owner_id": "u1", "content": "first"})
        with pytest.raises(ValueError, match="already exists"):
            store.add({"desire_id": "d1", "owner_id": "u1", "content": "duplicate"})

    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        from OriginAgent.bdi.desire_store import DesireStoreSqlite

        store = DesireStoreSqlite(workspace=tmp_path)
        assert store.get("nonexistent") is None

    def test_count_by_status(self, tmp_path: Path) -> None:
        from OriginAgent.bdi.desire_store import DesireStoreSqlite

        store = DesireStoreSqlite(workspace=tmp_path)
        store.add({"desire_id": "d1", "owner_id": "u1", "content": "a", "status": "active"})
        store.add({"desire_id": "d2", "owner_id": "u1", "content": "b", "status": "active"})
        store.add({"desire_id": "d3", "owner_id": "u1", "content": "c", "status": "pending"})

        counts = store.count_by_status()
        assert counts.get("active") == 2
        assert counts.get("pending") == 1

    def test_migration_from_jsonl(self, tmp_path: Path) -> None:
        """JSONL desire records are imported via migrate()."""
        from OriginAgent.bdi.desire_store import DesireStoreSqlite

        jsonl = tmp_path / "memory" / "bdi" / "desires.jsonl"
        _write_jsonl(jsonl, [
            {"desire_id": "m1", "owner_id": "u1", "content": "migrated-1", "status": "active", "priority": 80},
            {"desire_id": "m2", "owner_id": "u2", "content": "migrated-2", "status": "pending", "priority": 50},
        ])

        store = DesireStoreSqlite(workspace=tmp_path)
        result = store.migrate()

        assert result.ok
        assert result.records_imported == 2

        all_items = store.list_all()
        assert len(all_items) == 2
        ids = {item["desire_id"] for item in all_items}
        assert ids == {"m1", "m2"}
