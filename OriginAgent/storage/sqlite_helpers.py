"""Shared SQLite helpers for OriginAgent storage backends."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def connect(db_path: Path, *, wal: bool = True, timeout_ms: int = 10000) -> sqlite3.Connection:
    """Open a SQLite connection with OriginAgent-standard settings."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_schema(conn: sqlite3.Connection, ddl: str) -> None:
    """Execute DDL if tables don't exist. Idempotent."""
    conn.executescript(ddl)
    conn.commit()


def atomic_write(conn: sqlite3.Connection, fn, *args: Any, **kwargs: Any) -> Any:
    """Execute *fn(conn, *args, **kwargs)* inside a BEGIN/COMMIT transaction."""
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        return fn(conn, *args, **kwargs)
