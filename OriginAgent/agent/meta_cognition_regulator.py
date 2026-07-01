"""MetaCognitionRegulator — online monitoring, depth control, and budget scheduling.

The Regulator sits between the ``MetaCognitionRuntime`` (reflection) and the
``CognitiveLoop`` / ``InnerMonologueEngine`` (thought production).  It collects
real-time signals from across the meta-cognition system and produces
recommendations for depth control, fast/slow switching, verification needs,
and internal throttling.

Per CogniSphere §8.5.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── data types ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RegulatorSnapshot:
    """Point-in-time view of all monitored signals."""

    # Confidence signals
    avg_trigger_acceptance_rate: float = 0.0
    recent_tool_failure_count: int = 0
    recent_user_correction_count: int = 0

    # Load signals
    active_reflections_running: int = 0
    triggers_accepted_last_minute: int = 0
    triggers_suppressed_last_minute: int = 0

    # Quality signals
    pattern_count: int = 0
    journal_count: int = 0
    consecutive_reflection_failures: int = 0

    # Budget signals
    thought_production_rate: float = 0.0  # journals per minute
    estimated_token_load: float = 0.0  # relative 0-1

    collected_at: str = field(default_factory=lambda: _utcnow().isoformat())


@dataclass(frozen=True)
class RegulatorRecommendation:
    """One recommendation produced by the regulator."""

    action: str  # "deepen_reflection" | "switch_to_slow" | "verify_first" | "generate_evolution_seed" | "cool_down" | "noop"
    reason: str
    priority: str  # "low" | "medium" | "high"
    payload: dict[str, Any] = field(default_factory=dict)


# ── thresholds ───────────────────────────────────────────────────────────────

_HIGH_CORRECTION_RATE = 0.3  # ≥30% of triggers are user corrections → verify first
_HIGH_FAILURE_RATE = 0.25  # ≥25% tool failure rate → deepen reflection
_HIGH_SUPPRESSION_RATE = 0.6  # ≥60% suppressed → cool down
_LOW_CONFIDENCE_THRESHOLD = 0.3  # avg confidence < 0.3 → switch to slow
_CONSECUTIVE_FAILURE_LIMIT = 3  # ≥3 consecutive reflection failures → cool down
_HIGH_PATTERN_DENSITY = 10  # ≥10 patterns in recent window → evolution seed
_WINDOW_MINUTES = 5  # sliding window for rate calculations


class MetaCognitionRegulator:
    """Monitors meta-cognition signals and produces recommendations.

    Usage::

        regulator = MetaCognitionRegulator()
        regulator.record_trigger_decision(accepted=True, trigger_type="tool_failure")
        regulator.record_reflection_result(success=True)
        snapshot = regulator.snapshot()
        recs = regulator.recommend(snapshot)
        for rec in recs:
            if rec.action == "cool_down":
                reflector.throttle()
    """

    def __init__(self) -> None:
        # sliding windows — each entry is (timestamp,)
        self._trigger_decisions: deque[tuple[datetime, bool, str]] = deque(maxlen=500)
        self._reflection_results: deque[tuple[datetime, bool]] = deque(maxlen=200)
        self._running_reflections: int = 0
        self._consecutive_failures: int = 0
        self._tracked_sessions: set[str] = set()

    # ── signal recording ──────────────────────────────────────────

    def record_trigger_decision(self, *, accepted: bool, trigger_type: str) -> None:
        """Record one trigger-runtime decision."""
        self._trigger_decisions.append((_utcnow(), accepted, str(trigger_type).strip().lower()))

    def record_reflection_start(self) -> None:
        """Increment the active-reflection counter."""
        self._running_reflections += 1

    def record_reflection_end(self, *, success: bool) -> None:
        """Decrement the active-reflection counter and record outcome."""
        self._running_reflections = max(0, self._running_reflections - 1)
        self._reflection_results.append((_utcnow(), success))
        if success:
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1

    def record_session_activity(self, session_key: str) -> None:
        """Note that a session was active (for load estimation)."""
        self._tracked_sessions.add(str(session_key))

    # ── snapshot ──────────────────────────────────────────────────

    def snapshot(self) -> RegulatorSnapshot:
        """Collect a point-in-time view of all signals."""
        now = _utcnow()
        window = timedelta(minutes=_WINDOW_MINUTES)
        cutoff = now - window

        # filter recent entries
        recent_decisions = [(acc, ttype) for ts, acc, ttype in self._trigger_decisions if ts >= cutoff]
        recent_reflections = [succ for ts, succ in self._reflection_results if ts >= cutoff]

        total = len(recent_decisions)
        accepted = sum(1 for acc, _ in recent_decisions if acc)
        tool_failures = sum(1 for acc, ttype in recent_decisions if acc and ttype == "tool_failure")
        corrections = sum(1 for acc, ttype in recent_decisions if acc and ttype == "user_correction")
        suppressed = total - accepted

        reflection_total = len(recent_reflections)

        # journal rate: estimate from accepted triggers
        journal_rate = accepted / max(1, _WINDOW_MINUTES)

        # token load: rough proxy from active reflections + session count
        active_sessions = len(self._tracked_sessions)
        token_load = min(1.0, (self._running_reflections * 0.3 + active_sessions * 0.1))

        return RegulatorSnapshot(
            avg_trigger_acceptance_rate=accepted / max(1, total),
            recent_tool_failure_count=tool_failures,
            recent_user_correction_count=corrections,
            active_reflections_running=self._running_reflections,
            triggers_accepted_last_minute=accepted,
            triggers_suppressed_last_minute=suppressed,
            pattern_count=reflection_total,
            journal_count=accepted,
            consecutive_reflection_failures=self._consecutive_failures,
            thought_production_rate=journal_rate,
            estimated_token_load=token_load,
        )

    # ── recommendations ───────────────────────────────────────────

    def recommend(self, snapshot: RegulatorSnapshot | None = None) -> list[RegulatorRecommendation]:
        """Produce zero or more recommendations based on current signals."""
        snap = snapshot or self.snapshot()
        recs: list[RegulatorRecommendation] = []

        # 1. Quick-check: should we cool down?
        total_triggers = snap.triggers_accepted_last_minute + snap.triggers_suppressed_last_minute
        if total_triggers > 0:
            suppression_rate = snap.triggers_suppressed_last_minute / total_triggers
            if suppression_rate >= _HIGH_SUPPRESSION_RATE:
                recs.append(RegulatorRecommendation(
                    action="cool_down",
                    reason=f"suppression_rate={suppression_rate:.2f} >= {_HIGH_SUPPRESSION_RATE}; too many triggers being suppressed",
                    priority="medium",
                    payload={"suppression_rate": suppression_rate},
                ))

        # 2. Consecutive failures → cool down
        if snap.consecutive_reflection_failures >= _CONSECUTIVE_FAILURE_LIMIT:
            recs.append(RegulatorRecommendation(
                action="cool_down",
                reason=f"{snap.consecutive_reflection_failures} consecutive reflection failures",
                priority="high",
                payload={"consecutive_failures": snap.consecutive_reflection_failures},
            ))

        # 3. Low confidence → switch to slow thinking
        if snap.avg_trigger_acceptance_rate < _LOW_CONFIDENCE_THRESHOLD:
            recs.append(RegulatorRecommendation(
                action="switch_to_slow",
                reason=f"avg_trigger_acceptance_rate={snap.avg_trigger_acceptance_rate:.2f} < {_LOW_CONFIDENCE_THRESHOLD}",
                priority="high",
                payload={"acceptance_rate": snap.avg_trigger_acceptance_rate},
            ))

        # 4. High correction rate → verify first
        if total_triggers > 0:
            correction_rate = snap.recent_user_correction_count / total_triggers
            if correction_rate >= _HIGH_CORRECTION_RATE:
                recs.append(RegulatorRecommendation(
                    action="verify_first",
                    reason=f"user_correction_rate={correction_rate:.2f} >= {_HIGH_CORRECTION_RATE}",
                    priority="high",
                    payload={"correction_rate": correction_rate},
                ))

        # 5. High failure rate → deepen reflection
        if total_triggers > 0:
            failure_rate = snap.recent_tool_failure_count / total_triggers
            if failure_rate >= _HIGH_FAILURE_RATE:
                recs.append(RegulatorRecommendation(
                    action="deepen_reflection",
                    reason=f"tool_failure_rate={failure_rate:.2f} >= {_HIGH_FAILURE_RATE}",
                    priority="medium",
                    payload={"failure_rate": failure_rate},
                ))

        # 6. High pattern density → suggest evolution seed
        if snap.pattern_count >= _HIGH_PATTERN_DENSITY:
            recs.append(RegulatorRecommendation(
                action="generate_evolution_seed",
                reason=f"{snap.pattern_count} patterns in window; density suggests actionable patterns",
                priority="low",
                payload={"pattern_count": snap.pattern_count},
            ))

        # 7. Token load → cool down if overloaded
        if snap.estimated_token_load >= 0.8:
            recs.append(RegulatorRecommendation(
                action="cool_down",
                reason=f"estimated_token_load={snap.estimated_token_load:.2f} >= 0.8",
                priority="medium",
                payload={"token_load": snap.estimated_token_load},
            ))

        if not recs:
            recs.append(RegulatorRecommendation(action="noop", reason="all signals within normal range", priority="low"))

        return recs

    # ── introspection ─────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        snap = self.snapshot()
        recs = self.recommend(snap)
        return {
            "snapshot": {
                "avg_trigger_acceptance_rate": snap.avg_trigger_acceptance_rate,
                "recent_tool_failure_count": snap.recent_tool_failure_count,
                "recent_user_correction_count": snap.recent_user_correction_count,
                "active_reflections_running": snap.active_reflections_running,
                "consecutive_reflection_failures": snap.consecutive_reflection_failures,
                "estimated_token_load": snap.estimated_token_load,
            },
            "recommendations": [
                {"action": r.action, "reason": r.reason, "priority": r.priority}
                for r in recs
            ],
            "active_sessions": len(self._tracked_sessions),
        }
