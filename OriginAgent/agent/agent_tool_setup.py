"""Tool setup helpers for AgentLoop."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from loguru import logger

from OriginAgent.agent.audit import AuditLogger
from OriginAgent.agent.confirmation import ConfirmationManager, PendingConfirmationStore
from OriginAgent.agent.skills import BUILTIN_SKILLS_DIR
from OriginAgent.agent.tools.ask import AskUserTool
from OriginAgent.agent.tools.content_read import ContentReadTool
from OriginAgent.agent.tools.context import ToolContext
from OriginAgent.agent.tools.cron import CronTool
from OriginAgent.agent.tools.domain_loader import DomainToolLoader
from OriginAgent.agent.tools.evolution_control import EvolutionControlTool
from OriginAgent.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from OriginAgent.agent.tools.image_generation import ImageGenerationTool
from OriginAgent.agent.tools.loader import ToolLoader
from OriginAgent.agent.tools.long_task import CompleteGoalTool, LongTaskTool
from OriginAgent.agent.tools.local_awareness import (
    BindDeviceTool,
    CaptureCameraFrameTool,
    CaptureScreenTool,
    DiscoverLanDevicesTool,
    DiscoverLocalDevicesTool,
    InspectMediaTool,
    ListDeviceBindingsTool,
    RecordAudioSampleTool,
    RequestDevicePermissionTool,
    RevokeDeviceBindingTool,
    SpeakTextTool,
)
from OriginAgent.agent.tools.message import MessageTool
from OriginAgent.agent.tools.notebook import NotebookEditTool
from OriginAgent.agent.tools.registry import ToolRegistry
from OriginAgent.agent.tools.runtime_status import (
    ConfirmationSummaryTool,
    CronSummaryTool,
    InspectContextTool,
    InspectSnapshotTool,
    PlanActionTool,
    RuntimeStatusTool,
    ToolAuditSummaryTool,
)
from OriginAgent.agent.tools.search import GlobTool, GrepTool
from OriginAgent.agent.tools.session_search import SessionSearchTool
from OriginAgent.agent.tools.shell import ExecTool
from OriginAgent.agent.tools.spawn import SpawnTool
from OriginAgent.agent.tools.web import WebFetchTool, WebSearchTool
from OriginAgent.security.grants import CapabilityGrantStore


def should_register_exec(config: Any) -> bool:
    return bool(config.enable) and getattr(config, "profile", "secure") != "disabled"


def build_tool_context(
    *,
    config: Any,
    workspace: Path,
    bus: Any,
    subagent_manager: Any,
    cron_service: Any,
    sessions: Any,
    file_state_store: Any,
    provider_snapshot_loader: Callable[..., Any] | None,
    image_generation_provider_configs: dict[str, Any],
    timezone: str,
    audit_config: Any,
    context_extras: dict[str, Any] | None = None,
) -> ToolContext:
    """Build the shared tool construction context."""
    ctx = ToolContext(
        config=config,
        workspace=str(workspace),
        bus=bus,
        subagent_manager=subagent_manager,
        cron_service=cron_service,
        sessions=sessions,
        file_state_store=file_state_store,
        provider_snapshot_loader=provider_snapshot_loader,
        image_generation_provider_configs=image_generation_provider_configs,
        timezone=timezone,
        audit_config=audit_config,
    )
    for name, value in (context_extras or {}).items():
        setattr(ctx, name, value)
    return ctx


def build_domain_tool_context_extras(
    *,
    domain_pack_manager: Any,
    workspace: Path,
    config: Any,
    overrides: dict[str, Any] | None = None,
    contributions: list[Any] | None = None,
) -> dict[str, Any]:
    """Collect ToolContext attributes contributed by active domain packs."""
    extras: dict[str, Any] = {}
    if contributions is None:
        if not hasattr(domain_pack_manager, "active_runtime_contributions"):
            return extras
        contributions = domain_pack_manager.active_runtime_contributions(
            workspace=workspace,
            config=config,
            overrides=overrides or {},
        )
    for contribution in contributions:
        extras.update(getattr(contribution, "tool_context", {}) or {})
    return extras


def register_domain_tools(
    registry: ToolRegistry,
    *,
    domain_pack_manager: Any,
    context: ToolContext,
) -> None:
    """Load tools declared by active domain packs without replacing core tools."""
    registered = DomainToolLoader(domain_pack_manager).load(context, registry)
    if registered:
        logger.info("Registered domain tool(s): {}", ", ".join(sorted(registered)))


def register_plugin_tools(
    registry: ToolRegistry,
    *,
    context: ToolContext,
) -> None:
    """Load external OriginAgent tool plugins without replacing core tools."""
    registered = ToolLoader().load(context, registry, scope="core")
    if registered:
        logger.info("Registered OriginAgent tool plugin(s): {}", ", ".join(sorted(registered)))


def register_default_tools(
    registry: ToolRegistry,
    *,
    workspace: Path,
    bus: Any,
    config: Any,
    web_config: Any,
    exec_config: Any,
    restrict_to_workspace: bool,
    sessions: Any,
    pending_queues: dict[str, Any],
    cron_service: Any,
    audit_config: Any,
    domain_pack_manager: Any,
    background_review_service: Any,
    curator_service: Any,
    session_search_index_service: Any,
    subagent_manager: Any,
    file_state_store: Any,
    provider_snapshot_loader: Callable[..., Any] | None,
    image_generation_provider_configs: dict[str, Any],
    timezone: str,
    runtime_profile: str,
    introspection_service: Any | None = None,
    confirmation_store: PendingConfirmationStore | None = None,
    confirmation_manager: ConfirmationManager | None = None,
    grant_store: CapabilityGrantStore | None = None,
    domain_runtime_overrides: dict[str, Any] | None = None,
    evolution_config: Any | None = None,
    allowed_tool_names: set[str] | frozenset[str] | None = None,
    domain_runtime_contributions: list[Any] | None = None,
) -> None:
    """Register all default tools with the registry."""
    allowed_dir = workspace if (restrict_to_workspace or exec_config.sandbox) else None
    extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
    confirmation_store = confirmation_store or PendingConfirmationStore(workspace)
    confirmation_manager = confirmation_manager or ConfirmationManager(
        workspace,
        store=confirmation_store,
        audit_logger=AuditLogger(workspace),
    )
    grant_store = grant_store or CapabilityGrantStore(workspace)

    def _allowed(name: str) -> bool:
        return allowed_tool_names is None or name in allowed_tool_names

    def _register(tool: Any) -> None:
        registry.register(tool)

    def _register_named(name: str, factory: Callable[[], Any]) -> None:
        if _allowed(name):
            _register(factory())

    local_awareness_backend = (
        getattr(introspection_service._loop, "_local_awareness_backend", None)
        if introspection_service is not None and getattr(introspection_service, "_loop", None) is not None
        else None
    )

    _register_named("ask_user", AskUserTool)
    _register_named(
        PlanActionTool.name,
        lambda: PlanActionTool(
            introspection_service=introspection_service,
        ),
    )
    _register_named(
        RuntimeStatusTool.name,
        lambda: RuntimeStatusTool(
            workspace=workspace,
            registry=registry,
            sessions=sessions,
            pending_queues=pending_queues,
            cron_service=cron_service,
            audit_mode=audit_config.mode,
            runtime_profile=runtime_profile,
            confirmation_store=confirmation_store,
            domain_pack_manager=domain_pack_manager,
            background_review_service=background_review_service,
            curator_service=curator_service,
            session_search_index_service=session_search_index_service,
            evolution_config=evolution_config,
            nearline_memory_config=getattr(introspection_service, "_nearline_memory_config", None)
            if introspection_service is not None
            else None,
            introspection_service=introspection_service,
        )
    )
    _register_named(
        InspectContextTool.name,
        lambda: InspectContextTool(
            workspace=workspace,
            registry=registry,
            sessions=sessions,
            pending_queues=pending_queues,
            cron_service=cron_service,
            audit_mode=audit_config.mode,
            runtime_profile=runtime_profile,
            confirmation_store=confirmation_store,
            domain_pack_manager=domain_pack_manager,
            background_review_service=background_review_service,
            curator_service=curator_service,
            session_search_index_service=session_search_index_service,
            evolution_config=evolution_config,
            nearline_memory_config=getattr(introspection_service, "_nearline_memory_config", None)
            if introspection_service is not None
            else None,
            introspection_service=introspection_service,
        )
    )
    _register_named(
        InspectSnapshotTool.name,
        lambda: InspectSnapshotTool(
            sessions=sessions,
            introspection_service=introspection_service,
            provider=getattr(introspection_service._loop, "provider", None)
            if introspection_service is not None and getattr(introspection_service, "_loop", None) is not None
            else None,
            model=getattr(introspection_service._loop, "model", None)
            if introspection_service is not None and getattr(introspection_service, "_loop", None) is not None
            else None,
            auxiliary_router=getattr(introspection_service._loop, "auxiliary_router", None)
            if introspection_service is not None and getattr(introspection_service, "_loop", None) is not None
            else None,
        )
    )
    _register_named(
        EvolutionControlTool.name,
        lambda: EvolutionControlTool(workspace=workspace, evolution_config=evolution_config),
    )
    _register_named(
        ToolAuditSummaryTool.name,
        lambda: ToolAuditSummaryTool(workspace=workspace, audit_mode=audit_config.mode),
    )
    _register_named(
        CronSummaryTool.name,
        lambda: CronSummaryTool(cron_service=cron_service),
    )
    _register_named(
        ConfirmationSummaryTool.name,
        lambda: ConfirmationSummaryTool(
            workspace=workspace,
            confirmation_store=confirmation_store,
        ),
    )
    _register_named(
        DiscoverLocalDevicesTool.name,
        lambda: DiscoverLocalDevicesTool(
            workspace=workspace,
            config=config,
            backend=local_awareness_backend,
            introspection_service=introspection_service,
        ),
    )
    _register_named(
        DiscoverLanDevicesTool.name,
        lambda: DiscoverLanDevicesTool(
            workspace=workspace,
            config=config,
            backend=local_awareness_backend,
            introspection_service=introspection_service,
        ),
    )
    for tool_cls in (
        ListDeviceBindingsTool,
        BindDeviceTool,
        RevokeDeviceBindingTool,
        RequestDevicePermissionTool,
    ):
        _register_named(
            tool_cls.name,
            lambda tool_cls=tool_cls: tool_cls(
                workspace=workspace,
                config=config,
                backend=local_awareness_backend,
                introspection_service=introspection_service,
            ),
        )
    _register_named(
        CaptureCameraFrameTool.name,
        lambda: CaptureCameraFrameTool(
            workspace=workspace,
            config=config,
            backend=local_awareness_backend,
            introspection_service=introspection_service,
        ),
    )
    _register_named(
        CaptureScreenTool.name,
        lambda: CaptureScreenTool(
            workspace=workspace,
            config=config,
            backend=local_awareness_backend,
            introspection_service=introspection_service,
        ),
    )
    _register_named(
        RecordAudioSampleTool.name,
        lambda: RecordAudioSampleTool(
            workspace=workspace,
            config=config,
            backend=local_awareness_backend,
            introspection_service=introspection_service,
        ),
    )
    _register_named(
        SpeakTextTool.name,
        lambda: SpeakTextTool(
            workspace=workspace,
            config=config,
            backend=local_awareness_backend,
            introspection_service=introspection_service,
        ),
    )
    _register_named(
        InspectMediaTool.name,
        lambda: InspectMediaTool(
            workspace=workspace,
            config=config,
            backend=local_awareness_backend,
            introspection_service=introspection_service,
        ),
    )
    _register_named("long_task", lambda: LongTaskTool(sessions=sessions, bus=bus))
    _register_named("complete_goal", lambda: CompleteGoalTool(sessions=sessions, bus=bus))
    _register_named(
        "read_file",
        lambda: ReadFileTool(
            workspace=workspace,
            allowed_dir=allowed_dir,
            extra_allowed_dirs=extra_read,
        ),
    )
    for cls in (WriteFileTool, EditFileTool, ListDirTool):
        _register_named(
            cls(workspace=workspace, allowed_dir=allowed_dir).name,
            lambda cls=cls: cls(workspace=workspace, allowed_dir=allowed_dir),
        )
    for cls in (GlobTool, GrepTool):
        _register_named(
            cls(workspace=workspace, allowed_dir=allowed_dir).name,
            lambda cls=cls: cls(workspace=workspace, allowed_dir=allowed_dir),
        )
    if getattr(config.session_search, "enabled", True):
        _register_named(
            "session_search",
            lambda: SessionSearchTool(
                workspace=workspace,
                config=config.session_search,
                index_service=session_search_index_service,
                nearline_memory_config=(
                    getattr(introspection_service, "_nearline_memory_config", None)
                    if introspection_service is not None
                    else None
                ),
            ),
        )
    _register_named(
        "notebook_edit",
        lambda: NotebookEditTool(workspace=workspace, allowed_dir=allowed_dir),
    )

    if should_register_exec(exec_config):
        _register_named(
            "exec",
            lambda: ExecTool(
                working_dir=str(workspace),
                timeout=exec_config.timeout,
                restrict_to_workspace=restrict_to_workspace,
                sandbox=exec_config.sandbox,
                path_append=exec_config.path_append,
                allowed_env_keys=exec_config.allowed_env_keys,
                allow_patterns=exec_config.allow_patterns,
                deny_patterns=exec_config.deny_patterns,
                security_profile=exec_config.profile,
                allow_unsafe_exec=exec_config.allow_unsafe_exec,
                shell_syntax_policy=exec_config.shell_syntax_policy,
            ),
        )

    if web_config.enable:
        web_search_config_loader = None
        if provider_snapshot_loader is not None:
            def web_search_config_loader():
                from OriginAgent.config.loader import load_config, resolve_config_env_vars

                return resolve_config_env_vars(load_config()).tools.web.search

        _register_named(
            "web_search",
            lambda: WebSearchTool(
                config=web_config.search,
                proxy=web_config.proxy,
                user_agent=web_config.user_agent,
                config_loader=web_search_config_loader,
            ),
        )
        _register_named(
            "web_fetch",
            lambda: WebFetchTool(
                config=web_config.fetch,
                proxy=web_config.proxy,
                user_agent=web_config.user_agent,
                content_read_config=config.content_read,
            ),
        )

    if config.content_read.enabled:
        _register_named(
            "content_read",
            lambda: ContentReadTool(
                config=config.content_read,
                proxy=web_config.proxy,
                user_agent=web_config.user_agent,
            ),
        )

    if config.image_generation.enabled:
        _register_named(
            "generate_image",
            lambda: ImageGenerationTool(
                workspace=workspace,
                config=config.image_generation,
                provider_configs=image_generation_provider_configs,
            ),
        )

    _register_named(
        "message",
        lambda: MessageTool(send_callback=bus.publish_outbound, workspace=workspace),
    )
    _register_named("spawn", lambda: SpawnTool(manager=subagent_manager))
    if cron_service:
        _register_named(
            "cron",
            lambda: CronTool(cron_service, default_timezone=timezone or "UTC"),
        )

    if allowed_tool_names is not None:
        return
    domain_context_extras = build_domain_tool_context_extras(
        domain_pack_manager=domain_pack_manager,
        workspace=workspace,
        config=config,
        overrides=domain_runtime_overrides or {},
        contributions=domain_runtime_contributions,
    )
    context = build_tool_context(
        config=config,
        workspace=workspace,
        bus=bus,
        subagent_manager=subagent_manager,
        cron_service=cron_service,
        sessions=sessions,
        file_state_store=file_state_store,
        provider_snapshot_loader=provider_snapshot_loader,
        image_generation_provider_configs=image_generation_provider_configs,
        timezone=timezone,
        audit_config=audit_config,
        context_extras=domain_context_extras,
    )
    register_domain_tools(registry, domain_pack_manager=domain_pack_manager, context=context)
    register_plugin_tools(registry, context=context)
