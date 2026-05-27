"""Shared read-only runtime introspection service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from OriginAgent.agent.confirmation import PendingConfirmationStore
from OriginAgent.agent.domain_pack_governance import summarize_domain_pack_governance
from OriginAgent.agent.self_model import SelfModelService
from OriginAgent.agent.skills import SkillsLoader
from OriginAgent.agent.workflow_artifacts import summarize_workflow_artifacts


class RuntimeIntrospectionService:
    """Build safe read models for runtime introspection tools.

    The service deliberately has no mutation methods. Tools such as ``my`` may
    still mutate their own narrow allowlist directly, while system status tools
    consume these read-only projections.
    """

    def __init__(
        self,
        *,
        loop: Any | None = None,
        workspace: Path,
        registry: Any,
        sessions: Any,
        pending_queues: dict[str, Any],
        cron_service: Any | None = None,
        confirmation_store: PendingConfirmationStore | None = None,
        audit_mode: str = "minimal",
        runtime_profile: str = "default",
        domain_pack_manager: Any | None = None,
        background_review_service: Any | None = None,
        curator_service: Any | None = None,
        session_search_index_service: Any | None = None,
        evolution_config: Any | None = None,
    ) -> None:
        self._loop = loop
        self._workspace = Path(workspace)
        self._registry = registry
        self._sessions = sessions
        self._pending_queues = pending_queues
        self._cron_service = cron_service
        self._confirmation_store = confirmation_store
        self._audit_mode = audit_mode
        self._runtime_profile = runtime_profile
        self._domain_pack_manager = domain_pack_manager
        self._background_review_service = background_review_service
        self._curator_service = curator_service
        self._session_search_index_service = session_search_index_service
        self._evolution_config = evolution_config

    def current_loop_summary(self) -> dict[str, Any]:
        """Return the current loop fields used by the task-level self tool."""

        loop = self._loop
        if loop is None:
            return {}
        return {
            "max_iterations": getattr(loop, "max_iterations", None),
            "context_window_tokens": getattr(loop, "context_window_tokens", None),
            "model": getattr(loop, "model", None),
            "workspace": getattr(loop, "workspace", None),
            "provider_retry_mode": getattr(loop, "provider_retry_mode", None),
            "max_tool_result_chars": getattr(loop, "max_tool_result_chars", None),
            "_current_iteration": getattr(loop, "_current_iteration", None),
            "web_config": getattr(loop, "web_config", None),
            "exec_config": getattr(loop, "exec_config", None),
            "subagents": getattr(loop, "subagents", None),
            "_last_usage": getattr(loop, "_last_usage", None),
            "scratchpad": getattr(loop, "_runtime_vars", {}),
        }

    def system_status(self) -> dict[str, Any]:
        """Return the redacted system-level status used by runtime_status."""

        domain_status = self._domain_pack_status(self._domain_pack_manager)
        background_review_status = self._service_status(
            self._background_review_service,
            defaults={
                "background_review_enabled": False,
                "background_review_running_count": 0,
                "background_review_proposal_count": 0,
                "background_review_pending_count": 0,
                "background_review_last_created_at": None,
                "background_review_last_result": None,
            },
        )
        curator_status = self._service_status(
            self._curator_service,
            defaults={
                "curator_enabled": False,
                "curator_running_count": 0,
                "curator_proposal_count": 0,
                "curator_pending_count": 0,
                "curator_last_created_at": None,
                "curator_last_result": None,
                "curator_type_counts": {},
            },
        )
        workflow_status = self._workflow_artifact_status(self._workspace)
        skill_status = self._skill_lifecycle_status(self._workspace, self._domain_pack_manager)
        session_search_status = self._session_search_status(self._session_search_index_service)
        evolution_status = self._evolution_status(self._workspace, self._evolution_config)
        self_model = SelfModelService(
            self._workspace,
            registry=self._registry,
            sessions=self._sessions,
            pending_queues=self._pending_queues,
            cron_service=self._cron_service,
            confirmation_store=self._confirmation_store,
            audit_mode=self._audit_mode,
            runtime_profile=self._runtime_profile,
            domain_pack_manager=self._domain_pack_manager,
            background_review_service=self._background_review_service,
            curator_service=self._curator_service,
        ).build()
        return {
            "workspace_present": self._workspace.exists(),
            "workspace_name": self._workspace.name,
            "registered_tools_count": _safe_len(getattr(self._registry, "tool_names", [])),
            "active_sessions_count": _session_count(self._sessions),
            "pending_queue_count": len(self._pending_queues),
            "runtime_profile": self._runtime_profile,
            "audit_mode": self._audit_mode,
            "cron_available": self._cron_service is not None,
            "confirmation_available": self._confirmation_store is not None,
            **domain_status,
            **background_review_status,
            **curator_status,
            **skill_status,
            **workflow_status,
            **session_search_status,
            "evolution": evolution_status,
            "self_model": self_model,
        }

    @staticmethod
    def _domain_pack_status(manager: Any | None) -> dict[str, Any]:
        if manager is None:
            return {
                "domain_packs_count": 0,
                "active_domain_pack_ids": [],
                "registered_domain_tools_count": 0,
                "skipped_domain_tools_count": 0,
                "workspace_domain_pack_count": 0,
                "builtin_domain_pack_count": 0,
                "domain_pack_status_counts": {},
                "active_domain_pack_count": 0,
                "domain_pack_override_count": 0,
                "domain_pack_eval_status_counts": {},
                "last_domain_pack_event_at": None,
            }
        governance = summarize_domain_pack_governance(getattr(manager, "workspace", Path(".")), manager)
        try:
            packs = manager.list_packs()
        except Exception:
            return {
                "domain_packs_count": 0,
                "active_domain_pack_ids": [],
                "registered_domain_tools_count": 0,
                "skipped_domain_tools_count": 0,
                **governance,
            }
        counts = manager.domain_tool_runtime_counts() if hasattr(manager, "domain_tool_runtime_counts") else {}
        return {
            "domain_packs_count": len(packs),
            "active_domain_pack_ids": [pack.id for pack in packs if getattr(pack, "active", False)],
            "registered_domain_tools_count": int(counts.get("registered", 0) or 0),
            "skipped_domain_tools_count": int(counts.get("skipped", 0) or 0),
            **governance,
        }

    @staticmethod
    def _service_status(service: Any | None, *, defaults: dict[str, Any]) -> dict[str, Any]:
        if service is None or not hasattr(service, "runtime_status"):
            return dict(defaults)
        try:
            return dict(service.runtime_status())
        except Exception:
            return dict(defaults)

    @staticmethod
    def _workflow_artifact_status(workspace: Path) -> dict[str, Any]:
        try:
            return summarize_workflow_artifacts(workspace)
        except Exception:
            return {
                "workflow_artifacts_count": 0,
                "workflow_artifact_status_counts": {},
                "invalid_workflow_artifacts_count": 0,
            }

    @staticmethod
    def _skill_lifecycle_status(workspace: Path, domain_pack_manager: Any | None) -> dict[str, Any]:
        try:
            loader = SkillsLoader(workspace, domain_pack_manager=domain_pack_manager)
            return loader.lifecycle.stats(loader.list_skills(filter_unavailable=False))
        except Exception:
            return {
                "skills_count": 0,
                "workspace_skills_count": 0,
                "skill_lifecycle_status_counts": {},
                "skill_verification_status_counts": {},
                "unverified_skill_count": 0,
                "deprecated_skill_count": 0,
                "rejected_skill_count": 0,
                "always_workspace_skill_count": 0,
            }

    @staticmethod
    def _session_search_status(service: Any | None) -> dict[str, Any]:
        defaults = {
            "session_search_backend": "literal",
            "session_search_semantic_enabled": False,
            "session_search_index_available": False,
            "session_search_indexed_doc_count": 0,
            "session_search_indexed_source_counts": {},
            "session_search_index_stale": False,
            "session_search_refresh_running": False,
            "session_search_last_indexed_at": None,
            "session_search_last_index_error": None,
            "session_search_skipped_secret_risk_count": 0,
        }
        if service is None or not hasattr(service, "runtime_status"):
            return defaults
        try:
            return {**defaults, **dict(service.runtime_status())}
        except Exception:
            return defaults

    @staticmethod
    def _evolution_status(workspace: Path, config: Any | None) -> dict[str, Any]:
        try:
            if config is None:
                from OriginAgent.config.schema import EvolutionConfig

                config = EvolutionConfig()
            from OriginAgent.agent.evolution import OpportunitySignalStore

            status = OpportunitySignalStore(workspace).runtime_status(config)
            from OriginAgent.agent.background_review import ReviewProposalStore
            from OriginAgent.agent.evolution import AUTO_EVOLUTION_ORIGIN
            from OriginAgent.agent.evolution_dependencies import EvolutionDependencyStore
            from OriginAgent.agent.evolution_feedback import feedback_status
            from OriginAgent.agent.evolution_outcomes import EvolutionOutcomeStore
            from OriginAgent.agent.evolution_sandbox import sandbox_status_counts, trial_policy_status
            from OriginAgent.agent.evolution_snapshots import EvolutionSnapshotStore

            proposal_store = ReviewProposalStore(workspace)
            proposal_stats = proposal_store.stats(origin=AUTO_EVOLUTION_ORIGIN)
            outcome_stats = EvolutionOutcomeStore(workspace).stats()
            snapshot_stats = EvolutionSnapshotStore(workspace).stats()
            dependency_stats = EvolutionDependencyStore(workspace).stats()
            feedback_stats = feedback_status(workspace, config)
            recent_records = proposal_store.list_records(
                origin=AUTO_EVOLUTION_ORIGIN,
                limit=50,
            )
            applied_records = proposal_store.list_records(
                origin=AUTO_EVOLUTION_ORIGIN,
                status="applied",
                limit=50,
            )
            pending_records = proposal_store.list_records(
                origin=AUTO_EVOLUTION_ORIGIN,
                status="pending",
                limit=50,
            )
            issue_counts: dict[str, int] = {}
            for record in pending_records:
                payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
                gate = payload.get("promotion_gate") if isinstance(payload.get("promotion_gate"), dict) else {}
                if not gate:
                    gate = payload.get("static_gate") if isinstance(payload.get("static_gate"), dict) else {}
                counts = gate.get("issue_counts") if isinstance(gate.get("issue_counts"), dict) else {}
                for severity, count in counts.items():
                    try:
                        issue_counts[str(severity)] = issue_counts.get(str(severity), 0) + int(count)
                    except (TypeError, ValueError):
                        continue
            promotion_gate_counts: dict[str, int] = {}
            for record in recent_records:
                payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
                gate = payload.get("promotion_gate") if isinstance(payload.get("promotion_gate"), dict) else {}
                decision = str(gate.get("decision") or "").strip()
                if decision:
                    promotion_gate_counts[decision] = promotion_gate_counts.get(decision, 0) + 1
            sandbox_counts = sandbox_status_counts(workspace)
            trial_status = trial_policy_status(config)
            auto_verified = 0
            for record in applied_records:
                payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
                evolution = payload.get("evolution") if isinstance(payload.get("evolution"), dict) else {}
                if (
                    str(record.get("proposal_type") or "") == "workflow"
                    and str(evolution.get("origin") or "") == AUTO_EVOLUTION_ORIGIN
                    and str(record.get("review_reason") or "") == "auto_evolution verified low-risk workflow proposal"
                ):
                    auto_verified += 1
            return {
                **status,
                "pending_proposals_from_evolution": proposal_stats["pending_count"],
                "proposal_count_from_evolution": proposal_stats["proposal_count"],
                "auto_verified_workflows_count": auto_verified,
                "maintenance": _evolution_maintenance_policy(config),
                "outcomes": outcome_stats,
                "snapshots": snapshot_stats,
                "dependencies": dependency_stats,
                "feedback_calibration": feedback_stats,
                "promotion_gate_decision_counts": promotion_gate_counts,
                "static_gate_issue_counts": issue_counts,
                "sandbox": {
                    "enabled": bool(getattr(getattr(config, "sandbox", None), "enabled", True)),
                    "passed_workflow_proposals": sandbox_counts.get("passed", 0),
                    "failed_workflow_proposals": sandbox_counts.get("failed", 0),
                    "blocked_workflow_proposals": sandbox_counts.get("blocked", 0),
                },
                "trial": trial_status,
            }
        except Exception:
            mode = str(getattr(config, "mode", "conservative") if config is not None else "conservative")
            dry_run = bool(getattr(config, "dry_run", True) if config is not None else True)
            return {
                "mode": mode,
                "dry_run": dry_run,
                "opportunity_signals_count": 0,
                "converted_signals_count": 0,
                "suppressed_signals_count": 0,
                "feedback_adjusted_signals_count": 0,
                "feedback_negative_signals_count": 0,
                "feedback_positive_signals_count": 0,
                "pending_proposals_from_evolution": 0,
                "proposal_count_from_evolution": 0,
                "auto_verified_workflows_count": 0,
                "outcomes": {
                    "outcome_event_count": 0,
                    "outcome_type_counts": {},
                    "gate_decision_counts": {},
                    "sandbox_status_counts": {},
                    "review_status_counts": {},
                    "promotion_status_counts": {},
                    "rollback_status_counts": {},
                    "last_outcome_at": None,
                    "archive": {
                        "archived_outcome_count": 0,
                        "last_archived_at": None,
                    },
                },
                "maintenance": _evolution_maintenance_policy(config),
                "snapshots": {
                    "snapshot_count": 0,
                    "snapshot_type_counts": {},
                    "last_snapshot_at": None,
                },
                "dependencies": {
                    "tracked_artifacts": 0,
                    "dependency_edges": 0,
                    "rollback_blocked_artifacts": 0,
                    "stale_reference_count": 0,
                },
                "feedback_calibration": {
                    "enabled": True,
                    "processed_event_count": 0,
                    "feedback_event_count": 0,
                    "feedback_polarity_counts": {},
                    "last_calibrated_at": None,
                    "last_result": None,
                },
                "promotion_gate_decision_counts": {},
                "static_gate_issue_counts": {},
                "sandbox": {
                    "enabled": True,
                    "passed_workflow_proposals": 0,
                    "failed_workflow_proposals": 0,
                    "blocked_workflow_proposals": 0,
                },
                "trial": {
                    "enabled": True,
                    "isolated_workspace": True,
                    "read_only_tools_only": True,
                    "allowed_tools": ["glob", "grep", "read_file"],
                    "blocked_tools": ["cron", "edit_file", "exec", "message", "spawn", "write_file"],
                    "temp_dir_configured": False,
                },
                "skill_candidates_enabled": False,
                "eligible_workflow_signals": 0,
                "eligible_skill_signals": 0,
                "high_score_signals": [],
            }


def _safe_len(value: Any) -> int:
    try:
        return len(value)
    except Exception:
        return 0


def _session_count(sessions: Any) -> int:
    for attr in ("sessions", "_sessions"):
        value = getattr(sessions, attr, None)
        if value is not None:
            return _safe_len(value)
    return 0


def _evolution_maintenance_policy(config: Any | None) -> dict[str, Any]:
    return {
        "outcome_retention_days": int(
            getattr(config, "outcome_retention_days", 90) if config is not None else 90
        ),
        "outcome_archive_enabled": bool(
            getattr(config, "outcome_archive_enabled", True) if config is not None else True
        ),
        "dependency_stale_cleanup_enabled": bool(
            getattr(config, "dependency_stale_cleanup_enabled", True) if config is not None else True
        ),
    }
