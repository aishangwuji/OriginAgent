"""Shared SQLite store for audit events and opportunity signals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect
from OriginAgent.storage.sqlite_helpers import ensure_schema

AUDIT_DDL = """
    CREATE TABLE IF NOT EXISTS audit_events (
        event_id   TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        payload_json TEXT NOT NULL DEFAULT '{}'
    ) STRICT;
    CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type, created_at);
"""

SIGNAL_DDL = """
    CREATE TABLE IF NOT EXISTS opportunity_signals (
        signal_id  TEXT PRIMARY KEY,
        kind       TEXT NOT NULL DEFAULT '',
        summary    TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        payload_json TEXT NOT NULL DEFAULT '{}'
    ) STRICT;
    CREATE INDEX IF NOT EXISTS idx_signals_kind ON opportunity_signals(kind, created_at);
"""


class AuditSqliteStore:
    """SQLite store for audit events (action_decisions, confirmation_events, permission_decisions)."""

    def __init__(self, workspace: Path) -> None:
        d = Path(workspace) / "memory" / "audit"
        self.db_path = d / "audit_events.sqlite3"

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try:
            ensure_schema(conn, AUDIT_DDL)
        finally:
            conn.close()

    def log(self, event: dict[str, Any]) -> None:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO audit_events (event_id, event_type, created_at, payload_json) VALUES (?,?,?,?)",
                (event.get("event_id", ""), event.get("event_type", ""), event.get("created_at", ""), json.dumps(event, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()

    def read_by_type(self, event_type: str, *, limit: int = 200) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM audit_events WHERE event_type=? ORDER BY created_at DESC LIMIT ?",
                (event_type, max(1, limit)),
            ).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally:
            conn.close()


class OpportunitySignalSqliteStore:
    """SQLite store for evolution opportunity signals."""

    def __init__(self, workspace: Path) -> None:
        d = Path(workspace) / "memory"
        self.db_path = d / "opportunity_signals.sqlite3"

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try:
            ensure_schema(conn, SIGNAL_DDL)
        finally:
            conn.close()

    def append(self, signal: dict[str, Any]) -> None:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO opportunity_signals (signal_id, kind, summary, created_at, payload_json) VALUES (?,?,?,?,?)",
                (signal.get("signal_id", signal.get("id", "")), signal.get("kind", ""), signal.get("summary", ""), signal.get("created_at", ""), json.dumps(signal, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()

    def read_all(self, *, limit: int = 200) -> list[dict[str, Any]]:
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM opportunity_signals ORDER BY created_at DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally:
            conn.close()


__all__ = ["AuditSqliteStore", "OpportunitySignalSqliteStore"]
