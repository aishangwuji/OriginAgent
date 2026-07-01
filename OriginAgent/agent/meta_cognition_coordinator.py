"""Coordinator that owns meta-cognition runtime lifecycle.

Extracted from AgentLoop to reduce God-class surface area.
AgentLoop delegates trigger scanning, reflection scheduling, and
turn lifecycle to this single coordinator.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from OriginAgent.agent.meta_cognition_runtime import MetaCognitionRuntime
from OriginAgent.agent.meta_cognition_reflector import MetaCognitionReflector
from OriginAgent.agent.meta_cognition_regulator import MetaCognitionRegulator
from OriginAgent.agent.meta_cognition_models import MetaTrigger


class MetaCognitionCoordinator:
    """Owns meta-cognition runtime, scanning, and reflection scheduling.

    Single point of coordination for:
    - Turn lifecycle (start_turn / end_turn)
    - Trigger scanning after tool execution
    - Reflection scheduling
    - Status reporting
    """

    def __init__(
        self,
        *,
        runtime: MetaCognitionRuntime | None = None,
        reflector: MetaCognitionReflector | None = None,
        regulator: MetaCognitionRegulator | None = None,
        config: Any = None,
    ):
        self._runtime = runtime
        self._reflector = reflector
        self._regulator = regulator
        self._config = config
        # ── State formerly held as AgentLoop instance attributes ─────────
        self._fast_path_refs: set[str] = set()
        self._last_meta_cognition_summary: dict[str, Any] = {}
        self._last_meta_trigger_scan: list[dict[str, Any]] = []
        self._last_meta_artifacts: dict[str, Any] = {}

    # ── Turn lifecycle ─────────────────────────────────────────────────

    def start_turn(self, turn_id: str) -> None:
        """Begin meta-cognition scope for a turn."""
        if self._runtime is not None:
            self._runtime.start_turn(turn_id)

    def end_turn(self, turn_id: str) -> None:
        """End meta-cognition scope for a turn."""
        if self._runtime is not None:
            self._runtime.end_turn(turn_id)

    # ── Trigger recording ──────────────────────────────────────────────

    def record_trigger(
        self,
        trigger: MetaTrigger,
        *,
        turn_id: str | None = None,
    ) -> Any:
        """Record a meta trigger through the runtime and update local state.

        Returns the runtime's RecordTriggerResult, or None if runtime is
        unavailable.
        """
        if self._runtime is None:
            return None
        result = self._runtime.record_trigger(trigger, turn_id=turn_id)
        self._last_meta_cognition_summary = self._runtime.summary()
        self._last_meta_trigger_scan = [
            *self._last_meta_trigger_scan,
            {
                "trigger_id": trigger.trigger_id,
                "trigger_type": trigger.trigger_type,
                "decision": result.decision,
                "accepted": result.accepted,
                "suppression_reason": result.suppression_reason,
            },
        ][-50:]
        return result

    # ── Trigger scanning ───────────────────────────────────────────────

    def scan_triggers_for_turn(
        self,
        *,
        turn_id: str,
        session_key: str,
        runtime_context: Any = None,
    ) -> list[dict[str, Any]]:
        """Scan accepted triggers for a completed turn.

        Returns list of trigger summaries for reflection.
        """
        if self._runtime is None or not self._runtime.enabled:
            return []

        triggers = self._runtime.take_accepted_triggers_for_turn(turn_id)
        if not triggers:
            return []

        summaries: list[dict[str, Any]] = []
        for trigger in triggers:
            summaries.append({
                "trigger_id": trigger.trigger_id,
                "trigger_type": trigger.trigger_type,
                "source_reference": trigger.source_reference,
                "session_key": trigger.session_key,
                "payload": trigger.payload,
            })

        logger.debug(
            "MetaCognition: scanned {} triggers for turn {}",
            len(summaries),
            turn_id,
        )
        return summaries

    # ── Fast-path management ──────────────────────────────────────────

    def reset_fast_path(self) -> None:
        """Clear fast-path reference tracking for a new turn."""
        self._fast_path_refs = set()

    def add_fast_path_ref(self, ref: str) -> None:
        """Mark a source reference as having taken the fast path."""
        self._fast_path_refs.add(ref)

    def record_fast_path_decision(self, key: str) -> None:
        """Increment a fast-path decision counter in the summary dict."""
        prior_counts: dict[str, Any] = dict(
            (self._last_meta_cognition_summary or {}).get("fast_path_decision_counts", {}) or {}
        )
        self._last_meta_cognition_summary = {
            **dict(self._last_meta_cognition_summary or {}),
            "fast_path_decision_counts": {
                **prior_counts,
                key: 1 + int(prior_counts.get(key, 0) or 0),
            },
        }

    def runtime_reset_turn(self, turn_id: str) -> None:
        """Tell the runtime to reset its per-turn state."""
        if self._runtime is not None:
            self._runtime.reset_turn(turn_id)

    # ── Reflection scheduling ──────────────────────────────────────────

    def schedule_reflection(
        self,
        *,
        turn_id: str,
        session_key: str,
        trigger_summaries: list[dict[str, Any]],
    ) -> None:
        """Schedule an async reflection pass for collected triggers."""
        if not self._reflector or not trigger_summaries:
            return

        logger.info(
            "MetaCognition: scheduling reflection for turn {} ({} triggers)",
            turn_id,
            len(trigger_summaries),
        )

    # ── Reflection completion ─────────────────────────────────────────

    def on_reflection_complete(self, reflector: MetaCognitionReflector) -> None:
        """Update local state after a reflection pass finishes."""
        self._last_meta_artifacts = reflector.recent_artifacts(limit=10)
        runtime_status = getattr(reflector, "runtime_status", None)
        if callable(runtime_status):
            self._last_meta_cognition_summary = {
                **self._last_meta_cognition_summary,
                **runtime_status(),
            }

    # ── Status / introspection ─────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Return runtime status for introspection."""
        if self._runtime is not None:
            return self._runtime.summary()
        return {}

    @property
    def summary(self) -> dict[str, Any]:
        """Last meta-cognition summary (for introspection)."""
        return self._last_meta_cognition_summary

    @property
    def fast_path_refs(self) -> set[str]:
        """Set of source references that took the fast path this turn."""
        return self._fast_path_refs

    @property
    def last_artifacts(self) -> dict[str, Any]:
        """Most recent reflection artifacts (for introspection)."""
        return self._last_meta_artifacts

    @property
    def last_trigger_scan(self) -> list[dict[str, Any]]:
        """Rolling log of recent trigger scan entries."""
        return self._last_meta_trigger_scan
