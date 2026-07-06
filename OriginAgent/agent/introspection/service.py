"""Shared read-only runtime introspection service."""

from __future__ import annotations

import json
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from OriginAgent.agent.action_summary import action_summary_from_loop
from OriginAgent.agent.cognitive_audit import JsonlCognitiveAuditLedger
from OriginAgent.agent.cognitive_scheduler import JsonlCognitiveSchedulerLedger
from OriginAgent.agent.confirmation import PendingConfirmationStore
from OriginAgent.agent.domain_pack_governance import summarize_domain_pack_governance
from OriginAgent.agent.facts import FactStore, summarize_facts
from OriginAgent.agent.local_awareness import normalize_local_awareness_summary
from OriginAgent.agent.memory import MemoryStore
from OriginAgent.agent.runtime_mode import build_runtime_mode_summary
from OriginAgent.agent.runtime_models import RuntimeContextSnapshot
from OriginAgent.agent.scope import ScopeResolver
from OriginAgent.agent.self_model import SelfModelService, _workspace_memory_state
from OriginAgent.agent.skills import SkillsLoader
from OriginAgent.agent.workflow_artifacts import summarize_workflow_artifacts
from OriginAgent.config.doctor import build_config_doctor_report
from OriginAgent.config.loader import get_config_path
from OriginAgent.config.schema import AgentDefaults
from OriginAgent.memory.candidates import GovernedMemoryWriter
from OriginAgent.memory.policy import nearline_runtime_enabled
from OriginAgent.memory.store import NearlineMemoryStore


def _resolve_state_for_inspection(loop: Any, session_key: str | None) -> Any | None:
    """Peek at the SessionStateHolder entry for *session_key* without creating one.

    This is a read-only inspector helper that avoids the dual-write pattern.
    Returns ``None`` when there is no session_key or no tracked state.
    """
    if session_key is None:
        return None
    state_holder = getattr(loop, "_state_holder", None)
    if state_holder is None:
        return None
    # Access internal dict directly — this is an inspection-only path
    # that must not create entries.
    states = getattr(state_holder, "_states", None)
    if states is None or session_key not in states:
        return None
    return state_holder.get(session_key)


class RuntimeIntrospectionService:
    """Build safe read models for runtime introspection tools.

    The service deliberately has no mutation methods. Tools such as ``my`` may
    still mutate their own narrow allowlist directly, while system status tools
    consume these read-only projections.
    """

    CONTINUITY_CONTRACT_VERSION = "continuity.v1.freeze"

    def __init__(
        self,
        *,
        loop: Any | None = None,
        workspace: Path,
        registry: Any,
        sessions: Any,
        pending_queues: dict[str, Any],
        nearline_memory_config: Any | None = None,
        cron_service: Any | None = None,
        confirmation_store: PendingConfirmationStore | None = None,
        audit_mode: str = "minimal",
        runtime_profile: str = "default",
        domain_pack_manager: Any | None = None,
        background_review_service: Any | None = None,
        curator_service: Any | None = None,
        nearline_memory_service: Any | None = None,
        session_search_index_service: Any | None = None,
        evolution_config: Any | None = None,
        effective_config: Any | None = None,
    ) -> None:
        self._loop = loop
        self._workspace = Path(workspace)
        self._registry = registry
        self._sessions = sessions
        self._pending_queues = pending_queues
        self._nearline_memory_config = nearline_memory_config
        self._cron_service = cron_service
        self._confirmation_store = confirmation_store
        self._audit_mode = audit_mode
        self._runtime_profile = runtime_profile
        self._domain_pack_manager = domain_pack_manager
        self._background_review_service = background_review_service
        self._curator_service = curator_service
        self._nearline_memory_service = nearline_memory_service
        self._session_search_index_service = session_search_index_service
        self._evolution_config = evolution_config
        self._effective_config = effective_config

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
            "continuity": self.continuity_summary(),
            "cognition": self.cognition_summary(),
            "meta_cognition": self.meta_cognition_summary(),
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
        subagent_status = self._subagent_status(self._loop)
        reminder_status = self._reminder_status(self._cron_service)
        background_tasks = self.background_task_summary(
            background_review_status=background_review_status,
            curator_status=curator_status,
        )
        snapshot = self.runtime_context_snapshot(
            domain_status=domain_status,
            background_review_status=background_review_status,
            curator_status=curator_status,
        )
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
            nearline_memory_config=self._resolve_nearline_memory_config(),
            runtime_snapshot=snapshot,
        ).build()
        runtime_mode = self.runtime_mode_summary()
        config_doctor = self.config_doctor_report()
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
            **self._prefixed_task_status(background_review_status, "background_review"),
            **curator_status,
            **self._prefixed_task_status(curator_status, "curator"),
            **skill_status,
            **workflow_status,
            **session_search_status,
            **subagent_status,
            **reminder_status,
            "background_tasks": background_tasks,
            "evolution": evolution_status,
            "local_awareness": self.local_awareness_summary(),
            "self_model": self_model,
            "meta_cognition": self.meta_cognition_summary(),
            "runtime_mode": runtime_mode,
            "config_doctor": config_doctor,
        }

    def runtime_mode_summary(self) -> dict[str, Any]:
        """Return the normalized runtime mode summary."""

        return build_runtime_mode_summary(
            config=self._effective_config,
            loop=self._loop,
            enabled_channels=getattr(getattr(self._loop, "channel_manager", None), "enabled_channels", None),
            model=getattr(self._loop, "model", None),
            provider_name=(
                self._effective_config.get_provider_name(getattr(self._loop, "model", None))
                if self._effective_config is not None
                else None
            ),
        ).to_dict()

    def config_doctor_report(self) -> dict[str, Any]:
        """Return a sanitized config doctor report."""

        if self._effective_config is None:
            return {
                "raw_config_available": False,
                "effective_config": {},
                "unknown_fields": [],
                "ignored_fields": [],
                "conflicts": [],
                "capability_warnings": [],
                "channel_provider_matrix": [],
                "legacy_channel_sections": [],
            }
        return build_config_doctor_report(
            config=self._effective_config,
            config_path=get_config_path(),
        ).to_dict()

    def runtime_context_snapshot(
        self,
        *,
        domain_status: dict[str, Any] | None = None,
        background_review_status: dict[str, Any] | None = None,
        curator_status: dict[str, Any] | None = None,
    ) -> RuntimeContextSnapshot:
        domain_status = domain_status or self._domain_pack_status(self._domain_pack_manager)
        background_review_status = background_review_status or self._service_status(
            self._background_review_service,
            defaults={},
        )
        curator_status = curator_status or self._service_status(
            self._curator_service,
            defaults={},
        )
        reviews = self._review_snapshot()
        confirmations = self._confirmation_snapshot()
        background_tasks = self.background_task_summary(
            background_review_status=background_review_status,
            curator_status=curator_status,
        )
        facts_summary = self._facts_summary()
        memory_summary = self._memory_summary()
        nearline_memory_summary = self._nearline_memory_summary()
        return RuntimeContextSnapshot(
            runtime={
                "registered_tools_count": _safe_len(getattr(self._registry, "tool_names", [])),
                "active_sessions_count": _session_count(self._sessions),
                "pending_queue_count": len(self._pending_queues),
                "cron_available": self._cron_service is not None,
                "confirmation_available": self._confirmation_store is not None,
                "background_review_enabled": bool(background_review_status.get("background_review_enabled")),
                "curator_enabled": bool(curator_status.get("curator_enabled")),
                "local_awareness": self.local_awareness_summary(),
            },
            continuity=self.continuity_summary(),
            action=self._action_summary(),
            confirmations=confirmations,
            reviews=reviews,
            background_tasks=background_tasks,
            domains_summary=domain_status,
            skills_summary=self._skill_lifecycle_status(self._workspace, self._domain_pack_manager),
            facts_summary=facts_summary,
            memory_summary=memory_summary,
            nearline_memory_summary=nearline_memory_summary,
        )

    def continuity_summary(self) -> dict[str, Any]:
        loop = self._loop
        if loop is None:
            return {}
        working_memory = getattr(loop, "working_memory", None)
        sessions = getattr(loop, "sessions", None)
        session_key = getattr(loop, "_last_continuity_session_key", None)

        # Read from SessionStateHolder — no flat-attribute dual-writes.
        state = _resolve_state_for_inspection(loop, session_key)

        runtime_context = getattr(state, "last_runtime_context", None) if state is not None else None
        last_context_assembly = dict(getattr(state, "last_context_assembly", {}) or {}) if state is not None else {}
        recovered_checkpoint = (
            dict(getattr(state, "last_recovered_continuity_checkpoint", {}) or {})
            if state is not None else {}
        )
        out: dict[str, Any] = {
            "contract_version": self.CONTINUITY_CONTRACT_VERSION,
            "enabled": bool(working_memory is not None),
            "current_session_key": session_key,
            "last_context_assembly": last_context_assembly,
            "recovered_continuity_checkpoint": recovered_checkpoint,
        }
        if last_context_assembly:
            out["assembly"] = {
                "contract_version": str(
                    last_context_assembly.get("contract_version") or self.CONTINUITY_CONTRACT_VERSION
                ),
                "assembler_role": str(last_context_assembly.get("assembler_role") or ""),
                "assembly_order": list(last_context_assembly.get("assembly_order") or []),
                "block_kinds": list(last_context_assembly.get("block_kinds") or []),
                "continuity_block_kinds": list(last_context_assembly.get("continuity_block_kinds") or []),
                "reference_sources": list(last_context_assembly.get("reference_sources") or []),
                "retrieval_sources_used": list(last_context_assembly.get("retrieval_sources_used") or []),
                "world_selection_reasons": list(last_context_assembly.get("world_selection_reasons") or []),
            }
            out["budget"] = dict(last_context_assembly.get("budget") or {})
        if runtime_context is not None:
            out["runtime_context"] = {
                "actor_id": runtime_context.actor_id,
                "user_id": runtime_context.user_id,
                "session_id": runtime_context.session_id,
                "device_id": runtime_context.device_id,
                "trigger": runtime_context.trigger,
                "source": runtime_context.source,
                "scope": runtime_context.default_scope,
            }
        if working_memory is not None and sessions is not None and session_key:
            try:
                session = sessions.get_or_create(session_key)
                out["working_memory"] = working_memory.inspect(
                    session,
                    identity=runtime_context.identity if runtime_context is not None else None,
                )
            except Exception:
                out["working_memory"] = {}
        out["governance"] = dict(getattr(state, "last_governance_audit", {}) or {}) if state is not None else {}
        world_state = getattr(loop, "world_state", None)
        if world_state is not None and sessions is not None and session_key:
            try:
                session = sessions.get_or_create(session_key)
                out["world_state"] = world_state.inspect(
                    session,
                    identity=runtime_context if runtime_context is not None else None,
                )
            except Exception:
                out["world_state"] = {}
        return out

    def _action_summary(self) -> dict[str, Any]:
        return action_summary_from_loop(self._loop)

    def local_awareness_summary(self) -> dict[str, Any]:
        loop = self._loop
        config = getattr(getattr(loop, "tools_config", None), "local_awareness", None) if loop is not None else None
        cached = dict(getattr(loop, "_last_local_awareness_summary", {}) or {}) if loop is not None else {}
        backend = getattr(loop, "_local_awareness_backend", None) if loop is not None else None
        world_state = getattr(loop, "world_state", None) if loop is not None else None
        sessions = getattr(loop, "sessions", None) if loop is not None else None
        runtime_context = getattr(loop, "_last_runtime_context", None) if loop is not None else None
        session_key = getattr(loop, "_last_continuity_session_key", None) if loop is not None else None
        if world_state is not None and sessions is not None and runtime_context is not None and session_key:
            with suppress(Exception):
                session = sessions.get_or_create(session_key)
                if hasattr(world_state, "media_state_summary"):
                    cached.update(world_state.media_state_summary(session, identity=runtime_context))
                if hasattr(world_state, "device_state_summary"):
                    cached.update(world_state.device_state_summary(session, identity=runtime_context))
                if hasattr(world_state, "home_state_observability"):
                    cached.update(world_state.home_state_observability(session, identity=runtime_context))
        return normalize_local_awareness_summary(config, backend=backend, cached=cached)

    def cognition_summary(self) -> dict[str, Any]:
        loop = self._loop
        enabled = bool(getattr(loop, "_cognitive_loop_enabled", False)) if loop is not None else False
        messaging_enabled = bool(getattr(getattr(loop, "_active_intent_config", None), "enabled", False)) if loop is not None else False
        sidecar = getattr(loop, "cognitive_loop", None) if loop is not None else None
        scheduler = getattr(loop, "cognitive_scheduler", None) if loop is not None else None
        ledger = JsonlCognitiveAuditLedger(self._workspace)
        scheduler_ledger = JsonlCognitiveSchedulerLedger(self._workspace)
        summary = ledger.summary(limit=20)
        summary.update(scheduler_ledger.summary(limit=20))
        summary["enabled"] = enabled
        summary["messaging_enabled"] = messaging_enabled
        if loop is not None:
            summary["latest_scan"] = getattr(loop, "_last_cognitive_scan", {})
        if sidecar is not None:
            summary["config"] = {
                "enabled": bool(getattr(sidecar.config, "enabled", False)),
                "interval_seconds": int(getattr(sidecar.config, "interval_seconds", 0) or 0),
            }
        if scheduler is not None:
            summary["scheduler"] = scheduler.runtime_status()
        return summary

    def meta_cognition_summary(self) -> dict[str, Any]:
        loop = self._loop
        disabled_payload = {
            "contract_version": "meta_cognition.v1.freeze",
            "enabled": False,
            "trigger_collection_enabled": False,
            "structured_reflection_enabled": False,
            "pattern_consolidation_enabled": False,
            "evolution_bridge_enabled": False,
            "runtime_status": {},
            "recent_triggers": [],
            "recent_decisions": [],
            "recent_journals": [],
            "recent_reflections": [],
            "recent_confidence_traces": [],
            "recent_patterns": [],
            "recent_evolution_seeds": [],
            "decision_counts": {},
            "suppression_reason_counts": {},
            "uncertainty_stats": {"avg": 0.0, "max": 0.0, "high_count": 0, "threshold": 0.5},
            "artifact_status": {},
            "working_memory_bridge": {"enabled": False, "last_status": "disabled", "decision_counts": {}},
            "memory_candidate_bridge": {"enabled": False, "last_status": "disabled", "decision_counts": {}},
            "bridge_decision_counts": {},
            "pattern_counts": {},
            "seed_counts": {},
            "last_signal_upserts": [],
        }
        if loop is None:
            return disabled_payload
        runtime = getattr(loop, "_meta_cognition_runtime", None)
        if runtime is None:
            return disabled_payload
        summary = dict(runtime.summary() or {})
        reflector = getattr(loop, "_meta_cognition_reflector", None)
        if reflector is not None:
            artifact_summary = reflector.runtime_status()
            summary["structured_reflection_enabled"] = bool(
                artifact_summary.get("structured_reflection_enabled", False)
            )
            summary["pattern_consolidation_enabled"] = bool(
                getattr(reflector, "pattern_consolidation_enabled", False)
            )
            summary["evolution_bridge_enabled"] = bool(
                getattr(reflector, "evolution_bridge_enabled", False)
            )
            summary["artifact_status"] = dict(artifact_summary.get("artifact_status") or {})
            summary["bridge_decision_counts"] = dict(artifact_summary.get("bridge_decision_counts") or {})
            recent_artifacts = reflector.recent_artifacts(limit=10)
            summary["recent_journals"] = list(recent_artifacts.get("recent_journals") or [])
            summary["recent_reflections"] = list(recent_artifacts.get("recent_reflections") or [])
            summary["recent_confidence_traces"] = list(recent_artifacts.get("recent_confidence_traces") or [])
            summary["recent_patterns"] = list(recent_artifacts.get("recent_patterns") or [])
            summary["recent_evolution_seeds"] = list(recent_artifacts.get("recent_evolution_seeds") or [])
            summary["working_memory_bridge"] = dict(
                artifact_summary.get("artifact_status", {}).get("working_memory_bridge") or {}
            )
            summary["memory_candidate_bridge"] = dict(
                artifact_summary.get("artifact_status", {}).get("memory_candidate_bridge") or {}
            )
            summary["pattern_counts"] = {
                "written": int(summary.get("artifact_status", {}).get("patterns_written", 0) or 0),
            }
            summary["seed_counts"] = {
                "written": int(summary.get("artifact_status", {}).get("evolution_seeds_written", 0) or 0),
            }
            summary["last_signal_upserts"] = list(artifact_summary.get("last_signal_upserts") or [])[:10]
        else:
            summary.setdefault("structured_reflection_enabled", False)
            summary.setdefault("pattern_consolidation_enabled", False)
            summary.setdefault("evolution_bridge_enabled", False)
            summary.setdefault("artifact_status", {})
            summary.setdefault("bridge_decision_counts", {})
            summary.setdefault("recent_journals", [])
            summary.setdefault("recent_reflections", [])
            summary.setdefault("recent_confidence_traces", [])
            summary.setdefault("recent_patterns", [])
            summary.setdefault("recent_evolution_seeds", [])
            summary.setdefault(
                "working_memory_bridge",
                {"enabled": False, "last_status": "disabled", "decision_counts": {}},
            )
            summary.setdefault(
                "memory_candidate_bridge",
                {"enabled": False, "last_status": "disabled", "decision_counts": {}},
            )
            summary.setdefault("pattern_counts", {})
            summary.setdefault("seed_counts", {})
            summary.setdefault("last_signal_upserts", [])
        summary.setdefault("uncertainty_stats", {"avg": 0.0, "max": 0.0, "high_count": 0, "threshold": 0.5})
        coordinator = getattr(loop, "_meta_coordinator", None)
        if coordinator is not None:
            last_summary = dict(coordinator.summary or {})
            last_artifacts = dict(coordinator.last_artifacts or {})
        else:
            last_summary = {}
            last_artifacts = {}
        if last_summary:
            summary.setdefault("runtime_status", last_summary.get("runtime_status", {}))
            summary.setdefault("artifact_status", last_summary.get("artifact_status", {}))
            summary.setdefault("bridge_decision_counts", last_summary.get("bridge_decision_counts", {}))
            summary.setdefault("fast_path_decision_counts", last_summary.get("fast_path_decision_counts", {}))
        if last_artifacts:
            summary.setdefault("recent_journals", list(last_artifacts.get("recent_journals") or []))
            summary.setdefault("recent_reflections", list(last_artifacts.get("recent_reflections") or []))
            summary.setdefault("recent_confidence_traces", list(last_artifacts.get("recent_confidence_traces") or []))
            summary.setdefault("recent_patterns", list(last_artifacts.get("recent_patterns") or []))
            summary.setdefault("recent_evolution_seeds", list(last_artifacts.get("recent_evolution_seeds") or []))
        summary.setdefault("fast_path_decision_counts", {})
        return summary

    def inspect_context(self) -> dict[str, Any]:
        """Return a minimal Phase 1 debug view of the assembled context."""

        loop = self._loop
        runtime_context = getattr(loop, "_last_runtime_context", None) if loop is not None else None
        session_key = getattr(loop, "_last_continuity_session_key", None) if loop is not None else None
        continuity = self.continuity_summary()
        conversation_view = self._conversation_view(session_key)
        retrieval_view = self._retrieval_view(
            session_key=session_key,
            runtime_context=runtime_context,
        )
        world_view = self._world_view(
            session_key=session_key,
            runtime_context=runtime_context,
        )
        working_memory = continuity.get("working_memory", {}) if isinstance(continuity, dict) else {}
        current_scope = ScopeResolver.normalize_scope(
            getattr(runtime_context, "default_scope", None),
        )
        return {
            "contract_version": self.CONTINUITY_CONTRACT_VERSION,
            "enabled": bool(
                getattr(getattr(loop, "context", None), "_context_config", None)
                and getattr(loop.context._context_config, "enable_phase1_continuity", False)
            )
            if loop is not None
            else False,
            "session_key": session_key,
            "runtime_context": continuity.get("runtime_context", {}),
            "last_context_assembly": continuity.get("last_context_assembly", {}),
            "views": {
                "conversation": {
                    **conversation_view,
                    "current_message_preview": (
                        continuity.get("last_context_assembly", {}).get("current_message_preview", "")
                        if isinstance(continuity, dict)
                        else ""
                    ),
                    "injected_reference_sources": retrieval_view.get("dialogue_sources", []),
                    "injected_reference_blocks": retrieval_view.get("dialogue_blocks", []),
                },
                "working": {
                    "source": "RuntimeIntrospectionService.continuity_summary",
                    "runtime_context": continuity.get("runtime_context", {}),
                    "working_memory": working_memory,
                    "seeded_items": list(
                        continuity.get("last_context_assembly", {}).get("prewarm_seeded_items", {}).get("working", [])
                    )
                    if isinstance(continuity.get("last_context_assembly"), dict)
                    else [],
                },
                "retrieval": retrieval_view,
                "world": world_view,
                "action": self._action_summary(),
                "local_awareness": self.local_awareness_summary(),
                "meta_cognition": self.meta_cognition_summary(),
            },
            "governance": self._governance_view(continuity),
            "scope_filter": self._scope_filter_summary(
                current_scope=current_scope,
                current_owner_id=getattr(runtime_context, "user_id", None),
                working_memory=working_memory if isinstance(working_memory, dict) else {},
                world_view=world_view if isinstance(world_view, dict) else {},
            ),
            "context_assembly_trace": self.context_assembly_trace(),
        }

    def context_assembly_trace(self) -> dict[str, Any]:
        """Return a best-effort context assembly trace."""

        loop = self._loop
        trace = dict(getattr(getattr(loop, "context", None), "_last_context_assembly_audit", {}) or {})
        if not trace:
            trace = dict(getattr(loop, "_last_context_assembly", {}) or {})
        return {
            "contract_version": str(trace.get("contract_version") or self.CONTINUITY_CONTRACT_VERSION),
            "assembly_order": list(trace.get("assembly_order") or []),
            "block_kinds": list(trace.get("block_kinds") or []),
            "blocks": [
                dict(block)
                for block in list(trace.get("blocks") or [])
                if isinstance(block, dict)
            ],
            "reference_sources": list(trace.get("reference_sources") or []),
            "retrieval_sources_used": list(trace.get("retrieval_sources_used") or []),
            "world_selection_reasons": list(trace.get("world_selection_reasons") or []),
            "budget": dict(trace.get("budget") or {}),
            "trimmed_blocks": [
                dict(block)
                for block in list((trace.get("budget") or {}).get("trimmed_blocks") or [])
                if isinstance(block, dict)
            ],
            "media": {
                "requested_count": int(((trace.get("media") or {}).get("requested_count") or 0)),
                "accepted_count": int(((trace.get("media") or {}).get("accepted_count") or 0)),
                "text_included": bool((trace.get("media") or {}).get("text_included", False)),
                "accepted": [
                    dict(item)
                    for item in list((trace.get("media") or {}).get("accepted") or [])
                    if isinstance(item, dict)
                ],
                "rejected": [
                    dict(item)
                    for item in list((trace.get("media") or {}).get("rejected") or [])
                    if isinstance(item, dict)
                ],
            },
            "prewarm": {
                "enabled": bool(trace.get("prewarm_enabled", False)),
                "empty": bool(trace.get("prewarm_empty", False)),
                "reason": trace.get("prewarm_reason"),
                "sources": list(trace.get("prewarm_sources") or []),
                "seed_counts": dict(trace.get("prewarm_seed_counts") or {}),
            },
            "raw_trace": json.loads(json.dumps(trace, ensure_ascii=False, default=str)) if trace else {},
        }

    def background_task_summary(
        self,
        *,
        background_review_status: dict[str, Any] | None = None,
        curator_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        background_review_status = background_review_status or self._service_status(
            self._background_review_service,
            defaults={},
        )
        curator_status = curator_status or self._service_status(
            self._curator_service,
            defaults={},
        )
        dream_status = self._service_status(
            getattr(self._loop, "dream", None),
            defaults={},
        )
        auto_compact_status = self._service_status(
            getattr(self._loop, "auto_compact", None),
            defaults={},
        )
        nearline_status = self._service_status(
            self._nearline_memory_service,
            defaults={},
        )
        tasks = {
            "dream": dream_status,
            "nearline_memory": nearline_status,
            "background_review": background_review_status,
            "curator": curator_status,
            "auto_compact": auto_compact_status,
        }
        return {
            "task_count": len([value for value in tasks.values() if value]),
            "tasks": tasks,
        }

    def cron_desire_status(self) -> list[dict[str, Any]]:
        """Return a unified view of cron-desire links.

        Each entry joins:
        - cron job metadata (from CronService)
        - linked desire (from DesireStore)
        - delivery observation state (from CronObservationStore)
        """
        bridge = getattr(getattr(self._loop, "_host", None) if self._loop else None, "_cron_bridge", None)
        if bridge is None:
            return []
        obs = getattr(bridge, "_observation_store", None)
        if obs is None:
            return []
        try:
            links = obs.list_active_links()
        except Exception:
            return []
        cron_svc = self._cron_service
        results: list[dict[str, Any]] = []
        for link in links:
            entry: dict[str, Any] = {
                "cron_job_id": link["cron_job_id"],
                "desire_id": link["desire_id"],
                "created_at": link.get("created_at"),
                "last_delivery_at": link.get("last_delivery_at"),
                "last_delivery_status": link.get("last_delivery_status"),
                "consecutive_failures": link.get("consecutive_failures", 0),
                "cron_disabled": bool(link.get("cron_disabled", 0)),
            }
            if cron_svc is not None:
                job = cron_svc.get_job(link["cron_job_id"])
                if job is not None:
                    entry["cron_job_name"] = job.name
                    entry["cron_job_enabled"] = job.enabled
                    sched = job.schedule
                    if sched.kind == "at":
                        entry["timing"] = f"at {sched.at_ms}"
                    elif sched.kind == "every":
                        entry["timing"] = f"every {sched.every_ms}ms"
                    elif sched.kind == "cron":
                        entry["timing"] = f"cron {sched.expr}"
                    entry["last_status"] = job.state.last_status
                    entry["last_error"] = job.state.last_error
            results.append(entry)
        return results

    def _confirmation_snapshot(self) -> dict[str, Any]:
        store = self._confirmation_store
        if store is None:
            return {
                "pending_count": 0,
                "expired_count": 0,
                "kind_counts": {},
                "risk_counts": {},
            }
        try:
            confirmations = store.read_all()
        except Exception:
            return {
                "pending_count": 0,
                "expired_count": 0,
                "kind_counts": {},
                "risk_counts": {},
            }
        kind_counts: dict[str, int] = {}
        risk_counts: dict[str, int] = {}
        pending_count = 0
        expired_count = 0
        for confirmation in confirmations:
            kind = str(getattr(confirmation, "kind", "") or "unknown")
            risk = str(getattr(confirmation, "risk", "") or "unknown")
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            risk_counts[risk] = risk_counts.get(risk, 0) + 1
            if getattr(confirmation, "status", "") in {"pending", "notified"}:
                pending_count += 1
            elif getattr(confirmation, "status", "") == "expired":
                expired_count += 1
        return {
            "pending_count": pending_count,
            "expired_count": expired_count,
            "kind_counts": kind_counts,
            "risk_counts": risk_counts,
        }

    def _facts_summary(self) -> dict[str, Any]:
        try:
            store = FactStore(
                self._workspace,
                feature_flags=getattr(getattr(self._loop, "context", None), "memory", None).feature_flags
                if getattr(getattr(self._loop, "context", None), "memory", None) is not None
                else None,
            )
            return summarize_facts(self._workspace, fact_store=store)
        except Exception:
            return {}

    def _memory_summary(self) -> dict[str, Any]:
        try:
            store = MemoryStore(
                self._workspace,
                feature_flags=getattr(getattr(self._loop, "context", None), "memory", None).feature_flags
                if getattr(getattr(self._loop, "context", None), "memory", None) is not None
                else None,
            )
            content = store.read_memory()
            pending_history = store.read_unprocessed_history(
                since_cursor=store.get_last_dream_cursor(),
            )
            return {
                "has_memory_context": bool(content.strip()),
                "recent_history_pending_count": len(pending_history),
                "nearline": self._nearline_memory_summary(),
                **_workspace_memory_state(self._workspace),
            }
        except Exception:
            return {
                "has_memory_context": False,
                "recent_history_pending_count": 0,
                "nearline": self._nearline_memory_summary(),
                **_workspace_memory_state(self._workspace),
            }

    def _governance_view(self, continuity: dict[str, Any]) -> dict[str, Any]:
        producer = continuity.get("governance", {}) if isinstance(continuity, dict) else {}
        queue_backlog = {"total": 0, "by_kind": {}, "last_candidate_at": None}
        dream_pending = {
            "consumer": "dream",
            "cursor": 0,
            "pending_count": 0,
            "by_kind": {},
            "oldest_pending_at": None,
            "newest_pending_at": None,
        }
        nearline_pending = {
            "consumer": "nearline_profile",
            "cursor": 0,
            "pending_count": 0,
            "by_kind": {},
            "oldest_pending_at": None,
            "newest_pending_at": None,
        }
        with suppress(Exception):
            queue = GovernedMemoryWriter(self._workspace)
            queue_backlog = queue.summarize_queue()
            dream_pending = queue.pending_summary_for_consumer("dream", kinds=("fact", "constraint"))
            nearline_pending = queue.pending_summary_for_consumer(
                "nearline_profile",
                kinds=("preference", "task_pattern"),
            )
        dream_status = self._service_status(
            getattr(self._loop, "dream", None),
            defaults={
                "consumer_last_run": {
                    "consumer": "dream",
                    "consumed_count": 0,
                    "applied_count": 0,
                    "skipped_count": 0,
                    "duplicate_count": 0,
                    "cursor_before": 0,
                    "cursor_after": 0,
                    "last_run_at": None,
                    "reason": "not_available",
                },
                "forgetting_execution": {
                    "executed": True,
                    "working_memory_expired": False,
                    "working_memory_retained": True,
                    "working_memory_expired_session_keys": [],
                    "working_memory_retained_session_keys": [],
                    "stale_candidate_pruned_count": 0,
                    "fact_confidence_decayed_count": 0,
                    "fact_retention_changes": {},
                    "last_run_at": None,
                    "reason": "not_available",
                },
            },
        )
        nearline_status = self._service_status(
            self._nearline_memory_service,
            defaults={
                "profile_consumer_last_run": {
                    "consumer": "nearline_profile",
                    "consumed_count": 0,
                    "applied_count": 0,
                    "skipped_count": 0,
                    "duplicate_count": 0,
                    "cursor_before": 0,
                    "cursor_after": 0,
                    "last_run_at": None,
                    "reason": "not_available",
                },
            },
        )
        return {
            "promotions": list(producer.get("promotion_candidates", []))
            if isinstance(producer, dict)
            else [],
            "forgetting": list(producer.get("forgetting_actions", []))
            if isinstance(producer, dict)
            else [],
            "conflicts": int(producer.get("promotion_conflict_count", 0) or 0)
            if isinstance(producer, dict)
            else 0,
            "queue_backlog": {
                "total": int(queue_backlog.get("total", 0) or 0),
                "by_kind": dict(queue_backlog.get("by_kind", {}) or {}),
                "last_candidate_at": queue_backlog.get("last_candidate_at"),
                "oldest_pending_at": (
                    dream_pending.get("oldest_pending_at")
                    or nearline_pending.get("oldest_pending_at")
                ),
            },
            "consumer": {
                "dream": {
                    "last_run": dict(dream_status.get("consumer_last_run", {}) or {}),
                    "pending_backlog": dict(dream_pending),
                },
                "nearline_profile": {
                    "last_run": dict(nearline_status.get("profile_consumer_last_run", {}) or {}),
                    "pending_backlog": dict(nearline_pending),
                },
            },
            "forgetting_execution": dict(dream_status.get("forgetting_execution", {}) or {}),
            "stale_prune_counts": {
                "promotion_candidates": int(
                    (dream_status.get("forgetting_execution", {}) or {}).get("stale_candidate_pruned_count", 0) or 0
                ),
            },
        }

    def _nearline_memory_summary(self) -> dict[str, Any]:
        try:
            config = self._resolve_nearline_memory_config()
            if nearline_runtime_enabled(config):
                return NearlineMemoryStore(self._workspace).summary(
                    nearline_enabled=bool(config.enabled),
                    pipeline_enabled=bool(config.pipeline_enabled),
                    profile_shadow_write_enabled=bool(config.profile_shadow_write_enabled),
                )
            status = "disabled" if not bool(config.enabled) else "idle"
            return {
                "nearline_enabled": bool(config.enabled),
                "pipeline_enabled": bool(config.pipeline_enabled),
                "profile_shadow_write_enabled": bool(config.profile_shadow_write_enabled),
                "memcell_count": 0,
                "episode_count": 0,
                "foresight_count": 0,
                "agent_case_count": 0,
                "profile_count": 0,
                "last_memcell_at": None,
                "last_episode_at": None,
                "last_foresight_at": None,
                "last_agent_case_at": None,
                "last_profile_at": None,
                "latest_cursor": 0,
                "status": status,
            }
        except Exception:
            return {}

    def _resolve_nearline_memory_config(self) -> Any:
        config = getattr(self._nearline_memory_service, "config", None)
        if config is not None:
            return config
        if self._nearline_memory_config is not None:
            return self._nearline_memory_config
        loop_config = getattr(self._loop, "_nearline_memory_config", None)
        if loop_config is not None:
            return loop_config
        return AgentDefaults().nearline_memory

    def _review_snapshot(self) -> dict[str, Any]:
        store = getattr(self._background_review_service, "store", None)
        if store is None or not hasattr(store, "iter_all"):
            return {
                "pending_count": 0,
                "status_counts": {},
                "type_counts": {},
                "origin_counts": {},
            }
        try:
            records = list(store.iter_all())
        except Exception:
            return {
                "pending_count": 0,
                "status_counts": {},
                "type_counts": {},
                "origin_counts": {},
            }
        status_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        origin_counts: dict[str, int] = {}
        pending_count = 0
        for record in records:
            status = str(record.get("status") or "pending")
            proposal_type = str(record.get("proposal_type") or record.get("type") or "unknown")
            origin = str(record.get("origin") or "background_review")
            status_counts[status] = status_counts.get(status, 0) + 1
            type_counts[proposal_type] = type_counts.get(proposal_type, 0) + 1
            origin_counts[origin] = origin_counts.get(origin, 0) + 1
            if status == "pending":
                pending_count += 1
        return {
            "pending_count": pending_count,
            "status_counts": status_counts,
            "type_counts": type_counts,
            "origin_counts": origin_counts,
        }

    def _conversation_view(self, session_key: str | None) -> dict[str, Any]:
        defaults = {
            "source": "SessionManager.get_history",
            "message_count": 0,
            "messages": [],
        }
        sessions = self._sessions
        if not session_key or sessions is None or not hasattr(sessions, "get_or_create"):
            return defaults
        try:
            session = sessions.get_or_create(session_key)
        except Exception:
            return defaults
        if session is None or not hasattr(session, "get_history"):
            return defaults
        max_messages = int(getattr(self._loop, "_max_messages", 120) or 120)
        max_tokens = 0
        replay_budget = getattr(self._loop, "_replay_token_budget", None) if self._loop is not None else None
        if callable(replay_budget):
            try:
                max_tokens = int(replay_budget() or 0)
            except Exception:
                max_tokens = 0
        try:
            history = session.get_history(
                max_messages=max_messages,
                max_tokens=max_tokens,
                include_timestamps=True,
            )
        except Exception:
            return defaults
        return {
            **defaults,
            "message_count": len(history),
            "messages": [self._message_view(message) for message in history],
        }

    def _retrieval_view(
        self,
        *,
        session_key: str | None,
        runtime_context: Any | None,
    ) -> dict[str, Any]:
        defaults = {
            "source": "ContextBuilder.build_reference_context_blocks",
            "block_count": 0,
            "sources": [],
            "blocks": [],
            "dialogue_sources": [],
            "dialogue_blocks": [],
        }
        builder = getattr(self._loop, "context", None) if self._loop is not None else None
        if builder is None or not hasattr(builder, "build_reference_context_blocks"):
            return defaults
        try:
            blocks = builder.build_reference_context_blocks(
                session_summary=None,
                session_key=session_key,
                runtime_context=runtime_context,
                current_message=(
                    getattr(self._loop, "_last_context_assembly", {}).get("current_message_preview", "")
                    if self._loop is not None
                    else ""
                ),
            )
        except Exception:
            return defaults
        retrieval_blocks: list[dict[str, Any]] = []
        dialogue_blocks: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            meta = block.get("_meta", {}) if isinstance(block.get("_meta"), dict) else {}
            entry = {
                "kind": meta.get("kind"),
                "source": meta.get("source"),
                "text": _preview_text(block.get("text")),
            }
            if meta.get("source") == "recent_history":
                dialogue_blocks.append(entry)
            else:
                retrieval_blocks.append(entry)
        return {
            **defaults,
            "block_count": len(retrieval_blocks),
            "sources": [
                entry["source"]
                for entry in retrieval_blocks
                if isinstance(entry.get("source"), str) and entry["source"]
            ],
            "blocks": retrieval_blocks,
            "dialogue_sources": [
                entry["source"]
                for entry in dialogue_blocks
                if isinstance(entry.get("source"), str) and entry["source"]
            ],
            "dialogue_blocks": dialogue_blocks,
            "fusion_enabled": bool(getattr(builder, "_last_retrieval_fusion", {}).get("enabled")),
            "fusion_sources_used": list(getattr(builder, "_last_retrieval_fusion", {}).get("sources_used", [])),
            "fusion_source_counts": dict(getattr(builder, "_last_retrieval_fusion", {}).get("source_counts", {})),
            "fusion_deduped_count": int(getattr(builder, "_last_retrieval_fusion", {}).get("deduped_count", 0) or 0),
            "fusion_trimmed_count": int(getattr(builder, "_last_retrieval_fusion", {}).get("trimmed_count", 0) or 0),
            "fusion_scope_filtered": list(getattr(builder, "_last_retrieval_fusion", {}).get("scope_filtered", [])),
            "fusion_hits": dict(getattr(builder, "_last_retrieval_fusion", {}).get("hits", {})),
            "prewarm_hits": list(getattr(builder, "_last_retrieval_fusion", {}).get("hits", {}).get("prewarm_seed", [])),
        }

    def _world_view(
        self,
        *,
        session_key: str | None,
        runtime_context: Any | None,
    ) -> dict[str, Any]:
        defaults = {
            "source": "ContextBuilder.build_phase1_continuity_blocks",
            "snapshot": {
                "status": "placeholder",
                "version": "phase1",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "summary": {},
            "source_snapshot_ids": [],
            "fresh_until": None,
            "filtered_candidates": [],
            "freshness": {
                "generated_at": None,
                "fresh_until": None,
                "is_fresh": False,
            },
            "selection_reasons": [],
            "attention_write": {},
            "recent_events": [],
            "event_summary": {
                "total": 0,
                "by_kind": {},
                "latest_created_at": None,
            },
        }
        loop = self._loop
        sessions = getattr(loop, "sessions", None) if loop is not None else None
        world_state = getattr(loop, "world_state", None) if loop is not None else None
        if world_state is None or sessions is None or not session_key:
            return defaults
        try:
            session = sessions.get_or_create(session_key)
            snapshot = world_state.load(
                session,
                identity=runtime_context if runtime_context is not None else None,
            )
            filtered = (
                world_state.filtered_candidates(
                    session,
                    runtime_context=runtime_context,
                    current_message=(
                        getattr(loop, "_last_context_assembly", {}).get("current_message_preview", "")
                        if loop is not None
                        else ""
                    ),
                )
                if runtime_context is not None
                else {}
            )
            event_view = world_state.recent_events(
                session,
                identity=runtime_context if runtime_context is not None else None,
                limit=5,
            )
            home_state = world_state.home_state_summary(
                session,
                identity=runtime_context if runtime_context is not None else None,
            ) if runtime_context is not None and hasattr(world_state, "home_state_summary") else {}
            notices = world_state.attention_notices(
                session,
                identity=runtime_context if runtime_context is not None else None,
                limit=5,
            ) if runtime_context is not None and hasattr(world_state, "attention_notices") else {}
        except Exception:
            return defaults
        summary = (
            filtered.get("included_summary")
            if isinstance(filtered, dict) and filtered.get("included_summary")
            else snapshot.world_summary.to_json() if snapshot.world_summary is not None else {}
        )
        return {
            **defaults,
            "snapshot": snapshot.to_json(),
            "summary": summary,
            "source_snapshot_ids": list(summary.get("source_snapshot_ids") or []),
            "fresh_until": summary.get("fresh_until"),
            "filtered_candidates": (
                filtered.get("filtered_candidates", [])
                if isinstance(filtered, dict)
                else []
            ),
            "freshness": (
                filtered.get("freshness", defaults["freshness"])
                if isinstance(filtered, dict)
                else defaults["freshness"]
            ),
            "contested": (
                filtered.get("contested_summary", {})
                if isinstance(filtered, dict)
                else {}
            ),
            "selection_reasons": (
                filtered.get("selection_reasons", [])
                if isinstance(filtered, dict)
                else []
            ),
            "attention_write": dict(getattr(loop, "_last_world_attention_write", {}) or {}) if loop is not None else {},
            "recent_events": (
                event_view.get("recent_events", [])
                if isinstance(event_view, dict)
                else []
            ),
            "event_summary": (
                event_view.get("event_summary", defaults["event_summary"])
                if isinstance(event_view, dict)
                else defaults["event_summary"]
            ),
            "home_state": dict(home_state or {}),
            "attention_notices_summary": dict(notices.get("attention_notices_summary") or {}) if isinstance(notices, dict) else {},
            "suggested_next_steps": list(notices.get("suggested_next_steps") or []) if isinstance(notices, dict) else [],
        }

    def _scope_filter_summary(
        self,
        *,
        current_scope: str,
        current_owner_id: str | None,
        working_memory: dict[str, Any],
        world_view: dict[str, Any],
    ) -> dict[str, Any]:
        resolver = ScopeResolver()
        retrieval_scope = "user" if current_scope == "user" else "session"
        candidates = [
            self._scope_candidate(
                source="conversation_view",
                scope="session",
                owner_id=current_owner_id,
                current_scope=current_scope,
                current_owner_id=current_owner_id,
                resolver=resolver,
            ),
            self._scope_candidate(
                source="working_memory",
                scope=working_memory.get("scope"),
                owner_id=working_memory.get("owner_id"),
                current_scope=current_scope,
                current_owner_id=current_owner_id,
                resolver=resolver,
            ),
            self._scope_candidate(
                source="retrieval_view",
                scope=retrieval_scope,
                owner_id=current_owner_id if retrieval_scope == "user" else None,
                current_scope=current_scope,
                current_owner_id=current_owner_id,
                resolver=resolver,
            ),
            self._scope_candidate(
                source="world_view",
                scope=(
                    world_scope
                    if (world_scope := world_view.get("summary", {}).get("scope"))
                    else world_view.get("snapshot", {}).get("scope")
                ),
                owner_id=(
                    world_view.get("summary", {}).get("owner_id")
                    or world_view.get("snapshot", {}).get("owner_id")
                ),
                device_id=(
                    world_view.get("snapshot", {}).get("snapshots", [{}])[0].get("device_id")
                    if world_view.get("snapshot", {}).get("snapshots")
                    else None
                ),
                current_scope=current_scope,
                current_owner_id=current_owner_id,
                current_device_id=(
                    self.continuity_summary().get("runtime_context", {}).get("device_id")
                ),
                resolver=resolver,
            ),
        ]
        return {
            "source": "ScopeResolver.is_visible",
            "current_scope": current_scope,
            "current_owner_id": current_owner_id,
            "retrieval_scope_prefix_before": None,
            "retrieval_scope_prefix_after": "user" if current_scope == "user" else None,
            "visibility_matrix": {
                scope: resolver.is_visible(
                    scope=scope,
                    current_scope=current_scope,
                    owner_id=current_owner_id,
                    current_owner_id=current_owner_id,
                    current_device_id=self.continuity_summary().get("runtime_context", {}).get("device_id"),
                )
                for scope in ("task", "device", "session", "user")
            },
            "candidates": candidates,
        }

    @staticmethod
    def _scope_candidate(
        *,
        source: str,
        scope: str | None,
        owner_id: str | None,
        device_id: str | None = None,
        current_scope: str,
        current_owner_id: str | None,
        current_device_id: str | None = None,
        resolver: ScopeResolver,
    ) -> dict[str, Any]:
        normalized_scope = resolver.normalize_scope(scope)
        visible = resolver.is_visible(
            scope=normalized_scope,
            current_scope=current_scope,
            owner_id=owner_id,
            current_owner_id=current_owner_id,
            device_id=device_id,
            current_device_id=current_device_id,
        )
        return {
            "source": source,
            "scope": normalized_scope,
            "owner_id": owner_id,
            "device_id": device_id,
            "visible": visible,
            "reason": _visibility_reason(
                visible=visible,
                scope=normalized_scope,
                owner_id=owner_id,
                current_owner_id=current_owner_id,
                device_id=device_id,
                current_device_id=current_device_id,
            ),
        }

    @staticmethod
    def _message_view(message: dict[str, Any]) -> dict[str, Any]:
        view = {
            "role": message.get("role"),
            "content": _preview_message_content(message.get("content")),
        }
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            view["tool_call_count"] = len(tool_calls)
        return view

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
            "domain_pack_execution": [
                {
                    "id": pack.id,
                    "source": pack.source,
                    "executable_python_allowed": bool(getattr(pack, "executable_python_allowed", False)),
                    "override_execution_warning": getattr(pack, "override_execution_warning", None),
                }
                for pack in packs
            ],
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
    def _prefixed_task_status(status: dict[str, Any], prefix: str) -> dict[str, Any]:
        fields = (
            "last_status",
            "last_fault_class",
            "last_retryable",
            "last_degraded",
            "last_reason",
            "last_started_at",
            "last_finished_at",
            "consecutive_failures",
            "last_report",
        )
        return {
            f"{prefix}_{field}": status.get(field)
            for field in fields
            if field in status
        }

    @staticmethod
    def _reminder_status(cron_service: Any | None) -> dict[str, Any]:
        defaults = {
            "reminder_total": 0,
            "reminder_status_counts": {},
            "reminder_due_count": 0,
            "reminder_last_fired_at": None,
        }
        if cron_service is None:
            return defaults
        try:
            jobs = cron_service.list_jobs(include_disabled=True)
        except Exception:
            return defaults

        reminders = []
        for job in jobs:
            payload = getattr(job, "payload", None)
            if payload is None or not getattr(payload, "deliver", False):
                continue
            reminders.append(job)
        if not reminders:
            return defaults

        status_counts: dict[str, int] = {}
        due_count = 0
        last_fired_at: str | None = None
        now = datetime.now(timezone.utc)

        for job in reminders:
            state = getattr(job, "state", None)
            next_run_at_ms = getattr(state, "next_run_at_ms", None)
            last_run_at_ms = getattr(state, "last_run_at_ms", None)
            last_status = getattr(state, "last_status", None)

            if getattr(job, "enabled", False) is not True:
                status = "disabled"
            elif last_status == "success":
                status = "fired"
            elif next_run_at_ms is None:
                status = "completed"
            else:
                status = "pending"
            status_counts[status] = status_counts.get(status, 0) + 1

            if status == "pending" and isinstance(next_run_at_ms, int):
                next_run_at = datetime.fromtimestamp(next_run_at_ms / 1000, tz=timezone.utc)
                if next_run_at <= now:
                    due_count += 1

            if isinstance(last_run_at_ms, int):
                fired_at = datetime.fromtimestamp(last_run_at_ms / 1000, tz=timezone.utc).isoformat()
                if last_fired_at is None or fired_at > last_fired_at:
                    last_fired_at = fired_at

        return {
            "reminder_total": len(reminders),
            "reminder_status_counts": status_counts,
            "reminder_due_count": due_count,
            "reminder_last_fired_at": last_fired_at,
        }

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
            from OriginAgent.agent.evolution_control_plane import EvolutionControlPlane

            return EvolutionControlPlane(workspace, config).status()
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
                    "cooldown_count": 0,
                    "next_cooldown_expires_at": None,
                    "feedback_trend_window_days": 14,
                    "feedback_trend_counts": {},
                    "feedback_trends": {},
                    "last_calibrated_at": None,
                    "last_result": None,
                },
                "evolution_health": {
                    "score": 100,
                    "level": "healthy",
                    "reasons": [
                        "+ no successful rollbacks",
                        "+ no dependency conflicts",
                        "+ trial isolation enforced",
                    ],
                },
                "evolution_health_history": {
                    "health_history_retention_days": 90,
                    "max_health_history_snapshots": 100,
                    "snapshot_count": 0,
                    "latest_score": None,
                    "latest_level": None,
                    "previous_score": None,
                    "score_delta": 0,
                    "trend": "unknown",
                    "last_snapshot_at": None,
                },
                "operator_recommendations": [],
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
                "trial_logs": {
                    "max_step_output_chars": 2000,
                    "max_retained_trial_logs": 10,
                    "trial_log_retention_days": 30,
                    "trial_log_count": 0,
                    "trial_log_status_counts": {},
                    "last_trial_at": None,
                    "truncated_step_output_count": 0,
                },
                "skill_candidates_enabled": False,
                "eligible_workflow_signals": 0,
                "eligible_skill_signals": 0,
                "high_score_signals": [],
            }

    @staticmethod
    def _subagent_status(loop: Any | None) -> dict[str, Any]:
        defaults = {
            "subagent_task_total": 0,
            "subagent_recent_task_count": 0,
            "subagent_terminal_status_counts": {},
            "subagent_recent_tasks": [],
            "subagent_last_task_at": None,
            "subagent_running_count": 0,
        }
        subagents = getattr(loop, "subagents", None) if loop is not None else None
        if subagents is None or not hasattr(subagents, "runtime_status"):
            return defaults
        try:
            return {**defaults, **dict(subagents.runtime_status())}
        except Exception:
            return defaults


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


def _preview_text(value: Any, *, max_chars: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def _preview_message_content(content: Any) -> str:
    if isinstance(content, str):
        return _preview_text(content)
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
            else:
                text_parts.append(str(block))
        return _preview_text("\n".join(part for part in text_parts if part))
    return _preview_text(content)


def _visibility_reason(
    *,
    visible: bool,
    scope: str,
    owner_id: str | None,
    current_owner_id: str | None,
    device_id: str | None = None,
    current_device_id: str | None = None,
) -> str:
    if visible:
        return "included"
    if scope == "device" and device_id and current_device_id and device_id != current_device_id:
        return "device_mismatch"
    if scope == "user" and owner_id and current_owner_id and owner_id != current_owner_id:
        return "owner_mismatch"
    return "scope_not_visible"


def _evolution_maintenance_policy(config: Any | None) -> dict[str, Any]:
    trial = getattr(config, "trial", None) if config is not None else None
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
        "health_history_retention_days": int(
            getattr(config, "health_history_retention_days", 90) if config is not None else 90
        ),
        "max_health_history_snapshots": int(
            getattr(config, "max_health_history_snapshots", 100) if config is not None else 100
        ),
        "trial_log_retention_days": int(
            getattr(trial, "trial_log_retention_days", 30) if trial is not None else 30
        ),
        "max_retained_trial_logs": int(
            getattr(trial, "max_retained_trial_logs", 10) if trial is not None else 10
        ),
    }
