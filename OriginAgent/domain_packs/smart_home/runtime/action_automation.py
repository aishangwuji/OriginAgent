"""Loop-owned smart-home automation planning and preconditions."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from OriginAgent.agent.action_continuity import ActionContinuityInputs, ActionProposal
from OriginAgent.agent.action_runtime import ActionExecutionResult
from OriginAgent.domain_packs.smart_home.runtime.device_actions import TypedDeviceAction
from OriginAgent.domain_packs.smart_home.runtime.devices import DeviceRegistry


@dataclass(frozen=True)
class AutomationPreconditionDecision:
    outcome: str
    reason: str
    resolved_device_id: str | None = None
    resolved_room: str | None = None
    audit: dict[str, Any] = field(default_factory=dict)


class ActionAutomationPreconditionGate:
    """Automation-only gate for continuity-driven typed device actions."""

    def __init__(
        self,
        *,
        device_registry: DeviceRegistry | None,
        allowed_domains: tuple[str, ...] = ("lighting",),
    ) -> None:
        self._device_registry = device_registry
        self._allowed_domains = tuple(str(item).strip().lower() for item in allowed_domains if str(item).strip())

    def evaluate(
        self,
        action: TypedDeviceAction,
        continuity_inputs: ActionContinuityInputs,
    ) -> AutomationPreconditionDecision:
        world_view = continuity_inputs.world_view
        contested = bool(world_view.contested_summary.get("contested"))
        is_fresh = bool(world_view.freshness.get("is_fresh"))
        pending_related = self._has_related_pending_confirmation(action, continuity_inputs.pending_confirmations)
        low_risk = action.domain == "lighting" and action.action_type.startswith("set_light_")
        if action.domain not in self._allowed_domains:
            return AutomationPreconditionDecision(
                outcome="denied",
                reason="automation domain is not allowed",
                audit={"domain_allowed": False},
            )
        if contested:
            return AutomationPreconditionDecision(
                outcome="denied",
                reason="world state is contested",
                audit={"world_contested": True},
            )
        if not is_fresh:
            return AutomationPreconditionDecision(
                outcome="denied",
                reason="world state is stale",
                audit={"world_fresh": False},
            )
        if pending_related:
            return AutomationPreconditionDecision(
                outcome="pending_confirmation",
                reason="related confirmation is already pending",
                audit={"pending_confirmation_related": True},
            )
        if not low_risk:
            return AutomationPreconditionDecision(
                outcome="denied",
                reason="automation only supports low-risk single-step lighting actions",
                audit={"low_risk_action": False},
            )
        if self._device_registry is None:
            return AutomationPreconditionDecision(
                outcome="recommended_only",
                reason="device registry is unavailable",
                audit={"device_registry_available": False},
            )
        try:
            record = self._device_registry.resolve(
                actor_id=continuity_inputs.runtime_context.actor_id,
                domain=action.domain,
                room=action.room,
                device_ref=action.device_id,
            )
        except Exception as exc:
            return AutomationPreconditionDecision(
                outcome="recommended_only",
                reason=f"device resolution failed: {exc}",
                audit={"device_resolved": False},
            )
        return AutomationPreconditionDecision(
            outcome="allow",
            reason="automation preconditions satisfied",
            resolved_device_id=record.device_id,
            resolved_room=record.room,
            audit={
                "domain_allowed": True,
                "world_contested": False,
                "world_fresh": True,
                "pending_confirmation_related": False,
                "supporting_facts_check": "delegated_to_safety_gate",
                "low_risk_action": True,
                "device_resolved": True,
            },
        )

    @staticmethod
    def _has_related_pending_confirmation(
        action: TypedDeviceAction,
        pending_confirmations: list[dict[str, Any]],
    ) -> bool:
        scope_fragment = f".{str(action.device_id or '').strip().lower()}"
        for item in pending_confirmations:
            if str(item.get("status") or "") not in {"pending", "notified", "confirmed_once"}:
                continue
            scope = str(item.get("scope") or "").strip().lower()
            if scope_fragment and scope.endswith(scope_fragment):
                return True
        return False

class ActionAutomationCoordinator:
    """Synchronous rule-based proposal generation for Phase 4."""

    def __init__(
        self,
        *,
        device_registry: DeviceRegistry | None = None,
        max_recent_digests: int = 8,
    ) -> None:
        self._device_registry = device_registry
        self._recent_by_session: dict[str, deque[str]] = {}
        self._max_recent_digests = max(1, int(max_recent_digests or 8))

    def run_once(
        self,
        *,
        session_key: str,
        continuity_inputs: ActionContinuityInputs,
        max_actions_per_pass: int = 1,
    ) -> ActionProposal | None:
        if max_actions_per_pass < 1:
            return None
        world_focus = list(continuity_inputs.world_view.included_summary.get("focus") or [])
        lowered = " ".join(str(item).lower() for item in world_focus)
        if "dark" not in lowered and "too dim" not in lowered and "lights off" not in lowered:
            return None
        target = self._select_lighting_target(continuity_inputs=continuity_inputs)
        if target is None:
            return None
        action = TypedDeviceAction(
            action_type="set_light_power",
            device_id=target.device_id,
            domain="lighting",
            room=target.room,
            parameters={"power": "on"},
            requested_by=continuity_inputs.runtime_context.actor_id,
            trigger="automation",
        )
        digest = self._proposal_digest(session_key, action, continuity_inputs)
        recent = self._recent_by_session.setdefault(session_key, deque(maxlen=self._max_recent_digests))
        if digest in recent:
            return None
        recent.append(digest)
        summary = continuity_inputs.world_view.included_summary
        evidence_refs = {
            "world_ref": [str(summary.get("summary_id") or "")] if summary.get("summary_id") else [],
            "facts_ref": [str(item) for item in continuity_inputs.working_memory.get("priority_facts") or []][:4],
            "proposal_digest": digest,
        }
        return ActionProposal(
            typed_action=action,
            planning_reason="Rule-based lighting automation suggested by current world summary.",
            automation_origin="loop_owned_rule_based",
            evidence_refs=evidence_refs,
            source_session_key=session_key,
            source_scope=continuity_inputs.runtime_context.default_scope,
            target_hint=self._target_hint(target),
            proposal_digest=digest,
        )

    def _select_lighting_target(
        self,
        *,
        continuity_inputs: ActionContinuityInputs,
    ) -> DeviceRecord | None:
        if self._device_registry is None:
            return None
        lighting_records = [
            record
            for record in getattr(self._device_registry, "_records", ())
            if str(getattr(record, "domain", "") or "").strip().lower() == "lighting"
        ]
        if not lighting_records:
            return None
        if len(lighting_records) == 1:
            return lighting_records[0]
        world_focus = " ".join(
            str(item).strip().lower()
            for item in continuity_inputs.world_view.included_summary.get("focus") or []
        )
        room_matches = [
            record for record in lighting_records
            if record.room and str(record.room).strip().lower() in world_focus
        ]
        if len(room_matches) == 1:
            return room_matches[0]
        return None

    @staticmethod
    def _target_hint(record: DeviceRecord) -> str:
        room = str(record.room or "").strip()
        if room:
            return f"{room}.{record.device_id}"
        return record.device_id

    @staticmethod
    def _proposal_digest(
        session_key: str,
        action: TypedDeviceAction,
        continuity_inputs: ActionContinuityInputs,
    ) -> str:
        summary = continuity_inputs.world_view.included_summary
        payload = {
            "session_key": session_key,
            "action_type": action.action_type,
            "device_id": action.device_id,
            "room": action.room,
            "parameters": action.parameters,
            "world_summary": {
                "summary_id": summary.get("summary_id"),
                "focus": summary.get("focus"),
                "constraints": summary.get("constraints"),
            },
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(encoded.encode("utf-8")).hexdigest()


class ActionContinuityWritebackAdapter:
    """Translate automation execution results into continuity-side updates."""

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
        if result.status == "pending_confirmation":
            appendix.append(
                f"[Automation] Lighting automation needs confirmation: {result.reason} (confirmation_id={result.confirmation_id})"
            )
            attention_items.append(
                f"automation_pending_confirmation:{proposal.target_hint or proposal.typed_action.device_id}"
            )
        elif result.status in {"executed", "dry_run"}:
            residue.append(
                f"automation_result:{proposal.typed_action.action_type}:{result.status}:{proposal.target_hint or proposal.typed_action.device_id}"
            )
        elif result.status in {"failed", "denied", "ask_admin"}:
            residue.append(
                f"automation_diagnostic:{proposal.typed_action.action_type}:{result.status}:{result.reason}"
            )
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
        }
