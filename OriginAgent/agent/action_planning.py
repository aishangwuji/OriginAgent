"""Unified action proposal aggregation for Phase 4A."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from OriginAgent.agent.action_continuity import ActionContinuityInputs, ActionProposal


@dataclass(frozen=True)
class PlanningResult:
    proposals: list[ActionProposal] = field(default_factory=list)
    planner_sources: list[str] = field(default_factory=list)
    skipped_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposals": [proposal.to_dict() for proposal in self.proposals],
            "planner_sources": [str(item) for item in self.planner_sources],
            "skipped_reasons": [str(item) for item in self.skipped_reasons],
        }


class UnifiedActionPlanner:
    """Aggregate action proposals from active domain runtime contributions."""

    def plan_action(
        self,
        inputs: ActionContinuityInputs,
        *,
        session_key: str,
        domain_contributions: list[Any],
        max_actions: int = 1,
    ) -> PlanningResult:
        proposals: list[ActionProposal] = []
        planner_sources: list[str] = []
        skipped_reasons: list[str] = []
        max_actions = max(1, int(max_actions or 1))
        for contribution in list(domain_contributions or []):
            provider = getattr(contribution, "action_continuity_provider", None)
            if provider is None:
                continue
            provider_name = type(provider).__name__
            planner_sources.append(provider_name)
            if not hasattr(provider, "run_once"):
                skipped_reasons.append(f"{provider_name}: missing run_once")
                continue
            try:
                proposal = provider.run_once(
                    session_key=session_key,
                    continuity_inputs=inputs,
                    max_actions_per_pass=max_actions,
                )
            except Exception as exc:
                skipped_reasons.append(f"{provider_name}: {exc}")
                continue
            if proposal is None:
                skipped_reasons.append(f"{provider_name}: no_proposal")
                continue
            proposals.append(proposal)
        return PlanningResult(
            proposals=proposals,
            planner_sources=planner_sources,
            skipped_reasons=skipped_reasons,
        )
