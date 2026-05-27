"""Operator-facing read models for governed evolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from OriginAgent.agent.evolution import AUTO_EVOLUTION_ORIGIN, OpportunitySignalStore
from OriginAgent.agent.evolution_feedback import feedback_status
from OriginAgent.agent.evolution_health import evolution_health_score
from OriginAgent.agent.evolution_health_history import EvolutionHealthHistoryStore
from OriginAgent.agent.evolution_outcomes import EvolutionOutcomeStore, proposal_outcome_context, safe_append_outcome
from OriginAgent.agent.evolution_sandbox import sandbox_status_counts, trial_policy_status
from OriginAgent.agent.evolution_trial import TrialRunner
from OriginAgent.agent.evolution_dependencies import EvolutionDependencyStore
from OriginAgent.utils.helpers import truncate_text

_STEP_OUTPUT_PREVIEW_CHARS = 500
_MAX_RECOMMENDATIONS = 10


@dataclass(frozen=True)
class RetryTrialResult:
    ok: bool
    proposal_id: str
    status: str
    message: str
    trial: dict[str, Any] | None = None
    proposal: dict[str, Any] | None = None
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "proposal_id": self.proposal_id,
            "status": self.status,
            "message": self.message,
            "trial": self.trial,
            "proposal": self.proposal,
            "error": self.error,
        }


class EvolutionOperator:
    """Build operator views and guarded operations for evolution artifacts."""

    def __init__(self, workspace: Path, config: Any | None = None) -> None:
        self.workspace = Path(workspace)
        self.config = config

    def inspect_signal(self, opportunity_id: str) -> dict[str, Any]:
        signal_id = str(opportunity_id or "").strip()
        for signal in OpportunitySignalStore(self.workspace).read_all():
            if signal.opportunity_id == signal_id:
                return {
                    "found": True,
                    "signal": signal.to_record(),
                    "recommendations": _signal_recommendations(signal.to_record()),
                }
        return {
            "found": False,
            "error": "signal_not_found",
            "opportunity_id": signal_id,
        }

    def inspect_proposal(self, proposal_id: str) -> dict[str, Any]:
        from OriginAgent.agent.background_review import ReviewProposalStore

        proposal_key = str(proposal_id or "").strip()
        record = ReviewProposalStore(self.workspace).get(proposal_key)
        if record is None:
            return {
                "found": False,
                "error": "proposal_not_found",
                "proposal_id": proposal_key,
            }
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        return {
            "found": True,
            "proposal_id": proposal_key,
            "status": str(record.get("status") or "pending"),
            "proposal_type": str(record.get("proposal_type") or ""),
            "origin": str(record.get("origin") or ""),
            "title": str(record.get("title") or ""),
            "operator_insights": build_operator_insights(
                payload,
                proposal_type=str(record.get("proposal_type") or ""),
                config=self.config,
            ),
            "can_retry_trial": _can_retry_trial(record),
        }

    def explain_health(self) -> dict[str, Any]:
        outcome_stats = EvolutionOutcomeStore(self.workspace).stats()
        dependency_stats = EvolutionDependencyStore(self.workspace).stats()
        feedback_stats = feedback_status(self.workspace, self.config)
        sandbox_counts = sandbox_status_counts(self.workspace)
        trial_status = trial_policy_status(self.config)
        health = evolution_health_score(
            outcome_stats=outcome_stats,
            dependency_stats=dependency_stats,
            feedback_stats=feedback_stats,
            sandbox_counts=sandbox_counts,
            trial_status=trial_status,
        )
        history = EvolutionHealthHistoryStore(self.workspace).summary()
        return {
            "health": health,
            "history": history,
            "recommendations": self.list_recommendations(
                health=health,
                health_history=history,
                outcome_stats=outcome_stats,
                dependency_stats=dependency_stats,
                feedback_stats=feedback_stats,
                sandbox_counts=sandbox_counts,
            ),
        }

    def list_recommendations(
        self,
        *,
        health: dict[str, Any] | None = None,
        health_history: dict[str, Any] | None = None,
        outcome_stats: dict[str, Any] | None = None,
        dependency_stats: dict[str, Any] | None = None,
        feedback_stats: dict[str, Any] | None = None,
        sandbox_counts: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        from OriginAgent.agent.background_review import ReviewProposalStore

        outcome_stats = outcome_stats or EvolutionOutcomeStore(self.workspace).stats()
        dependency_stats = dependency_stats or EvolutionDependencyStore(self.workspace).stats()
        feedback_stats = feedback_stats or feedback_status(self.workspace, self.config)
        sandbox_counts = sandbox_counts or sandbox_status_counts(self.workspace)
        if health is None:
            health = evolution_health_score(
                outcome_stats=outcome_stats,
                dependency_stats=dependency_stats,
                feedback_stats=feedback_stats,
                sandbox_counts=sandbox_counts,
                trial_status=trial_policy_status(self.config),
            )
        if health_history is None:
            health_history = EvolutionHealthHistoryStore(self.workspace).summary()

        items: list[dict[str, Any]] = []
        if str(health_history.get("trend") or "") == "degrading":
            items.append(_recommendation(
                code="health_trend_degrading",
                severity="warning",
                action="consider_conservative_or_dry_run",
                message="Evolution health is trending down; consider conservative mode or dry_run=true until proposals are reviewed.",
            ))
        if int(dependency_stats.get("stale_reference_count") or 0) > 0:
            items.append(_recommendation(
                code="stale_dependencies",
                severity="warning",
                action="run_maintenance",
                message="Stale evolution dependencies exist; run maintenance before promoting or rolling back related artifacts.",
            ))
        blocked_or_failed = int(sandbox_counts.get("blocked") or 0) + int(sandbox_counts.get("failed") or 0)
        if blocked_or_failed:
            items.append(_recommendation(
                code="sandbox_attention_needed",
                severity="warning",
                action="inspect_or_retry_trial",
                message=f"{blocked_or_failed} auto-evolution proposal(s) were blocked or failed by sandbox checks.",
            ))
        rollback_succeeded = int(_mapping(outcome_stats.get("rollback_status_counts")).get("succeeded") or 0)
        if rollback_succeeded:
            items.append(_recommendation(
                code="rollback_feedback",
                severity="warning",
                action="inspect_suppression_candidates",
                message="Recent successful rollbacks occurred; inspect related signals and consider suppression.",
            ))
        trend_counts = _mapping(feedback_stats.get("feedback_trend_counts"))
        if int(trend_counts.get("negative") or 0) > 0:
            items.append(_recommendation(
                code="negative_feedback",
                severity="info",
                action="inspect_feedback_adjusted_signals",
                message="Recent negative feedback lowered one or more opportunity signals.",
            ))

        store = ReviewProposalStore(self.workspace)
        for record in store.list_records(origin=AUTO_EVOLUTION_ORIGIN, status="pending", limit=20):
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            insights = build_operator_insights(
                payload,
                proposal_type=str(record.get("proposal_type") or ""),
                config=self.config,
            )
            recommended_action = str(insights.get("recommended_action") or "")
            if recommended_action in {"review_required", "reject"}:
                items.append(_recommendation(
                    code="pending_evolution_proposal",
                    severity="info" if recommended_action == "review_required" else "warning",
                    action="inspect_proposal",
                    message=f"Review auto-evolution proposal `{record.get('id')}`: {record.get('title')}",
                    proposal_id=str(record.get("id") or ""),
                    opportunity_id=str(_mapping(payload.get("evolution")).get("opportunity_id") or ""),
                ))
            if len(items) >= _MAX_RECOMMENDATIONS:
                break

        for signal in OpportunitySignalStore(self.workspace).read_all():
            for item in _signal_recommendations(signal.to_record()):
                items.append(item)
                if len(items) >= _MAX_RECOMMENDATIONS:
                    break
            if len(items) >= _MAX_RECOMMENDATIONS:
                break
        return items[:_MAX_RECOMMENDATIONS]

    def retry_trial(
        self,
        proposal_id: str,
        *,
        fixtures: dict[str, str] | None = None,
        actor: str = "manual_override",
    ) -> RetryTrialResult:
        from OriginAgent.agent.background_review import ReviewProposalStore

        proposal_key = str(proposal_id or "").strip()
        store = ReviewProposalStore(self.workspace)
        record = store.get(proposal_key)
        if record is None:
            return RetryTrialResult(
                ok=False,
                proposal_id=proposal_key,
                status="missing",
                message="Review proposal was not found.",
                error="proposal_not_found",
            )
        if not _can_retry_trial(record):
            return RetryTrialResult(
                ok=False,
                proposal_id=proposal_key,
                status="unsupported",
                message="Only pending auto-evolution workflow proposals can retry trial.",
                proposal=record,
                error="unsupported_proposal",
            )
        payload = dict(record.get("payload") if isinstance(record.get("payload"), dict) else {})
        payload["review_proposal_id"] = proposal_key
        trial = TrialRunner(self.workspace, self.config).run_workflow_payload(payload, fixtures=fixtures or {})
        compact_trial = compact_trial_result(trial)
        payload["trial"] = compact_trial
        payload["operator_insights"] = build_operator_insights(
            payload,
            proposal_type=str(record.get("proposal_type") or ""),
            config=self.config,
        )
        updated = store.update_payload(proposal_key, payload)
        if updated is None:
            context = proposal_outcome_context(record)
            context["sandbox_status"] = str(compact_trial.get("status") or "")
            safe_append_outcome(
                EvolutionOutcomeStore(self.workspace),
                "trial_retry_failed",
                **context,
                metadata={
                    "actor": actor,
                    "trial_id": str(compact_trial.get("log_id") or ""),
                    "error": "payload_update_failed",
                },
            )
            return RetryTrialResult(
                ok=False,
                proposal_id=proposal_key,
                status="failed",
                message="Trial retry ran but proposal payload could not be updated.",
                trial=compact_trial,
                proposal=record,
                error="payload_update_failed",
            )
        context = proposal_outcome_context(updated or record)
        context["sandbox_status"] = str(compact_trial.get("status") or "")
        safe_append_outcome(
            EvolutionOutcomeStore(self.workspace),
            "trial_retried",
            **context,
            metadata={
                "actor": actor,
                "trial_id": str(compact_trial.get("log_id") or ""),
                "summary": compact_trial.get("summary") if isinstance(compact_trial.get("summary"), dict) else {},
            },
        )
        return RetryTrialResult(
            ok=True,
            proposal_id=proposal_key,
            status=str(compact_trial.get("status") or "unknown"),
            message="Trial retry completed and proposal payload was updated.",
            trial=compact_trial,
            proposal=updated,
        )


def build_operator_insights(
    payload: dict[str, Any],
    *,
    proposal_type: str,
    config: Any | None = None,
) -> dict[str, Any]:
    """Summarize proposal evidence for human operators."""

    static_gate = _mapping(payload.get("static_gate"))
    sandbox = _mapping(payload.get("sandbox"))
    trial = _mapping(payload.get("trial"))
    gate = _mapping(payload.get("promotion_gate"))
    evolution = _mapping(payload.get("evolution"))
    proposal_kind = str(proposal_type or payload.get("subject_type") or "").strip().lower()

    risk_level = str(gate.get("risk_level") or evolution.get("risk_level") or "medium")
    recommended_action = str(gate.get("suggested_action") or "review_required")
    return {
        "trial_summary": _trial_summary(trial or sandbox),
        "risk_summary": {
            "level": risk_level,
            "static_gate_decision": str(static_gate.get("decision") or ""),
            "promotion_gate_decision": str(gate.get("decision") or ""),
            "sandbox_status": str((trial or sandbox).get("status") or gate.get("sandbox_status") or ""),
            "issue_counts": dict(gate.get("issue_counts") or static_gate.get("issue_counts") or {}),
            "issues": _issue_messages(static_gate, sandbox, trial),
        },
        "health_impact": _estimate_health_impact(evolution, gate, sandbox, trial),
        "recommended_action": recommended_action,
        "why_not_auto_active": _why_not_auto_active(
            proposal_kind=proposal_kind,
            gate=gate,
            config=config,
        ),
    }


def compact_trial_result(result: dict[str, Any]) -> dict[str, Any]:
    step_results = result.get("step_results") if isinstance(result.get("step_results"), list) else []
    compact_steps: list[dict[str, Any]] = []
    for step in step_results[:20]:
        if not isinstance(step, dict):
            continue
        output = str(step.get("output") or "")
        compact_steps.append({
            "index": step.get("index"),
            "title": truncate_text(str(step.get("title") or ""), 120),
            "tool": str(step.get("tool") or ""),
            "status": str(step.get("status") or ""),
            "executed": bool(step.get("executed")),
            "read_only": bool(step.get("read_only", True)),
            "isolated_workspace": bool(step.get("isolated_workspace", True)),
            "output": truncate_text(output, _STEP_OUTPUT_PREVIEW_CHARS),
            "output_truncated": len(output) > _STEP_OUTPUT_PREVIEW_CHARS,
            "issues": step.get("issues") if isinstance(step.get("issues"), list) else [],
        })
    return {
        "status": str(result.get("status") or "unknown"),
        "mode": str(result.get("mode") or "trial"),
        "read_only": bool(result.get("read_only", True)),
        "isolated_workspace": bool(result.get("isolated_workspace", True)),
        "gate_status": str(result.get("gate_status") or ""),
        "log_id": str(result.get("log_id") or ""),
        "summary": result.get("summary") if isinstance(result.get("summary"), dict) else {},
        "policy": result.get("policy") if isinstance(result.get("policy"), dict) else {},
        "step_results": compact_steps,
    }


def _trial_summary(source: dict[str, Any]) -> dict[str, Any]:
    step_results = source.get("step_results") if isinstance(source.get("step_results"), list) else []
    failed_steps = [
        {
            "index": step.get("index"),
            "title": step.get("title"),
            "tool": step.get("tool"),
            "status": step.get("status"),
        }
        for step in step_results
        if isinstance(step, dict) and str(step.get("status") or "") not in {"", "passed", "skipped"}
    ][:5]
    return {
        "status": str(source.get("status") or "not_run"),
        "mode": str(source.get("mode") or "sandbox"),
        "read_only": bool(source.get("read_only", True)),
        "isolated_workspace": bool(source.get("isolated_workspace", True)),
        "replay_summary": source.get("replay_summary") if isinstance(source.get("replay_summary"), dict) else {},
        "summary": source.get("summary") if isinstance(source.get("summary"), dict) else {},
        "failed_steps": failed_steps,
    }


def _issue_messages(*sources: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    for source in sources:
        issues = source.get("issues") if isinstance(source.get("issues"), list) else []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            severity = str(issue.get("severity") or "")
            if severity not in {"pending", "reject", "warning"}:
                continue
            message = truncate_text(str(issue.get("message") or issue.get("code") or ""), 300)
            if message:
                messages.append(message)
    return messages[:8]


def _estimate_health_impact(
    evolution: dict[str, Any],
    gate: dict[str, Any],
    sandbox: dict[str, Any],
    trial: dict[str, Any],
) -> dict[str, Any]:
    status = str((trial or sandbox).get("status") or "")
    decision = str(gate.get("decision") or "")
    priority = _safe_float(evolution.get("priority_score"), 0.0)
    if decision == "blocked" or status in {"blocked", "failed"}:
        direction = "negative"
        estimate = -5
    elif decision == "pass" and status in {"passed", "skipped", ""}:
        direction = "neutral_positive"
        estimate = 1 if priority >= 0.8 else 0
    else:
        direction = "neutral"
        estimate = 0
    return {
        "estimated_score_delta": estimate,
        "direction": direction,
        "basis": {
            "priority_score": priority,
            "gate_decision": decision,
            "trial_or_sandbox_status": status,
        },
    }


def _why_not_auto_active(*, proposal_kind: str, gate: dict[str, Any], config: Any | None) -> list[str]:
    reasons = [
        "OriginAgent governance does not auto-activate evolution artifacts.",
        "`verified` is a validation state, not an activation state.",
    ]
    if proposal_kind == "skill":
        reasons.append("Evolution-generated skills must remain `always: false` and require review before activation.")
    elif proposal_kind == "workflow":
        reasons.append("Workflow proposals may be verified, but active use still requires an explicit review/apply path.")
    if bool(gate.get("auto_verify_eligible")):
        reasons.append("This proposal may be auto-verified when policy allows, but still will not become active automatically.")
    elif not bool(getattr(config, "auto_verify_workflows", False) if config is not None else False):
        reasons.append("Auto-verification is disabled by current policy.")
    return reasons


def _signal_recommendations(signal: dict[str, Any]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    opportunity_id = str(signal.get("opportunity_id") or "")
    negative_count = int(signal.get("feedback_negative_count") or 0)
    if negative_count >= 2 and str(signal.get("status") or "") != "suppressed":
        recommendations.append(_recommendation(
            code="signal_negative_feedback",
            severity="warning",
            action="consider_suppress_signal",
            message="Opportunity signal has repeated negative feedback; consider suppressing it.",
            opportunity_id=opportunity_id,
        ))
    if str(signal.get("verification_status") or "") == "rolled_back":
        recommendations.append(_recommendation(
            code="signal_rolled_back",
            severity="warning",
            action="keep_suppressed_or_rework",
            message="Opportunity came from a rolled-back artifact; keep it suppressed or rework before retrying.",
            opportunity_id=opportunity_id,
        ))
    return recommendations


def _can_retry_trial(record: dict[str, Any]) -> bool:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    evolution = _mapping(payload.get("evolution"))
    return (
        str(record.get("status") or "pending") == "pending"
        and str(record.get("proposal_type") or "") == "workflow"
        and (
            str(record.get("origin") or "") == AUTO_EVOLUTION_ORIGIN
            or str(evolution.get("origin") or "") == AUTO_EVOLUTION_ORIGIN
        )
    )


def _recommendation(
    *,
    code: str,
    severity: str,
    action: str,
    message: str,
    proposal_id: str = "",
    opportunity_id: str = "",
) -> dict[str, Any]:
    result = {
        "code": code,
        "severity": severity,
        "action": action,
        "message": message,
    }
    if proposal_id:
        result["proposal_id"] = proposal_id
    if opportunity_id:
        result["opportunity_id"] = opportunity_id
    return result


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
