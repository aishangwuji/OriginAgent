"""Sidecar runtime for bounded meta-cognition trigger collection."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from OriginAgent.agent.meta_cognition_audit import JsonlMetaCognitionAuditLedger
from OriginAgent.agent.meta_cognition_models import MetaTrigger, RecordTriggerResult


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_to_ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


@dataclass
class MetaCognitionRuntimeStatus:
    accepted_total: int = 0
    suppressed_total: int = 0
    last_recorded_at: str | None = None
    turn_counters: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class MetaCognitionRuntime:
    """Session-aware trigger recorder with dedup, cooldown, and turn limits."""

    CONTRACT_VERSION = "meta_cognition.v1.freeze"

    def __init__(self, *, config: Any, audit: JsonlMetaCognitionAuditLedger):
        self._config = config
        self._audit = audit
        self._last_seen_by_source: dict[tuple[str, str, str], str] = {}
        self._last_seen_by_session_and_type: dict[tuple[str, str], str] = {}
        self._status = MetaCognitionRuntimeStatus()
        self._recent_results: list[dict[str, Any]] = []
        self._queue: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._config, "enabled", False))

    @property
    def trigger_collection_enabled(self) -> bool:
        return bool(getattr(self._config, "trigger_collection_enabled", False))

    def record_trigger(
        self,
        trigger: MetaTrigger,
        *,
        turn_id: str | None = None,
    ) -> RecordTriggerResult:
        if not self.enabled or not self.trigger_collection_enabled:
            result = RecordTriggerResult(
                accepted=False,
                decision="dropped_runtime_disabled",
                suppression_reason="runtime_disabled",
            )
            self._audit.append_runtime_decision(trigger=trigger, result=result, turn_id=turn_id)
            self._remember_result(trigger, result)
            return result

        source_key = (trigger.session_key, trigger.trigger_type, trigger.source_reference)
        if source_key in self._last_seen_by_source:
            result = RecordTriggerResult(
                accepted=False,
                decision="suppressed_duplicate",
                suppression_reason="duplicate_source_reference",
            )
            self._audit.append_runtime_decision(trigger=trigger, result=result, turn_id=turn_id)
            self._remember_result(trigger, result)
            self._status.suppressed_total += 1
            return result

        now_ts = _iso_to_ts(trigger.created_at or _utcnow_iso())
        session_cooldown_seconds = int(getattr(self._config, "session_cooldown_seconds", 0) or 0)
        trigger_type_cooldown_seconds = int(
            getattr(self._config, "trigger_type_cooldown_seconds", 0) or 0
        )
        cooldown_hit = False
        reason = None
        session_keys = [
            (trigger.session_key, "__session__"),
            (trigger.session_key, trigger.trigger_type),
        ]
        for key in session_keys:
            last = self._last_seen_by_session_and_type.get(key)
            if not last:
                continue
            delta = now_ts - _iso_to_ts(last)
            if key[1] == "__session__" and delta < session_cooldown_seconds:
                cooldown_hit = True
                reason = "session_cooldown"
                break
            if key[1] == trigger.trigger_type and delta < trigger_type_cooldown_seconds:
                cooldown_hit = True
                reason = "trigger_type_cooldown"
                break
        if cooldown_hit:
            result = RecordTriggerResult(
                accepted=False,
                decision="suppressed_cooldown",
                suppression_reason=reason,
            )
            self._audit.append_runtime_decision(trigger=trigger, result=result, turn_id=turn_id)
            self._remember_result(trigger, result)
            self._status.suppressed_total += 1
            return result

        turn_key = str(turn_id or trigger.session_key)
        accepted_in_turn = self._status.turn_counters.get(turn_key, 0)
        max_accepted = int(getattr(self._config, "max_accepted_triggers_per_turn", 1) or 1)
        if accepted_in_turn >= max_accepted:
            result = RecordTriggerResult(
                accepted=False,
                decision="suppressed_turn_limit",
                suppression_reason="turn_limit_reached",
            )
            self._audit.append_runtime_decision(trigger=trigger, result=result, turn_id=turn_id)
            self._remember_result(trigger, result)
            self._status.suppressed_total += 1
            return result

        self._audit.append_trigger(trigger)
        result = RecordTriggerResult(accepted=True, decision="accepted")
        self._audit.append_runtime_decision(trigger=trigger, result=result, turn_id=turn_id)
        self._last_seen_by_source[source_key] = trigger.created_at
        self._last_seen_by_session_and_type[(trigger.session_key, "__session__")] = trigger.created_at
        self._last_seen_by_session_and_type[(trigger.session_key, trigger.trigger_type)] = trigger.created_at
        self._status.turn_counters[turn_key] = accepted_in_turn + 1
        self._status.accepted_total += 1
        self._status.last_recorded_at = trigger.created_at
        self._queue.append(trigger.to_json())
        queue_max_items = int(getattr(self._config, "queue_max_items", 200) or 200)
        if queue_max_items > 0:
            self._queue = self._queue[-queue_max_items:]
        self._remember_result(trigger, result)
        return result

    def reset_turn(self, turn_id: str | None) -> None:
        if turn_id:
            self._status.turn_counters.pop(str(turn_id), None)

    def recent_triggers(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._audit.recent_triggers(limit=limit)

    def recent_decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._audit.recent_decisions(limit=limit)

    def summary(self) -> dict[str, Any]:
        audit_summary = self._audit.summary(limit=20)
        return {
            "contract_version": self.CONTRACT_VERSION,
            "enabled": self.enabled,
            "trigger_collection_enabled": self.trigger_collection_enabled,
            "runtime_status": self._status.to_json(),
            "recent_triggers": self.recent_triggers(limit=20),
            "recent_decisions": self.recent_decisions(limit=20),
            "decision_counts": dict(audit_summary.get("decision_counts") or {}),
            "suppression_reason_counts": dict(audit_summary.get("suppression_reason_counts") or {}),
        }

    def _remember_result(self, trigger: MetaTrigger, result: RecordTriggerResult) -> None:
        self._recent_results.append(
            {
                "trigger_id": trigger.trigger_id,
                "session_key": trigger.session_key,
                "trigger_type": trigger.trigger_type,
                "decision": result.decision,
                "accepted": result.accepted,
                "suppression_reason": result.suppression_reason,
            }
        )
        self._recent_results = self._recent_results[-50:]
