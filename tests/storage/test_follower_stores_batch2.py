"""Tests for SQLite-backed follower stores — Batch 2 (ToolCall, ActiveIntent, Subagent)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from OriginAgent.agent.active_intents_sqlite import ActiveIntentLedgerSqlite
from OriginAgent.agent.subagent_records_sqlite import (
    SubagentLifecycleStoreSqlite,
    SubagentTaskStoreSqlite,
    SubagentToolStoreSqlite,
)
from OriginAgent.agent.tools.audit_sqlite import ToolCallAuditSqlite


# ===========================================================================
# Helpers
# ===========================================================================


def _write_jsonl(path: Path, lines: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for record in lines:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ===========================================================================
# ToolCallAuditSqlite
# ===========================================================================


class TestToolCallAuditSqlite:
    def test_migrate_preserves_events(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "audit" / "tool_calls.jsonl"
        db = tmp_path / "memory" / "audit" / "tool_calls.sqlite3"
        _write_jsonl(jsonl, [
            {"event_id": "e1", "tool_name": "read_file", "status": "success", "duration_ms": 50},
            {"event_id": "e2", "tool_name": "exec", "status": "policy_denied", "duration_ms": 5},
        ])
        store = ToolCallAuditSqlite(workspace=tmp_path, db_path=db)

        result = store.migrate()

        assert result.ok
        assert result.records_imported == 2

    def test_migrate_is_idempotent(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "audit" / "tool_calls.jsonl"
        db = tmp_path / "memory" / "audit" / "tool_calls.sqlite3"
        _write_jsonl(jsonl, [{"event_id": "e1", "tool_name": "exec"}])
        store = ToolCallAuditSqlite(workspace=tmp_path, db_path=db)

        store.migrate()
        result = store.migrate()

        assert result.ok
        assert result.records_imported == 1  # INSERT OR IGNORE

    def test_recent_returns_newest_first(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "audit" / "tool_calls.jsonl"
        db = tmp_path / "memory" / "audit" / "tool_calls.sqlite3"
        _write_jsonl(jsonl, [
            {"event_id": "e1", "tool_name": "glob", "created_at": "2026-01-01T00:00:00Z"},
            {"event_id": "e2", "tool_name": "grep", "created_at": "2026-02-01T00:00:00Z"},
        ])
        store = ToolCallAuditSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        recent = store.recent(limit=1)

        assert len(recent) == 1
        assert recent[0]["tool_name"] == "grep"

    def test_by_tool_filters_correctly(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "audit" / "tool_calls.jsonl"
        db = tmp_path / "memory" / "audit" / "tool_calls.sqlite3"
        _write_jsonl(jsonl, [
            {"event_id": "e1", "tool_name": "read_file"},
            {"event_id": "e2", "tool_name": "exec"},
            {"event_id": "e3", "tool_name": "read_file"},
        ])
        store = ToolCallAuditSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        results = store.by_tool("read_file")

        assert len(results) == 2

    def test_by_status_filters_correctly(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "audit" / "tool_calls.jsonl"
        db = tmp_path / "memory" / "audit" / "tool_calls.sqlite3"
        _write_jsonl(jsonl, [
            {"event_id": "e1", "tool_name": "glob", "status": "success"},
            {"event_id": "e2", "tool_name": "exec", "status": "policy_denied"},
        ])
        store = ToolCallAuditSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        denied = store.by_status("policy_denied")

        assert len(denied) == 1
        assert denied[0]["tool_name"] == "exec"


# ===========================================================================
# ActiveIntentLedgerSqlite
# ===========================================================================


class TestActiveIntentLedgerSqlite:
    def test_migrate_preserves_records(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "records.jsonl"
        db = tmp_path / "memory" / "active_intents" / "active_intent_records.sqlite3"
        _write_jsonl(jsonl, [
            {"session_key": "s1", "intent_type": "goal_nudge", "intent_id": "i1",
             "timestamp": "2026-01-01T00:00:00Z", "outcome": "emitted"},
            {"session_key": "s2", "intent_type": "foresight_nudge", "intent_id": "i2",
             "timestamp": "2026-02-01T00:00:00Z", "outcome": "suppressed"},
        ])
        store = ActiveIntentLedgerSqlite(workspace=tmp_path, db_path=db)

        result = store.migrate()

        assert result.ok
        assert result.records_imported == 2

    def test_migrate_is_idempotent(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "records.jsonl"
        db = tmp_path / "memory" / "active_intents" / "active_intent_records.sqlite3"
        _write_jsonl(jsonl, [{"session_key": "s1", "intent_type": "goal_nudge", "intent_id": "i1"}])
        store = ActiveIntentLedgerSqlite(workspace=tmp_path, db_path=db)

        store.migrate()
        result = store.migrate()

        assert result.records_imported == 1

    def test_recent_returns_newest_first(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "records.jsonl"
        db = tmp_path / "memory" / "active_intents" / "active_intent_records.sqlite3"
        _write_jsonl(jsonl, [
            {"session_key": "s1", "intent_type": "goal_nudge", "intent_id": "i1",
             "timestamp": "2026-01-01T00:00:00Z"},
            {"session_key": "s2", "intent_type": "goal_nudge", "intent_id": "i2",
             "timestamp": "2026-02-01T00:00:00Z"},
        ])
        store = ActiveIntentLedgerSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        recent = store.recent(limit=1)

        assert len(recent) == 1
        assert recent[0]["intent_id"] == "i2"

    def test_by_session_filters_correctly(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "records.jsonl"
        db = tmp_path / "memory" / "active_intents" / "active_intent_records.sqlite3"
        _write_jsonl(jsonl, [
            {"session_key": "sess-a", "intent_type": "goal_nudge", "intent_id": "i1"},
            {"session_key": "sess-b", "intent_type": "goal_nudge", "intent_id": "i2"},
        ])
        store = ActiveIntentLedgerSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        results = store.by_session("sess-a")

        assert len(results) == 1
        assert results[0]["session_key"] == "sess-a"

    def test_by_intent_type_filters_correctly(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "records.jsonl"
        db = tmp_path / "memory" / "active_intents" / "active_intent_records.sqlite3"
        _write_jsonl(jsonl, [
            {"session_key": "s1", "intent_type": "goal_nudge", "intent_id": "i1"},
            {"session_key": "s2", "intent_type": "foresight_nudge", "intent_id": "i2"},
        ])
        store = ActiveIntentLedgerSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        results = store.by_intent_type("goal_nudge")

        assert len(results) == 1
        assert results[0]["intent_type"] == "goal_nudge"

    def test_empty_store_returns_empty_lists(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "active_intents" / "records.jsonl"
        db = tmp_path / "memory" / "active_intents" / "active_intent_records.sqlite3"
        _write_jsonl(jsonl, [])
        store = ActiveIntentLedgerSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        assert store.recent() == []
        assert store.by_session("nonexistent") == []
        assert store.by_intent_type("nonexistent") == []


# ===========================================================================
# SubagentTaskStoreSqlite, SubagentLifecycleStoreSqlite, SubagentToolStoreSqlite
# ===========================================================================


class TestSubagentTaskStoreSqlite:
    def test_migrate_preserves_tasks(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "subagents" / "tasks.jsonl"
        db = tmp_path / "memory" / "subagents" / "subagent_tasks.sqlite3"
        _write_jsonl(jsonl, [
            {"subagent_id": "sa1", "task_label": "Task 1", "terminal_status": "completed",
             "created_at": "2026-01-01T00:00:00Z"},
            {"subagent_id": "sa2", "task_label": "Task 2", "terminal_status": "failed",
             "created_at": "2026-01-02T00:00:00Z"},
        ])
        store = SubagentTaskStoreSqlite(workspace=tmp_path, db_path=db)

        result = store.migrate()

        assert result.ok
        assert result.records_imported == 2

    def test_migrate_is_idempotent(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "subagents" / "tasks.jsonl"
        db = tmp_path / "memory" / "subagents" / "subagent_tasks.sqlite3"
        _write_jsonl(jsonl, [{"subagent_id": "sa1", "created_at": "2026-01-01T00:00:00Z"}])
        store = SubagentTaskStoreSqlite(workspace=tmp_path, db_path=db)

        store.migrate()
        result = store.migrate()

        assert result.records_imported == 1

    def test_recent_latest_tasks(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "subagents" / "tasks.jsonl"
        db = tmp_path / "memory" / "subagents" / "subagent_tasks.sqlite3"
        _write_jsonl(jsonl, [
            {"subagent_id": "sa1", "created_at": "2026-01-01T00:00:00Z"},
            {"subagent_id": "sa2", "created_at": "2026-02-01T00:00:00Z"},
        ])
        store = SubagentTaskStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        recent = store.recent(limit=1)

        assert len(recent) == 1
        assert recent[0]["subagent_id"] == "sa2"


class TestSubagentLifecycleStoreSqlite:
    def test_migrate_and_query_by_subagent(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "subagents" / "lifecycle.jsonl"
        db = tmp_path / "memory" / "subagents" / "subagent_lifecycle.sqlite3"
        _write_jsonl(jsonl, [
            {"subagent_id": "sa1", "state": "spawned", "created_at": "2026-01-01T00:00:00Z"},
            {"subagent_id": "sa1", "state": "running", "created_at": "2026-01-01T00:00:01Z"},
            {"subagent_id": "sa2", "state": "spawned", "created_at": "2026-01-02T00:00:00Z"},
        ])
        store = SubagentLifecycleStoreSqlite(workspace=tmp_path, db_path=db)

        result = store.migrate()
        assert result.records_imported == 3

        sa1_events = store.for_subagent("sa1")
        assert len(sa1_events) == 2
        assert sa1_events[0]["state"] == "spawned"

    def test_migrate_is_idempotent(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "subagents" / "lifecycle.jsonl"
        db = tmp_path / "memory" / "subagents" / "subagent_lifecycle.sqlite3"
        _write_jsonl(jsonl, [{"subagent_id": "sa1", "state": "spawned",
                               "created_at": "2026-01-01T00:00:00Z"}])
        store = SubagentLifecycleStoreSqlite(workspace=tmp_path, db_path=db)

        store.migrate()
        result = store.migrate()

        assert result.records_imported == 1


class TestSubagentToolStoreSqlite:
    def test_migrate_and_query_by_subagent(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "subagents" / "tools.jsonl"
        db = tmp_path / "memory" / "subagents" / "subagent_tools.sqlite3"
        _write_jsonl(jsonl, [
            {"subagent_id": "sa1", "tool_name": "read_file", "started_at": "2026-01-01T00:00:00Z",
             "status": "success", "duration_ms": 100},
            {"subagent_id": "sa1", "tool_name": "grep", "started_at": "2026-01-01T00:00:01Z",
             "status": "success", "duration_ms": 50},
        ])
        store = SubagentToolStoreSqlite(workspace=tmp_path, db_path=db)

        result = store.migrate()
        assert result.records_imported == 2

        tools = store.for_subagent("sa1")
        assert len(tools) == 2

    def test_by_tool_filters_correctly(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "subagents" / "tools.jsonl"
        db = tmp_path / "memory" / "subagents" / "subagent_tools.sqlite3"
        _write_jsonl(jsonl, [
            {"subagent_id": "sa1", "tool_name": "read_file", "started_at": "t1"},
            {"subagent_id": "sa2", "tool_name": "exec", "started_at": "t2"},
            {"subagent_id": "sa3", "tool_name": "read_file", "started_at": "t3"},
        ])
        store = SubagentToolStoreSqlite(workspace=tmp_path, db_path=db)
        store.migrate()

        read_file_tools = store.by_tool("read_file")

        assert len(read_file_tools) == 2

    def test_migrate_is_idempotent(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "memory" / "subagents" / "tools.jsonl"
        db = tmp_path / "memory" / "subagents" / "subagent_tools.sqlite3"
        _write_jsonl(jsonl, [{"subagent_id": "sa1", "tool_name": "read_file",
                               "started_at": "2026-01-01T00:00:00Z"}])
        store = SubagentToolStoreSqlite(workspace=tmp_path, db_path=db)

        store.migrate()
        result = store.migrate()

        assert result.records_imported == 1
