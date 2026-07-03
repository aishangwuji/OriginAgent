"""SQLite-backed skill lifecycle event store.

Migrates from ``skill_lifecycle_events.jsonl`` into a local SQLite database.
Append-only pattern with INSERT OR IGNORE deduplication.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OriginAgent.storage.jsonl_migration import AppendOnlyMigrator
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect, ensure_schema


class SkillLifecycleStoreSqlite(AppendOnlyMigrator):
    """SQLite-backed skill lifecycle event store.

    Drop-in replacement for the append-only portion of ``SkillLifecycleStore``
    backed by a local SQLite database.
    """

    DDL = """
        CREATE TABLE IF NOT EXISTS skill_lifecycle_events (
            event_id          TEXT PRIMARY KEY,
            skill_name        TEXT NOT NULL,
            action            TEXT NOT NULL,
            created_at        TEXT NOT NULL DEFAULT (datetime('now')),
            reason            TEXT NOT NULL DEFAULT '',
            actor             TEXT NOT NULL DEFAULT 'user',
            artifact_path     TEXT NOT NULL DEFAULT '',
            review_proposal_id TEXT NOT NULL DEFAULT '',
            previous_json     TEXT,
            next_json         TEXT,
            payload_json      TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_sk_lifecycle_skill
            ON skill_lifecycle_events(skill_name, created_at);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        memory_dir = workspace / "memory"
        super().__init__(
            workspace=workspace,
            db_path=db_path or memory_dir / "skill_lifecycle_events.sqlite3",
            jsonl_path=memory_dir / "skill_lifecycle_events.jsonl",
        )

    # ------------------------------------------------------------------
    # Migrator contract
    # ------------------------------------------------------------------

    def table_ddl(self) -> str:
        return self.DDL

    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("event_id") and line.get("skill_name"))

    def insert_row(self, conn: Any, line: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO skill_lifecycle_events
               (event_id, skill_name, action, created_at, reason, actor,
                artifact_path, review_proposal_id, previous_json, next_json,
                payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                line.get("event_id", ""),
                line.get("skill_name", ""),
                line.get("action", ""),
                line.get("created_at", ""),
                line.get("reason", ""),
                line.get("actor", "user"),
                line.get("artifact_path", ""),
                line.get("review_proposal_id", ""),
                json.dumps(line.get("previous"), ensure_ascii=False) if line.get("previous") else None,
                json.dumps(line.get("next"), ensure_ascii=False) if line.get("next") else None,
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
    # Query helpers
    # ------------------------------------------------------------------

    def latest_event(self, skill_name: str) -> dict[str, Any] | None:
        """Return the most recent lifecycle event for *skill_name*."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute(
                """SELECT payload_json FROM skill_lifecycle_events
                   WHERE skill_name = ?
                   ORDER BY created_at DESC
                   LIMIT 1""",
                (skill_name,),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["payload_json"])
        finally:
            conn.close()

    def list_events(
        self, skill_name: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return all lifecycle events for *skill_name* newest-first."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT payload_json FROM skill_lifecycle_events
                   WHERE skill_name = ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (skill_name, limit),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()

    def all_latest_events(self) -> dict[str, dict[str, Any]]:
        """Return {skill_name: latest_event} for all skills."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT skill_name, payload_json FROM skill_lifecycle_events
                   WHERE (skill_name, created_at) IN (
                       SELECT skill_name, MAX(created_at)
                       FROM skill_lifecycle_events
                       GROUP BY skill_name
                   )"""
            ).fetchall()
            return {row["skill_name"]: json.loads(row["payload_json"]) for row in rows}
        finally:
            conn.close()

    def append_event(self, event: dict[str, Any]) -> None:
        """Append a single lifecycle event (for runtime use, not just migration)."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            conn.execute(
                """INSERT OR IGNORE INTO skill_lifecycle_events
                   (event_id, skill_name, action, created_at, reason, actor,
                    artifact_path, review_proposal_id, previous_json, next_json,
                    payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.get("event_id", ""),
                    event.get("skill_name", ""),
                    event.get("action", ""),
                    event.get("created_at", ""),
                    event.get("reason", ""),
                    event.get("actor", "user"),
                    event.get("artifact_path", ""),
                    event.get("review_proposal_id", ""),
                    json.dumps(event.get("previous"), ensure_ascii=False) if event.get("previous") else None,
                    json.dumps(event.get("next"), ensure_ascii=False) if event.get("next") else None,
                    json.dumps(event, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()
