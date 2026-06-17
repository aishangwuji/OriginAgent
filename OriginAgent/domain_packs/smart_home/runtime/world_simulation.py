"""Smart-home specific hook for the P1 world simulator."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from OriginAgent.agent.action_runtime import (
    ActionExecutionResult,
    ActionIntent,
    ActionSimulationHook,
    SimulationPrecheckDecision,
)
from OriginAgent.agent.facts import FactStore
from OriginAgent.agent.world_simulator import (
    SimulationFeedback,
    SimulationRequest,
    WorldSimulator,
)
from OriginAgent.agent.world_state import WorldStateManager
from OriginAgent.session.manager import SessionManager


class SmartHomeWorldSimulationHook(ActionSimulationHook):
    """Simulation hook used by smart-home user actions before execution."""

    def __init__(
        self,
        *,
        workspace: Path,
        world_state: WorldStateManager,
        fact_store: FactStore,
        sessions: SessionManager,
        timezone_name: str | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.sessions = sessions
        self.simulator = WorldSimulator(
            self.workspace,
            world_state=world_state,
            fact_store=fact_store,
            timezone_name=timezone_name,
        )
        self.simulator.seed_edges_from_facts()

    def precheck(self, intent: ActionIntent, *, now: datetime) -> SimulationPrecheckDecision | None:
        session_key = str(intent.continuity_session_ref or "").strip()
        if not session_key:
            return None
        session = self.sessions.get_or_create(session_key)
        request = SimulationRequest(
            request_id=f"simreq:{session_key}:{intent.action}:{int(now.timestamp())}",
            action=intent.action,
            scope=intent.scope,
            trigger=intent.trigger,
            risk=intent.risk,
            payload=dict(intent.payload),
            requested_by=intent.requested_by,
            world_ref=intent.continuity_world_ref,
            facts_ref=list(intent.continuity_facts_ref),
        )
        trace = self.simulator.predict_action(request, session=session, current_time=now)
        if trace.status != "ok":
            return SimulationPrecheckDecision(
                decision="allow",
                trace=trace,
                reason=trace.simulation_skipped_reason or trace.status,
            )
        if trace.risk_score >= 0.8:
            return SimulationPrecheckDecision(
                decision="ask_confirmation",
                trace=trace,
                reason="simulation predicted elevated smart-home side effects",
                prompt_suffix=self._prompt_suffix(trace),
            )
        if trace.recommended_confirmation:
            return SimulationPrecheckDecision(
                decision="ask_confirmation",
                trace=trace,
                reason="simulation recommends a confirmation before execution",
                prompt_suffix=self._prompt_suffix(trace),
            )
        return SimulationPrecheckDecision(
            decision="allow",
            trace=trace,
            reason="simulation allows execution",
        )

    def record_result(
        self,
        intent: ActionIntent,
        result: ActionExecutionResult,
        *,
        now: datetime,
    ) -> None:
        if not intent.simulation_trace_id:
            return
        if result.simulation_status != "ok":
            return
        if result.status in {"executed", "dry_run"}:
            outcome = "matched"
        elif result.status in {"denied", "failed", "ask_admin"}:
            outcome = "contradicted"
        else:
            outcome = "evidence_insufficient"
        feedback = SimulationFeedback(
            trace_id=intent.simulation_trace_id,
            outcome=outcome,
            observed_outcome={
                "result_status": result.status,
                "reason": result.reason,
            },
            mismatch_score=0.0 if outcome == "matched" else (1.0 if outcome == "contradicted" else None),
            evidence_refs={
                "action_id": result.action_id,
                "scope": intent.scope,
            },
            observed_at=now.isoformat(),
            calibration_state="pending",
        )
        self.simulator.record_outcome(feedback)

    @staticmethod
    def _prompt_suffix(trace: Any) -> str:
        percentage = int(round(max(0.0, min(1.0, trace.risk_score)) * 100))
        return f"system predicted about {percentage}% chance of triggering night vision mode"
