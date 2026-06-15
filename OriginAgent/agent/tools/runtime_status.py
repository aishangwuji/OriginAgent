"""Redacted runtime explain tools."""

from __future__ import annotations

import json
from collections import Counter
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from OriginAgent.agent.confirmation import PendingConfirmationStore
from OriginAgent.agent.introspection.service import RuntimeIntrospectionService
from OriginAgent.agent.snapshot_inspection import SnapshotInspectionService
from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.context import RequestContext
from OriginAgent.cron.service import CronService


class RuntimeStatusTool(Tool):
    name = "originagent_runtime_status"

    def __init__(
        self,
        *,
        workspace: Path,
        registry: Any,
        sessions: Any,
        pending_queues: dict[str, Any],
        cron_service: CronService | None = None,
        confirmation_store: PendingConfirmationStore | None = None,
        audit_mode: str = "minimal",
        runtime_profile: str = "default",
        domain_pack_manager: Any | None = None,
        background_review_service: Any | None = None,
        curator_service: Any | None = None,
        session_search_index_service: Any | None = None,
        evolution_config: Any | None = None,
        nearline_memory_config: Any | None = None,
        introspection_service: RuntimeIntrospectionService | None = None,
    ) -> None:
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
        self._nearline_memory_config = nearline_memory_config
        self._introspection_service = introspection_service

    @property
    def description(self) -> str:
        return "Return a redacted OriginAgent runtime status summary."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self) -> dict[str, Any]:
        service = self._introspection_service or RuntimeIntrospectionService(
            workspace=self._workspace,
            registry=self._registry,
            sessions=self._sessions,
            pending_queues=self._pending_queues,
            nearline_memory_config=self._nearline_memory_config,
            cron_service=self._cron_service,
            confirmation_store=self._confirmation_store,
            audit_mode=self._audit_mode,
            runtime_profile=self._runtime_profile,
            domain_pack_manager=self._domain_pack_manager,
            background_review_service=self._background_review_service,
            curator_service=self._curator_service,
            session_search_index_service=self._session_search_index_service,
            evolution_config=self._evolution_config,
        )
        return service.system_status()


class PlanActionTool(Tool):
    name = "originagent_plan_action"

    def __init__(
        self,
        *,
        introspection_service: RuntimeIntrospectionService | None = None,
    ) -> None:
        self._introspection_service = introspection_service

    @property
    def description(self) -> str:
        return "Return the last cached action planner result without re-running planning."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self) -> dict[str, Any]:
        loop = getattr(self._introspection_service, "_loop", None) if self._introspection_service is not None else None
        audit = dict(getattr(loop, "_last_action_continuity_audit", {}) or {}) if loop is not None else {}
        planner_result = audit.get("planner_result")
        if not isinstance(planner_result, dict):
            planner_result = {}
        planning_evidence = audit.get("planning_evidence")
        if not isinstance(planning_evidence, dict):
            planning_evidence = {}
        return {
            "available": bool(planner_result),
            "status": audit.get("status"),
            "reason": audit.get("reason"),
            "planner_result": dict(planner_result),
            "planning_evidence": dict(planning_evidence),
            "automation_origin": audit.get("automation_origin"),
            "selected_proposal_digest": audit.get("selected_proposal_digest"),
            "skipped_reasons": list(audit.get("skipped_reasons", []) or []),
            "selection_reason": audit.get("selection_reason"),
            "execution_result": dict(audit.get("execution_result", {}) or {}),
            "continuity_writeback": dict(audit.get("continuity_writeback", {}) or {}),
        }


class InspectContextTool(Tool):
    name = "originagent_inspect_context"

    def __init__(
        self,
        *,
        workspace: Path,
        registry: Any,
        sessions: Any,
        pending_queues: dict[str, Any],
        cron_service: CronService | None = None,
        confirmation_store: PendingConfirmationStore | None = None,
        audit_mode: str = "minimal",
        runtime_profile: str = "default",
        domain_pack_manager: Any | None = None,
        background_review_service: Any | None = None,
        curator_service: Any | None = None,
        session_search_index_service: Any | None = None,
        evolution_config: Any | None = None,
        nearline_memory_config: Any | None = None,
        introspection_service: RuntimeIntrospectionService | None = None,
    ) -> None:
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
        self._nearline_memory_config = nearline_memory_config
        self._introspection_service = introspection_service

    @property
    def description(self) -> str:
        return "Return a minimal debug view of the current turn context assembly."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self) -> dict[str, Any]:
        service = self._introspection_service or RuntimeIntrospectionService(
            workspace=self._workspace,
            registry=self._registry,
            sessions=self._sessions,
            pending_queues=self._pending_queues,
            nearline_memory_config=self._nearline_memory_config,
            cron_service=self._cron_service,
            confirmation_store=self._confirmation_store,
            audit_mode=self._audit_mode,
            runtime_profile=self._runtime_profile,
            domain_pack_manager=self._domain_pack_manager,
            background_review_service=self._background_review_service,
            curator_service=self._curator_service,
            session_search_index_service=self._session_search_index_service,
            evolution_config=self._evolution_config,
        )
        return service.inspect_context()


class InspectSnapshotTool(Tool):
    name = "originagent_inspect_snapshot"

    def __init__(
        self,
        *,
        sessions: Any,
        introspection_service: RuntimeIntrospectionService | None = None,
        inspection_service: SnapshotInspectionService | None = None,
        provider: Any | None = None,
        model: str | None = None,
        auxiliary_router: Any | None = None,
    ) -> None:
        self._sessions = sessions
        self._introspection_service = introspection_service
        self._inspection_service = inspection_service
        self._provider = provider
        self._model = model
        self._auxiliary_router = auxiliary_router
        self._request_ctx: ContextVar[RequestContext | None] = ContextVar(
            "inspect_snapshot_request_ctx",
            default=None,
        )

    def set_context(self, ctx: RequestContext) -> None:
        self._request_ctx.set(ctx)

    @property
    def description(self) -> str:
        return "Inspect a specific world snapshot in the current session and write back a minimal inspection result."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "snapshot_id": {"type": "string", "minLength": 1},
            },
            "required": ["snapshot_id"],
            "additionalProperties": False,
        }

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, snapshot_id: str, **_: Any) -> dict[str, Any] | str:
        ctx = self._request_ctx.get()
        if ctx is None or not ctx.session_key:
            return "Error: inspect_snapshot requires an active session context."
        runtime_context = getattr(ctx, "runtime_context", None)
        if runtime_context is None:
            return "Error: inspect_snapshot requires runtime identity context."
        service = self._introspection_service
        loop = getattr(service, "_loop", None) if service is not None else None
        world_state = getattr(loop, "world_state", None) if loop is not None else None
        if world_state is None:
            return "Error: world state manager is unavailable."
        session = self._sessions.get_or_create(ctx.session_key)
        inspection_service = self._inspection_service
        provider = self._provider or getattr(loop, "provider", None) if loop is not None else self._provider
        auxiliary_router = (
            self._auxiliary_router or getattr(loop, "auxiliary_router", None)
            if loop is not None
            else self._auxiliary_router
        )
        can_build_service = (
            loop is not None
            and (provider is not None or auxiliary_router is not None)
            and hasattr(world_state, "get_snapshot")
            and hasattr(world_state, "apply_inspection")
        )
        if inspection_service is None and can_build_service:
            inspection_service = SnapshotInspectionService(
                world_state=world_state,
                provider=provider,
                model=self._model or getattr(loop, "model", None),
                auxiliary_router=auxiliary_router,
            )
            self._inspection_service = inspection_service
        if inspection_service is not None:
            result = await inspection_service.inspect(
                session,
                runtime_context=runtime_context,
                snapshot_id=snapshot_id,
                requested_by=str(runtime_context.source or "user_turn"),
            )
        else:
            result = world_state.inspect_snapshot(
                session,
                runtime_context=runtime_context,
                snapshot_id=snapshot_id,
                requested_by=str(runtime_context.source or "user_turn"),
            )
            if isinstance(result, dict):
                result["inspection_path"] = "legacy_fallback"
        if "error" in result:
            return f"Error: {result['error']}"
        return result


class ToolAuditSummaryTool(Tool):
    name = "originagent_tool_audit_summary"

    def __init__(self, *, workspace: Path, audit_mode: str = "minimal") -> None:
        self._path = Path(workspace) / "memory" / "audit" / "tool_calls.jsonl"
        self._audit_mode = audit_mode

    @property
    def description(self) -> str:
        return "Return redacted aggregate counts for generic tool audit events."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self) -> dict[str, Any]:
        if self._audit_mode == "off":
            return {
                "audit_mode": self._audit_mode,
                "enabled": False,
                "total": 0,
                "status_counts": {},
                "policy_rule_counts": {},
                "top_failed_tools": {},
                "latest_created_at": None,
            }
        status_counts: Counter[str] = Counter()
        policy_rule_counts: Counter[str] = Counter()
        failed_tools: Counter[str] = Counter()
        total = 0
        latest_created_at: str | None = None
        for event in _iter_jsonl_dicts(self._path):
            total += 1
            status = _string_value(event.get("status")) or "unknown"
            tool_name = _string_value(event.get("tool_name")) or "unknown"
            status_counts[status] += 1
            if status != "success":
                failed_tools[tool_name] += 1
            policy_rule = _string_value(event.get("policy_rule"))
            if policy_rule:
                policy_rule_counts[policy_rule] += 1
            created_at = _string_value(event.get("created_at"))
            if created_at and (latest_created_at is None or created_at > latest_created_at):
                latest_created_at = created_at
        return {
            "audit_mode": self._audit_mode,
            "enabled": True,
            "total": total,
            "status_counts": dict(status_counts),
            "policy_rule_counts": dict(policy_rule_counts),
            "top_failed_tools": dict(failed_tools.most_common(5)),
            "latest_created_at": latest_created_at,
        }


class CronSummaryTool(Tool):
    name = "originagent_cron_summary"

    def __init__(self, *, cron_service: CronService | None = None) -> None:
        self._cron_service = cron_service

    @property
    def description(self) -> str:
        return "Return a redacted summary of scheduled OriginAgent jobs."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self) -> dict[str, Any]:
        if self._cron_service is None:
            return {
                "available": False,
                "job_count": 0,
                "enabled_count": 0,
                "schedule_kind_counts": {},
                "has_next_run_count": 0,
                "capability_summary_counts": {},
            }
        jobs = self._cron_service.list_jobs(include_disabled=True)
        schedule_counts: Counter[str] = Counter()
        capability_counts: Counter[str] = Counter()
        enabled_count = 0
        has_next_run_count = 0
        for job in jobs:
            schedule_counts[str(job.schedule.kind or "unknown")] += 1
            if job.enabled:
                enabled_count += 1
            if job.state.next_run_at_ms is not None:
                has_next_run_count += 1
            capability_counts[_capability_summary(job.payload.capability_snapshot)] += 1
        return {
            "available": True,
            "job_count": len(jobs),
            "enabled_count": enabled_count,
            "schedule_kind_counts": dict(schedule_counts),
            "has_next_run_count": has_next_run_count,
            "capability_summary_counts": dict(capability_counts),
        }


class ConfirmationSummaryTool(Tool):
    name = "originagent_confirmation_summary"

    def __init__(
        self,
        *,
        workspace: Path,
        confirmation_store: PendingConfirmationStore | None = None,
    ) -> None:
        self._store = confirmation_store or PendingConfirmationStore(Path(workspace))

    @property
    def description(self) -> str:
        return "Return a redacted summary of pending OriginAgent confirmations."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self) -> dict[str, Any]:
        confirmations = self._store.read_all()
        kind_counts: Counter[str] = Counter()
        status_counts: Counter[str] = Counter()
        risk_counts: Counter[str] = Counter()
        pending_count = 0
        expired_count = 0
        for confirmation in confirmations:
            kind_counts[confirmation.kind] += 1
            status_counts[confirmation.status] += 1
            risk_counts[confirmation.risk or "unknown"] += 1
            if confirmation.status in {"pending", "notified"}:
                pending_count += 1
            if confirmation.status == "expired":
                expired_count += 1
        return {
            "confirmation_count": len(confirmations),
            "kind_counts": dict(kind_counts),
            "status_counts": dict(status_counts),
            "risk_counts": dict(risk_counts),
            "pending_count": pending_count,
            "expired_count": expired_count,
        }


def _iter_jsonl_dicts(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    yield data
    except FileNotFoundError:
        return
    except OSError:
        return


def _string_value(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _capability_summary(snapshot: Any) -> str:
    if not snapshot:
        return "none"
    if hasattr(snapshot, "model_dump"):
        snapshot = snapshot.model_dump()
    elif hasattr(snapshot, "__dict__"):
        snapshot = vars(snapshot)
    if not isinstance(snapshot, dict):
        return "unknown"
    source = _string_value(snapshot.get("source")) or "unknown"
    trigger = _string_value(snapshot.get("trigger")) or "unknown"
    flags = [
        key
        for key in (
            "can_exec",
            "can_read_files",
            "can_write_files",
            "can_send_cross_target",
            "can_create_cron",
            "can_spawn",
        )
        if snapshot.get(key) is True
    ]
    return f"{source}:{trigger}:enabled_flags={len(flags)}"
