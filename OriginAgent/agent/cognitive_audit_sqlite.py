"""SQLite-backed cognitive audit ledger.

Migrates from ``events.jsonl`` and ``decisions.jsonl`` into SQLite.
Both tables follow the append-only pattern.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OriginAgent.storage.jsonl_migration import AppendOnlyMigrator
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect
from OriginAgent.storage.sqlite_helpers import ensure_schema


class CognitiveEventsSqlite(AppendOnlyMigrator):
    """SQLite-backed cognitive events store."""

    DDL = """
        CREATE TABLE IF NOT EXISTS cognitive_events (
            event_id      TEXT PRIMARY KEY,
            session_key   TEXT NOT NULL DEFAULT '',
            event_type    TEXT NOT NULL DEFAULT '',
            source_type   TEXT NOT NULL DEFAULT '',
            source_reference TEXT NOT NULL DEFAULT '',
            summary       TEXT NOT NULL DEFAULT '',
            priority      TEXT NOT NULL DEFAULT 'medium',
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json  TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_cog_events_session
            ON cognitive_events(session_key, created_at);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        memory_dir = workspace / "memory" / "cognitive"
        super().__init__(
            workspace=workspace, db_path=db_path or memory_dir / "cognitive_events.sqlite3",
            jsonl_path=memory_dir / "events.jsonl",
        )

    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("event_id") and line.get("session_key"))

    def insert_row(self, conn: Any, line: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO cognitive_events
               (event_id, session_key, event_type, source_type, source_reference,
                summary, priority, created_at, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (line.get("event_id",""), line.get("session_key",""), line.get("event_type",""),
             line.get("source_type",""), line.get("source_reference",""), line.get("summary",""),
             line.get("priority","medium"), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try: ensure_schema(conn, self.DDL)
        finally: conn.close()

    def append(self, data: dict[str, Any]) -> None:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            self.insert_row(conn, data)
            conn.commit()
        finally:
            conn.close()

    def recent(self, *, limit: int = 200) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM cognitive_events ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally: conn.close()

    def by_session(self, session_key: str, *, limit: int = 100) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM cognitive_events WHERE session_key=? ORDER BY created_at DESC LIMIT ?",
                (session_key, limit),
            ).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally: conn.close()


class CognitiveDecisionsSqlite(AppendOnlyMigrator):
    """SQLite-backed cognitive decisions store."""

    DDL = """
        CREATE TABLE IF NOT EXISTS cognitive_decisions (
            event_id      TEXT PRIMARY KEY,
            session_key   TEXT NOT NULL DEFAULT '',
            trigger_id    TEXT NOT NULL DEFAULT '',
            trigger_type  TEXT NOT NULL DEFAULT '',
            outcome       TEXT NOT NULL DEFAULT '',
            accepted      INTEGER NOT NULL DEFAULT 0,
            suppression_reason TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json  TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_cog_decisions_session
            ON cognitive_decisions(session_key, created_at);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        memory_dir = workspace / "memory" / "cognitive"
        super().__init__(
            workspace=workspace, db_path=db_path or memory_dir / "cognitive_decisions.sqlite3",
            jsonl_path=memory_dir / "decisions.jsonl",
        )

    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("event_id"))

    def insert_row(self, conn: Any, line: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO cognitive_decisions
               (event_id, session_key, trigger_id, trigger_type, outcome, accepted,
                suppression_reason, created_at, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (line.get("event_id",""), line.get("session_key",""), line.get("trigger_id",""),
             line.get("trigger_type",""), line.get("outcome",""), 1 if line.get("accepted") else 0,
             line.get("suppression_reason",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try: ensure_schema(conn, self.DDL)
        finally: conn.close()

    def append(self, data: dict[str, Any]) -> None:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            self.insert_row(conn, data)
            conn.commit()
        finally:
            conn.close()

    def recent(self, *, limit: int = 200) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM cognitive_decisions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally: conn.close()
