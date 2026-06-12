"""Append-only audit helpers for sidecar meta-cognition triggers and decisions."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from OriginAgent.agent.meta_cognition_models import MetaTrigger, RecordTriggerResult
from OriginAgent.utils.helpers import ensure_dir

_RECENT_SCAN_LIMIT = 200


class JsonlMetaCognitionAuditLedger:
    """Append-only ledger for meta-cognition triggers and runtime decisions."""

    def __init__(self, workspace: Path):
        root = Path(workspace) / "memory" / "meta_cognition"
        self._triggers_path = root / "triggers.jsonl"
        self._decisions_path = root / "decisions.jsonl"
        self._lock = threading.Lock()

    def append_trigger(self, trigger: MetaTrigger) -> None:
        self._append(self._triggers_path, trigger.to_json())

    def append_runtime_decision(
        self,
        *,
        trigger: MetaTrigger,
        result: RecordTriggerResult,
        turn_id: str | None = None,
    ) -> None:
        payload = {
            "trigger_id": trigger.trigger_id,
            "session_key": trigger.session_key,
            "trigger_type": trigger.trigger_type,
            "source_reference": trigger.source_reference,
            "decision": result.decision,
            "accepted": result.accepted,
            "suppression_reason": result.suppression_reason,
            "turn_id": turn_id,
        }
        self._append(self._decisions_path, payload)

    def recent_triggers(self, limit: int = _RECENT_SCAN_LIMIT) -> list[dict[str, Any]]:
        return self._recent(self._triggers_path, limit=limit)

    def recent_decisions(self, limit: int = _RECENT_SCAN_LIMIT) -> list[dict[str, Any]]:
        return self._recent(self._decisions_path, limit=limit)

    def summary(self, *, limit: int = 20) -> dict[str, Any]:
        triggers = self.recent_triggers(limit=limit)
        decisions = self.recent_decisions(limit=limit)
        decision_counts: dict[str, int] = {}
        suppression_reason_counts: dict[str, int] = {}
        for record in decisions:
            decision = str(record.get("decision") or "unknown")
            decision_counts[decision] = decision_counts.get(decision, 0) + 1
            reason = str(record.get("suppression_reason") or "").strip()
            if reason:
                suppression_reason_counts[reason] = suppression_reason_counts.get(reason, 0) + 1
        return {
            "trigger_count": len(triggers),
            "decision_count": len(decisions),
            "latest_trigger": triggers[-1] if triggers else None,
            "latest_decision": decisions[-1] if decisions else None,
            "recent_trigger_types": [record.get("trigger_type") for record in triggers],
            "decision_counts": decision_counts,
            "suppression_reason_counts": suppression_reason_counts,
        }

    def _append(self, path: Path, payload: dict[str, Any]) -> None:
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

