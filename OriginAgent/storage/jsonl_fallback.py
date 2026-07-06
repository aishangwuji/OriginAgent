"""Shared helpers for JSONL fallback during SQLite cutover.

When SQLite is active:
- ``locked()`` returns a no-op context manager (SQLite handles concurrency)
- ``write_jsonl_cold()`` writes JSONL with buffered I/O only (no ``os.fsync``)
  so it doesn't block on disk synchronization
- ``should_write_jsonl()`` checks the config flag

When SQLite is **not** active (CLI commands, tests without factory):
- ``locked()`` returns a ``FileLock``
- Write helpers are not called (JSONL-only path handles its own fsync)
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock


def locked(sqlite_store: Any, lock_path: Path) -> contextlib.AbstractContextManager:
    """Return a no-op context manager when *sqlite_store* is set, else a FileLock.

    Usage::

        with locked(self._sqlite, self._lock_path) as _:
            # critical section — either SQLite-managed or FileLock-protected
    """
    if sqlite_store is not None:
        return contextlib.nullcontext()
    return FileLock(str(lock_path))


def write_jsonl_cold(
    path: Path,
    records: list[Any],
    *,
    to_dict: Callable[[Any], dict[str, Any]] | None = None,
) -> int:
    """Append *records* to *path* as JSONL with buffered I/O (no ``os.fsync``).

    This is a *cold backup* write — the data is handed to the kernel's page
    cache but NOT forced to durable storage.  If the machine crashes before
    the page cache is flushed, the backup may lag behind the SQLite primary.

    Returns the number of records written.
    """
    if not records:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as handle:
        for rec in records:
            data = rec.to_dict() if to_dict is not None and hasattr(rec, "to_dict") else (rec if isinstance(rec, dict) else {})
            handle.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
        handle.flush()  # hand to kernel page cache — no fsync
    return count


def should_write_jsonl(jsonl_fallback_enabled: bool, sqlite_store: Any) -> bool:
    """Return ``True`` when SQLite is active AND the config flag allows JSONL backup writes."""
    return bool(sqlite_store is not None and jsonl_fallback_enabled)


__all__ = ["locked", "write_jsonl_cold", "should_write_jsonl"]
