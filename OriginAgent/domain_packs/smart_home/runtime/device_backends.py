"""Low-risk device backends and typed action execution entrypoint."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from loguru import logger

from OriginAgent.agent.action_runtime import (
    ActionExecutionResult,
    ActionIntent,
    SafeActionExecutor,
    _continuity_metadata,
)
from OriginAgent.agent.action_safety import ActionDecision
from OriginAgent.agent.audit import AuditLogger
from OriginAgent.agent.action_continuity import ActionContinuityInputs, ActionProposal
from .action_automation import ActionAutomationPreconditionGate, AutomationPreconditionDecision
from .device_actions import (
    ALLOWED_DEVICE_DOMAINS,
    DISABLED_DEVICE_DOMAINS,
    DeviceActionSchemaError,
    TypedDeviceAction,
    TypedActionPlanner,
)
from .devices import sanitize_device_scope
from .permissions import infer_device_domain

_ACTION_DOMAINS = {
    "set_light_power": "lighting",
    "set_light_brightness": "lighting",
    "set_light_color_temperature": "lighting",
    "set_media_volume": "media",
    "play_media": "media",
    "stop_media": "media",
    "set_temperature": "climate",
    "set_hvac_mode": "climate",
}


class LowRiskDeviceBackend:
    def __init__(self, *, allowed_domains: set[str] | None = None):
        self.allowed_domains = set(allowed_domains or ALLOWED_DEVICE_DOMAINS)

    def execute(self, intent: ActionIntent) -> dict[str, Any]:
        domain = self._validate_intent(intent)
        return {
            "dry_run": True,
            "backend": "low_risk_mock",
            "domain": domain,
            "action": intent.action,
            "accepted": True,
        }

    def _validate_intent(self, intent: ActionIntent) -> str:
        expected_domain = _ACTION_DOMAINS.get(intent.action)
        if expected_domain is None:
            raise ValueError(f"unsupported low-risk device action: {intent.action}")
        if expected_domain not in self.allowed_domains:
            raise ValueError(f"domain is not allowed by this backend: {expected_domain}")

        inferred_domain = infer_device_domain(intent.scope, intent.action)
        if inferred_domain in DISABLED_DEVICE_DOMAINS or inferred_domain not in self.allowed_domains:
            raise ValueError(f"scope is not allowed by low-risk backend: {intent.scope}")
        if expected_domain != inferred_domain:
            raise ValueError(
                f"scope domain {inferred_domain} does not match action domain {expected_domain}"
            )

        scope_parts = [part for part in str(intent.scope or "").casefold().split(".") if part]
        if expected_domain not in scope_parts:
            raise ValueError(f"scope does not contain expected domain: {expected_domain}")

        payload = intent.payload if isinstance(intent.payload, dict) else {}
        payload_action_type = payload.get("action_type")
        payload_domain = payload.get("domain")
        payload_device_id = payload.get("device_id")
        if payload_action_type != intent.action:
            raise ValueError("payload action_type does not match intent action")
        if payload_domain != expected_domain:
            raise ValueError("payload domain does not match action domain")
        if not isinstance(payload_device_id, str) or not payload_device_id.strip():
            raise ValueError("payload device_id is required")
        if payload_device_id.casefold() not in scope_parts:
            raise ValueError("payload device_id does not match scope")
        return expected_domain


class DeviceActionExecutor:
    def __init__(
        self,
        planner: TypedActionPlanner,
        safe_executor: SafeActionExecutor,
        *,
        audit_logger: AuditLogger | None = None,
        automation_gate: ActionAutomationPreconditionGate | None = None,
    ):
        self.planner = planner
        self.safe_executor = safe_executor
        self.audit_logger = audit_logger or safe_executor.audit_logger
        self.automation_gate = automation_gate

    def submit_typed(
        self,
        action: TypedDeviceAction,
        *,
        session_key: str | None = None,
        world_ref: str | None = None,
        facts_ref: list[str] | None = None,
        now: datetime | None = None,
    ) -> ActionExecutionResult:
        try:
            intent = self.planner.to_intent(action)
        except DeviceActionSchemaError as exc:
            return self._schema_failure_result(action, exc, now=now)
        if session_key or world_ref or facts_ref:
            intent = self._with_runtime_evidence(
                intent,
                session_key=session_key,
                world_ref=world_ref,
                facts_ref=facts_ref,
            )
        return self.safe_executor.submit(intent, now=now)

    def submit_automation(
        self,
        action: TypedDeviceAction,
        *,
        continuity_inputs: ActionContinuityInputs,
        proposal: ActionProposal | None = None,
        now: datetime | None = None,
        ) -> tuple[ActionExecutionResult, AutomationPreconditionDecision]:
        if self.automation_gate is None:
            decision = AutomationPreconditionDecision(
                outcome="denied",
                reason="automation gate is unavailable",
                audit={"gate_available": False},
            )
            return (
                ActionExecutionResult(
                    status="denied",
                    action_id=f"action_{uuid.uuid4().hex[:12]}",
                    reason=decision.reason,
                    backend_called=False,
                ),
                decision,
            )
        decision = self.automation_gate.evaluate(action, continuity_inputs)
        if decision.resolved_device_id:
            action = TypedDeviceAction(
                action_type=action.action_type,
                device_id=decision.resolved_device_id,
                domain=action.domain,
                room=decision.resolved_room or action.room,
                parameters=dict(action.parameters),
                requested_by=action.requested_by,
                trigger=action.trigger,
                idempotency_key=action.idempotency_key,
            )
        try:
            intent = self.planner.to_intent(action)
        except DeviceActionSchemaError as exc:
            return self._schema_failure_result(action, exc, now=now), decision
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
            result = ActionExecutionResult(
                status="pending_confirmation",
                action_id=f"action_{uuid.uuid4().hex[:12]}",
                reason=decision.reason,
                confirmation_id=confirmation.confirmation_id if confirmation is not None else None,
                backend_called=False,
            )
            return result, decision
        if decision.outcome != "allow":
            status = {
                "recommended_only": "denied",
                "denied": "denied",
            }.get(decision.outcome, "denied")
            result = ActionExecutionResult(
                status=status,
                action_id=f"action_{uuid.uuid4().hex[:12]}",
                reason=decision.reason,
                backend_called=False,
            )
            return result, decision
        return self.safe_executor.submit(intent, now=now), decision

    def automation_preconditions(
        self,
        action: TypedDeviceAction,
        *,
        continuity_inputs: ActionContinuityInputs,
    ) -> AutomationPreconditionDecision:
        if self.automation_gate is None:
            return AutomationPreconditionDecision(
                outcome="denied",
                reason="automation gate is unavailable",
                audit={"gate_available": False},
            )
        return self.automation_gate.evaluate(action, continuity_inputs)

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
    def _with_runtime_evidence(
        intent: ActionIntent,
        *,
        session_key: str | None = None,
        world_ref: str | None = None,
        facts_ref: list[str] | None = None,
    ) -> ActionIntent:
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
            continuity_session_ref=str(session_key).strip() if session_key else None,
            continuity_world_ref=str(world_ref).strip() if world_ref else None,
            continuity_facts_ref=[
                str(item).strip()
                for item in (facts_ref or [])
                if str(item).strip()
            ],
            continuity_origin=intent.continuity_origin,
            continuity_proposal_digest=intent.continuity_proposal_digest,
            simulation_trace_id=intent.simulation_trace_id,
        )

    def _schema_failure_result(
        self,
        action: TypedDeviceAction,
        exc: DeviceActionSchemaError,
        *,
        now: datetime | None,
    ) -> ActionExecutionResult:
        action_id = f"action_{uuid.uuid4().hex[:12]}"
        result = ActionExecutionResult(
            status="failed",
            action_id=action_id,
            reason=f"invalid typed device action: {exc}",
            backend_called=False,
        )
        self._audit_schema_failure(action_id, action, exc, result, now=now)
        return result

    def _audit_schema_failure(
        self,
        action_id: str,
        action: TypedDeviceAction,
        exc: DeviceActionSchemaError,
        result: ActionExecutionResult,
        *,
        now: datetime | None,
    ) -> None:
        if self.audit_logger is None:
            return
        try:
            parameter_keys = (
                sorted(str(key) for key in action.parameters.keys())
                if isinstance(action.parameters, dict)
                else []
            )
            self.audit_logger.log_action_decision(
                action_id=action_id,
                actor_id=action.requested_by,
                action=action.action_type,
                scope=_typed_action_scope_hint(action),
                risk=None,
                trigger=action.trigger,
                decision=result.status,
                reason=result.reason,
                metadata={
                    "gate_decision": "not_evaluated",
                    "result_status": result.status,
                    "backend_called": False,
                    "schema_validated": False,
                    "schema_error": exc.code,
                    "typed_action_type": action.action_type,
                    "typed_action_domain": action.domain,
                    "parameter_keys": parameter_keys,
                },
                created_at=now,
            )
        except Exception as audit_exc:
            logger.warning("Audit typed action schema failure write failed: {}", audit_exc)


def _typed_action_scope_hint(action: TypedDeviceAction) -> str | None:
    domain = str(action.domain or "").strip().lower()
    device_id = str(action.device_id or "").strip().lower()
    if not domain or not device_id:
        return None
    room = str(action.room or "").strip().lower()
    if room:
        return sanitize_device_scope(f"home.{room}.{domain}.{device_id}")
    return sanitize_device_scope(f"home.{domain}.{device_id}")
