"""SQLite-backed FactStore — replaces memory/facts.jsonl.

FactStore is a current-state store (read-modify-write), not append-only.
Stores individual fact records with upsert semantics.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OriginAgent.storage.jsonl_migration import ReadModifyWriteMigrator
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect
from OriginAgent.storage.sqlite_helpers import ensure_schema


class FactStoreSqlite(ReadModifyWriteMigrator):
    """SQLite-backed fact store.

    Drop-in replacement for the JSONL portion of ``FactStore``.
    Each fact is a row; upserts are ``INSERT OR REPLACE`` by fact_id.
    """

    DDL = """
        CREATE TABLE IF NOT EXISTS facts (
            fact_id          TEXT PRIMARY KEY,
            session_key      TEXT NOT NULL DEFAULT '',
            content          TEXT NOT NULL DEFAULT '',
            category         TEXT NOT NULL DEFAULT 'note',
            domain           TEXT NOT NULL DEFAULT '',
            status           TEXT NOT NULL DEFAULT 'active',
            confidence       REAL NOT NULL DEFAULT 0.5,
            scope            TEXT NOT NULL DEFAULT 'session',
            owner_id         TEXT NOT NULL DEFAULT '',
            source           TEXT NOT NULL DEFAULT '',
            consistency_state TEXT NOT NULL DEFAULT 'consistent',
            superseded_by    TEXT,
            superseded_at    TEXT,
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json     TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_facts_session ON facts(session_key, status);
        CREATE INDEX IF NOT EXISTS idx_facts_owner ON facts(owner_id, scope, status);
        CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category, status);
        CREATE INDEX IF NOT EXISTS idx_facts_content ON facts(content);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        d = Path(workspace) / "memory"
        super().__init__(
            workspace=workspace,
            db_path=db_path or d / "facts.sqlite3",
            jsonl_path=d / "facts.jsonl",
        )

    # ── Migrator contract ────────────────────────────────────────

    def table_ddl(self) -> str:
        return self.DDL

    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("fact_id"))

    def upsert_row(self, conn: Any, line: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR REPLACE INTO facts
               (fact_id, session_key, content, category, domain, status,
                confidence, scope, owner_id, source, consistency_state,
                superseded_by, superseded_at, created_at, updated_at,
                payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                line.get("fact_id", ""),
                line.get("session_key", ""),
                line.get("content", ""),
                line.get("category", "note"),
                line.get("domain", ""),
                line.get("status", "active"),
                float(line.get("confidence", 0.5) or 0.5),
                line.get("scope", "session"),
                line.get("owner_id", ""),
                line.get("source", ""),
                line.get("consistency_state", "consistent"),
                line.get("superseded_by"),
                line.get("superseded_at"),
                line.get("created_at", ""),
                line.get("updated_at", ""),
                json.dumps(line, ensure_ascii=False),
            ),
        )

    # ── Schema management ─────────────────────────────────────────

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try:
            ensure_schema(conn, self.DDL)
        finally:
            conn.close()

    # ── Query API (mirrors FactStore public methods) ──────────────

    def read_all(self) -> list[dict[str, Any]]:
        """Return every fact row as a dict, ordered by created_at."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM facts ORDER BY created_at"
            ).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally:
            conn.close()

    def get(self, fact_id: str) -> dict[str, Any] | None:
        """Return a single fact by *fact_id*, or ``None``."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT payload_json FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            return json.loads(row["payload_json"]) if row else None
        finally:
            conn.close()

    def upsert(self, record: Any) -> dict[str, Any]:
        """Upsert a fact record (dict or object with to_dict) and return it."""
        data = record.to_dict() if hasattr(record, "to_dict") else dict(record)
        data["updated_at"] = data.get("updated_at", "")
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            self.upsert_row(conn, data)
            conn.commit()
        finally:
            conn.close()
        return record

    def delete(self, fact_id: str) -> bool:
        """Delete by *fact_id*. Returns ``True`` if a row was removed."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            cursor = conn.execute("DELETE FROM facts WHERE fact_id = ?", (fact_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def count(self) -> int:
        """Return the total number of facts."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM facts").fetchone()
            return row["c"] if row else 0
        finally:
            conn.close()

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Basic LIKE search over fact content."""
        self._ensure_schema()
        pattern = f"%{query}%"
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM facts WHERE content LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (pattern, max(1, limit)),
            ).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally:
            conn.close()


__all__ = ["FactStoreSqlite"]
