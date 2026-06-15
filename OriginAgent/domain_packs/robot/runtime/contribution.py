"""Runtime contribution for the robot domain pack."""

from __future__ import annotations

from OriginAgent.agent.domain_packs import DomainRuntimeContribution
from OriginAgent.domain_packs.robot.runtime.robot_actions import RobotActionPlanner


def build_runtime_contribution(context) -> DomainRuntimeContribution:
    return DomainRuntimeContribution(
        tool_context={},
        action_continuity_provider=RobotActionPlanner(),
        action_continuity_writeback_adapter=None,
    )
