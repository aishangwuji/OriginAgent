"""JSONL-based persistence for BDI Desires with atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from filelock import FileLock
from loguru import logger

from OriginAgent.bdi.models import Desire, DesirePriority, DesireStatus, now_iso
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

    def _read_all(self) -> dict[str, Desire]:
        """Read all desires into a dict keyed by desire_id."""
        if not self._path.exists():
            return {}
        result: dict[str, Desire] = {}
        with FileLock(str(self._lock_path)):
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

    def _write_all(self, desires: dict[str, Desire]) -> None:
        """Atomically write all desires."""
        ensure_dir(self._dir)
        with FileLock(str(self._lock_path)):
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

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, desire: Desire) -> None:
        desires = self._read_all()
        if desire.desire_id in desires:
            raise ValueError(f"Desire {desire.desire_id} already exists")
        desires[desire.desire_id] = desire
        self._write_all(desires)

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
        desires = self._read_all()
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
        self._write_all(desires)
        return current

    def update_direct(self, desire: Desire) -> None:
        """Directly replace a desire (after external mutation)."""
        desires = self._read_all()
        if desire.desire_id not in desires:
            raise ValueError(f"Desire {desire.desire_id} does not exist")
        desires[desire.desire_id] = desire
        self._write_all(desires)

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
