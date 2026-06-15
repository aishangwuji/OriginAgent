"""Structured continuity contracts for action planning and writeback."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from OriginAgent.agent.identity import RuntimeContext


@dataclass(frozen=True)
class ActionWorldView:
    included_summary: dict[str, Any] = field(default_factory=dict)
    contested_summary: dict[str, Any] = field(default_factory=dict)
    freshness: dict[str, Any] = field(default_factory=dict)
    selection_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ActionContinuityInputs:
    runtime_context: RuntimeContext
    working_memory: dict[str, Any] = field(default_factory=dict)
    world_view: ActionWorldView = field(default_factory=ActionWorldView)
    governance_summary: dict[str, Any] = field(default_factory=dict)
    retrieval_hints: dict[str, Any] = field(default_factory=dict)
    pending_confirmations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_context": {
                "actor_id": self.runtime_context.actor_id,
                "user_id": self.runtime_context.user_id,
                "session_id": self.runtime_context.session_id,
                "device_id": self.runtime_context.device_id,
                "trigger": self.runtime_context.trigger,
                "channel": self.runtime_context.channel,
                "chat_id": self.runtime_context.chat_id,
                "session_key": self.runtime_context.session_key,
                "source": self.runtime_context.source,
                "default_scope": self.runtime_context.default_scope,
            },
            "working_memory": dict(self.working_memory),
            "world_view": self.world_view.to_dict(),
            "governance_summary": dict(self.governance_summary),
            "retrieval_hints": dict(self.retrieval_hints),
            "pending_confirmations": [dict(item) for item in self.pending_confirmations],
        }


@dataclass(frozen=True)
class ActionProposal:
    typed_action: Any
    planning_reason: str
    automation_origin: str
    evidence_refs: dict[str, list[str] | str]
    source_session_key: str | None = None
    source_scope: str | None = None
    target_hint: str | None = None
    proposal_digest: str | None = None
    preview_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        typed_action = self.typed_action
        if hasattr(typed_action, "__dict__"):
            typed_payload = dict(vars(typed_action))
        else:
            typed_payload = {"value": str(typed_action)}
        return {
            "typed_action": typed_payload,
            "planning_reason": self.planning_reason,
            "automation_origin": self.automation_origin,
            "evidence_refs": {
                key: list(value) if isinstance(value, (list, tuple)) else str(value)
                for key, value in self.evidence_refs.items()
            },
            "source_session_key": self.source_session_key,
            "source_scope": self.source_scope,
            "target_hint": self.target_hint,
            "proposal_digest": self.proposal_digest,
            "preview_only": self.preview_only,
        }
