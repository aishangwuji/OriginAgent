"""SQLite-backed Tier-2 stores — RMW and complex append-only patterns.

Covers: memory_candidates, scheduler_runs, deliberation cycles,
and idempotency keys.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OriginAgent.storage.jsonl_migration import AppendOnlyMigrator
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect, ensure_schema


# ==========================================================================
# Shared helpers
# ==========================================================================

def _ensure(store) -> None:
    conn = sqlite_connect(store.db_path)
    try: ensure_schema(conn, store.table_ddl())
    finally: conn.close()


# ==========================================================================
# MemoryCandidatesSqlite  (append-only queue with cursor tracking)
# ==========================================================================

class MemoryCandidatesSqlite(AppendOnlyMigrator):
    """SQLite-backed memory candidate queue.

    Appends are INSERT OR IGNORE by candidate_id. Consumer cursors track
    per-consumer read position for incremental polling.
    """

    DDL = """
        CREATE TABLE IF NOT EXISTS memory_candidates (
            candidate_id       TEXT PRIMARY KEY,
            kind               TEXT NOT NULL DEFAULT 'fact',
            summary            TEXT NOT NULL DEFAULT '',
            source_session_key TEXT NOT NULL DEFAULT '',
            source_refs_json   TEXT NOT NULL DEFAULT '[]',
            source_excerpt     TEXT NOT NULL DEFAULT '',
            confidence         REAL NOT NULL DEFAULT 0.8,
            sensitivity        TEXT NOT NULL DEFAULT 'low',
            scope              TEXT NOT NULL DEFAULT 'user',
            owner_id           TEXT NOT NULL DEFAULT 'user',
            created_at         TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json       TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_mc_kind
            ON memory_candidates(kind, created_at);
        CREATE INDEX IF NOT EXISTS idx_mc_owner
            ON memory_candidates(owner_id, created_at);
        CREATE TABLE IF NOT EXISTS memory_candidate_cursors (
            consumer   TEXT PRIMARY KEY,
            cursor_pos INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        ) STRICT;
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        d = workspace / "memory"
        super().__init__(workspace=workspace, db_path=db_path or d / "memory_candidates.sqlite3",
                         jsonl_path=d / "memory_candidates.jsonl")

    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("candidate_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO memory_candidates (candidate_id,kind,summary,source_session_key,"
            "source_refs_json,source_excerpt,confidence,sensitivity,scope,owner_id,created_at,payload_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (line.get("candidate_id",""), line.get("kind","fact"), line.get("summary",""),
             line.get("source_session_key",""), json.dumps(line.get("source_refs",[]), ensure_ascii=False),
             line.get("source_excerpt",""), float(line.get("confidence",0.8) or 0.8),
             line.get("sensitivity","low"), line.get("scope","user"), line.get("owner_id","user"),
             line.get("created_at",""), json.dumps(line, ensure_ascii=False)))

    # Query API

    def read_all(self, *, kinds: tuple[str,...] | None = None,
                 owner_id: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        _ensure(self)
        conn = sqlite_connect(self.db_path)
        try:
            where = ["1=1"]; params: list[Any] = []
            if kinds: where.append(f"kind IN ({','.join('?'*len(kinds))})"); params.extend(kinds)
            if owner_id: where.append("owner_id=?"); params.append(owner_id)
            q = f"SELECT payload_json FROM memory_candidates WHERE {' AND '.join(where)} ORDER BY created_at"
            if limit and limit > 0: q += f" LIMIT {limit}"
            rows = conn.execute(q, params).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally: conn.close()

    def by_kind(self, kind: str, *, limit: int = 200) -> list[dict[str, Any]]:
        return self.read_all(kinds=(kind,), limit=limit)

    # Consumer cursor API

    def get_cursor(self, consumer: str) -> int:
        _ensure(self)
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT cursor_pos FROM memory_candidate_cursors WHERE consumer=?", (consumer,)
            ).fetchone()
            return row["cursor_pos"] if row else 0
        finally: conn.close()

    def set_cursor(self, consumer: str, cursor_pos: int) -> None:
        _ensure(self)
        conn = sqlite_connect(self.db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO memory_candidate_cursors (consumer,cursor_pos,updated_at) VALUES (?,?,datetime('now'))",
                (consumer, cursor_pos))
            conn.commit()
        finally: conn.close()


# ==========================================================================
# SchedulerRunsSqlite  (append-only audit)
# ==========================================================================

class SchedulerRunsSqlite(AppendOnlyMigrator):
    """SQLite-backed cognitive scheduler run ledger."""

    DDL = """
        CREATE TABLE IF NOT EXISTS scheduler_runs (
            run_id               TEXT PRIMARY KEY,
            trigger              TEXT NOT NULL DEFAULT 'manual',
            scheduled_job_id     TEXT,
            scanned_session_count INTEGER NOT NULL DEFAULT 0,
            decision_count       INTEGER NOT NULL DEFAULT 0,
            emitted_count        INTEGER NOT NULL DEFAULT 0,
            suppressed_count     INTEGER NOT NULL DEFAULT 0,
            skipped_count        INTEGER NOT NULL DEFAULT 0,
            errored_session_count INTEGER NOT NULL DEFAULT 0,
            created_at           TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json         TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_scheduler_runs_created
            ON scheduler_runs(created_at);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        d = workspace / "memory" / "cognitive"
        super().__init__(workspace=workspace, db_path=db_path or d / "scheduler_runs.sqlite3",
                         jsonl_path=d / "scheduler_runs.jsonl")

    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("run_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO scheduler_runs (run_id,trigger,scheduled_job_id,scanned_session_count,"
            "decision_count,emitted_count,suppressed_count,skipped_count,errored_session_count,created_at,payload_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (line.get("run_id",""), line.get("trigger","manual"), line.get("scheduled_job_id"),
             line.get("scanned_session_count",0), line.get("decision_count",0), line.get("emitted_count",0),
             line.get("suppressed_count",0), line.get("skipped_count",0), line.get("errored_session_count",0),
             line.get("created_at",""), json.dumps(line, ensure_ascii=False)))

    def recent(self, *, limit: int = 200) -> list[dict[str, Any]]:
        _ensure(self)
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM scheduler_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally: conn.close()


# ==========================================================================
# DeliberationCyclesSqlite  (append-only audit, atomic full-rewrite)
# ==========================================================================

class DeliberationCyclesSqlite(AppendOnlyMigrator):
    """SQLite-backed BDI deliberation cycle audit."""

    DDL = """
        CREATE TABLE IF NOT EXISTS deliberation_cycles (
            cycle_id    TEXT PRIMARY KEY,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_delib_cycles_created
            ON deliberation_cycles(created_at);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        d = workspace / "memory" / "bdi"
        super().__init__(workspace=workspace, db_path=db_path or d / "deliberation_cycles.sqlite3",
                         jsonl_path=d / "cycles.jsonl")

    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("cycle_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO deliberation_cycles (cycle_id,created_at,payload_json) VALUES (?,?,?)",
            (line.get("cycle_id",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))

    def recent(self, *, limit: int = 200) -> list[dict[str, Any]]:
        _ensure(self)
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM deliberation_cycles ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally: conn.close()


# ==========================================================================
# IdempotencyKeysSqlite  (simple key-value, NOT an AppendOnlyMigrator)
# ==========================================================================

class IdempotencyKeysSqlite:
    """SQLite-backed idempotency key store for successful actions."""

    DDL = """
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            idempotency_key TEXT PRIMARY KEY,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        ) STRICT;
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        d = workspace / "memory" / "action"
        self.db_path = db_path or d / "idempotency_keys.sqlite3"
        self.jsonl_path = d / "idempotency_keys.jsonl"

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try: ensure_schema(conn, self.DDL)
        finally: conn.close()

    def migrate(self):
        """Import existing keys from JSONL into SQLite. Idempotent."""
        from dataclasses import dataclass

        @dataclass
        class _Result:
            ok: bool; records_imported: int = 0; records_skipped: int = 0; error: str = ""

        if not self.jsonl_path.exists():
            return _Result(ok=True)

        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        imported = skipped = 0
        try:
            with open(self.jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    try: data = json.loads(line)
                    except json.JSONDecodeError: skipped += 1; continue
                    key = data.get("idempotency_key") if isinstance(data, dict) else None
                    if not isinstance(key, str) or not key: skipped += 1; continue
                    conn.execute("INSERT OR IGNORE INTO idempotency_keys (idempotency_key) VALUES (?)", (key,))
                    imported += 1
            conn.commit()
            return _Result(ok=True, records_imported=imported, records_skipped=skipped)
        finally: conn.close()

    def load(self) -> set[str]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute("SELECT idempotency_key FROM idempotency_keys").fetchall()
            return {r["idempotency_key"] for r in rows}
        finally: conn.close()

    def add(self, key: str) -> None:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            conn.execute("INSERT OR IGNORE INTO idempotency_keys (idempotency_key) VALUES (?)", (key,))
            conn.commit()
        finally: conn.close()

    def contains(self, key: str) -> bool:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM idempotency_keys WHERE idempotency_key=?", (key,)
            ).fetchone()
            return row is not None
        finally: conn.close()
