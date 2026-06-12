"""Append-only audit helpers for sidecar meta-cognition triggers, artifacts, and decisions."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from OriginAgent.agent.meta_cognition_models import (
    ConfidenceTrace,
    ErrorPattern,
    EvolutionSeed,
    MetaTrigger,
    RecordTriggerResult,
    ReflectionRecord,
    ThoughtJournalEntry,
)
from OriginAgent.utils.helpers import ensure_dir

_RECENT_SCAN_LIMIT = 200


class JsonlMetaCognitionAuditLedger:
    """Append-only ledger for meta-cognition triggers, artifacts, and runtime decisions."""

    def __init__(self, workspace: Path):
        root = Path(workspace) / "memory" / "meta_cognition"
        self._triggers_path = root / "triggers.jsonl"
        self._decisions_path = root / "decisions.jsonl"
        self._journals_path = root / "journals.jsonl"
        self._reflections_path = root / "reflections.jsonl"
        self._confidence_traces_path = root / "confidence_traces.jsonl"
        self._patterns_path = root / "patterns.jsonl"
        self._evolution_seeds_path = root / "evolution_seeds.jsonl"
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

    def append_journal(self, journal: ThoughtJournalEntry) -> None:
        self._append(self._journals_path, journal.to_json())

    def append_reflection(self, reflection: ReflectionRecord) -> None:
        self._append(self._reflections_path, reflection.to_json())

    def append_confidence_trace(self, trace: ConfidenceTrace) -> None:
        self._append(self._confidence_traces_path, trace.to_json())

    def append_pattern(self, pattern: ErrorPattern) -> None:
        self._append(self._patterns_path, pattern.to_json())

    def append_evolution_seed(self, seed: EvolutionSeed) -> None:
        self._append(self._evolution_seeds_path, seed.to_json())

    def recent_triggers(self, limit: int = _RECENT_SCAN_LIMIT) -> list[dict[str, Any]]:
        return self._recent(self._triggers_path, limit=limit)

    def recent_decisions(self, limit: int = _RECENT_SCAN_LIMIT) -> list[dict[str, Any]]:
        return self._recent(self._decisions_path, limit=limit)

    def recent_journals(self, limit: int = _RECENT_SCAN_LIMIT) -> list[dict[str, Any]]:
        return self._recent(self._journals_path, limit=limit)

    def recent_reflections(self, limit: int = _RECENT_SCAN_LIMIT) -> list[dict[str, Any]]:
        return self._recent(self._reflections_path, limit=limit)

    def recent_confidence_traces(self, limit: int = _RECENT_SCAN_LIMIT) -> list[dict[str, Any]]:
        return self._recent(self._confidence_traces_path, limit=limit)

    def recent_patterns(self, limit: int = _RECENT_SCAN_LIMIT) -> list[dict[str, Any]]:
        return self._recent(self._patterns_path, limit=limit)

    def recent_evolution_seeds(self, limit: int = _RECENT_SCAN_LIMIT) -> list[dict[str, Any]]:
        return self._recent(self._evolution_seeds_path, limit=limit)

    def summary(self, *, limit: int = 20) -> dict[str, Any]:
        triggers = self.recent_triggers(limit=limit)
        decisions = self.recent_decisions(limit=limit)
        journals = self.recent_journals(limit=limit)
        reflections = self.recent_reflections(limit=limit)
        traces = self.recent_confidence_traces(limit=limit)
        patterns = self.recent_patterns(limit=limit)
        seeds = self.recent_evolution_seeds(limit=limit)
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
            "journal_count": len(journals),
            "reflection_count": len(reflections),
            "confidence_trace_count": len(traces),
            "pattern_count": len(patterns),
            "evolution_seed_count": len(seeds),
            "latest_trigger": triggers[-1] if triggers else None,
            "latest_decision": decisions[-1] if decisions else None,
            "latest_journal": journals[-1] if journals else None,
            "latest_reflection": reflections[-1] if reflections else None,
            "latest_confidence_trace": traces[-1] if traces else None,
            "latest_pattern": patterns[-1] if patterns else None,
            "latest_evolution_seed": seeds[-1] if seeds else None,
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
