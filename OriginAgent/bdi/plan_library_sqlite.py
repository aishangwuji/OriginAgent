"""SQLite-backed PlanLibrary store.

Replaces the JSONL portion of PlanLibrary with individual row upserts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OriginAgent.storage.jsonl_migration import ReadModifyWriteMigrator
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect
from OriginAgent.storage.sqlite_helpers import ensure_schema


class PlanLibrarySqlite(ReadModifyWriteMigrator):
    """SQLite-backed plan library store.

    Each plan is a row; upserts are ``INSERT OR REPLACE`` by plan_id.
    """

    DDL = """
        CREATE TABLE IF NOT EXISTS plans (
            plan_id            TEXT PRIMARY KEY,
            keywords_json      TEXT NOT NULL DEFAULT '[]',
            action             TEXT NOT NULL DEFAULT '',
            scope              TEXT NOT NULL DEFAULT 'session',
            payload_template_json TEXT NOT NULL DEFAULT '{}',
            description        TEXT NOT NULL DEFAULT '',
            hit_count          INTEGER NOT NULL DEFAULT 0,
            last_used_at       TEXT NOT NULL DEFAULT '',
            created_at         TEXT NOT NULL DEFAULT (datetime('now')),
            source_desire_id   TEXT,
            source_agent_case_id TEXT,
            payload_json       TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_plans_hit
            ON plans(hit_count DESC);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        d = Path(workspace) / "memory" / "bdi"
        super().__init__(
            workspace=workspace,
            db_path=db_path or d / "plans.sqlite3",
            jsonl_path=d / "plans.jsonl",
        )

    # ── Migrator contract ────────────────────────────────────────

    def table_ddl(self) -> str:
        return self.DDL

    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("plan_id"))

    def upsert_row(self, conn: Any, line: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR REPLACE INTO plans
               (plan_id, keywords_json, action, scope,
                payload_template_json, description, hit_count,
                last_used_at, created_at, source_desire_id,
                source_agent_case_id, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                line.get("plan_id", ""),
                json.dumps(line.get("keywords", []), ensure_ascii=False),
                line.get("action", ""),
                line.get("scope", "session"),
                json.dumps(line.get("payload_template", {}), ensure_ascii=False),
                line.get("description", ""),
                line.get("hit_count", 0),
                line.get("last_used_at", ""),
                line.get("created_at", ""),
                line.get("source_desire_id"),
                line.get("source_agent_case_id"),
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

    # ── Query API (mirrors PlanLibrary public methods) ────────────

    def list_plans(self) -> list[dict[str, Any]]:
        """Return all plans ordered by hit_count DESC."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM plans ORDER BY hit_count DESC"
            ).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        finally:
            conn.close()

    def get(self, plan_id: str) -> dict[str, Any] | None:
        """Return a single plan by *plan_id*, or ``None``."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT payload_json FROM plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            return json.loads(row["payload_json"]) if row else None
        finally:
            conn.close()

    def add(self, plan: Any) -> dict[str, Any]:
        """Upsert a plan (dict or PlanTemplate) and return it."""
        data = plan.to_json() if hasattr(plan, "to_json") else dict(plan)
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            self.upsert_row(conn, data)
            conn.commit()
        finally:
            conn.close()
        return plan

    def delete(self, plan_id: str) -> bool:
        """Delete by *plan_id*. Returns ``True`` if a row was removed."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            cursor = conn.execute("DELETE FROM plans WHERE plan_id = ?", (plan_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def increment_hit(self, plan_id: str) -> bool:
        """Increment hit_count for *plan_id*. Returns ``True`` if found."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            cursor = conn.execute(
                "UPDATE plans SET hit_count = hit_count + 1, last_used_at = datetime('now') WHERE plan_id = ?",
                (plan_id,),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


__all__ = ["PlanLibrarySqlite"]
