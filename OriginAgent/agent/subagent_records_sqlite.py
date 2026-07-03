"""SQLite-backed subagent execution records.

Migrates from ``tasks.jsonl``, ``lifecycle.jsonl``, and ``tools.jsonl``
into a single local SQLite database with three tables.
Append-only pattern with INSERT OR IGNORE deduplication.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OriginAgent.storage.jsonl_migration import AppendOnlyMigrator
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect, ensure_schema


class SubagentTaskStoreSqlite(AppendOnlyMigrator):
    """SQLite-backed subagent task records."""

    DDL = """
        CREATE TABLE IF NOT EXISTS subagent_tasks (
            subagent_id              TEXT NOT NULL,
            root_subagent_id         TEXT,
            parent_subagent_id       TEXT,
            subagent_depth           INTEGER NOT NULL DEFAULT 0,
            parent_session_key       TEXT,
            origin_channel           TEXT,
            origin_chat_id           TEXT,
            origin_message_id        TEXT,
            task_label               TEXT NOT NULL DEFAULT '',
            task_summary             TEXT NOT NULL DEFAULT '',
            delegated_profile_summary TEXT NOT NULL DEFAULT '',
            allowed_tools_summary_json TEXT NOT NULL DEFAULT '[]',
            provider_summary         TEXT NOT NULL DEFAULT '',
            isolation_mode           TEXT NOT NULL DEFAULT '',
            terminal_status          TEXT NOT NULL DEFAULT 'spawned',
            stop_reason              TEXT,
            failure_summary          TEXT,
            started_at               TEXT,
            ended_at                 TEXT,
            created_at               TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json             TEXT NOT NULL DEFAULT '{}',
            UNIQUE(subagent_id, created_at)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_subagent_tasks_status
            ON subagent_tasks(terminal_status, created_at);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        memory_dir = workspace / "memory" / "subagents"
        super().__init__(
            workspace=workspace,
            db_path=db_path or memory_dir / "subagent_tasks.sqlite3",
            jsonl_path=memory_dir / "tasks.jsonl",
        )

    def table_ddl(self) -> str:
        return self.DDL

    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("subagent_id"))

    def insert_row(self, conn: Any, line: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO subagent_tasks
               (subagent_id, root_subagent_id, parent_subagent_id,
                subagent_depth, parent_session_key, origin_channel,
                origin_chat_id, origin_message_id, task_label,
                task_summary, delegated_profile_summary,
                allowed_tools_summary_json, provider_summary,
                isolation_mode, terminal_status, stop_reason,
                failure_summary, started_at, ended_at, created_at,
                payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                line.get("subagent_id", ""),
                line.get("root_subagent_id"),
                line.get("parent_subagent_id"),
                line.get("subagent_depth", 0),
                line.get("parent_session_key"),
                line.get("origin_channel"),
                line.get("origin_chat_id"),
                line.get("origin_message_id"),
                line.get("task_label", ""),
                line.get("task_summary", ""),
                line.get("delegated_profile_summary", ""),
                json.dumps(line.get("allowed_tools_summary", []), ensure_ascii=False),
                line.get("provider_summary", ""),
                line.get("isolation_mode", ""),
                line.get("terminal_status", "spawned"),
                line.get("stop_reason"),
                line.get("failure_summary"),
                line.get("started_at"),
                line.get("ended_at"),
                line.get("created_at", ""),
                json.dumps(line, ensure_ascii=False),
            ),
        )

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try:
            ensure_schema(conn, self.DDL)
        finally:
            conn.close()

    def recent(self, *, limit: int = 10) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM subagent_tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()


class SubagentLifecycleStoreSqlite(AppendOnlyMigrator):
    """SQLite-backed subagent lifecycle records."""

    DDL = """
        CREATE TABLE IF NOT EXISTS subagent_lifecycle (
            subagent_id        TEXT NOT NULL,
            root_subagent_id   TEXT,
            parent_subagent_id TEXT,
            subagent_depth     INTEGER NOT NULL DEFAULT 0,
            parent_session_key TEXT,
            state              TEXT NOT NULL DEFAULT 'spawned',
            detail             TEXT,
            created_at         TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json       TEXT NOT NULL DEFAULT '{}',
            UNIQUE(subagent_id, state, created_at)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_subagent_lifecycle_id
            ON subagent_lifecycle(subagent_id, created_at);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        memory_dir = workspace / "memory" / "subagents"
        super().__init__(
            workspace=workspace,
            db_path=db_path or memory_dir / "subagent_lifecycle.sqlite3",
            jsonl_path=memory_dir / "lifecycle.jsonl",
        )

    def table_ddl(self) -> str:
        return self.DDL

    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("subagent_id"))

    def insert_row(self, conn: Any, line: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO subagent_lifecycle
               (subagent_id, root_subagent_id, parent_subagent_id,
                subagent_depth, parent_session_key, state, detail,
                created_at, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                line.get("subagent_id", ""),
                line.get("root_subagent_id"),
                line.get("parent_subagent_id"),
                line.get("subagent_depth", 0),
                line.get("parent_session_key"),
                line.get("state", "spawned"),
                line.get("detail"),
                line.get("created_at", ""),
                json.dumps(line, ensure_ascii=False),
            ),
        )

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try:
            ensure_schema(conn, self.DDL)
        finally:
            conn.close()

    def for_subagent(self, subagent_id: str) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT payload_json FROM subagent_lifecycle
                   WHERE subagent_id = ?
                   ORDER BY created_at""",
                (subagent_id,),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()


class SubagentToolStoreSqlite(AppendOnlyMigrator):
    """SQLite-backed subagent tool usage records."""

    DDL = """
        CREATE TABLE IF NOT EXISTS subagent_tools (
            subagent_id        TEXT NOT NULL,
            root_subagent_id   TEXT,
            parent_subagent_id TEXT,
            subagent_depth     INTEGER NOT NULL DEFAULT 0,
            parent_session_key TEXT,
            tool_name          TEXT NOT NULL DEFAULT '',
            status             TEXT NOT NULL DEFAULT 'success',
            started_at         TEXT NOT NULL DEFAULT '',
            ended_at           TEXT NOT NULL DEFAULT '',
            duration_ms        INTEGER NOT NULL DEFAULT 0,
            argument_summary   TEXT NOT NULL DEFAULT '',
            result_summary     TEXT,
            policy_rule        TEXT,
            error_kind         TEXT,
            created_at         TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json       TEXT NOT NULL DEFAULT '{}',
            UNIQUE(subagent_id, tool_name, started_at)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_subagent_tools_id
            ON subagent_tools(subagent_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_subagent_tools_name
            ON subagent_tools(tool_name, created_at);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        memory_dir = workspace / "memory" / "subagents"
        super().__init__(
            workspace=workspace,
            db_path=db_path or memory_dir / "subagent_tools.sqlite3",
            jsonl_path=memory_dir / "tools.jsonl",
        )

    def table_ddl(self) -> str:
        return self.DDL

    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("subagent_id") and line.get("tool_name"))

    def insert_row(self, conn: Any, line: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO subagent_tools
               (subagent_id, root_subagent_id, parent_subagent_id,
                subagent_depth, parent_session_key, tool_name, status,
                started_at, ended_at, duration_ms, argument_summary,
                result_summary, policy_rule, error_kind, created_at,
                payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                line.get("subagent_id", ""),
                line.get("root_subagent_id"),
                line.get("parent_subagent_id"),
                line.get("subagent_depth", 0),
                line.get("parent_session_key"),
                line.get("tool_name", ""),
                line.get("status", "success"),
                line.get("started_at", ""),
                line.get("ended_at", ""),
                line.get("duration_ms", 0),
                line.get("argument_summary", ""),
                line.get("result_summary"),
                line.get("policy_rule"),
                line.get("error_kind"),
                line.get("created_at", ""),
                json.dumps(line, ensure_ascii=False),
            ),
        )

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try:
            ensure_schema(conn, self.DDL)
        finally:
            conn.close()

    def for_subagent(self, subagent_id: str) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT payload_json FROM subagent_tools
                   WHERE subagent_id = ?
                   ORDER BY created_at""",
                (subagent_id,),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()

    def by_tool(self, tool_name: str, *, limit: int = 100) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT payload_json FROM subagent_tools
                   WHERE tool_name = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (tool_name, limit),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()
