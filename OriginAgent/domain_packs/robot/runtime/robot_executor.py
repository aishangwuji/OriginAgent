"""Robot simulator and handoff executor facade for Phase 4BC."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from OriginAgent.agent.action_continuity import ActionContinuityInputs, ActionProposal
from OriginAgent.agent.action_runtime import (
    ActionDecision,
    ActionExecutionResult,
    ActionIntent,
    SafeActionExecutor,
    _continuity_metadata,
)
from OriginAgent.agent.confirmation import ConfirmationManager
from OriginAgent.agent.permissions import PermissionResolver

from OriginAgent.domain_packs.robot.runtime.robot_actions import TypedRobotAction
from OriginAgent.domain_packs.robot.runtime.robot_backends import DryRunRobotBackend
from OriginAgent.domain_packs.robot.runtime.robot_safety import RobotActionSafetyGate


@dataclass(frozen=True)
class RobotAutomationPreconditionDecision:
    outcome: str
    reason: str
    audit: dict[str, Any] = field(default_factory=dict)


class RobotActionExecutor:
    def __init__(
        self,
        *,
        confirmation_manager: ConfirmationManager,
        permission_resolver: PermissionResolver | None = None,
    ) -> None:
        self.backend = DryRunRobotBackend()
        self.safe_executor = SafeActionExecutor(
            gate=RobotActionSafetyGate(),
            confirmation_manager=confirmation_manager,
            backend=self.backend,
            permission_resolver=permission_resolver or PermissionResolver(),
        )

    def preview_typed(self, action: TypedRobotAction) -> dict[str, Any]:
        return self.backend.execute(self._to_intent(action))

    def submit_typed(self, action: TypedRobotAction, *, now: datetime | None = None) -> ActionExecutionResult:
        return self.safe_executor.submit(self._to_intent(action), now=now)

    def submit_automation(
        self,
        action: TypedRobotAction,
        *,
        continuity_inputs: ActionContinuityInputs,
        proposal: ActionProposal | None = None,
        now: datetime | None = None,
    ) -> tuple[ActionExecutionResult, RobotAutomationPreconditionDecision]:
        decision = self.automation_preconditions(
            action,
            continuity_inputs=continuity_inputs,
            proposal=proposal,
        )
        intent = self._to_intent(action)
        if proposal is not None:
            intent = self._with_proposal_evidence(intent, proposal)
        if decision.outcome == "pending_confirmation":
            confirmation = self.safe_executor.confirmation_manager.create_from_action_decision(
                intent.to_request(),
                ActionDecision(
                    decision="ask_confirmation",
                    reason=decision.reason,
                    presence_status="unknown",
                ),
                now=now,
                metadata=_continuity_metadata(intent),
                action_payload=intent.sanitized().payload,
                idempotency_key=intent.idempotency_key,
            )
            return (
                ActionExecutionResult(
                    status="pending_confirmation",
                    action_id=f"action_{uuid.uuid4().hex[:12]}",
                    reason=decision.reason,
                    confirmation_id=confirmation.confirmation_id if confirmation is not None else None,
                    backend_called=False,
                ),
                decision,
            )
        if decision.outcome != "allow":
            return (
                ActionExecutionResult(
                    status="denied",
                    action_id=f"action_{uuid.uuid4().hex[:12]}",
                    reason=decision.reason,
                    backend_called=False,
                ),
                decision,
            )
        result = ActionExecutionResult(
            status="dry_run",
            action_id=f"action_{uuid.uuid4().hex[:12]}",
            reason=decision.reason,
            backend_result=self.backend.execute(intent),
            backend_called=True,
            is_real_execution=False,
            backend_kind=self.backend.backend_kind,
            physical_target_domain="robot",
        )
        return result, decision

    def automation_preconditions(
        self,
        action: TypedRobotAction,
        *,
        continuity_inputs: ActionContinuityInputs,
        proposal: ActionProposal | None = None,
    ) -> RobotAutomationPreconditionDecision:
        action_type = str(action.action_type or "").strip().lower()
        mode = str(action.parameters.get("mode") or "").strip().lower()
        if action_type == "preview_robot_motion":
            return RobotAutomationPreconditionDecision(
                outcome="deny",
                reason="preview-only robot proposal is not executable",
                audit={"preview_only": True},
            )
        if mode == "handoff":
            return RobotAutomationPreconditionDecision(
                outcome="pending_confirmation",
                reason="robot operator handoff requires confirmation",
                audit={"handoff": True, "simulator": False},
            )
        if mode == "simulator":
            return RobotAutomationPreconditionDecision(
                outcome="allow",
                reason="robot simulator path permitted",
                audit={"handoff": False, "simulator": True},
            )
        return RobotAutomationPreconditionDecision(
            outcome="deny",
            reason="robot automation mode is unsupported",
            audit={"handoff": False, "simulator": False},
        )

    @staticmethod
    def _with_proposal_evidence(intent: ActionIntent, proposal: ActionProposal) -> ActionIntent:
        evidence = proposal.evidence_refs or {}
        facts_ref = evidence.get("facts_ref") or []
        if not isinstance(facts_ref, list):
            facts_ref = [str(facts_ref)]
        world_ref = evidence.get("world_ref") or []
        world_value = world_ref[0] if isinstance(world_ref, list) and world_ref else (
            str(world_ref) if world_ref else None
        )
        digest = proposal.proposal_digest or str(evidence.get("proposal_digest") or "").strip() or None
        return ActionIntent(
            action=intent.action,
            scope=intent.scope,
            trigger=intent.trigger,
            risk=intent.risk,
            requested_by=intent.requested_by,
            requires_presence_empty=intent.requires_presence_empty,
            uses_facts=list(intent.uses_facts),
            payload=dict(intent.payload),
            idempotency_key=intent.idempotency_key,
            continuity_session_ref=proposal.source_session_key,
            continuity_world_ref=str(world_value).strip() if world_value else None,
            continuity_facts_ref=[str(item).strip() for item in facts_ref if str(item).strip()],
            continuity_origin=proposal.automation_origin,
            continuity_proposal_digest=digest,
        )

    @staticmethod
    def _to_intent(action: TypedRobotAction) -> ActionIntent:
        return ActionIntent(
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


class RobotActionWritebackAdapter:
    """Record robot simulator and handoff outcomes without mutating device state."""

    def writeback(
        self,
        *,
        session: Any,
        continuity_inputs: ActionContinuityInputs,
        proposal: ActionProposal,
        result: ActionExecutionResult,
        working_memory: Any,
    ) -> dict[str, Any]:
        appendix: list[str] = []
        residue: list[str] = []
        attention_items: list[str] = []
        target = proposal.target_hint or getattr(proposal.typed_action, "target", "robot")
        if result.status == "pending_confirmation":
            appendix.append(
                f"[Automation] Robot operator handoff needs confirmation: {result.reason} (confirmation_id={result.confirmation_id})"
            )
            attention_items.append(f"robot_handoff_pending:{target}")
        elif result.status in {"dry_run", "executed"}:
            residue.append(f"robot_simulator_result:{target}:{result.status}")
        elif result.status in {"failed", "denied", "ask_admin"}:
            residue.append(f"robot_diagnostic:{target}:{result.status}:{result.reason}")
        if attention_items or residue:
            working_memory.upsert(
                session,
                identity=continuity_inputs.runtime_context.identity,
                attention_items=(
                    [*(continuity_inputs.working_memory.get("attention_items") or []), *attention_items]
                    if attention_items
                    else None
                ),
                tool_residue=(
                    [*(continuity_inputs.working_memory.get("tool_residue") or []), *residue]
                    if residue
                    else None
                ),
            )
        return {
            "appendix": appendix,
            "attention_items": attention_items,
            "tool_residue": residue,
            "result_status": result.status,
            "handoff": result.status == "pending_confirmation",
            "backend_kind": result.backend_kind,
        }
