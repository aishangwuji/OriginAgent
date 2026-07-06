"""ThoughtSubstrate — lifecycle manager for ThoughtFrame open/close/persist.

Manages a per-session "open" (current) Thinking Frame in memory, and persists
closed frames plus the derived ThoughtJournalEntry to append-only JSONL files
under ``workspace / memory / thought_substrate /``.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from OriginAgent.agent.meta_cognition_models import (
    ThoughtJournalEntry,
    _normalize_retention_hint,
    _normalize_str_list,
    _utcnow_iso,
)
from OriginAgent.agent.meta_cognition_redact import redact_meta_text
from OriginAgent.agent.thought_substrate_models import ThoughtFrame
from OriginAgent.utils.helpers import ensure_dir

# ── file layout ──────────────────────────────────────────────────────────────

_SUBSTRATE_DIR = Path("memory") / "thought_substrate"
_FRAMES_FILE = "frames.jsonl"
_JOURNALS_FILE = "journals.jsonl"
_RECENT_SCAN_LIMIT = 200
_DEFAULT_MAX_FRAMES_PER_SESSION = 500
_DEFAULT_SAMPLING_RATE = 1.0


def _utcnow() -> str:
    return _utcnow_iso()


def _new_frame_id() -> str:
    return f"thought_frame_{uuid.uuid4().hex}"


def _new_entry_id() -> str:
    return f"thought_entry_{uuid.uuid4().hex}"


# ── storage class ────────────────────────────────────────────────────────────


class ThoughtSubstrate:
    """Per-session open-frame manager with append-only JSONL persistence.

    Thread-safe: all state mutations are guarded by a single lock.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        max_frames_per_session: int = _DEFAULT_MAX_FRAMES_PER_SESSION,
        sampling_rate: float = _DEFAULT_SAMPLING_RATE,
        audit: Any = None,
        sqlite_frames: Any = None,
        sqlite_journals: Any = None,
    ) -> None:
        root = Path(workspace) / _SUBSTRATE_DIR
        self._frames_path = root / _FRAMES_FILE
        self._journals_path = root / _JOURNALS_FILE
        self._max_frames = max(1, int(max_frames_per_session))
        self._sampling_rate = max(0.0, min(1.0, float(sampling_rate)))
        self._audit = audit  # optional JsonlMetaCognitionAuditLedger
        self._sqlite_frames = sqlite_frames  # optional ThoughtFramesSqlite
        self._sqlite_journals = sqlite_journals  # optional ThoughtJournalsSqlite

        self._open_frames: dict[str, ThoughtFrame] = {}
        self._lock = threading.Lock()

    # ── public API ───────────────────────────────────────────────────────────

    def open_frame(
        self,
        session_key: str,
        *,
        trigger_refs: list[str] | None = None,
        observation_summary: str = "",
        active_goal: str = "",
        candidate_hypotheses: list[str] | None = None,
        intended_strategy: str = "",
        verification_needs: list[str] | None = None,
        simulation_requests: list[str] | None = None,
        confidence: float = 0.0,
        uncertainty_flags: list[str] | None = None,
    ) -> ThoughtFrame:
        """Open a new ThoughtFrame for *session_key*.

        If a frame is already open for this session it is automatically closed
        first (a new ``ThoughtJournalEntry`` is produced and persisted).
        """
        with self._lock:
            # auto-close any existing open frame
            existing = self._open_frames.pop(session_key, None)
            if existing is not None:
                self._persist_frame_and_journal(
                    existing,
                    enrichment=None,
                )

            frame = ThoughtFrame(
                frame_id=_new_frame_id(),
                session_key=session_key,
                trigger_refs=trigger_refs or [],
                observation_summary=observation_summary,
                active_goal=active_goal,
                candidate_hypotheses=candidate_hypotheses or [],
                intended_strategy=intended_strategy,
                verification_needs=verification_needs or [],
                simulation_requests=simulation_requests or [],
                confidence=confidence,
                uncertainty_flags=uncertainty_flags or [],
                created_at=_utcnow(),
            )
            self._open_frames[session_key] = frame
            return frame

    def get_open_frame(self, session_key: str) -> ThoughtFrame | None:
        """Return the currently open frame for *session_key*, or ``None``."""
        with self._lock:
            return self._open_frames.get(session_key)

    def close_frame(
        self,
        session_key: str,
        *,
        enrichment: dict[str, Any] | None = None,
    ) -> ThoughtJournalEntry | None:
        """Close the open frame for *session_key* and persist it.

        *enrichment* can carry fields from the LLM reflection pass that are
        merged into the produced ``ThoughtJournalEntry``:

        - ``expected_outcome`` / ``actual_outcome`` / ``mismatch_summary``
        - ``strategy_summary`` / ``assumptions``
        - ``retention_hint`` (one of discard/short/review/candidate)
        - ``evidence_refs``
        - ``suggested_next_action``
        """
        with self._lock:
            frame = self._open_frames.pop(session_key, None)
            if frame is None:
                return None
            return self._persist_frame_and_journal(frame, enrichment=enrichment)

    def recent_frames(
        self,
        session_key: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return the most recent frame rows, optionally filtered by session."""
        if self._sqlite_frames is not None:
            if session_key is not None:
                return self._sqlite_frames.by_session(session_key, limit=limit)[:limit]
            return self._sqlite_frames.recent(limit=limit)
        return self._recent(self._frames_path, session_key=session_key, limit=limit)

    def recent_journals(
        self,
        session_key: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return the most recent journal rows, optionally filtered by session."""
        if self._sqlite_journals is not None:
            return self._sqlite_journals.recent(limit=limit)
        return self._recent(self._journals_path, session_key=session_key, limit=limit)

    def summary(self, *, limit: int = 20) -> dict[str, Any]:
        frames = self.recent_frames(limit=limit)
        journals = self.recent_journals(limit=limit)
        return {
            "open_frame_count": len(self._open_frames),
            "total_frame_count": len(frames),
            "total_journal_count": len(journals),
            "latest_frame": frames[-1] if frames else None,
            "latest_journal": journals[-1] if journals else None,
            "open_sessions": list(self._open_frames.keys()),
        }

    def enforce_retention_policy(self) -> None:
        """Trim frames.jsonl when any session exceeds the per-session limit.

        This is a safe, best-effort scan that reads, filters, and atomically
        rewrites the file.
        """
        self._enforce_retention(self._frames_path)
        self._enforce_retention(self._journals_path)

    # ── internals ────────────────────────────────────────────────────────────

    def _build_journal_from_frame(
        self,
        frame: ThoughtFrame,
        enrichment: dict[str, Any] | None,
    ) -> ThoughtJournalEntry:
        enrichment = enrichment or {}

        # merge enrichment fields
        strategy = enrichment.get("strategy_summary") or frame.intended_strategy
        expected = enrichment.get("expected_outcome", "")
        actual = enrichment.get("actual_outcome", "")
        mismatch = enrichment.get("mismatch_summary", "")
        suggested = enrichment.get("suggested_next_action", "")
        retention = _normalize_retention_hint(enrichment.get("retention_hint", "discard"))
        extra_evidence = _normalize_str_list(enrichment.get("evidence_refs"), limit=8, max_chars=160)
        extra_assumptions = _normalize_str_list(enrichment.get("assumptions"), limit=8, max_chars=160)

        combined_evidence: list[str] = []
        seen: set[str] = set()
        for ref in list(frame.trigger_refs) + extra_evidence:
            if ref and ref not in seen:
                combined_evidence.append(ref)
                seen.add(ref)

        combined_assumptions: list[str] = []
        seen_a: set[str] = set()
        for ref in extra_assumptions:
            if ref and ref not in seen_a:
                combined_assumptions.append(ref)
                seen_a.add(ref)

        return ThoughtJournalEntry(
            entry_id=_new_entry_id(),
            session_key=frame.session_key,
            frame_id=frame.frame_id,
            trigger_type="",
            task_reference="",
            strategy_summary=redact_meta_text(strategy, max_chars=240),
            assumptions=combined_assumptions,
            event_refs=[],
            evidence_refs=combined_evidence,
            confidence=frame.confidence,
            expected_outcome=redact_meta_text(expected, max_chars=240),
            actual_outcome=redact_meta_text(actual, max_chars=240),
            mismatch_summary=redact_meta_text(mismatch, max_chars=240),
            suggested_next_action=redact_meta_text(suggested, max_chars=240),
            summary=redact_meta_text(frame.observation_summary, max_chars=240),
            retention_hint=retention,
            payload={"frame_id": frame.frame_id, "session_key": frame.session_key},
        )

    def _persist_frame_and_journal(
        self,
        frame: ThoughtFrame,
        enrichment: dict[str, Any] | None,
    ) -> ThoughtJournalEntry:
        journal = self._build_journal_from_frame(frame, enrichment=enrichment)
        frame_json = frame.to_json()
        journal_json = journal.to_json()
        # Primary: SQLite
        if self._sqlite_frames is not None:
            try:
                self._sqlite_frames.append(frame_json)
            except Exception:
                logger.opt(exception=True).warning("thought_substrate: sqlite frames append failed, falling back to JSONL")
                self._append(self._frames_path, frame_json)
                self._append(self._journals_path, journal_json)
                return journal
        else:
            self._append(self._frames_path, frame_json)
        if self._sqlite_journals is not None:
            try:
                self._sqlite_journals.append(journal_json)
            except Exception:
                logger.opt(exception=True).warning("thought_substrate: sqlite journals append failed, falling back to JSONL")
                self._append(self._journals_path, journal_json)
        else:
            self._append(self._journals_path, journal_json)
        # mirror to the existing audit ledger when available
        if self._audit is not None:
            try:
                self._audit.append_journal(journal)
            except Exception:
                logger.opt(exception=True).warning("thought_substrate: audit append_journal failed")
        return journal

    def _append(self, path: Path, payload: dict[str, Any]) -> None:
        with self._lock:
            ensure_dir(path.parent)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

    @staticmethod
    def _recent(
        path: Path,
        *,
        session_key: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        items: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        if session_key is None or payload.get("session_key") == session_key:
                            items.append(payload)
        except Exception:
            return []
        if limit <= 0:
            return items
        return items[-limit:]

    def _enforce_retention(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            with self._lock:
                with path.open("r", encoding="utf-8") as handle:
                    all_rows = [json.loads(line) for line in handle if line.strip()]
            if len(all_rows) <= self._max_frames:
                return
            # per-session LRU: keep the most recent N per session_key
            by_session: dict[str, list[dict[str, Any]]] = {}
            for row in all_rows:
                sk = str(row.get("session_key") or "")
                by_session.setdefault(sk, []).append(row)
            kept: list[dict[str, Any]] = []
            for rows in by_session.values():
                kept.extend(rows[-self._max_frames :])
            if len(kept) >= len(all_rows):
                return
            with self._lock:
                ensure_dir(path.parent)
                tmp = path.with_suffix(".jsonl.tmp")
                with tmp.open("w", encoding="utf-8") as handle:
                    for row in kept:
                        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
                        handle.write("\n")
                        handle.flush()
                    os.fsync(handle.fileno())
                tmp.replace(path)
        except Exception:
            logger.opt(exception=True).warning("thought_substrate: enforce_retention error")
