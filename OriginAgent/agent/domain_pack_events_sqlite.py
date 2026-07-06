"""SQLite-backed domain pack event store.

Migrates from ``domain_pack_events.jsonl`` into a local SQLite database.
Append-only pattern with INSERT OR IGNORE deduplication.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OriginAgent.storage.jsonl_migration import AppendOnlyMigrator
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect
from OriginAgent.storage.sqlite_helpers import ensure_schema


class DomainPackEventsSqlite(AppendOnlyMigrator):
    """SQLite-backed domain pack governance event store.

    Drop-in replacement for the append-only event portion of
    ``DomainPackGovernanceService`` backed by a local SQLite database.
    """

    DDL = """
        CREATE TABLE IF NOT EXISTS domain_pack_events (
            event_id           TEXT PRIMARY KEY,
            pack_id            TEXT NOT NULL,
            action             TEXT NOT NULL,
            created_at         TEXT NOT NULL DEFAULT (datetime('now')),
            reason             TEXT NOT NULL DEFAULT '',
            actor              TEXT NOT NULL DEFAULT 'user',
            source             TEXT NOT NULL DEFAULT '',
            overrides_builtin  INTEGER NOT NULL DEFAULT 0,
            validation_summary TEXT NOT NULL DEFAULT '',
            review_proposal_id TEXT NOT NULL DEFAULT '',
            previous_json      TEXT,
            next_json          TEXT,
            result_json        TEXT,
            eval_summary_json  TEXT,
            artifact_paths_json TEXT,
            payload_json       TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_dp_events_pack
            ON domain_pack_events(pack_id, created_at);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        memory_dir = workspace / "memory"
        super().__init__(
            workspace=workspace,
            db_path=db_path or memory_dir / "domain_pack_events.sqlite3",
            jsonl_path=memory_dir / "domain_pack_events.jsonl",
        )

    # ------------------------------------------------------------------
    # Migrator contract
    # ------------------------------------------------------------------

    def table_ddl(self) -> str:
        return self.DDL

    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("event_id") and line.get("pack_id"))

    def insert_row(self, conn: Any, line: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO domain_pack_events
               (event_id, pack_id, action, created_at, reason, actor,
                source, overrides_builtin, validation_summary,
                review_proposal_id, previous_json, next_json,
                result_json, eval_summary_json, artifact_paths_json,
                payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                line.get("event_id", ""),
                line.get("pack_id", ""),
                line.get("action", ""),
                line.get("created_at", ""),
                line.get("reason", ""),
                line.get("actor", "user"),
                line.get("source", ""),
                1 if line.get("overrides_builtin") else 0,
                line.get("validation_summary", ""),
                line.get("review_proposal_id", ""),
                json.dumps(line.get("previous"), ensure_ascii=False) if line.get("previous") else None,
                json.dumps(line.get("next"), ensure_ascii=False) if line.get("next") else None,
                json.dumps(line.get("result"), ensure_ascii=False) if line.get("result") else None,
                json.dumps(line.get("eval_summary"), ensure_ascii=False) if line.get("eval_summary") else None,
                json.dumps(line.get("artifact_paths"), ensure_ascii=False) if line.get("artifact_paths") else None,
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

    def events_for_pack(self, pack_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return events for *pack_id* ordered newest-first."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT payload_json FROM domain_pack_events
                   WHERE pack_id = ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (pack_id, limit),
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()

    def latest_event(self, pack_id: str) -> dict[str, Any] | None:
        """Return the most recent event for *pack_id*."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute(
                """SELECT payload_json FROM domain_pack_events
                   WHERE pack_id = ?
                   ORDER BY created_at DESC
                   LIMIT 1""",
                (pack_id,),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["payload_json"])
        finally:
            conn.close()

    def append_event(self, event: dict[str, Any]) -> None:
        """Append a single governance event (for runtime use)."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            conn.execute(
                """INSERT OR IGNORE INTO domain_pack_events
                   (event_id, pack_id, action, created_at, reason, actor,
                    source, overrides_builtin, validation_summary,
                    review_proposal_id, previous_json, next_json,
                    result_json, eval_summary_json, artifact_paths_json,
                    payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.get("event_id", ""),
                    event.get("pack_id", ""),
                    event.get("action", ""),
                    event.get("created_at", ""),
                    event.get("reason", ""),
                    event.get("actor", "user"),
                    event.get("source", ""),
                    1 if event.get("overrides_builtin") else 0,
                    event.get("validation_summary", ""),
                    event.get("review_proposal_id", ""),
                    json.dumps(event.get("previous"), ensure_ascii=False) if event.get("previous") else None,
                    json.dumps(event.get("next"), ensure_ascii=False) if event.get("next") else None,
                    json.dumps(event.get("result"), ensure_ascii=False) if event.get("result") else None,
                    json.dumps(event.get("eval_summary"), ensure_ascii=False) if event.get("eval_summary") else None,
                    json.dumps(event.get("artifact_paths"), ensure_ascii=False) if event.get("artifact_paths") else None,
                    json.dumps(event, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def read_all(self) -> list[dict[str, Any]]:
        """Return all events, oldest first."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM domain_pack_events ORDER BY created_at"
            ).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]
        finally:
            conn.close()
