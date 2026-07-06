"""SQLite-backed Tier-1 append-only stores.

Covers: thought_substrate (frames, journals), world_simulator
(causal_edges, traces, feedback), and evolution auxiliary stores.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OriginAgent.storage.jsonl_migration import AppendOnlyMigrator
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect
from OriginAgent.storage.sqlite_helpers import ensure_schema

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


# ==========================================================================
# ThoughtSubstrate — frames.jsonl
# ==========================================================================

class ThoughtFramesSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS thought_frames (
            frame_id     TEXT PRIMARY KEY,
            session_key  TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_thought_frames_session
            ON thought_frames(session_key, created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory" / "thought_substrate"
        super().__init__(workspace=workspace, db_path=db_path or d / "thought_frames.sqlite3",
                         jsonl_path=d / "frames.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("frame_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO thought_frames (frame_id,session_key,created_at,payload_json) VALUES (?,?,?,?)",
            (line.get("frame_id",""), line.get("session_key",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))
    def recent(self, *, limit=200): return _recent(self, "thought_frames", limit=limit)
    def by_session(self, session_key: str, *, limit: int = 100) -> list[dict[str, Any]]:
        _ensure(self)
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM thought_frames WHERE session_key=? ORDER BY created_at DESC LIMIT ?",
                (session_key, limit)).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally: conn.close()
    def append(self, data: dict[str, Any]) -> None:
        _ensure(self)
        conn = sqlite_connect(self.db_path)
        try:
            self.insert_row(conn, data)
            conn.commit()
        finally:
            conn.close()


# ==========================================================================
# ThoughtSubstrate — journals.jsonl
# ==========================================================================

class ThoughtJournalsSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS thought_journals (
            entry_id     TEXT PRIMARY KEY,
            session_key  TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_thought_journals_session
            ON thought_journals(session_key, created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory" / "thought_substrate"
        super().__init__(workspace=workspace, db_path=db_path or d / "thought_journals.sqlite3",
                         jsonl_path=d / "journals.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("entry_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO thought_journals (entry_id,session_key,created_at,payload_json) VALUES (?,?,?,?)",
            (line.get("entry_id",""), line.get("session_key",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))
    def recent(self, *, limit=200): return _recent(self, "thought_journals", limit=limit)
    def append(self, data: dict[str, Any]) -> None:
        _ensure(self)
        conn = sqlite_connect(self.db_path)
        try:
            self.insert_row(conn, data)
            conn.commit()
        finally:
            conn.close()


# ==========================================================================
# WorldSimulator — causal_edges.jsonl
# ==========================================================================

class CausalEdgesSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS causal_edges (
            edge_id      TEXT PRIMARY KEY,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_causal_edges_created
            ON causal_edges(created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory" / "world_simulator"
        super().__init__(workspace=workspace, db_path=db_path or d / "causal_edges.sqlite3",
                         jsonl_path=d / "causal_edges.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("edge_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO causal_edges (edge_id,created_at,payload_json) VALUES (?,?,?)",
            (line.get("edge_id",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))
    def recent(self, *, limit=200): return _recent(self, "causal_edges", limit=limit)


# ==========================================================================
# WorldSimulator — simulation_traces.jsonl
# ==========================================================================

class SimulationTracesSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS simulation_traces (
            trace_id     TEXT PRIMARY KEY,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_sim_traces_created
            ON simulation_traces(created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory" / "world_simulator"
        super().__init__(workspace=workspace, db_path=db_path or d / "simulation_traces.sqlite3",
                         jsonl_path=d / "simulation_traces.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("trace_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO simulation_traces (trace_id,created_at,payload_json) VALUES (?,?,?)",
            (line.get("trace_id",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))
    def recent(self, *, limit=200): return _recent(self, "simulation_traces", limit=limit)


# ==========================================================================
# WorldSimulator — simulation_feedback.jsonl
# ==========================================================================

class SimulationFeedbackSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS simulation_feedback (
            feedback_id  TEXT PRIMARY KEY,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_sim_feedback_created
            ON simulation_feedback(created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory" / "world_simulator"
        super().__init__(workspace=workspace, db_path=db_path or d / "simulation_feedback.sqlite3",
                         jsonl_path=d / "simulation_feedback.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("feedback_id"))
    def insert_row(self, conn, line):
        conn.execute(
            "INSERT OR IGNORE INTO simulation_feedback (feedback_id,created_at,payload_json) VALUES (?,?,?)",
            (line.get("feedback_id",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))
    def recent(self, *, limit=200): return _recent(self, "simulation_feedback", limit=limit)


# ==========================================================================
# Evolution auxiliary stores
# ==========================================================================

class EvolutionSandboxCacheSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS evolution_sandbox_cache (
            cache_key    TEXT PRIMARY KEY,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory"
        super().__init__(workspace=workspace, db_path=db_path or d / "evolution_sandbox_cache.sqlite3",
                         jsonl_path=d / "evolution_sandbox_cache.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("cache_key"))
    def insert_row(self, conn, line):
        conn.execute("INSERT OR IGNORE INTO evolution_sandbox_cache (cache_key,created_at,payload_json) VALUES (?,?,?)",
                     (line.get("cache_key",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))


class EvolutionOutcomesSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS evolution_outcomes (
            event_id     TEXT PRIMARY KEY,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_evo_outcomes_created
            ON evolution_outcomes(created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory"
        super().__init__(workspace=workspace, db_path=db_path or d / "evolution_outcomes.sqlite3",
                         jsonl_path=d / "evolution_outcomes.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("event_id"))
    def insert_row(self, conn, line):
        conn.execute("INSERT OR IGNORE INTO evolution_outcomes (event_id,created_at,payload_json) VALUES (?,?,?)",
                     (line.get("event_id",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))


class EvolutionDependenciesSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS evolution_dependencies (
            dep_id       TEXT PRIMARY KEY,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory"
        super().__init__(workspace=workspace, db_path=db_path or d / "evolution_dependencies.sqlite3",
                         jsonl_path=d / "evolution_dependencies.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("dep_id"))
    def insert_row(self, conn, line):
        conn.execute("INSERT OR IGNORE INTO evolution_dependencies (dep_id,created_at,payload_json) VALUES (?,?,?)",
                     (line.get("dep_id",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))


class EvolutionHealthHistorySqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS evolution_health_history (
            snapshot_id  TEXT PRIMARY KEY,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_evo_health_created
            ON evolution_health_history(created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory"
        super().__init__(workspace=workspace, db_path=db_path or d / "evolution_health_history.sqlite3",
                         jsonl_path=d / "evolution_health_history.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("snapshot_id"))
    def insert_row(self, conn, line):
        conn.execute("INSERT OR IGNORE INTO evolution_health_history (snapshot_id,created_at,payload_json) VALUES (?,?,?)",
                     (line.get("snapshot_id",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))


class EvolutionTrialLogsSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS evolution_trial_logs (
            trial_id     TEXT PRIMARY KEY,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_evo_trials_created
            ON evolution_trial_logs(created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory"
        super().__init__(workspace=workspace, db_path=db_path or d / "evolution_trial_logs.sqlite3",
                         jsonl_path=d / "evolution_trial_logs.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("trial_id"))
    def insert_row(self, conn, line):
        conn.execute("INSERT OR IGNORE INTO evolution_trial_logs (trial_id,created_at,payload_json) VALUES (?,?,?)",
                     (line.get("trial_id",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))


class EvolutionConfigPatchesSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS evolution_config_patches (
            patch_id     TEXT PRIMARY KEY,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_evo_patches_created
            ON evolution_config_patches(created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory"
        super().__init__(workspace=workspace, db_path=db_path or d / "evolution_config_patches.sqlite3",
                         jsonl_path=d / "evolution_config_patches.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("patch_id"))
    def insert_row(self, conn, line):
        conn.execute("INSERT OR IGNORE INTO evolution_config_patches (patch_id,created_at,payload_json) VALUES (?,?,?)",
                     (line.get("patch_id",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))


class MetaProgrammingCompilationsSqlite(AppendOnlyMigrator):
    DDL = """
        CREATE TABLE IF NOT EXISTS meta_programming_compilations (
            compilation_id TEXT PRIMARY KEY,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            payload_json  TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_mp_compilations_created
            ON meta_programming_compilations(created_at);
    """
    def __init__(self, workspace: Path, db_path: Path | None = None):
        d = workspace / "memory"
        super().__init__(workspace=workspace, db_path=db_path or d / "meta_programming_compilations.sqlite3",
                         jsonl_path=d / "meta_programming_compilations.jsonl")
    def table_ddl(self) -> str: return self.DDL
    def validate_line(self, line): return bool(line.get("compilation_id"))
    def insert_row(self, conn, line):
        conn.execute("INSERT OR IGNORE INTO meta_programming_compilations (compilation_id,created_at,payload_json) VALUES (?,?,?)",
                     (line.get("compilation_id",""), line.get("created_at",""), json.dumps(line, ensure_ascii=False)))
