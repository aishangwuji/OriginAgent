"""Runtime contribution for the robot domain pack."""

from __future__ import annotations

from OriginAgent.agent.confirmation import ConfirmationManager
from OriginAgent.agent.domain_packs import DomainRuntimeContribution
from OriginAgent.domain_packs.robot.runtime.robot_actions import RobotActionPlanner
from OriginAgent.domain_packs.robot.runtime.robot_executor import (
    RobotActionExecutor,
    RobotActionWritebackAdapter,
)


def build_runtime_contribution(context) -> DomainRuntimeContribution:
    confirmation_manager = getattr(context, "confirmation_manager", None) or ConfirmationManager(context.workspace)
    executor = RobotActionExecutor(confirmation_manager=confirmation_manager)
    return DomainRuntimeContribution(
        tool_context={
            "device_action_executor": executor,
        },
        action_continuity_provider=RobotActionPlanner(),
        action_continuity_writeback_adapter=RobotActionWritebackAdapter(),
    )
