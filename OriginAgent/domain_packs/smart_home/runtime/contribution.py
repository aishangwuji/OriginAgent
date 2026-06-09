"""Runtime contribution for the smart_home domain pack."""

from __future__ import annotations

from OriginAgent.agent.domain_packs import DomainRuntimeContribution
from OriginAgent.domain_packs.smart_home.runtime.action_automation import (
    ActionAutomationCoordinator,
    ActionContinuityWritebackAdapter,
)

from OriginAgent.domain_packs.smart_home.runtime.device_factory import build_device_action_executor


def build_runtime_contribution(context) -> DomainRuntimeContribution:
    tools_config = getattr(context.config, "device", None)
    device_registry = context.overrides.get("device_registry")
    executor = context.overrides.get("device_action_executor")
    if executor is None and tools_config is not None:
        executor = build_device_action_executor(
            workspace=context.workspace,
            config=tools_config,
            device_registry=device_registry,
        )
    return DomainRuntimeContribution(
        tool_context={
            "device_action_executor": executor,
            "device_registry": device_registry,
        },
        action_continuity_provider=ActionAutomationCoordinator(
            device_registry=device_registry,
            max_recent_digests=max(
                4,
                int(getattr(tools_config, "automation_max_actions_per_pass", 1) or 1) * 4,
            )
            if tools_config is not None
            else 8,
        ),
        action_continuity_writeback_adapter=ActionContinuityWritebackAdapter(),
    )
