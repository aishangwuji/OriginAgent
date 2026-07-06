"""SQLite-backed tool call audit store.

Migrates from ``tool_calls.jsonl`` into a local SQLite database.
Append-only pattern with INSERT OR IGNORE deduplication.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OriginAgent.storage.jsonl_migration import AppendOnlyMigrator
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect, ensure_schema


class ToolCallAuditSqlite(AppendOnlyMigrator):
    """SQLite-backed tool call audit event store.

    Drop-in replacement for the append-only portion of ``JsonlToolAuditSink``
    backed by a local SQLite database.
    """

    DDL = """
        CREATE TABLE IF NOT EXISTS tool_call_audit (
            event_id         TEXT PRIMARY KEY,
            tool_name        TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'success',
            duration_ms      INTEGER NOT NULL DEFAULT 0,
            read_only        INTEGER NOT NULL DEFAULT 1,
            exclusive        INTEGER NOT NULL DEFAULT 0,
            error_kind       TEXT,
            actor_id_hash    TEXT,
            session_key_hash TEXT,
            subagent_task_id TEXT,
            parent_session_key_hash TEXT,
            origin_channel   TEXT,
            origin_chat_id_hash TEXT,
            target_kind      TEXT,
            target_hash      TEXT,
            policy_rule      TEXT,
            result_size      INTEGER,
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            prev_hash        TEXT,
            event_hash       TEXT,
            payload_json     TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_tool_audit_name
            ON tool_call_audit(tool_name, created_at);
        CREATE INDEX IF NOT EXISTS idx_tool_audit_status
            ON tool_call_audit(status, created_at);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        memory_dir = workspace / "memory" / "audit"
        super().__init__(
            workspace=workspace,
            db_path=db_path or memory_dir / "tool_calls.sqlite3",
            jsonl_path=memory_dir / "tool_calls.jsonl",
        )

    # ------------------------------------------------------------------
    # Migrator contract
    # ------------------------------------------------------------------

    def table_ddl(self) -> str:
        return self.DDL

    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("event_id") and line.get("tool_name"))

    def insert_row(self, conn: Any, line: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO tool_call_audit
               (event_id, tool_name, status, duration_ms, read_only, exclusive,
                error_kind, actor_id_hash, session_key_hash, subagent_task_id,
                parent_session_key_hash, origin_channel, origin_chat_id_hash,
                target_kind, target_hash, policy_rule, result_size,
                created_at, prev_hash, event_hash, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                line.get("event_id", ""),
                line.get("tool_name", ""),
                line.get("status", "success"),
                line.get("duration_ms", 0),
                1 if line.get("read_only") else 0,
                1 if line.get("exclusive") else 0,
                line.get("error_kind"),
                line.get("actor_id_hash"),
                line.get("session_key_hash"),
                line.get("subagent_task_id"),
                line.get("parent_session_key_hash"),
                line.get("origin_channel"),
                line.get("origin_chat_id_hash"),
                line.get("target_kind"),
                line.get("target_hash"),
                line.get("policy_rule"),
                line.get("result_size"),
                line.get("created_at", ""),
                line.get("prev_hash"),
                line.get("event_hash"),
                json.dumps(line, ensure_ascii=False),
            ),
        )

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try:
            ensure_schema(conn, self.DDL)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def append(self, data: dict[str, Any]) -> None:
        """Append one audit event record."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            self.insert_row(conn, data)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def recent(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Return most recent audit events."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM tool_call_audit ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()

    def by_tool(self, tool_name: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return audit events for a specific tool."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT payload_json FROM tool_call_audit
                   WHERE tool_name = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (tool_name, limit),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()

    def by_status(self, status: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return audit events with a specific status."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT payload_json FROM tool_call_audit
                   WHERE status = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (status, limit),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()
