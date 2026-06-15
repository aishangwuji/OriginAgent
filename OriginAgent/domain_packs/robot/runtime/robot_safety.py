"""Fail-closed robot safety gate for Phase 4A."""

from __future__ import annotations

from OriginAgent.agent.action_safety import ActionDecision, ActionRequest


class RobotActionSafetyGate:
    def evaluate(self, request: ActionRequest) -> ActionDecision:
        return ActionDecision(
            decision="deny",
            reason="Robot execution is not available in P4A",
            presence_status="unknown",
        )
