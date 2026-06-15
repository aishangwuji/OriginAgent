"""Governed memory candidate queue and consumer cursors."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock

from OriginAgent.utils.helpers import ensure_dir

MemoryCandidateKind = Literal["fact", "preference", "task_pattern", "constraint"]
_VALID_KINDS: tuple[MemoryCandidateKind, ...] = ("fact", "preference", "task_pattern", "constraint")


def _trim_text(value: Any, *, max_chars: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def _normalize_kind(value: Any) -> MemoryCandidateKind:
    kind = str(value or "").strip().lower()
    if kind not in _VALID_KINDS:
        raise ValueError(f"Unsupported memory candidate kind: {value!r}")
    return kind  # type: ignore[return-value]


@dataclass(frozen=True)
class MemoryCandidate:
    candidate_id: str
    kind: MemoryCandidateKind
    summary: str
    source_session_key: str
    source_refs: list[str] = field(default_factory=list)
    source_excerpt: str = ""
    confidence: float = 0.8
    sensitivity: str = "low"
    scope: str = "user"
    owner_id: str = "user"
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "MemoryCandidate":
        return cls(
            candidate_id=str(payload.get("candidate_id") or ""),
            kind=_normalize_kind(payload.get("kind") or "fact"),
            summary=str(payload.get("summary") or ""),
            source_session_key=str(payload.get("source_session_key") or ""),
            source_refs=[str(item) for item in payload.get("source_refs", []) if str(item or "").strip()],
            source_excerpt=str(payload.get("source_excerpt") or ""),
            confidence=float(payload.get("confidence", 0.8) or 0.8),
            sensitivity=str(payload.get("sensitivity") or "low"),
            scope=str(payload.get("scope") or "user"),
            owner_id=str(payload.get("owner_id") or "user"),
            created_at=str(payload.get("created_at") or ""),
            metadata=dict(payload.get("metadata") or {}),
        )


class GovernedMemoryWriter:
    """Append-only memory candidate queue with consumer cursors."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.memory_dir = ensure_dir(self.workspace / "memory")
        self.queue_path = self.memory_dir / "memory_candidates.jsonl"
        self._state_dir = ensure_dir(self.memory_dir / "governance")
        self._lock_path = self._state_dir / ".memory_candidates.lock"

    def append(self, candidate: MemoryCandidate) -> MemoryCandidate:
        normalized = self._normalize_candidate(candidate)
        with self._locked():
            existing = self._read_all_unlocked()
            if any(item.candidate_id == normalized.candidate_id for item in existing):
                return normalized
            self.queue_path.parent.mkdir(parents=True, exist_ok=True)
            with self.queue_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(normalized.to_json(), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._fsync_parent(self.queue_path)
        return normalized

    def read_all(
        self,
        *,
        kinds: tuple[str, ...] | None = None,
        owner_id: str | None = None,
        limit: int | None = None,
    ) -> list[MemoryCandidate]:
        items = self._read_all_unlocked()
        filtered = self._filter_candidates(items, kinds=kinds, owner_id=owner_id)
        if limit is not None and limit > 0:
            return filtered[-limit:]
        return filtered

    def read_pending_for_consumer(
        self,
        consumer: str,
        *,
        kinds: tuple[str, ...] | None = None,
        owner_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[list[MemoryCandidate], int]:
        cursor = self.read_consumer_cursor(consumer)
        pending: list[MemoryCandidate] = []
        end_cursor = cursor
        if not self.queue_path.exists():
            return pending, end_cursor
        try:
            with self.queue_path.open("r", encoding="utf-8") as handle:
                for line_no, raw in enumerate(handle, start=1):
                    if line_no <= cursor:
                        continue
                    line = raw.strip()
                    if not line:
                        end_cursor = line_no
                        continue
                    with suppress(json.JSONDecodeError, TypeError, ValueError):
                        candidate = MemoryCandidate.from_json(json.loads(line))
                        if self._matches_filters(candidate, kinds=kinds, owner_id=owner_id):
                            pending.append(candidate)
                            if limit is not None and limit > 0 and len(pending) >= limit:
                                end_cursor = line_no
                                break
                    end_cursor = line_no
        except OSError:
            return [], cursor
        return pending, end_cursor

    def summarize_queue(
        self,
        *,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        candidates = self.read_all(owner_id=owner_id)
        by_kind: dict[str, int] = {}
        last_candidate_at: str | None = None
        for candidate in candidates:
            by_kind[candidate.kind] = by_kind.get(candidate.kind, 0) + 1
            created_at = str(candidate.created_at or "").strip()
            if created_at and (last_candidate_at is None or created_at > last_candidate_at):
                last_candidate_at = created_at
        return {
            "total": len(candidates),
            "by_kind": by_kind,
            "last_candidate_at": last_candidate_at,
        }

    def pending_summary_for_consumer(
        self,
        consumer: str,
        *,
        kinds: tuple[str, ...] | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        cursor_before = self.read_consumer_cursor(consumer)
        pending, _end_cursor = self.read_pending_for_consumer(
            consumer,
            kinds=kinds,
            owner_id=owner_id,
        )
        by_kind: dict[str, int] = {}
        oldest_pending_at: str | None = None
        newest_pending_at: str | None = None
        for candidate in pending:
            by_kind[candidate.kind] = by_kind.get(candidate.kind, 0) + 1
            created_at = str(candidate.created_at or "").strip()
            if not created_at:
                continue
            if oldest_pending_at is None or created_at < oldest_pending_at:
                oldest_pending_at = created_at
            if newest_pending_at is None or created_at > newest_pending_at:
                newest_pending_at = created_at
        return {
            "consumer": str(consumer or "").strip() or "default",
            "cursor": cursor_before,
            "pending_count": len(pending),
            "by_kind": by_kind,
            "oldest_pending_at": oldest_pending_at,
            "newest_pending_at": newest_pending_at,
        }

    def read_consumer_cursor(self, consumer: str) -> int:
        path = self._consumer_cursor_path(consumer)
        with suppress(OSError, ValueError):
            return max(0, int(path.read_text(encoding="utf-8").strip()))
        return 0

    def advance_consumer_cursor(self, consumer: str, cursor: int) -> int:
        cursor = max(0, int(cursor or 0))
        path = self._consumer_cursor_path(consumer)
        with self._locked():
            current = self.read_consumer_cursor(consumer)
            if cursor <= current:
                return current
            self._write_text_atomic(path, str(cursor))
        return cursor

    def _normalize_candidate(self, candidate: MemoryCandidate) -> MemoryCandidate:
        kind = _normalize_kind(candidate.kind)
        summary = _trim_text(candidate.summary, max_chars=240)
        if not summary:
            raise ValueError("Memory candidate summary cannot be empty.")
        source_session_key = _trim_text(candidate.source_session_key, max_chars=200) or "unknown"
        source_refs = []
        seen_refs: set[str] = set()
        for value in list(candidate.source_refs or [])[:8]:
            ref = _trim_text(value, max_chars=160)
            if not ref or ref in seen_refs:
                continue
            seen_refs.add(ref)
            source_refs.append(ref)
        source_excerpt = _trim_text(candidate.source_excerpt or summary, max_chars=320)
        scope = _trim_text(candidate.scope or "user", max_chars=160) or "user"
        owner_id = _trim_text(candidate.owner_id or "user", max_chars=120) or "user"
        sensitivity = _trim_text(candidate.sensitivity or "low", max_chars=40) or "low"
        confidence = max(0.0, min(float(candidate.confidence or 0.0), 1.0))
        created_at = _trim_text(candidate.created_at, max_chars=64)
        metadata = dict(candidate.metadata or {})
        candidate_id = _trim_text(candidate.candidate_id, max_chars=120)
        if not candidate_id:
            candidate_id = self._candidate_id_for(
                kind=kind,
                summary=summary,
                source_session_key=source_session_key,
                scope=scope,
                owner_id=owner_id,
            )
        return MemoryCandidate(
            candidate_id=candidate_id,
            kind=kind,
            summary=summary,
            source_session_key=source_session_key,
            source_refs=source_refs,
            source_excerpt=source_excerpt,
            confidence=confidence,
            sensitivity=sensitivity,
            scope=scope,
            owner_id=owner_id,
            created_at=created_at,
            metadata=metadata,
        )

    def _read_all_unlocked(self) -> list[MemoryCandidate]:
        rows: list[MemoryCandidate] = []
        if not self.queue_path.exists():
            return rows
        try:
            with self.queue_path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    line = raw.strip()
                    if not line:
                        continue
                    with suppress(json.JSONDecodeError, TypeError, ValueError):
                        rows.append(MemoryCandidate.from_json(json.loads(line)))
        except OSError:
            return []
        return rows

    @staticmethod
    def _filter_candidates(
        items: list[MemoryCandidate],
        *,
        kinds: tuple[str, ...] | None,
        owner_id: str | None,
    ) -> list[MemoryCandidate]:
        return [
            item
            for item in items
            if GovernedMemoryWriter._matches_filters(item, kinds=kinds, owner_id=owner_id)
        ]

    @staticmethod
    def _matches_filters(
        item: MemoryCandidate,
        *,
        kinds: tuple[str, ...] | None,
        owner_id: str | None,
    ) -> bool:
        if kinds:
            normalized_kinds = {str(kind or "").strip().lower() for kind in kinds}
            if item.kind not in normalized_kinds:
                return False
        if owner_id and str(item.owner_id or "").strip() != str(owner_id).strip():
            return False
        return True

    @staticmethod
    def _candidate_id_for(
        *,
        kind: MemoryCandidateKind,
        summary: str,
        source_session_key: str,
        scope: str,
        owner_id: str,
    ) -> str:
        payload = "|".join([kind, summary.casefold(), source_session_key, scope, owner_id])
        return f"memcand_{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]}"

    def _consumer_cursor_path(self, consumer: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(consumer or "default"))
        return self._state_dir / f"{safe}.cursor"

    def _locked(self) -> FileLock:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self._lock_path))

    @staticmethod
    def _fsync_parent(path: Path) -> None:
        with suppress(PermissionError, OSError):
            fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _write_text_atomic(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            self._fsync_parent(path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
