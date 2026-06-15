"""Robot preview backend stubs for Phase 4A."""

from __future__ import annotations

from typing import Any

from OriginAgent.agent.action_runtime import ActionIntent


class DryRunRobotBackend:
    backend_kind = "robot_simulator"

    def execute(self, intent: ActionIntent) -> dict[str, Any]:
        return {
            "backend": self.backend_kind,
            "dry_run": True,
            "accepted": True,
            "target_present": bool(intent.payload.get("target")),
        }
