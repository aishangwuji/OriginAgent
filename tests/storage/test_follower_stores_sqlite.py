"""Tests for SQLite-backed follower stores (Reminder, SkillLifecycle, DomainPack)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from OriginAgent.agent.domain_pack_events_sqlite import DomainPackEventsSqlite
from OriginAgent.agent.reminders_sqlite import ReminderStoreSqlite
from OriginAgent.agent.skill_lifecycle_sqlite import SkillLifecycleStoreSqlite
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect


# ===========================================================================
# Test helpers
# ===========================================================================


def _write_jsonl(path: Path, lines: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for record in lines:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _row_count(conn: Any, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ===========================================================================
# ReminderStoreSqlite tests  (read-modify-write)
# ===========================================================================


class TestReminderStoreSqlite:
    def test_migrate_empty_jsonl(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "reminders.jsonl"
        db = tmp_path / "memory" / "active_intents" / "reminders.sqlite3"
        store = ReminderStoreSqlite(workspace=tmp_path, db_path=db)

        result = store.migrate()

        assert result.ok
        assert result.records_imported == 0

    def test_migrate_preserves_all_records(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "reminders.jsonl"
        db = tmp_path / "memory" / "active_intents" / "reminders.sqlite3"
        records = [
            {"reminder_id": "r1", "session_key": "s1", "channel": "cli",
             "chat_id": "c1", "content": "test", "due_at": "2026-01-01T00:00:00+00:00",
             "status": "pending"},
            {"reminder_id": "r2", "session_key": "s2", "channel": "discord",
             "chat_id": "c2", "content": "test2", "due_at": "2026-02-01T00:00:00+00:00",
             "status": "fired"},
        ]
        _write_jsonl(jsonl, records)
        store = ReminderStoreSqlite(workspace=tmp_path, db_path=db)

        result = store.migrate()

        assert result.ok
        assert result.records_imported == 2
        assert result.records_skipped == 0

    def test_migrate_is_idempotent(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "reminders.jsonl"
        db = tmp_path / "memory" / "active_intents" / "reminders.sqlite3"
        _write_jsonl(jsonl, [{"reminder_id": "r1", "content": "test", "status": "pending"}])
        store = ReminderStoreSqlite(workspace=tmp_path, db_path=db)

        store.migrate()
        result = store.migrate()  # second run

        assert result.ok
        assert result.records_imported == 1  # same record INSERT OR REPLACE'd

    def test_skips_invalid_lines(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "reminders.jsonl"
        db = tmp_path / "memory" / "active_intents" / "reminders.sqlite3"
        _write_jsonl(jsonl, [
            {"reminder_id": "r1", "content": "ok"},
            {"no_reminder_id": "bad"},  # missing reminder_id → skipped
        ])
        store = ReminderStoreSqlite(workspace=tmp_path, db_path=db)

        result = store.migrate()

        assert result.ok
        assert result.records_imported == 1
        assert result.records_skipped == 1

    def test_read_all_returns_all(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "reminders.jsonl"
        db = tmp_path / "memory" / "active_intents" / "reminders.sqlite3"
        records = [
            {"reminder_id": "r1", "content": "a"},
            {"reminder_id": "r2", "content": "b"},
        ]
        _write_jsonl(jsonl, records)
        store = ReminderStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        all_records = store.read_all()

        assert len(all_records) == 2
        contents = sorted(r["content"] for r in all_records)
        assert contents == ["a", "b"]

    def test_get_returns_correct_record(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "reminders.jsonl"
        db = tmp_path / "memory" / "active_intents" / "reminders.sqlite3"
        _write_jsonl(jsonl, [{"reminder_id": "target", "content": "found"}])
        store = ReminderStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        record = store.get("target")

        assert record is not None
        assert record["content"] == "found"

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "reminders.jsonl"
        db = tmp_path / "memory" / "active_intents" / "reminders.sqlite3"
        _write_jsonl(jsonl, [])
        store = ReminderStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        assert store.get("nonexistent") is None

    def test_list_due_finds_overdue(self, tmp_path: Path) -> None:
        from datetime import datetime, timezone, timedelta

        jsonl = tmp_path / "memory" / "active_intents" / "reminders.jsonl"
        db = tmp_path / "memory" / "active_intents" / "reminders.sqlite3"
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _write_jsonl(jsonl, [
            {"reminder_id": "due1", "content": "overdue", "due_at": past, "status": "pending"},
            {"reminder_id": "future1", "content": "later", "due_at": future, "status": "pending"},
        ])
        store = ReminderStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        due = store.list_due()

        assert len(due) == 1
        assert due[0]["reminder_id"] == "due1"

    def test_list_by_session_filters_correctly(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "reminders.jsonl"
        db = tmp_path / "memory" / "active_intents" / "reminders.sqlite3"
        _write_jsonl(jsonl, [
            {"reminder_id": "r1", "session_key": "sess-a", "content": "a"},
            {"reminder_id": "r2", "session_key": "sess-b", "content": "b"},
        ])
        store = ReminderStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        results = store.list_by_session("sess-a")

        assert len(results) == 1
        assert results[0]["reminder_id"] == "r1"

    def test_delete_removes_record(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "reminders.jsonl"
        db = tmp_path / "memory" / "active_intents" / "reminders.sqlite3"
        _write_jsonl(jsonl, [{"reminder_id": "r1", "content": "x"}])
        store = ReminderStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        assert store.delete("r1") is True
        assert store.get("r1") is None
        assert store.delete("r1") is False  # already gone

    def test_upsert_creates_and_updates(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "reminders.jsonl"
        db = tmp_path / "memory" / "active_intents" / "reminders.sqlite3"
        _write_jsonl(jsonl, [])
        store = ReminderStoreSqlite(workspace=tmp_path, db_path=db)

        # Create
        store.upsert({"reminder_id": "r1", "content": "first", "status": "pending"})
        assert store.get("r1")["content"] == "first"

        # Update
        store.upsert({"reminder_id": "r1", "content": "updated", "status": "completed"})
        assert store.get("r1")["content"] == "updated"


# ===========================================================================
# SkillLifecycleStoreSqlite tests  (append-only)
# ===========================================================================


class TestSkillLifecycleStoreSqlite:
    def test_migrate_empty(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "skill_lifecycle_events.jsonl"
        db = tmp_path / "memory" / "skill_lifecycle_events.sqlite3"
        store = SkillLifecycleStoreSqlite(workspace=tmp_path, db_path=db)

        result = store.migrate()

        assert result.ok
        assert result.records_imported == 0

    def test_migrate_preserves_events(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "skill_lifecycle_events.jsonl"
        db = tmp_path / "memory" / "skill_lifecycle_events.sqlite3"
        events = [
            {"event_id": "e1", "skill_name": "my-skill", "action": "verify",
             "created_at": "2026-01-01T00:00:00+00:00"},
            {"event_id": "e2", "skill_name": "my-skill", "action": "activate",
             "created_at": "2026-01-02T00:00:00+00:00"},
            {"event_id": "e3", "skill_name": "other-skill", "action": "verify",
             "created_at": "2026-01-01T00:00:00+00:00"},
        ]
        _write_jsonl(jsonl, events)
        store = SkillLifecycleStoreSqlite(workspace=tmp_path, db_path=db)

        result = store.migrate()

        assert result.ok
        assert result.records_imported == 3

    def test_migrate_is_idempotent(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "skill_lifecycle_events.jsonl"
        db = tmp_path / "memory" / "skill_lifecycle_events.sqlite3"
        _write_jsonl(jsonl, [{"event_id": "e1", "skill_name": "s", "action": "verify"}])
        store = SkillLifecycleStoreSqlite(workspace=tmp_path, db_path=db)

        store.migrate()
        result = store.migrate()

        assert result.ok
        assert result.records_imported == 1  # INSERT OR IGNORE

    def test_latest_event_returns_most_recent(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "skill_lifecycle_events.jsonl"
        db = tmp_path / "memory" / "skill_lifecycle_events.sqlite3"
        _write_jsonl(jsonl, [
            {"event_id": "e1", "skill_name": "s", "action": "verify",
             "created_at": "2026-01-01T00:00:00+00:00"},
            {"event_id": "e2", "skill_name": "s", "action": "deprecate",
             "created_at": "2026-02-01T00:00:00+00:00"},
        ])
        store = SkillLifecycleStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        latest = store.latest_event("s")

        assert latest is not None
        assert latest["action"] == "deprecate"

    def test_latest_event_missing_returns_none(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "skill_lifecycle_events.jsonl"
        db = tmp_path / "memory" / "skill_lifecycle_events.sqlite3"
        _write_jsonl(jsonl, [])
        store = SkillLifecycleStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        assert store.latest_event("nonexistent") is None

    def test_all_latest_events_groups_by_skill(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "skill_lifecycle_events.jsonl"
        db = tmp_path / "memory" / "skill_lifecycle_events.sqlite3"
        _write_jsonl(jsonl, [
            {"event_id": "e1", "skill_name": "a", "action": "verify",
             "created_at": "2026-01-01T00:00:00+00:00"},
            {"event_id": "e2", "skill_name": "a", "action": "activate",
             "created_at": "2026-01-02T00:00:00+00:00"},
            {"event_id": "e3", "skill_name": "b", "action": "propose",
             "created_at": "2026-01-01T00:00:00+00:00"},
        ])
        store = SkillLifecycleStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        latest = store.all_latest_events()

        assert len(latest) == 2
        assert latest["a"]["action"] == "activate"
        assert latest["b"]["action"] == "propose"

    def test_append_event_adds_at_runtime(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "skill_lifecycle_events.jsonl"
        db = tmp_path / "memory" / "skill_lifecycle_events.sqlite3"
        _write_jsonl(jsonl, [])
        store = SkillLifecycleStoreSqlite(workspace=tmp_path, db_path=db)

        store.append_event({"event_id": "new1", "skill_name": "s", "action": "verify"})

        assert store.latest_event("s") is not None
        assert store.latest_event("s")["event_id"] == "new1"


# ===========================================================================
# DomainPackEventsSqlite tests  (append-only)
# ===========================================================================


class TestDomainPackEventsSqlite:
    def test_migrate_empty(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "domain_pack_events.jsonl"
        db = tmp_path / "memory" / "domain_pack_events.sqlite3"
        store = DomainPackEventsSqlite(workspace=tmp_path, db_path=db)

        result = store.migrate()

        assert result.ok
        assert result.records_imported == 0

    def test_migrate_preserves_events(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "domain_pack_events.jsonl"
        db = tmp_path / "memory" / "domain_pack_events.sqlite3"
        events = [
            {"event_id": "ev1", "pack_id": "research", "action": "install",
             "created_at": "2026-01-01T00:00:00+00:00"},
            {"event_id": "ev2", "pack_id": "research", "action": "activate",
             "created_at": "2026-01-02T00:00:00+00:00"},
        ]
        _write_jsonl(jsonl, events)
        store = DomainPackEventsSqlite(workspace=tmp_path, db_path=db)

        result = store.migrate()

        assert result.ok
        assert result.records_imported == 2

    def test_migrate_is_idempotent(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "domain_pack_events.jsonl"
        db = tmp_path / "memory" / "domain_pack_events.sqlite3"
        _write_jsonl(jsonl, [{"event_id": "ev1", "pack_id": "p", "action": "install"}])
        store = DomainPackEventsSqlite(workspace=tmp_path, db_path=db)

        store.migrate()
        result = store.migrate()

        assert result.ok
        assert result.records_imported == 1

    def test_events_for_pack_filters_by_pack_id(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "domain_pack_events.jsonl"
        db = tmp_path / "memory" / "domain_pack_events.sqlite3"
        _write_jsonl(jsonl, [
            {"event_id": "e1", "pack_id": "pack-a", "action": "install"},
            {"event_id": "e2", "pack_id": "pack-b", "action": "install"},
        ])
        store = DomainPackEventsSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        results = store.events_for_pack("pack-a")

        assert len(results) == 1
        assert results[0]["pack_id"] == "pack-a"

    def test_latest_event_returns_most_recent(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "domain_pack_events.jsonl"
        db = tmp_path / "memory" / "domain_pack_events.sqlite3"
        _write_jsonl(jsonl, [
            {"event_id": "e1", "pack_id": "p", "action": "install",
             "created_at": "2026-01-01T00:00:00+00:00"},
            {"event_id": "e2", "pack_id": "p", "action": "uninstall",
             "created_at": "2026-02-01T00:00:00+00:00"},
        ])
        store = DomainPackEventsSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        latest = store.latest_event("p")

        assert latest is not None
        assert latest["action"] == "uninstall"

    def test_append_event_adds_at_runtime(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "domain_pack_events.jsonl"
        db = tmp_path / "memory" / "domain_pack_events.sqlite3"
        _write_jsonl(jsonl, [])
        store = DomainPackEventsSqlite(workspace=tmp_path, db_path=db)

        store.append_event({"event_id": "new1", "pack_id": "p", "action": "install"})

        assert store.latest_event("p") is not None
        assert store.latest_event("p")["event_id"] == "new1"
