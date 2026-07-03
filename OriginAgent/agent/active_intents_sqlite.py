"""SQLite-backed active intent ledger.

Migrates from ``records.jsonl`` into a local SQLite database.
Append-only pattern with INSERT OR IGNORE deduplication.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OriginAgent.storage.jsonl_migration import AppendOnlyMigrator
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect, ensure_schema


class ActiveIntentLedgerSqlite(AppendOnlyMigrator):
    """SQLite-backed active intent ledger.

    Drop-in replacement for ``JsonlActiveIntentLedger`` backed by SQLite.
    """

    DDL = """
        CREATE TABLE IF NOT EXISTS active_intent_records (
            event_id          TEXT PRIMARY KEY,
            session_key       TEXT NOT NULL,
            intent_type       TEXT NOT NULL DEFAULT '',
            intent_id         TEXT NOT NULL DEFAULT '',
            source_type       TEXT NOT NULL DEFAULT '',
            source_reference  TEXT NOT NULL DEFAULT '',
            outcome           TEXT NOT NULL DEFAULT 'emitted',
            summary           TEXT NOT NULL DEFAULT '',
            suppression_reason TEXT,
            timestamp         TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json      TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_ai_session
            ON active_intent_records(session_key, timestamp);
        CREATE INDEX IF NOT EXISTS idx_ai_intent_type
            ON active_intent_records(intent_type, timestamp);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        memory_dir = workspace / "memory" / "active_intents"
        super().__init__(
            workspace=workspace,
            db_path=db_path or memory_dir / "active_intent_records.sqlite3",
            jsonl_path=memory_dir / "records.jsonl",
        )

    # ------------------------------------------------------------------
    # Migrator contract
    # ------------------------------------------------------------------

    def table_ddl(self) -> str:
        return self.DDL

    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("session_key") and line.get("intent_type"))

    def insert_row(self, conn: Any, line: dict[str, Any]) -> None:
        event_id = line.get("timestamp", "") + ":" + line.get("intent_id", "")
        conn.execute(
            """INSERT OR IGNORE INTO active_intent_records
               (event_id, session_key, intent_type, intent_id, source_type,
                source_reference, outcome, summary, suppression_reason,
                timestamp, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                line.get("session_key", ""),
                line.get("intent_type", ""),
                line.get("intent_id", ""),
                line.get("source_type", ""),
                line.get("source_reference", ""),
                line.get("outcome", "emitted"),
                line.get("summary", ""),
                line.get("suppression_reason"),
                line.get("timestamp", ""),
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
    # Query helpers (mirrors JsonlActiveIntentLedger API)
    # ------------------------------------------------------------------

    def recent(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Return most recent records, newest first."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM active_intent_records ORDER BY timestamp DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()

    def by_session(
        self, session_key: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return records for a specific session."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT payload_json FROM active_intent_records
                   WHERE session_key = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (session_key, limit),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()

    def by_intent_type(
        self, intent_type: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return records of a specific intent type."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT payload_json FROM active_intent_records
                   WHERE intent_type = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (intent_type, limit),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()
