"""SQLite-backed reminder store.

Migrates from ``reminders.jsonl`` into a local SQLite database with
INSERT OR REPLACE semantics and indexed due-date queries.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from OriginAgent.storage.jsonl_migration import ReadModifyWriteMigrator
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect, ensure_schema


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReminderStoreSqlite(ReadModifyWriteMigrator):
    """SQLite-backed store for one-shot reminders.

    Drop-in replacement for ``ReminderStore`` with the same query API
    but backed by a local SQLite database.
    """

    DDL = """
        CREATE TABLE IF NOT EXISTS reminders (
            reminder_id  TEXT PRIMARY KEY,
            session_key  TEXT NOT NULL DEFAULT '',
            channel      TEXT NOT NULL DEFAULT '',
            chat_id      TEXT NOT NULL DEFAULT '',
            content      TEXT NOT NULL DEFAULT '',
            due_at       TEXT NOT NULL DEFAULT '',
            status       TEXT NOT NULL DEFAULT 'pending',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
            fired_at     TEXT,
            completed_at TEXT,
            cancelled_at TEXT,
            last_delivery_at TEXT,
            delivery_count  INTEGER NOT NULL DEFAULT 0,
            source       TEXT NOT NULL DEFAULT 'user_request',
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_reminders_status
            ON reminders(status, due_at);
        CREATE INDEX IF NOT EXISTS idx_reminders_session
            ON reminders(session_key);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        memory_dir = workspace / "memory" / "active_intents"
        super().__init__(
            workspace=workspace,
            db_path=db_path or memory_dir / "reminders.sqlite3",
            jsonl_path=memory_dir / "reminders.jsonl",
        )

    # ------------------------------------------------------------------
    # Migrator contract
    # ------------------------------------------------------------------

    def table_ddl(self) -> str:
        return self.DDL

    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("reminder_id"))

    def upsert_row(self, conn: Any, line: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR REPLACE INTO reminders
               (reminder_id, session_key, channel, chat_id, content,
                due_at, status, created_at, updated_at, fired_at,
                completed_at, cancelled_at, last_delivery_at,
                delivery_count, source, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                line.get("reminder_id", ""),
                line.get("session_key", ""),
                line.get("channel", ""),
                line.get("chat_id", ""),
                line.get("content", ""),
                line.get("due_at", ""),
                line.get("status", "pending"),
                line.get("created_at", ""),
                line.get("updated_at", ""),
                line.get("fired_at"),
                line.get("completed_at"),
                line.get("cancelled_at"),
                line.get("last_delivery_at"),
                line.get("delivery_count", 0),
                line.get("source", "user_request"),
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
    # CRUD  (same signatures as ReminderStore)
    # ------------------------------------------------------------------

    def read_all(self) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM reminders ORDER BY created_at"
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()

    def upsert(self, record: Any) -> Any:
        """Upsert a ReminderRecord (or dict) and return it."""
        data = record.to_dict() if hasattr(record, "to_dict") else dict(record)
        data["updated_at"] = _utcnow_iso()
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO reminders
                   (reminder_id, session_key, channel, chat_id, content,
                    due_at, status, created_at, updated_at, fired_at,
                    completed_at, cancelled_at, last_delivery_at,
                    delivery_count, source, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data.get("reminder_id", ""),
                    data.get("session_key", ""),
                    data.get("channel", ""),
                    data.get("chat_id", ""),
                    data.get("content", ""),
                    data.get("due_at", ""),
                    data.get("status", "pending"),
                    data.get("created_at", ""),
                    data["updated_at"],
                    data.get("fired_at"),
                    data.get("completed_at"),
                    data.get("cancelled_at"),
                    data.get("last_delivery_at"),
                    data.get("delivery_count", 0),
                    data.get("source", "user_request"),
                    json.dumps(data, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return record

    def get(self, reminder_id: str) -> dict[str, Any] | None:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT payload_json FROM reminders WHERE reminder_id = ?",
                (reminder_id,),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["payload_json"])
        finally:
            conn.close()

    def list_due(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current = (now or datetime.now(timezone.utc)).isoformat()
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT payload_json FROM reminders
                   WHERE status IN ('pending', 'due')
                     AND due_at <= ?
                   ORDER BY due_at""",
                (current,),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()

    def list_by_session(self, session_key: str) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT payload_json FROM reminders
                   WHERE session_key = ?
                   ORDER BY created_at DESC""",
                (session_key,),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()

    def list_by_status(self, status: str) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT payload_json FROM reminders
                   WHERE status = ?
                   ORDER BY due_at""",
                (status,),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()

    def delete(self, reminder_id: str) -> bool:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            cursor = conn.execute(
                "DELETE FROM reminders WHERE reminder_id = ?",
                (reminder_id,),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def transition_status(
        self,
        reminder_id: str,
        *,
        status: str,
        field_name: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Atomically transition a reminder's status in SQLite.

        Returns the updated record dict, or None if reminder_id not found.
        Sets ``status`` and ``updated_at``; if ``field_name`` is provided
        (e.g. "fired_at", "completed_at"), also sets that field to ``now``.
        For ``status="fired"``, additionally sets ``fired_at`` and
        ``last_delivery_at`` and increments ``delivery_count`` — matching
        ReminderStore.mark_fired semantics.

        This closes the sqlite/JSONL inconsistency where ``upsert``/``get``
        used sqlite but ``mark_fired``/``_transition`` only wrote to JSONL,
        causing ``get`` after ``mark_fired`` to return the stale status.
        """
        current = (now or datetime.now(timezone.utc)).isoformat()
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT payload_json FROM reminders WHERE reminder_id = ?",
                (reminder_id,),
            ).fetchone()
            if row is None:
                return None
            data = json.loads(row["payload_json"])
            data["status"] = status
            data["updated_at"] = current
            if field_name is not None:
                data[field_name] = current
            if status == "fired":
                data["fired_at"] = current
                data["last_delivery_at"] = current
                data["delivery_count"] = int(data.get("delivery_count", 0)) + 1
            conn.execute(
                """INSERT OR REPLACE INTO reminders
                   (reminder_id, session_key, channel, chat_id, content,
                    due_at, status, created_at, updated_at, fired_at,
                    completed_at, cancelled_at, last_delivery_at,
                    delivery_count, source, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data.get("reminder_id", ""),
                    data.get("session_key", ""),
                    data.get("channel", ""),
                    data.get("chat_id", ""),
                    data.get("content", ""),
                    data.get("due_at", ""),
                    data.get("status", "pending"),
                    data.get("created_at", ""),
                    data["updated_at"],
                    data.get("fired_at"),
                    data.get("completed_at"),
                    data.get("cancelled_at"),
                    data.get("last_delivery_at"),
                    data.get("delivery_count", 0),
                    data.get("source", "user_request"),
                    json.dumps(data, ensure_ascii=False),
                ),
            )
            conn.commit()
            return data
        finally:
            conn.close()
