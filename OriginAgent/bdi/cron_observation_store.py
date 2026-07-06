"""SQLite-backed store for cron-desire links and delivery outcomes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect
from OriginAgent.storage.sqlite_helpers import ensure_schema

DDL = """
    CREATE TABLE IF NOT EXISTS cron_desire_links (
        cron_job_id         TEXT PRIMARY KEY,
        desire_id           TEXT NOT NULL,
        session_key         TEXT NOT NULL DEFAULT '',
        owner_id            TEXT NOT NULL DEFAULT '',
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        last_delivery_at    TEXT,
        last_delivery_status TEXT,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        cron_disabled       INTEGER NOT NULL DEFAULT 0,
        payload_json        TEXT NOT NULL DEFAULT '{}'
    ) STRICT;

    CREATE TABLE IF NOT EXISTS cron_delivery_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        cron_job_id TEXT NOT NULL,
        attempted_at TEXT NOT NULL DEFAULT (datetime('now')),
        status      TEXT NOT NULL DEFAULT '',
        error       TEXT NOT NULL DEFAULT '',
        duration_ms INTEGER NOT NULL DEFAULT 0
    ) STRICT;
    CREATE INDEX IF NOT EXISTS idx_cdl_job ON cron_delivery_log(cron_job_id, attempted_at);
"""


class CronObservationStore:
    """SQLite-backed store for cron-desire links and delivery log.

    Two tables:
    - ``cron_desire_links`` — maps cron job IDs to desire IDs
    - ``cron_delivery_log`` — append-only delivery attempt log
    """

    def __init__(self, workspace: Path) -> None:
        d = Path(workspace) / "memory" / "bdi"
        self.db_path = d / "cron_observations.sqlite3"

    # ── Schema management ─────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        conn = sqlite_connect(self.db_path)
        try:
            ensure_schema(conn, DDL)
        finally:
            conn.close()

    # ── Links CRUD ────────────────────────────────────────────────────

    def save_link(
        self,
        cron_job_id: str,
        desire_id: str,
        *,
        session_key: str = "",
        owner_id: str = "",
    ) -> None:
        """Upsert a cron-desire link."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO cron_desire_links
                   (cron_job_id, desire_id, session_key, owner_id, created_at,
                    last_delivery_at, last_delivery_status, consecutive_failures,
                    cron_disabled, payload_json)
                   VALUES (?, ?, ?, ?, datetime('now'), NULL, NULL, 0, 0, '{}')""",
                (cron_job_id, desire_id, session_key, owner_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_link_by_job_id(self, cron_job_id: str) -> dict[str, Any] | None:
        """Return the link dict for *cron_job_id*, or ``None``."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM cron_desire_links WHERE cron_job_id = ?",
                (cron_job_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_link_by_desire_id(self, desire_id: str) -> dict[str, Any] | None:
        """Return the link dict for *desire_id*, or ``None``."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM cron_desire_links WHERE desire_id = ?",
                (desire_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_failing_links(self) -> list[dict[str, Any]]:
        """Return links with ``consecutive_failures >= 2``."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM cron_desire_links WHERE consecutive_failures >= 2 AND cron_disabled = 0"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_active_links(self) -> list[dict[str, Any]]:
        """Return all active links (not disabled)."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM cron_desire_links WHERE cron_disabled = 0 ORDER BY created_at"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def set_cron_disabled(self, cron_job_id: str, disabled: bool = True) -> bool:
        """Mark a cron job as disabled in the link table.

        Returns ``True`` if a row was updated.
        """
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            cursor = conn.execute(
                "UPDATE cron_desire_links SET cron_disabled = ? WHERE cron_job_id = ?",
                (1 if disabled else 0, cron_job_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    # ── Delivery log ──────────────────────────────────────────────────

    def record_delivery(
        self,
        cron_job_id: str,
        *,
        status: str,
        error: str = "",
        duration_ms: int = 0,
    ) -> None:
        """Append a delivery attempt to the log and update the link counter."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO cron_delivery_log (cron_job_id, status, error, duration_ms) VALUES (?, ?, ?, ?)",
                (cron_job_id, status, error, duration_ms),
            )
            if status == "ok":
                conn.execute(
                    "UPDATE cron_desire_links SET last_delivery_at = datetime('now'), last_delivery_status = 'ok', consecutive_failures = 0 WHERE cron_job_id = ?",
                    (cron_job_id,),
                )
            else:
                conn.execute(
                    """UPDATE cron_desire_links
                       SET last_delivery_at = datetime('now'), last_delivery_status = 'error',
                           consecutive_failures = consecutive_failures + 1
                       WHERE cron_job_id = ?""",
                    (cron_job_id,),
                )
            conn.commit()
        finally:
            conn.close()

    def delivery_log(self, cron_job_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent delivery log entries for *cron_job_id*."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM cron_delivery_log WHERE cron_job_id = ? ORDER BY attempted_at DESC LIMIT ?",
                (cron_job_id, max(1, limit)),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Stats ─────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Aggregate statistics for observation data."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            total = conn.execute("SELECT COUNT(*) AS c FROM cron_desire_links").fetchone()["c"]
            active = conn.execute("SELECT COUNT(*) AS c FROM cron_desire_links WHERE cron_disabled = 0").fetchone()["c"]
            failing = conn.execute("SELECT COUNT(*) AS c FROM cron_desire_links WHERE consecutive_failures >= 2 AND cron_disabled = 0").fetchone()["c"]
            return {"total_links": total, "active_links": active, "failing_links": failing}
        finally:
            conn.close()


__all__ = ["CronObservationStore"]
