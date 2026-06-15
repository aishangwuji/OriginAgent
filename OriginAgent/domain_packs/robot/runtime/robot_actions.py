"""Preview-only robot action contracts for Phase 4A."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from OriginAgent.agent.action_continuity import ActionContinuityInputs, ActionProposal


@dataclass(frozen=True)
class TypedRobotAction:
    action_type: str
    target: str
    parameters: dict[str, Any] = field(default_factory=dict)
    requested_by: str | None = None
    trigger: str = "automation"
    idempotency_key: str | None = None


class RobotActionPlanner:
    automation_origin = "robot"

    def run_once(
        self,
        *,
        session_key: str,
        continuity_inputs: ActionContinuityInputs,
        max_actions_per_pass: int = 1,
    ) -> ActionProposal | None:
        if max_actions_per_pass < 1:
            return None
        focus = " ".join(
            str(item or "").strip().lower()
            for item in continuity_inputs.world_view.included_summary.get("focus") or []
        )
        if not any(keyword in focus for keyword in ("robot", "unitree", "arm", "g1")):
            return None
        action = TypedRobotAction(
            action_type="preview_robot_motion",
            target="unitree_g1",
            parameters={"mode": "simulator", "focus": focus[:160]},
            requested_by=continuity_inputs.runtime_context.actor_id,
            trigger="automation",
            idempotency_key=f"robot-preview:{session_key}",
        )
        return ActionProposal(
            typed_action=action,
            planning_reason="Preview-only robot proposal generated from world focus.",
            automation_origin=self.automation_origin,
            evidence_refs={
                "world_ref": [str(continuity_inputs.world_view.included_summary.get("summary_id") or "")],
                "proposal_digest": f"robot-preview:{session_key}",
            },
            source_session_key=session_key,
            source_scope=continuity_inputs.runtime_context.default_scope,
            target_hint="unitree_g1.preview",
            proposal_digest=f"robot-preview:{session_key}",
            preview_only=True,
        )
