"""SQLite-backed meta-cognition audit stores.

Migrates 7 JSONL files (triggers, decisions, journals, reflections,
confidence_traces, patterns, evolution_seeds) into SQLite.
All follow the append-only pattern.
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

def _recent(store, table: str, *, limit: int = 200) -> list[dict[str, Any]]:
    _ensure(store)
    conn = sqlite_connect(store.db_path)
    try:
        rows = conn.execute(
            f"SELECT payload_json FROM {table} ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]
    finally: conn.close()

def _append_one(store, data: dict[str, Any]) -> None:
    _ensure(store)
    conn = sqlite_connect(store.db_path)
    try:
        store.insert_row(conn, data)
        conn.commit()
    finally:
        conn.close()


# ==========================================================================
# Triggers
# ==========================================================================

class MetaTriggersSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS meta_triggers (
            trigger_id     TEXT PRIMARY KEY,
            session_key    TEXT NOT NULL DEFAULT '',
            trigger_type   TEXT NOT NULL DEFAULT '',
            source_reference TEXT NOT NULL DEFAULT '',
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json   TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_meta_triggers_session
            ON meta_triggers(session_key, created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory" / "meta_cognition"
        super().__init__(workspace=workspace, db_path=db_path or d / "meta_triggers.sqlite3",
                         jsonl_path=d / "triggers.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("trigger_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO meta_triggers (trigger_id, session_key, trigger_type, source_reference, created_at, payload_json) VALUES (?,?,?,?,?,?)",
            (line.get("trigger_id",""), line.get("session_key",""), line.get("trigger_type",""),
             line.get("source_reference",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))
    def recent(self, *, limit=200): return _recent(self, "meta_triggers", limit=limit)
    def append(self, data: dict[str, Any]) -> None: _append_one(self, data)

# ==========================================================================
# Decisions
# ==========================================================================

class MetaDecisionsSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS meta_decisions (
            event_id         TEXT PRIMARY KEY,
            trigger_id       TEXT NOT NULL DEFAULT '',
            session_key      TEXT NOT NULL DEFAULT '',
            trigger_type     TEXT NOT NULL DEFAULT '',
            source_reference TEXT NOT NULL DEFAULT '',
            decision         TEXT NOT NULL DEFAULT '',
            accepted         INTEGER NOT NULL DEFAULT 0,
            suppression_reason TEXT NOT NULL DEFAULT '',
            turn_id          TEXT,
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json     TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_meta_decisions_session
            ON meta_decisions(session_key, created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory" / "meta_cognition"
        super().__init__(workspace=workspace, db_path=db_path or d / "meta_decisions.sqlite3",
                         jsonl_path=d / "decisions.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("trigger_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO meta_decisions (event_id,trigger_id,session_key,trigger_type,source_reference,decision,accepted,suppression_reason,turn_id,created_at,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (line.get("trigger_id","")+":"+line.get("created_at",""), line.get("trigger_id",""), line.get("session_key",""),
             line.get("trigger_type",""), line.get("source_reference",""), line.get("decision",""),
             1 if line.get("accepted") else 0, line.get("suppression_reason",""), line.get("turn_id"),
             line.get("created_at",""), json.dumps(line, ensure_ascii=False)))
    def recent(self, *, limit=200): return _recent(self, "meta_decisions", limit=limit)
    def append(self, data: dict[str, Any]) -> None: _append_one(self, data)

# ==========================================================================
# Journals
# ==========================================================================

class MetaJournalsSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS meta_journals (
            entry_id     TEXT PRIMARY KEY,
            session_key  TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_meta_journals_session
            ON meta_journals(session_key, created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory" / "meta_cognition"
        super().__init__(workspace=workspace, db_path=db_path or d / "meta_journals.sqlite3",
                         jsonl_path=d / "journals.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("entry_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO meta_journals (entry_id, session_key, created_at, payload_json) VALUES (?,?,?,?)",
            (line.get("entry_id",""), line.get("session_key",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))
    def recent(self, *, limit=200): return _recent(self, "meta_journals", limit=limit)
    def append(self, data: dict[str, Any]) -> None: _append_one(self, data)

# ==========================================================================
# Reflections
# ==========================================================================

class MetaReflectionsSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS meta_reflections (
            reflection_id TEXT PRIMARY KEY,
            session_key   TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json  TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_meta_reflections_session
            ON meta_reflections(session_key, created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory" / "meta_cognition"
        super().__init__(workspace=workspace, db_path=db_path or d / "meta_reflections.sqlite3",
                         jsonl_path=d / "reflections.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("reflection_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO meta_reflections (reflection_id, session_key, created_at, payload_json) VALUES (?,?,?,?)",
            (line.get("reflection_id",""), line.get("session_key",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))
    def recent(self, *, limit=200): return _recent(self, "meta_reflections", limit=limit)
    def append(self, data: dict[str, Any]) -> None: _append_one(self, data)

# ==========================================================================
# Confidence traces
# ==========================================================================

class MetaConfidenceTracesSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS meta_confidence_traces (
            trace_id     TEXT PRIMARY KEY,
            session_key  TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_meta_traces_session
            ON meta_confidence_traces(session_key, created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory" / "meta_cognition"
        super().__init__(workspace=workspace, db_path=db_path or d / "meta_confidence_traces.sqlite3",
                         jsonl_path=d / "confidence_traces.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("trace_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO meta_confidence_traces (trace_id, session_key, created_at, payload_json) VALUES (?,?,?,?)",
            (line.get("trace_id",""), line.get("session_key",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))
    def recent(self, *, limit=200): return _recent(self, "meta_confidence_traces", limit=limit)
    def append(self, data: dict[str, Any]) -> None: _append_one(self, data)

# ==========================================================================
# Patterns
# ==========================================================================

class MetaPatternsSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS meta_patterns (
            pattern_id   TEXT PRIMARY KEY,
            session_key  TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_meta_patterns_session
            ON meta_patterns(session_key, created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory" / "meta_cognition"
        super().__init__(workspace=workspace, db_path=db_path or d / "meta_patterns.sqlite3",
                         jsonl_path=d / "patterns.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("pattern_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO meta_patterns (pattern_id, session_key, created_at, payload_json) VALUES (?,?,?,?)",
            (line.get("pattern_id",""), line.get("session_key",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))
    def recent(self, *, limit=200): return _recent(self, "meta_patterns", limit=limit)
    def append(self, data: dict[str, Any]) -> None: _append_one(self, data)

# ==========================================================================
# Evolution seeds
# ==========================================================================

class MetaEvolutionSeedsSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS meta_evolution_seeds (
            seed_id      TEXT PRIMARY KEY,
            session_key  TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_meta_seeds_session
            ON meta_evolution_seeds(session_key, created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory" / "meta_cognition"
        super().__init__(workspace=workspace, db_path=db_path or d / "meta_evolution_seeds.sqlite3",
                         jsonl_path=d / "evolution_seeds.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("seed_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO meta_evolution_seeds (seed_id, session_key, created_at, payload_json) VALUES (?,?,?,?)",
            (line.get("seed_id",""), line.get("session_key",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))
    def recent(self, *, limit=200): return _recent(self, "meta_evolution_seeds", limit=limit)
    def append(self, data: dict[str, Any]) -> None: _append_one(self, data)
