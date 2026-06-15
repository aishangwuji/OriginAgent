"""Preview-only robot executor facade for Phase 4A."""

from __future__ import annotations

from typing import Any

from OriginAgent.agent.action_runtime import ActionExecutionResult, ActionIntent

from OriginAgent.domain_packs.robot.runtime.robot_actions import TypedRobotAction
from OriginAgent.domain_packs.robot.runtime.robot_backends import DryRunRobotBackend
from OriginAgent.domain_packs.robot.runtime.robot_safety import RobotActionSafetyGate


class RobotActionExecutor:
    def __init__(self) -> None:
        self.backend = DryRunRobotBackend()

    def preview_typed(self, action: TypedRobotAction) -> dict[str, Any]:
        intent = ActionIntent(
            action=action.action_type,
            scope=f"robot.{action.target}",
            trigger=action.trigger,
            risk="high",
            requested_by=action.requested_by,
            payload={
                "action_type": action.action_type,
                "domain": "robot",
                "target": action.target,
                **dict(action.parameters),
            },
            idempotency_key=action.idempotency_key,
        )
        return self.backend.execute(intent)

    def submit_typed(self, action: TypedRobotAction) -> ActionExecutionResult:
        intent = ActionIntent(
            action=action.action_type,
            scope=f"robot.{action.target}",
            trigger=action.trigger,
            risk="high",
            requested_by=action.requested_by,
            payload={
                "action_type": action.action_type,
                "domain": "robot",
                "target": action.target,
                **dict(action.parameters),
            },
            idempotency_key=action.idempotency_key,
        )
        decision = RobotActionSafetyGate().evaluate(intent.to_request())
        return ActionExecutionResult(
            status="denied",
            action_id="robot_action_denied",
            reason=decision.reason,
            backend_called=False,
        )
