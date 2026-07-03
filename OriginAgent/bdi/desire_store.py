"""JSONL-based persistence for BDI Desires with atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from filelock import FileLock
from loguru import logger

from OriginAgent.bdi.models import Desire, DesirePriority, DesireStatus, now_iso
from OriginAgent.storage.jsonl_migration import ReadModifyWriteMigrator
from OriginAgent.storage.sqlite_helpers import connect as sqlite_connect, ensure_schema
from OriginAgent.utils.helpers import ensure_dir


class DesireStore:
    """CRUD store for Desires backed by a JSONL file.

    Follows the same atomic-write pattern as agent/memory.py:
    temp-file + fsync + rename + dir-fsync.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self._dir = self.workspace / "memory" / "bdi"
        self._path = self._dir / "desires.jsonl"
        self._lock_path = self._dir / ".desires.lock"
        ensure_dir(self._dir)

    # ------------------------------------------------------------------
    # Atomic I/O
    # ------------------------------------------------------------------

    def _read_all_unlocked(self) -> dict[str, Desire]:
        """Read all desires WITHOUT locking (caller must hold the lock)."""
        if not self._path.exists():
            return {}
        result: dict[str, Desire] = {}
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = Desire.from_json(json.loads(line))
                    result[d.desire_id] = d
                except Exception:
                    logger.warning("BDI: skipping corrupt desire line")
        return result

    def _write_all_unlocked(self, desires: dict[str, Desire]) -> None:
        """Atomically write all desires WITHOUT locking (caller must hold the lock)."""
        ensure_dir(self._dir)
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(self._dir),
            delete=False,
            suffix=".tmp",
        )
        try:
            for d in desires.values():
                tmp.write(json.dumps(d.to_json(), ensure_ascii=False) + "\n")
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, str(self._path))
            # Directory fsync for durability
            try:
                dir_fd = os.open(str(self._dir), os.O_RDONLY)
                os.fsync(dir_fd)
                os.close(dir_fd)
            except OSError:
                pass
        except Exception:
            Path(tmp.name).unlink(missing_ok=True)
            raise

    def _read_all(self) -> dict[str, Desire]:
        """Read all desires into a dict keyed by desire_id (thread-safe)."""
        with FileLock(str(self._lock_path)):
            return self._read_all_unlocked()

    @contextmanager
    def _read_modify_write(self):
        """Context manager holding the lock across the full read-modify-write cycle."""
        with FileLock(str(self._lock_path)):
            desires = self._read_all_unlocked()
            yield desires
            self._write_all_unlocked(desires)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, desire: Desire) -> None:
        with self._read_modify_write() as desires:
            if desire.desire_id in desires:
                raise ValueError(f"Desire {desire.desire_id} already exists")
            desires[desire.desire_id] = desire

    def get(self, desire_id: str) -> Desire | None:
        return self._read_all().get(desire_id)

    def update(
        self,
        desire_id: str,
        *,
        status: DesireStatus | None = None,
        priority: DesirePriority | None = None,
        reasoning: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Desire | None:
        with self._read_modify_write() as desires:
            current = desires.get(desire_id)
            if current is None:
                return None

            if status is not None:
                current = current.transition_to(status, reasoning=reasoning)
            if priority is not None:
                current = current.__replace__(
                    priority=priority,
                    updated_at=now_iso(),
                    last_reasoning=reasoning or current.last_reasoning,
                )
            if metadata is not None:
                merged = {**current.metadata, **metadata}
                current = current.__replace__(metadata=merged, updated_at=now_iso())

            if current.evaluation_count == current.__class__(
                desire_id=current.desire_id,
                owner_id=current.owner_id,
                session_key=current.session_key,
                content=current.content,
                status=current.status,
                priority=current.priority,
            ).evaluation_count:
                current = current.with_evaluation(reasoning=reasoning)
            else:
                current = current.__replace__(
                    updated_at=now_iso(),
                    last_reasoning=reasoning or current.last_reasoning,
                )

            desires[desire_id] = current
            return current

    def update_direct(self, desire: Desire) -> None:
        """Directly replace a desire (after external mutation)."""
        with self._read_modify_write() as desires:
            if desire.desire_id not in desires:
                raise ValueError(f"Desire {desire.desire_id} does not exist")
            desires[desire.desire_id] = desire

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def list_all(self) -> list[Desire]:
        desires = self._read_all().values()
        return sorted(desires, key=lambda d: (-d.priority.value, d.created_at))

    def list_active(self) -> list[Desire]:
        return [d for d in self.list_all() if d.status == DesireStatus.ACTIVE]

    def list_deliberable(self) -> list[Desire]:
        return [d for d in self.list_all() if d.is_deliberable]

    def list_by_status(self, status: DesireStatus) -> list[Desire]:
        return [d for d in self.list_all() if d.status == status]

    def list_overdue(self) -> list[Desire]:
        return [d for d in self.list_all() if d.is_overdue]

    def count_by_status(self) -> dict[DesireStatus, int]:
        counts: dict[DesireStatus, int] = {s: 0 for s in DesireStatus}
        for d in self._read_all().values():
            counts[d.status] += 1
        return counts

    def find_by_source_foresight(self, foresight_id: str) -> list[Desire]:
        return [
            d for d in self.list_all()
            if d.source_foresight_id == foresight_id
        ]


class DesireStoreSqlite(ReadModifyWriteMigrator):
    """SQLite-backed desire store.

    Migrates from the JSONL desire file into a local SQLite database.
    Supports the same CRUD operations as the original DesireStore but
    uses SELECT / INSERT OR REPLACE semantics and individual-column access
    for indexed queries.
    """

    DDL = """
        CREATE TABLE IF NOT EXISTS desires (
            desire_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL DEFAULT '',
            session_key TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 50,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            deadline_at TEXT,
            satisfied_at TEXT,
            source_foresight_id TEXT,
            source_episode_id TEXT,
            source_cycle_id TEXT,
            evaluation_count INTEGER NOT NULL DEFAULT 0,
            last_reasoning TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_desires_status
            ON desires(status, priority);
    """

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        memory_dir = workspace / "memory" / "bdi"
        super().__init__(
            workspace=workspace,
            db_path=db_path or memory_dir / "desires.sqlite3",
            jsonl_path=memory_dir / "desires.jsonl",
        )

    # ------------------------------------------------------------------
    # Migrator contract
    # ------------------------------------------------------------------

    def table_ddl(self) -> str:
        return self.DDL

    def validate_line(self, line: dict[str, Any]) -> bool:
        return bool(line.get("desire_id"))

    def upsert_row(self, conn, line: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR REPLACE INTO desires
               (desire_id, owner_id, session_key, content, status, priority,
                created_at, updated_at, deadline_at, satisfied_at,
                source_foresight_id, source_episode_id, source_cycle_id,
                evaluation_count, last_reasoning, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                line.get("desire_id", ""),
                line.get("owner_id", ""),
                line.get("session_key", ""),
                line.get("content", ""),
                _normalize_status(line.get("status", "pending")),
                _normalize_priority(line.get("priority", 50)),
                line.get("created_at", ""),
                line.get("updated_at", ""),
                line.get("deadline_at"),
                line.get("satisfied_at"),
                line.get("source_foresight_id"),
                line.get("source_episode_id"),
                line.get("source_cycle_id"),
                line.get("evaluation_count", 0),
                line.get("last_reasoning", ""),
                json.dumps(line, ensure_ascii=False),
            ),
        )

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Create the desires table if it does not exist."""
        conn = sqlite_connect(self.db_path)
        try:
            ensure_schema(conn, self.DDL)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(self, desire_id: str) -> dict[str, Any] | None:
        """Return the raw dict for *desire_id* or None."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT payload_json FROM desires WHERE desire_id = ?",
                (desire_id,),
            ).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            conn.close()

    def add(self, line: dict[str, Any]) -> None:
        """Insert a new desire from *line*."""
        if not line.get("desire_id"):
            raise ValueError("desire_id is required")
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT 1 FROM desires WHERE desire_id = ?",
                    (line["desire_id"],),
                ).fetchone()
                if existing:
                    raise ValueError(f"Desire {line['desire_id']} already exists")
                self.upsert_row(conn, line)
        finally:
            conn.close()

    def update(
        self,
        desire_id: str,
        *,
        status: str | None = None,
        priority: int | None = None,
        last_reasoning: str = "",
    ) -> dict[str, Any] | None:
        """Update one or more fields on *desire_id* and return the updated row."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT payload_json FROM desires WHERE desire_id = ?",
                    (desire_id,),
                ).fetchone()
                if not row:
                    return None
                data = json.loads(row[0])
                if status is not None:
                    data["status"] = _normalize_status(status)
                if priority is not None:
                    data["priority"] = _normalize_priority(priority)
                if last_reasoning:
                    data["last_reasoning"] = last_reasoning
                from datetime import datetime, timezone
                data["updated_at"] = datetime.now(timezone.utc).isoformat()
                self.upsert_row(conn, data)
            return self.get(desire_id)
        finally:
            conn.close()

    def list_all(self) -> list[dict[str, Any]]:
        """Return all desires ordered by priority DESC, created_at ASC."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM desires ORDER BY priority DESC, created_at ASC"
            ).fetchall()
            return [json.loads(row[0]) for row in rows]
        finally:
            conn.close()

    def count_by_status(self) -> dict[str, int]:
        """Return counts grouped by status."""
        self._ensure_schema()
        conn = sqlite_connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM desires GROUP BY status"
            ).fetchall()
            return {row["status"]: row["cnt"] for row in rows}
        finally:
            conn.close()


# ------------------------------------------------------------------
# Internal normalisation helpers (avoid pulling in full Desire model)
# ------------------------------------------------------------------

_STATUS_NORMALIZE_MAP: dict[str, str] = {
    "pending": "pending",
    "active": "active",
    "suspended": "suspended",
    "satisfied": "satisfied",
    "cancelled": "cancelled",
}


def _normalize_status(raw: Any, default: str = "pending") -> str:
    value = str(raw).strip().lower() if raw is not None else default
    return _STATUS_NORMALIZE_MAP.get(value, default)


def _normalize_priority(raw: Any, default: int = 50) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    if v not in (10, 50, 80, 100):
        return default
    return v
