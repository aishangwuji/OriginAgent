"""Append-only audit helpers for backend cognition events and decisions."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from loguru import logger

from OriginAgent.agent.cognitive_events import CognitiveDecision, CognitiveEvent
from OriginAgent.utils.helpers import ensure_dir

_RECENT_SCAN_LIMIT = 200


class JsonlCognitiveAuditLedger:
    """Append-only ledger for cognitive events and bounded policy decisions."""

    def __init__(
        self,
        workspace: Path,
        *,
        sqlite_events: Any = None,
        sqlite_decisions: Any = None,
    ):
        root = Path(workspace) / "memory" / "cognitive"
        self._events_path = root / "events.jsonl"
        self._decisions_path = root / "decisions.jsonl"
        self._lock = threading.Lock()
        self._sqlite_events = sqlite_events
        self._sqlite_decisions = sqlite_decisions

    @staticmethod
    def _sqlite_append(store: Any, payload: dict[str, Any], label: str) -> None:
        if store is not None:
            try:
                store.append(payload)
            except Exception:
                logger.opt(exception=True).warning("cognitive_audit: sqlite {} append failed", label)

    def append_event(self, event: CognitiveEvent) -> None:
        payload = event.to_json()
        if self._sqlite_events is not None:
            try:
                self._sqlite_events.append(payload)
            except Exception:
                logger.opt(exception=True).warning("cognitive_audit: sqlite event append failed")
                self._jsonl_append(self._events_path, payload)
                return
        self._jsonl_append(self._events_path, payload)

    def append_decision(self, decision: CognitiveDecision) -> None:
        payload = decision.to_json()
        if self._sqlite_decisions is not None:
            try:
                self._sqlite_decisions.append(payload)
            except Exception:
                logger.opt(exception=True).warning("cognitive_audit: sqlite decision append failed")
                self._jsonl_append(self._decisions_path, payload)
                return
        self._jsonl_append(self._decisions_path, payload)

    def _recent_sqlite(self, store: Any, fallback_path: Path, limit: int) -> list[dict[str, Any]]:
        if store is not None:
            try:
                return store.recent(limit=limit)
            except Exception:
                pass
        return self._recent(fallback_path, limit=limit)

    def recent_events(self, limit: int = _RECENT_SCAN_LIMIT) -> list[dict[str, Any]]:
        return self._recent_sqlite(self._sqlite_events, self._events_path, limit)

    def recent_decisions(self, limit: int = _RECENT_SCAN_LIMIT) -> list[dict[str, Any]]:
        return self._recent_sqlite(self._sqlite_decisions, self._decisions_path, limit)

    def summary(self, *, limit: int = 20) -> dict[str, Any]:
        events = self.recent_events(limit=limit)
        decisions = self.recent_decisions(limit=limit)
        suppression_counts: dict[str, int] = {}
        outcome_counts: dict[str, int] = {}
        for record in decisions:
            outcome = str(record.get("outcome") or "unknown")
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
            reason = str(record.get("suppression_reason") or "").strip()
            if reason:
                suppression_counts[reason] = suppression_counts.get(reason, 0) + 1
        return {
            "event_count": len(events),
            "decision_count": len(decisions),
            "latest_event": events[-1] if events else None,
            "latest_decision": decisions[-1] if decisions else None,
            "recent_event_types": [record.get("event_type") for record in events],
            "recent_outcomes": [record.get("outcome") for record in decisions],
            "outcome_counts": outcome_counts,
            "suppression_reason_counts": suppression_counts,
        }

    def _jsonl_append(self, path: Path, payload: dict[str, Any]) -> None:
        with self._lock:
            ensure_dir(path.parent)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

    @staticmethod
    def _recent(path: Path, *, limit: int) -> list[dict[str, Any]]:
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
                        items.append(payload)
        except Exception:
            return []
        if limit <= 0:
            return items
        return items[-limit:]
