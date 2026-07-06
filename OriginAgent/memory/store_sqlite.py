"""SQLite-backed NearlineMemoryStore — replaces 6 JSONL files.

Single ``memory_records`` table with a ``kind`` discriminator column
replaces memcells.jsonl, episodes.jsonl, foresights.jsonl,
agent_cases.jsonl, profiles.jsonl, and events.jsonl.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect
from OriginAgent.storage.sqlite_helpers import ensure_schema

KINDS = frozenset({"memcell", "episode", "foresight", "agent_case", "profile", "event"})


class NearlineMemoryStoreSqlite:
    """SQLite-backed nearline memory store.

    Single table replaces 6 JSONL files.  Each row has a ``kind``
    discriminator and a ``payload_json`` blob.
    """

    DDL = """
        CREATE TABLE IF NOT EXISTS memory_records (
            record_id   TEXT NOT NULL,
            kind        TEXT NOT NULL,
            session_key TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (kind, record_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_mr_kind ON memory_records(kind, created_at);
        CREATE INDEX IF NOT EXISTS idx_mr_session ON memory_records(session_key, kind, created_at);
    """

    def __init__(self, workspace: Path) -> None:
        d = Path(workspace) / "memory" / "nearline"
        self.db_path = d / "memory_records.sqlite3"

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try:
            ensure_schema(conn, self.DDL)
        finally:
            conn.close()

    def append(self, kind: str, records: list[Any]) -> int:
        """Append records of *kind*. Each record is an object with to_json() or a dict."""
        if kind not in KINDS:
            raise ValueError(f"Unknown memory record kind: {kind}")
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            count = 0
            for rec in records:
                data = rec.to_json() if hasattr(rec, "to_json") else (dict(rec) if isinstance(rec, dict) else {})
                record_id = data.get(
                    "memcell_id", data.get("episode_id", data.get("foresight_id",
                               data.get("case_id", data.get("profile_id", data.get("event_id", ""))))))
                if not record_id:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO memory_records (record_id, kind, session_key, created_at, payload_json) VALUES (?,?,?,?,?)",
                    (record_id, kind, data.get("session_key", ""), data.get("created_at", "") or data.get("timestamp", ""), json.dumps(data, ensure_ascii=False)),
                )
                count += 1
            conn.commit()
            return count
        finally:
            conn.close()

    def read_all(self, kind: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Return all records of *kind*, oldest first."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            q = "SELECT payload_json FROM memory_records WHERE kind=? ORDER BY created_at"
            params: list[Any] = [kind]
            if limit and limit > 0:
                q += " LIMIT ?"
                params.append(limit)
            rows = conn.execute(q, params).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally:
            conn.close()

    def count(self, kind: str) -> int:
        """Return the number of records of *kind*."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM memory_records WHERE kind=?", (kind,)).fetchone()
            return row["c"] if row else 0
        finally:
            conn.close()

    def delete(self, kind: str, record_id: str) -> bool:
        """Delete a single record by *kind* and *record_id*."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            cursor = conn.execute("DELETE FROM memory_records WHERE kind=? AND record_id=?", (kind, record_id))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


__all__ = ["NearlineMemoryStoreSqlite"]
