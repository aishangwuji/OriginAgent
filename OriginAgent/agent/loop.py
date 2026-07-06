"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from OriginAgent.agent import model_presets as preset_helpers
from OriginAgent.agent.action_safety import ActionDecision
from OriginAgent.agent.action_summary import normalize_action_summary
from OriginAgent.agent.agent_cognitive_runtime import AgentCognitiveRuntime, CognitiveRuntimeDeps
from OriginAgent.agent.agent_host import AgentHost, AgentHostDependencies
from OriginAgent.agent.agent_loop_components import build_loop_components
from OriginAgent.agent.agent_runtime import (
    AgentRuntime,
    BackgroundServices,
    CoreServices,
    MemoryServices,
    MetaCognitionServices,
    RuntimeConfig,
    RuntimeDependencies,
    StoreServices,
)
from OriginAgent.agent.agent_runtime_context import (
    build_bus_progress_callback,
    build_retry_wait_callback,
    runtime_chat_id,
    snapshot_for_trigger,
)
from OriginAgent.agent.agent_runtime_context import (
    set_tool_context as set_tools_runtime_context,
)
from OriginAgent.agent.agent_tool_setup import (
    build_domain_tool_context_extras,
    build_tool_context,
    register_default_tools,
    register_domain_tools,
    register_plugin_tools,
    should_register_exec,
)
from OriginAgent.agent.agent_turn_persist import TurnPersistManager
from OriginAgent.agent.agent_turn_pipeline import (
    TURN_PIPELINE_TRANSITIONS,
    AgentTurnPipeline,
    TurnContext,
    TurnEvent,
    TurnPipelineDeps,
    TurnState,
)
from OriginAgent.agent.cognitive_events import CognitiveDecision, CognitiveEvent
from OriginAgent.agent.confirmation import classify_confirmation_reply
from OriginAgent.agent.context import ContextBuilder
from OriginAgent.agent.domain_packs import DomainPackManager
from OriginAgent.agent.hook import AgentHook
from OriginAgent.agent.identity import ActorResolver, RuntimeContext
from OriginAgent.agent.message_dispatcher import MessageDispatcher, MessageDispatcherDeps
from OriginAgent.agent.meta_cognition_coordinator import MetaCognitionCoordinator
from OriginAgent.agent.meta_cognition_models import MetaTrigger
from OriginAgent.agent.meta_cognition_triggers import (
    bridge_runtime_event_to_trigger,
    build_task_completion_trigger,
    build_tool_failure_trigger,
)
from OriginAgent.agent.self_model import SelfModelService
from OriginAgent.agent.services import AgentServiceContainer
from OriginAgent.agent.session_state import SessionStateHolder
from OriginAgent.agent.subagent import SubagentManager
from OriginAgent.agent.system_turn_handler import (
    SystemTurnHandler,
    SystemTurnHandlerDeps,
    SystemTurnLoopContext,
)
from OriginAgent.agent.tools.ask import (
    ask_user_options_from_messages,
    ask_user_outbound,
    ask_user_tool_result_messages,
    pending_ask_user_id,
)
from OriginAgent.agent.tools.audit import ToolAuditConfig
from OriginAgent.agent.tools.message import MessageTool
from OriginAgent.agent.tools.self import MyTool
from OriginAgent.agent.turn_orchestrator import TurnOrchestrator, TurnOrchestratorDeps
from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.command import CommandContext, CommandRouter, register_builtin_commands
from OriginAgent.config.schema import AgentDefaults
from OriginAgent.domain_packs.robot.runtime.robot_actions import TypedRobotAction
from OriginAgent.providers.base import LLMProvider
from OriginAgent.providers.factory import ProviderSnapshot
from OriginAgent.security.capabilities import CapabilitySnapshot
from OriginAgent.security.grants import issue_tool_approval_grant
from OriginAgent.session.goal_state import goal_state_raw, goal_state_ws_blob, parse_goal_state
from OriginAgent.session.manager import Session, SessionManager
from OriginAgent.utils.image_generation_intent import image_generation_prompt
from OriginAgent.utils.webui_titles import mark_webui_session
from OriginAgent.utils.webui_transcript import append_transcript_object, delete_webui_transcript

if TYPE_CHECKING:
    from OriginAgent.agent.loop_options import LoopOptions
    from OriginAgent.config.schema import (
        AuxiliaryConfig,
        BackgroundReviewConfig,
        ChannelsConfig,
        Config,
        CuratorConfig,
        DomainPacksConfig,
        EvolutionConfig,
        ExecToolConfig,
        ModelPresetConfig,
        NearlineMemoryConfig,
        ProviderConfig,
        ToolsConfig,
        WebToolsConfig,
    )
    from OriginAgent.cron.service import CronService


UNIFIED_SESSION_KEY = "unified:default"
_SENSITIVE_TOOL_LOG_FALLBACK_NAMES: frozenset[str] = frozenset({
    "exec", "message", "web_fetch",
})
_SENSITIVE_TOOL_LOG_FALLBACK_PREFIXES: tuple[str, ...] = (
    "originagent_device_",
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim_text(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


CONTINUITY_RUNTIME_IDENTITY_KEY = "continuity_runtime_identity_v1"
CONTINUITY_CHECKPOINT_KEY = "continuity_checkpoint_v1"


def _is_sensitive_tool_log(name: str) -> bool:
    return name in _SENSITIVE_TOOL_LOG_FALLBACK_NAMES or any(
        name.startswith(prefix) for prefix in _SENSITIVE_TOOL_LOG_FALLBACK_PREFIXES
    )


def _should_register_exec(config: Any) -> bool:
    return should_register_exec(config)


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"
    _PENDING_USER_TURN_KEY = "pending_user_turn"

    # Event-driven state transition table.
    # Handlers return an event string; the driver looks up the next state here.
    _TRANSITIONS: dict[tuple[TurnState, TurnEvent], TurnState] = TURN_PIPELINE_TRANSITIONS

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int | None = None,
        context_window_tokens: int | None = None,
        context_block_limit: int | None = None,
        max_tool_result_chars: int | None = None,
        provider_retry_mode: str = "standard",
        tool_hint_max_length: int | None = None,
        web_config: WebToolsConfig | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        transcription_provider_config: dict[str, Any] | None = None,
        timezone: str | None = None,
        runtime_profile: str = "default",
        session_ttl_minutes: int = 0,
        consolidation_ratio: float = 0.5,
        max_messages: int = 120,
        hooks: list[AgentHook] | None = None,
        unified_session: bool = False,
        disabled_skills: list[str] | None = None,
        tools_config: ToolsConfig | None = None,
        image_generation_provider_config: ProviderConfig | None = None,
        image_generation_provider_configs: dict[str, ProviderConfig] | None = None,
        provider_snapshot_loader: Callable[..., ProviderSnapshot] | None = None,
        provider_signature: tuple[object, ...] | None = None,
        model_presets: dict[str, ModelPresetConfig] | None = None,
        model_preset: str | None = None,
        preset_snapshot_loader: preset_helpers.PresetSnapshotLoader | None = None,
        runtime_model_publisher: Callable[[str, str | None], None] | None = None,
        device_action_executor: Any | None = None,
        device_tools_real_mode: bool = False,
        device_registry: Any | None = None,
        domain_runtime_overrides: dict[str, Any] | None = None,
        actor_resolver: ActorResolver | None = None,
        tool_audit_config: ToolAuditConfig | None = None,
        pairing_config: Any | None = None,
        auxiliary_config: "AuxiliaryConfig | None" = None,
        auxiliary_source_config: "Config | None" = None,
        auxiliary_provider_factory: Callable[["ModelPresetConfig"], LLMProvider] | None = None,
        primary_provider_name: str | None = None,
        domain_packs_config: "DomainPacksConfig | None" = None,
        domain_pack_manager: DomainPackManager | None = None,
        learning_config: "BackgroundReviewConfig | None" = None,
        learning_config_loader: Callable[[], "BackgroundReviewConfig"] | None = None,
        meta_cognition_config: Any | None = None,
        curator_config: "CuratorConfig | None" = None,
        curator_config_loader: Callable[[], "CuratorConfig"] | None = None,
        evolution_config: "EvolutionConfig | None" = None,
        evolution_config_loader: Callable[[], "EvolutionConfig"] | None = None,
        dream_config: Any | None = None,
        nearline_memory_config: "NearlineMemoryConfig | None" = None,
        cold_archive_enabled: bool = True,
        tool_concurrency_limit: int | None = None,
        allow_agent_initiated_messages: bool | None = None,
        enable_backend_cognition: bool | None = None,
        active_intent_interval_seconds: int | None = None,
        active_intent_session_cooldown_seconds: int | None = None,
        active_intent_intent_cooldown_seconds: int | None = None,
        active_intent_max_messages_per_session_per_pass: int | None = None,
        effective_config: Any | None = None,
        tiered_config: Any | None = None,
    ):
        from OriginAgent.config.schema import ExecToolConfig, ToolsConfig, WebToolsConfig

        _tc = tools_config or ToolsConfig()
        defaults = AgentDefaults()
        built = build_loop_components(
            loop=self,
            context_builder_cls=ContextBuilder,
            session_manager_cls=SessionManager,
            subagent_manager_cls=SubagentManager,
            domain_pack_manager_cls=DomainPackManager,
            bus=bus,
            provider=provider,
            workspace=workspace,
            defaults=defaults,
            tools_config=_tc,
            web_config=web_config or WebToolsConfig(),
            exec_config=exec_config or ExecToolConfig(),
            channels_config=channels_config,
            model=model,
            max_iterations=max_iterations,
            context_window_tokens=context_window_tokens,
            context_block_limit=context_block_limit,
            max_tool_result_chars=max_tool_result_chars,
            provider_retry_mode=provider_retry_mode,
            tool_hint_max_length=tool_hint_max_length,
            cron_service=cron_service,
            restrict_to_workspace=restrict_to_workspace,
            session_manager=session_manager,
            mcp_servers=mcp_servers,
            timezone=timezone,
            runtime_profile=runtime_profile,
            session_ttl_minutes=session_ttl_minutes,
            consolidation_ratio=consolidation_ratio,
            max_messages=max_messages,
            hooks=hooks,
            unified_session=unified_session,
            disabled_skills=disabled_skills,
            image_generation_provider_config=image_generation_provider_config,
            image_generation_provider_configs=image_generation_provider_configs,
            provider_snapshot_loader=provider_snapshot_loader,
            provider_signature=provider_signature,
            model_presets=model_presets,
            model_preset=model_preset,
            preset_snapshot_loader=preset_snapshot_loader,
            runtime_model_publisher=runtime_model_publisher,
            device_action_executor=device_action_executor,
            device_registry=device_registry,
            domain_runtime_overrides=domain_runtime_overrides,
            actor_resolver=actor_resolver,
            tool_audit_config=tool_audit_config,
            pairing_config=pairing_config,
            auxiliary_config=auxiliary_config,
            auxiliary_source_config=auxiliary_source_config,
            auxiliary_provider_factory=auxiliary_provider_factory,
            primary_provider_name=primary_provider_name,
            domain_packs_config=domain_packs_config,
            domain_pack_manager=domain_pack_manager,
            learning_config=learning_config or defaults.learning.background_review,
            learning_config_loader=learning_config_loader,
            meta_cognition_config=meta_cognition_config,
            curator_config=curator_config,
            curator_config_loader=curator_config_loader,
            evolution_config=evolution_config,
            evolution_config_loader=evolution_config_loader,
            dream_config=dream_config,
            nearline_memory_config=nearline_memory_config,
            cold_archive_enabled=cold_archive_enabled,
            tool_concurrency_limit=tool_concurrency_limit,
            allow_agent_initiated_messages=allow_agent_initiated_messages,
            enable_backend_cognition=enable_backend_cognition,
            active_intent_interval_seconds=active_intent_interval_seconds,
            active_intent_session_cooldown_seconds=active_intent_session_cooldown_seconds,
            active_intent_intent_cooldown_seconds=active_intent_intent_cooldown_seconds,
            active_intent_max_messages_per_session_per_pass=active_intent_max_messages_per_session_per_pass,
            effective_config=effective_config,
            tiered_config=tiered_config,
        )
        for name, value in built.values.items():
            setattr(self, name, value)
        self._bind_action_resume_precheck()
        self._register_default_tools()
        if _tc.my.enable:
            self.tools.register(
                MyTool(
                    loop=self,
                    modify_allowed=_tc.my.allow_set,
                    introspection_service=self.introspection,
                )
            )
        self._state_holder = SessionStateHolder(ttl_s=3600.0)
        # Session-scoped state — delegated to SessionStateHolder.
        # Direct attribute access is kept for backward compatibility
        # with getattr(loop, "_last_*") consumers; _record_* methods
        # write to both the attribute and the holder.
        self._runtime_vars: dict[str, Any] = {}
        self._capability_snapshot: CapabilitySnapshot | None = None
        self._current_iteration: int = 0
        self._last_runtime_context: RuntimeContext | None = None
        self._last_continuity_session_key: str | None = None
        self._last_context_assembly: dict[str, Any] = {}
        self._last_recovered_continuity_checkpoint: dict[str, Any] = {}
        self._last_governance_audit: dict[str, Any] = {}
        self._last_action_continuity_audit: dict[str, Any] = {}
        self._cached_action_summary: dict[str, Any] = normalize_action_summary({})
        self._last_cognitive_scan: dict[str, Any] = {}
        self._last_world_attention_write: dict[str, Any] = {}
        self._meta_coordinator = MetaCognitionCoordinator(
            runtime=getattr(self, "_meta_cognition_runtime", None),
            reflector=getattr(self, "_meta_cognition_reflector", None),
            regulator=getattr(self, "_meta_cognition_regulator", None),
            config=getattr(self, "_meta_cognition_config", None),
        )
        self.commands = CommandRouter()
        register_builtin_commands(self.commands)
        self._turn_pipeline = AgentTurnPipeline(self._build_turn_pipeline_deps())
        self._cognitive_runtime = AgentCognitiveRuntime(self._build_cognitive_runtime_deps())
        self.services = AgentServiceContainer.from_mapping(
            built.values,
            commands=self.commands,
            turn_pipeline=self._turn_pipeline,
            cognitive_runtime=self._cognitive_runtime,
        )
        self._system_turn_handler = SystemTurnHandler(
            SystemTurnHandlerDeps(
                services=self.services,
                loop_context=self._build_system_turn_loop_context(),
            )
        )
        self._turn_orchestrator = TurnOrchestrator(
            TurnOrchestratorDeps(
                turn_pipeline=self._turn_pipeline,
                transitions=self._TRANSITIONS,
                system_turn_handler=self._system_turn_handler,
                scan_meta_triggers_for_turn=self._scan_meta_triggers_for_turn,
                schedule_meta_cognition_reflection=self._schedule_meta_cognition_reflection,
            )
        )
        self._message_dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=self))
        if getattr(self, "_meta_cognition_runtime", None) is not None:
            self._install_meta_cognition_observer()

        # ── AgentHost: infrastructure lifecycle ──────────────────────────
        # Constructed here so transcription/BDI compat attributes below can
        # alias _host-owned state.
        gw = getattr(effective_config, "gateway", None) if effective_config else None
        _bdi_cfg: Any = getattr(gw, "bdi", None) if gw is not None else None
        _tenants_cfg = getattr(gw, "tenants", None) if gw is not None else None
        _speaker_cfg = getattr(gw, "speaker_recognition", None) if gw is not None else None

        from OriginAgent.identity.resolver import IdentityResolver
        from OriginAgent.identity.tenant import TenantRegistry
        self._tenant_registry = TenantRegistry(self.workspace, _tenants_cfg)
        self._identity_resolver = IdentityResolver.from_config(
            self._tenant_registry, _speaker_cfg
        )

        self._host = AgentHost(AgentHostDependencies(
            # Phase 2a
            tools=self.tools,
            mcp_servers=self._mcp_servers,
            cognitive_runtime=self._cognitive_runtime,
            # Phase 2b — Provider
            provider=self.provider,
            model=self.model,
            model_presets=self.model_presets,
            model_preset=self.model_preset,
            provider_snapshot_loader=self._provider_snapshot_loader,
            preset_snapshot_loader=self._preset_snapshot_loader,
            runtime_model_publisher=self._runtime_model_publisher,
            provider_signature=self._provider_signature,
            runner=self.runner,
            subagents=self.subagents,
            auxiliary_router=self.auxiliary_router,
            background_review=self.background_review,
            consolidator=self.consolidator,
            dream=self.dream,
            # Phase 2b — BDI
            workspace=self.workspace,
            bdi_config=_bdi_cfg if _bdi_cfg and _bdi_cfg.enabled else None,
            meta_cognition_config=getattr(self, "_meta_cognition_config", None),
            # Phase 2b — Transcription
            transcription_provider_config=transcription_provider_config,
            tools_config=self.tools_config,
            # Phase 2c — Tenant identity
            tenants_config=_tenants_cfg,
        ))
        # Re-point _background_tasks so tests and compat code that read
        # loop._background_tasks see the host-owned set.
        self._background_tasks = self._host._background_tasks  # type: ignore[assignment]

        # ── Compat aliases from AgentHost ─────────────────────────────────
        self._transcription_provider = self._host._transcription_provider
        self._local_awareness_backend = self._host._local_awareness_backend
        self._last_local_awareness_summary = self._host._last_local_awareness_summary
        self._desire_store = self._host._desire_store
        self._bdi_engine = self._host.bdi_engine
        self._inner_monologue_engine = self._host._inner_monologue_engine

        # ── AgentRuntime: stateless message router ────────────────────────
        self._runtime = AgentRuntime(RuntimeDependencies(
            # ── New: grouped sub-containers ────────────────────────────
            core=CoreServices(
                tools=self.tools,
                provider=self.provider,
                runner=self.runner,
                context=self.context,
                sessions=self.sessions,
                bus=self.bus,
                workspace=self.workspace,
                subagents=self.subagents,
            ),
            meta=MetaCognitionServices(
                runtime=getattr(self, "_meta_cognition_runtime", None),
                reflector=getattr(self, "_meta_cognition_reflector", None),
                regulator=getattr(self, "_meta_cognition_regulator", None),
                config=getattr(self, "_meta_cognition_config", None),
                coordinator=self._meta_coordinator,
                perception_fusion=getattr(self, "_perception_fusion", None),
            ),
            memory=MemoryServices(
                state_holder=self._state_holder,
                working_memory=self.working_memory,
                nearline_memory=self.nearline_memory,
                session_search_index=self.session_search_index,
                consolidator=self.consolidator,
                dream=self.dream,
                session_cold_archive=self.session_cold_archive,
                rolling_episode_compaction=self.rolling_episode_compaction,
                memory_governance=self.memory_governance,
                auto_compact=self.auto_compact,
            ),
            background=BackgroundServices(
                background_review=self.background_review,
                curator=self.curator,
                cognitive_loop=self.cognitive_loop,
                cognitive_scheduler=self.cognitive_scheduler,
                cognitive_audit=self._cognitive_audit,
                cron_service=self.cron_service,
            ),
            stores=StoreServices(
                file_state_store=self._file_state_store,
                confirmation_store=self._confirmation_store,
                confirmation_manager=self._confirmation_manager,
                grant_store=self._grant_store,
            ),
            config=RuntimeConfig(
                model=self.model,
                max_iterations=self.max_iterations,
                context_window_tokens=self.context_window_tokens,
                context_block_limit=self.context_block_limit,
                max_tool_result_chars=self.max_tool_result_chars,
                provider_retry_mode=self.provider_retry_mode,
                tool_hint_max_length=self.tool_hint_max_length,
                restrict_to_workspace=self.restrict_to_workspace,
                unified_session=self._unified_session,
                runtime_profile=self._runtime_profile,
                consolidation_ratio=0.5,
                max_messages=self._max_messages,
            ),

            # ── Existing flat fields (keep ALL, unchanged) ──────────────
            state_holder=self._state_holder,
            host=self._host,
            tools=self.tools,
            provider=self.provider,
            runner=self.runner,
            context=self.context,
            sessions=self.sessions,
            bus=self.bus,
            workspace=self.workspace,
            subagents=self.subagents,
            working_memory=self.working_memory,
            commands=self.commands,
            action_planner=self.action_planner,
            active_intents=self.active_intents,
            reminder_store=self._reminder_store,
            world_state=self.world_state,
            model=self.model,
            max_iterations=self.max_iterations,
            context_window_tokens=self.context_window_tokens,
            context_block_limit=self.context_block_limit,
            max_tool_result_chars=self.max_tool_result_chars,
            provider_retry_mode=self.provider_retry_mode,
            tool_hint_max_length=self.tool_hint_max_length,
            tools_config=self.tools_config,
            web_config=self.web_config,
            exec_config=self.exec_config,
            restrict_to_workspace=self.restrict_to_workspace,
            unified_session=self._unified_session,
            runtime_profile=self._runtime_profile,
            consolidation_ratio=0.5,
            domain_packs=self.domain_packs,
            max_messages=self._max_messages,
            background_review=self.background_review,
            curator=self.curator,
            nearline_memory=self.nearline_memory,
            session_search_index=self.session_search_index,
            consolidator=self.consolidator,
            dream=self.dream,
            cognitive_loop=self.cognitive_loop,
            cognitive_scheduler=self.cognitive_scheduler,
            cognitive_audit=self._cognitive_audit,
            session_cold_archive=self.session_cold_archive,
            rolling_episode_compaction=self.rolling_episode_compaction,
            memory_governance=self.memory_governance,
            auto_compact=self.auto_compact,
            meta_cognition_runtime=getattr(self, "_meta_cognition_runtime", None),
            meta_cognition_reflector=getattr(self, "_meta_cognition_reflector", None),
            meta_cognition_regulator=getattr(self, "_meta_cognition_regulator", None),
            meta_cognition_config=getattr(self, "_meta_cognition_config", None),
            meta_coordinator=self._meta_coordinator,
            perception_fusion=getattr(self, "_perception_fusion", None),
            file_state_store=self._file_state_store,
            confirmation_store=self._confirmation_store,
            confirmation_manager=self._confirmation_manager,
            grant_store=self._grant_store,
            cron_service=self.cron_service,
            introspection=self.introspection,
            actor_resolver=self.actor_resolver,
            auxiliary_router=self.auxiliary_router,
            tool_audit_config=self._tool_audit_config,
            evolution_config=self.evolution_config,
            extra_hooks=self._extra_hooks,
            pending_queues=self._pending_queues,
            domain_runtime_overrides=self._domain_runtime_overrides,
            domain_runtime_contributions=self._domain_runtime_contributions,
            bdi_engine=self._bdi_engine,
            sqlite_stores=getattr(self, "_sqlite_stores", None),
        ))

    @classmethod
    def from_config(
        cls,
        config: Any,
        bus: MessageBus | None = None,
        **extra: Any,
    ) -> AgentLoop:
        """Create an AgentLoop from config with the common parameter set.

        Extra keyword arguments are forwarded to ``AgentLoop.__init__``,
        allowing callers to override or extend the standard config-derived
        parameters (e.g. ``cron_service``, ``session_manager``).
        """
        from OriginAgent.config.profiles import apply_runtime_profile
        from OriginAgent.providers.factory import make_provider

        config = apply_runtime_profile(config)
        if bus is None:
            bus = MessageBus()
        defaults = config.agents.defaults
        resolved = config.resolve_preset()
        provider = extra.pop("provider", None) or make_provider(config, resolved)
        model = extra.pop("model", None) or resolved.model
        primary_provider_name = config.get_provider_name(model) or resolved.provider
        context_window_tokens = (
            extra.pop("context_window_tokens", None)
            or resolved.context_window_tokens
            or defaults.context_window_tokens
        )
        model_presets = preset_helpers.configured_model_presets(config)
        provider_snapshot_loader = extra.get("provider_snapshot_loader")
        preset_snapshot_loader = extra.pop(
            "preset_snapshot_loader",
            preset_helpers.make_preset_snapshot_loader(config, provider_snapshot_loader),
        )
        domain_pack_manager = extra.get("domain_pack_manager") or DomainPackManager(
            config.workspace_path,
            config=defaults.domain_packs,
        )
        extra["domain_pack_manager"] = domain_pack_manager
        domain_runtime_overrides = dict(extra.pop("domain_runtime_overrides", {}) or {})
        if "device_action_executor" in extra:
            domain_runtime_overrides.setdefault(
                "device_action_executor",
                extra.pop("device_action_executor"),
            )
        if "device_registry" in extra:
            domain_runtime_overrides.setdefault("device_registry", extra.pop("device_registry"))

        def _background_review_config_loader():
            from OriginAgent.config.loader import load_config

            return load_config().agents.defaults.learning.background_review

        def _curator_config_loader():
            from OriginAgent.config.loader import load_config

            return load_config().agents.defaults.learning.curator

        def _evolution_config_loader():
            from OriginAgent.config.loader import load_config

            return load_config().agents.defaults.learning.evolution

        transcription_provider_name = (
            config.tools.local_awareness.audio.transcription_provider
            or config.channels.transcription_provider
        )
        if transcription_provider_name == "openai":
            transcription_api_key = config.providers.openai.api_key
            transcription_api_base = config.providers.openai.api_base
            transcription_resource_id = None
        elif transcription_provider_name == "volcengine":
            transcription_api_key = config.providers.volcengine.api_key
            transcription_api_base = config.providers.volcengine.api_base
            transcription_resource_id = os.environ.get("VOLCENGINE_TRANSCRIPTION_RESOURCE_ID") or "volc.bigasr.auc_turbo"
        else:
            transcription_api_key = config.providers.groq.api_key
            transcription_api_base = config.providers.groq.api_base
            transcription_resource_id = None

        return cls(
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=model,
            max_iterations=defaults.max_tool_iterations,
            context_window_tokens=context_window_tokens,
            context_block_limit=defaults.context_block_limit,
            max_tool_result_chars=defaults.max_tool_result_chars,
            provider_retry_mode=defaults.provider_retry_mode,
            tool_hint_max_length=defaults.tool_hint_max_length,
            web_config=config.tools.web,
            exec_config=config.tools.exec,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            mcp_servers=config.tools.mcp_servers,
            channels_config=config.channels,
            transcription_provider_config={
                "provider": transcription_provider_name,
                "api_key": transcription_api_key,
                "api_base": transcription_api_base,
                "language": config.channels.transcription_language,
                "resource_id": transcription_resource_id,
                "user_id": "originagent",
                "tts_config": {
                    "api_key": config.providers.volcengine.api_key,
                    "api_base": config.providers.volcengine.api_base,
                    "resource_id": os.environ.get("VOLCENGINE_TTS_RESOURCE_ID") or "seed-tts-2.0",
                    "sample_rate": 24000,
                    "output_dir": str(config.workspace_path / config.tools.local_awareness.audio.save_dir),
                },
            },
            timezone=defaults.timezone,
            runtime_profile=config.runtime.profile,
            unified_session=defaults.unified_session,
            disabled_skills=defaults.disabled_skills,
            session_ttl_minutes=defaults.session_ttl_minutes,
            cold_archive_enabled=defaults.cold_archive_enabled,
            consolidation_ratio=defaults.consolidation_ratio,
            max_messages=defaults.max_messages,
            tools_config=config.tools,
            model_presets=model_presets,
            model_preset=defaults.model_preset or "default",
            preset_snapshot_loader=preset_snapshot_loader,
            domain_runtime_overrides=domain_runtime_overrides,
            tool_audit_config=config.tools.audit,
            pairing_config=config.security.pairing,
            auxiliary_config=defaults.auxiliary,
            auxiliary_source_config=config,
            primary_provider_name=primary_provider_name,
            domain_packs_config=defaults.domain_packs,
            learning_config=defaults.learning.background_review,
            learning_config_loader=_background_review_config_loader,
            meta_cognition_config=defaults.learning.meta_cognition,
            curator_config=defaults.learning.curator,
            curator_config_loader=_curator_config_loader,
            evolution_config=defaults.learning.evolution,
            evolution_config_loader=_evolution_config_loader,
            dream_config=defaults.dream,
            nearline_memory_config=defaults.nearline_memory,
            enable_backend_cognition=defaults.enable_backend_cognition,
            effective_config=config,
            tiered_config=config.gateway.tiered_router,
            **extra,
        )

    @classmethod
    def from_options(
        cls,
        options: LoopOptions,
        provider: Any,
        **extra: Any,
    ) -> AgentLoop:
        """Create ``AgentLoop`` from a grouped ``LoopOptions`` object.

        This is the preferred construction path for new code.  The options
        object groups 60+ flat parameters into concern-specific dataclasses
        (``ProviderOptions``, ``ToolOptions``, ``ChannelOptions``,
        ``LearningOptions``, ``RuntimeOptions``).

        Extra keyword arguments are forwarded to ``AgentLoop.__init__``,
        allowing callers to override or extend derived options.
        """
        from OriginAgent.agent.loop_options import LoopOptions

        if not isinstance(options, LoopOptions):
            raise TypeError(f"expected LoopOptions, got {type(options).__name__}")

        return cls(
            bus=options.bus,
            provider=provider,
            workspace=options.workspace,
            model=options.provider.model,
            max_iterations=options.provider.max_iterations,
            context_window_tokens=options.provider.context_window_tokens,
            context_block_limit=options.provider.context_block_limit,
            max_tool_result_chars=options.provider.max_tool_result_chars,
            provider_retry_mode=options.provider.provider_retry_mode,
            tool_hint_max_length=options.tools.tool_hint_max_length,
            web_config=options.tools.web_config,
            exec_config=options.tools.exec_config,
            cron_service=options.runtime.cron_service,
            restrict_to_workspace=options.tools.restrict_to_workspace,
            session_manager=options.channels.session_manager,
            mcp_servers=options.tools.mcp_servers,
            channels_config=options.channels.channels_config,
            transcription_provider_config=options.channels.transcription_provider_config,
            timezone=options.channels.timezone,
            runtime_profile=options.runtime.runtime_profile,
            session_ttl_minutes=options.channels.session_ttl_minutes,
            consolidation_ratio=options.learning.consolidation_ratio,
            max_messages=options.channels.max_messages,
            hooks=options.runtime.hooks,
            unified_session=options.channels.unified_session,
            disabled_skills=options.runtime.disabled_skills,
            tools_config=options.tools.tools_config,
            image_generation_provider_config=options.tools.image_generation_provider_config,
            image_generation_provider_configs=options.tools.image_generation_provider_configs,
            provider_snapshot_loader=options.provider.provider_snapshot_loader,
            provider_signature=options.provider.provider_signature,
            model_presets=options.provider.model_presets,
            model_preset=options.provider.model_preset,
            preset_snapshot_loader=options.provider.preset_snapshot_loader,
            runtime_model_publisher=options.provider.runtime_model_publisher,
            device_action_executor=options.tools.device_action_executor,
            device_tools_real_mode=options.tools.device_tools_real_mode,
            device_registry=options.tools.device_registry,
            domain_runtime_overrides=options.runtime.domain_runtime_overrides,
            actor_resolver=options.runtime.actor_resolver,
            tool_audit_config=options.tools.tool_audit_config,
            pairing_config=options.runtime.pairing_config,
            auxiliary_config=options.provider.auxiliary_config,
            auxiliary_source_config=options.provider.auxiliary_source_config,
            auxiliary_provider_factory=options.provider.auxiliary_provider_factory,
            primary_provider_name=options.provider.primary_provider_name,
            domain_packs_config=options.runtime.domain_packs_config,
            domain_pack_manager=options.runtime.domain_pack_manager,
            learning_config=options.learning.learning_config,
            learning_config_loader=options.learning.learning_config_loader,
            meta_cognition_config=options.learning.meta_cognition_config,
            curator_config=options.learning.curator_config,
            curator_config_loader=options.learning.curator_config_loader,
            evolution_config=options.learning.evolution_config,
            evolution_config_loader=options.learning.evolution_config_loader,
            dream_config=options.learning.dream_config,
            nearline_memory_config=options.learning.nearline_memory_config,
            cold_archive_enabled=options.channels.cold_archive_enabled,
            tool_concurrency_limit=options.tools.tool_concurrency_limit,
            allow_agent_initiated_messages=options.learning.allow_agent_initiated_messages,
            enable_backend_cognition=options.learning.enable_backend_cognition,
            active_intent_interval_seconds=options.learning.active_intent_interval_seconds,
            active_intent_session_cooldown_seconds=options.learning.active_intent_session_cooldown_seconds,
            active_intent_intent_cooldown_seconds=options.learning.active_intent_intent_cooldown_seconds,
            active_intent_max_messages_per_session_per_pass=(
                options.learning.active_intent_max_messages_per_session_per_pass
            ),
            effective_config=options.runtime.effective_config,
            tiered_config=options.runtime.tiered_config,
            **extra,
        )

    def _build_turn_pipeline_deps(self) -> TurnPipelineDeps:
        return TurnPipelineDeps(
            auto_compact=self.auto_compact,
            commands=self.commands,
            command_loop=self,
            get_consolidator=self._get_consolidator,
            get_tools=self._get_tools,
            get_context=self._get_context,
            sessions=self.sessions,
            bus=self.bus,
            get_working_memory=self._get_working_memory,
            get_memory_governance=self._get_memory_governance,
            get_rolling_episode_compaction=self._get_rolling_episode_compaction,
            workspace=self.workspace,
            tools_config=self.tools_config,
            domain_runtime_contributions=self._domain_runtime_contributions,
            domain_runtime_overrides=self._domain_runtime_overrides,
            archive_session_file_cap=self._archive_session_file_cap,
            restore_runtime_checkpoint=self._restore_runtime_checkpoint,
            restore_pending_user_turn=self._restore_pending_user_turn,
            load_continuity_checkpoint=self._load_continuity_checkpoint,
            record_recovered_continuity_checkpoint=self._record_recovered_continuity_checkpoint,
            mark_webui_session=mark_webui_session,
            persist_shortcut_command_turn=self._persist_shortcut_command_turn,
            is_webui_message=self._is_webui_message,
            resolve_runtime_context=self._resolve_runtime_context,
            record_runtime_context=self._record_runtime_context,
            write_continuity_runtime_identity=self._write_continuity_runtime_identity,
            snapshot_for_trigger=self._snapshot_for_trigger,
            update_working_memory_from_turn=self._update_working_memory_from_turn,
            set_tool_context=self._set_tool_context,
            replay_token_budget=self._replay_token_budget,
            build_initial_messages=self._build_initial_messages,
            persist_user_message_early=self._persist_user_message_early,
            schedule_session_search_refresh=self._schedule_session_search_refresh,
            build_progress_callback=self._build_bus_progress_callback,
            build_retry_wait_callback=self._build_retry_wait_callback,
            pending_ask_user_id=pending_ask_user_id,
            consume_tool_approval_reply=self._consume_tool_approval_reply,
            build_recovered_continuity_context=self.context.build_recovered_continuity_context,
            run_agent_loop=self._run_agent_loop,
            clear_pending_user_turn=self._clear_pending_user_turn,
            clear_runtime_checkpoint=self._clear_runtime_checkpoint,
            save_turn=self._save_turn,
            record_governance_audit=self._record_governance_audit,
            save_continuity_checkpoint=self._save_continuity_checkpoint,
            schedule_background=self._schedule_background,
            schedule_nearline_memory=self._schedule_nearline_memory,
            schedule_background_review=self._schedule_background_review,
            schedule_curator_review=self._schedule_curator_review,
            automation_enabled=self._automation_enabled,
            action_planner=self.action_planner,
            record_action_continuity_audit=self._record_action_continuity_audit,
            assemble_outbound=self._assemble_outbound,
            get_max_messages=self._get_max_messages,
        )

    # ── Lazy getters for TurnPipelineDeps ────────────────────────────────
    # These replace anonymous lambdas so stack traces show meaningful names.

    def _get_consolidator(self):
        return self.consolidator

    def _get_tools(self):
        return self.tools

    def _get_context(self):
        return self.context

    def _get_working_memory(self):
        return self.working_memory

    def _get_memory_governance(self):
        return self.memory_governance

    def _get_rolling_episode_compaction(self):
        return self.rolling_episode_compaction

    def _get_max_messages(self):
        return self._max_messages

    def _resolve_state_key(self, session_key: str | None = None) -> str:
        """Resolve the effective session key for state-holder lookups.

        Falls back to ``_last_continuity_session_key`` when the caller
        doesn't pass an explicit key (backward-compat for pre-existing
        code paths).
        """
        if session_key:
            return session_key
        return getattr(self, "_last_continuity_session_key", None) or "__default__"

    def _record_runtime_context(self, session_key: str, runtime_context: RuntimeContext) -> None:
        state = self._state_holder.get(session_key)
        state.last_runtime_context = runtime_context
        state.last_continuity_session_key = session_key
        # Track current session key for _resolve_state_key fallback.
        # This is NOT a dual-write: it is the sole tracking variable for
        # "which session is currently active", needed because the callback
        # interface for _record_* methods does not carry session_key.
        self._last_continuity_session_key = session_key

    def _record_continuity_session_key(self, session_key: str) -> None:
        self._state_holder.get(session_key).last_continuity_session_key = session_key

    def _record_context_assembly(self, payload: dict[str, Any]) -> None:
        state_key = self._resolve_state_key()
        self._state_holder.get(state_key).last_context_assembly = dict(payload)

    def _record_recovered_continuity_checkpoint(
        self,
        checkpoint: dict[str, Any] | None,
    ) -> None:
        state_key = self._resolve_state_key()
        self._state_holder.get(state_key).last_recovered_continuity_checkpoint = dict(checkpoint or {})

    def _record_governance_audit(self, audit: dict[str, Any]) -> None:
        state_key = self._resolve_state_key()
        self._state_holder.get(state_key).last_governance_audit = dict(audit)

    def _record_action_continuity_audit(self, audit: dict[str, Any]) -> None:
        state_key = self._resolve_state_key()
        state = self._state_holder.get(state_key)
        state.last_action_continuity_audit = dict(audit)
        state.cached_action_summary = normalize_action_summary(dict(audit))

    def _build_cognitive_runtime_deps(self) -> CognitiveRuntimeDeps:
        return CognitiveRuntimeDeps(
            cognitive_loop=self.cognitive_loop,
            cognitive_scheduler=self.cognitive_scheduler,
            bus=self.bus,
            sessions=self.sessions,
            active_intents=self.active_intents,
            reminder_store=self._reminder_store,
            working_memory=self.working_memory,
            cognitive_audit=self._cognitive_audit,
            running_flag=lambda: self._running,
            build_runtime_context=lambda session_key: self._build_cognitive_runtime_context(session_key),
            collect_candidates=lambda session_key: self._collect_cognitive_candidates(session_key),
            write_cognitive_event_to_working_memory=(
                lambda session, **kwargs: self._write_cognitive_event_to_working_memory(session, **kwargs)
            ),
            record_last_scan=lambda payload: self._record_cognitive_scan(payload),
            utcnow_iso=_utcnow_iso,
        )

    def _record_cognitive_scan(self, payload: dict[str, Any]) -> None:
        state_key = self._resolve_state_key()
        self._state_holder.get(state_key).last_cognitive_scan = dict(payload)

    def _get_turn_orchestrator(self) -> TurnOrchestrator:
        return self._turn_orchestrator

    def _get_message_dispatcher(self) -> MessageDispatcher:
        return self._message_dispatcher

    def _build_system_turn_loop_context(self) -> SystemTurnLoopContext:
        return SystemTurnLoopContext(
            restore_runtime_checkpoint=lambda session: self._restore_runtime_checkpoint(session),
            restore_pending_user_turn=lambda session: self._restore_pending_user_turn(session),
            persist_subagent_followup=lambda session, msg: self._persist_subagent_followup(session, msg),
            resolve_runtime_context=lambda *args, **kwargs: self._resolve_runtime_context(*args, **kwargs),
            snapshot_for_trigger=lambda trigger: self._snapshot_for_trigger(trigger),
            update_working_memory_from_turn=lambda *args, **kwargs: self._update_working_memory_from_turn(
                *args,
                **kwargs,
            ),
            set_tool_context=lambda *args, **kwargs: self._set_tool_context(*args, **kwargs),
            replay_token_budget=lambda: self._replay_token_budget(),
            snapshot_context_assembly_from_messages=lambda *args, **kwargs: self._snapshot_context_assembly_from_messages(
                *args,
                **kwargs,
            ),
            run_agent_loop=lambda *args, **kwargs: self._run_agent_loop(*args, **kwargs),
            save_turn=lambda session, messages, skip: self._save_turn(session, messages, skip),
            archive_session_file_cap=self._archive_session_file_cap,
            clear_runtime_checkpoint=lambda session: self._clear_runtime_checkpoint(session),
            schedule_background=lambda coro: self._schedule_background(coro),
            schedule_nearline_memory=lambda ctx: self._schedule_nearline_memory(ctx),
            get_max_messages=lambda: self._max_messages,
            get_context_window_tokens=lambda: self.context_window_tokens,
            record_runtime_context=lambda session_key, runtime_context: self._record_runtime_context(
                session_key,
                runtime_context,
            ),
            record_continuity_session_key=lambda session_key: self._record_continuity_session_key(session_key),
            record_context_assembly=lambda payload: self._record_context_assembly(payload),
        )

    def _sync_subagent_runtime_limits(self) -> None:
        """Keep subagent runtime limits aligned with mutable loop settings.

        Delegates to AgentRuntime when available; falls back for tests
        that bypass __init__.
        """
        if hasattr(self, "_runtime") and self._runtime is not None:
            self._runtime._sync_subagent_runtime_limits()
        else:
            self.subagents.max_iterations = self.max_iterations

    def _archive_session_file_cap(self, messages: list[dict], *, session_key: str, reason: str) -> None:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._archive_session_file_cap(messages, session_key=session_key, reason=reason)
        if self.session_cold_archive is not None:
            self.session_cold_archive.archive(session_key, messages, reason=reason)
        self.context.memory.raw_archive(messages)

    def _apply_provider_snapshot(self, snapshot: ProviderSnapshot) -> None:
        """Swap model/provider for future turns without disturbing an active one.

        Updates loop-level identity fields directly; delegates sub-service
        propagation (runner, subagents, consolidator, dream, etc.) to AgentHost.
        """
        if snapshot.signature == self._provider_signature:
            return
        provider = snapshot.provider
        model = snapshot.model
        context_window_tokens = snapshot.context_window_tokens
        old_model = self.model
        old_context_window_tokens = self.context_window_tokens
        self.provider = provider
        self.model = model
        self.context_window_tokens = context_window_tokens
        self._provider_signature = snapshot.signature
        self._default_selection_signature = preset_helpers.default_selection_signature(
            snapshot.signature
        )
        logger.info(
            "Runtime model updated for next turn: {} -> {} (context window {} -> {})",
            old_model,
            model,
            old_context_window_tokens,
            context_window_tokens,
        )
        if self._runtime_model_publisher:
            self._runtime_model_publisher(model, self.model_preset)
        # Propagate to sub-services via AgentHost
        if hasattr(self, "_host") and self._host is not None:
            self._host._apply_provider_snapshot(snapshot)

    def _refresh_provider_snapshot(self) -> None:
        """Refresh the active provider snapshot before each turn.

        Delegates snapshot loading to AgentHost when available; falls back
        to the original logic for callers that bypass ``__init__``.
        """
        if hasattr(self, "_host") and self._host is not None:
            snapshot = self._host.refresh_provider_snapshot()
            if snapshot is not None:
                self._apply_provider_snapshot(snapshot)
            return

        # Fallback: original logic for tests that bypass __init__
        if self.model_preset and self.model_preset != "default":
            if self._preset_snapshot_loader is None:
                return
            try:
                snapshot = self._preset_snapshot_loader(self.model_preset)
            except Exception:
                logger.exception("Failed to refresh model preset config")
                return
            if snapshot.signature == self._provider_signature:
                return
            self._apply_provider_snapshot(snapshot)
            return

        if self._provider_snapshot_loader is None:
            return
        try:
            snapshot = self._provider_snapshot_loader()
        except Exception:
            logger.exception("Failed to refresh provider config")
            return
        if snapshot.signature == self._provider_signature:
            return
        self.model_preset = "default"
        self._apply_provider_snapshot(snapshot)

    def set_model_preset(self, name: str) -> None:
        """Switch the active runtime model preset for subsequent turns.

        Delegates snapshot building to AgentHost when available; falls back
        for callers that bypass ``__init__``.
        """
        if hasattr(self, "_host") and self._host is not None:
            snapshot = self._host.build_preset_snapshot(name)
            if snapshot is not None:
                self.model_preset = preset_helpers.normalize_preset_name(name, self.model_presets)
                self._apply_provider_snapshot(snapshot)
            return

        # Fallback for tests that bypass __init__
        preset_name = preset_helpers.normalize_preset_name(name, self.model_presets)
        snapshot = preset_helpers.build_runtime_preset_snapshot(
            name=preset_name,
            presets=self.model_presets,
            provider=self.provider,
            loader=self._preset_snapshot_loader,
        )
        self.model_preset = preset_name
        self._apply_provider_snapshot(snapshot)

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        register_default_tools(
            self.tools,
            workspace=self.workspace,
            bus=self.bus,
            config=self.tools_config,
            web_config=self.web_config,
            exec_config=self.exec_config,
            restrict_to_workspace=self.restrict_to_workspace,
            sessions=self.sessions,
            pending_queues=self._pending_queues,
            cron_service=self.cron_service,
            audit_config=self._tool_audit_config,
            domain_pack_manager=self.domain_packs,
            background_review_service=self.background_review,
            curator_service=self.curator,
            session_search_index_service=self.session_search_index,
            subagent_manager=self.subagents,
            file_state_store=self._file_state_store,
            provider_snapshot_loader=self._provider_snapshot_loader,
            image_generation_provider_configs=self._image_generation_provider_configs,
            timezone=self.context.timezone or "UTC",
            runtime_profile=self._runtime_profile,
            introspection_service=self.introspection,
            confirmation_store=self._confirmation_store,
            confirmation_manager=self._confirmation_manager,
            grant_store=self._grant_store,
            domain_runtime_overrides=self._domain_runtime_overrides,
            evolution_config=self.evolution_config,
            domain_runtime_contributions=self._domain_runtime_contributions,
            cron_bridge=getattr(getattr(self, "_host", None), "_cron_bridge", None),
        )

    def _build_tool_context(self):
        return build_tool_context(
            config=self.tools_config,
            workspace=self.workspace,
            bus=self.bus,
            subagent_manager=self.subagents,
            cron_service=self.cron_service,
            sessions=self.sessions,
            file_state_store=self._file_state_store,
            provider_snapshot_loader=self._provider_snapshot_loader,
            image_generation_provider_configs=self._image_generation_provider_configs,
            timezone=self.context.timezone or "UTC",
            audit_config=self._tool_audit_config,
            context_extras=build_domain_tool_context_extras(
                domain_pack_manager=self.domain_packs,
                workspace=self.workspace,
                config=self.tools_config,
                overrides=self._domain_runtime_overrides,
                contributions=self._domain_runtime_contributions,
            ),
        )

    def _register_domain_tools(self) -> None:
        """Load tools declared by active domain packs without replacing core tools."""
        register_domain_tools(
            self.tools,
            domain_pack_manager=self.domain_packs,
            context=self._build_tool_context(),
        )

    def _register_plugin_tools(self) -> None:
        """Load external OriginAgent tool plugins without replacing core tools."""
        register_plugin_tools(self.tools, context=self._build_tool_context())

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy).

        Delegates to AgentHost; kept as compat shell for callers like
        ``process_direct()``.
        """
        await self._host._connect_mcp()

    def _set_tool_context(
        self, channel: str, chat_id: str,
        message_id: str | None = None, metadata: dict | None = None,
        session_key: str | None = None,
        actor_id: str | None = None,
        trigger: str | None = None,
        capability_snapshot: CapabilitySnapshot | None = None,
        runtime_context: RuntimeContext | None = None,
        turn_id: str | None = None,
    ) -> None:
        """Update context for all tools that need routing info.

        Delegates to AgentRuntime when available.
        """
        if hasattr(self, "_runtime") and self._runtime is not None:
            self._runtime._set_tool_context(
                channel, chat_id,
                message_id=message_id, metadata=metadata,
                session_key=session_key, actor_id=actor_id,
                trigger=trigger, capability_snapshot=capability_snapshot,
                runtime_context=runtime_context, turn_id=turn_id,
            )
            return
        snapshot = capability_snapshot or self._capability_snapshot
        set_tools_runtime_context(
            self.tools,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
            metadata=metadata,
            session_key=session_key,
            actor_id=actor_id,
            trigger=trigger,
            capability_snapshot=snapshot,
            runtime_context=runtime_context,
            unified_session=self._unified_session,
            unified_session_key=UNIFIED_SESSION_KEY,
            turn_id=turn_id,
        )

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        from OriginAgent.utils.helpers import strip_think

        return strip_think(text) or None

    @staticmethod
    def _runtime_chat_id(msg: InboundMessage) -> str:
        """Return the chat id shown in runtime metadata for the model."""
        return runtime_chat_id(msg)

    @staticmethod
    def _snapshot_for_trigger(trigger: str | None) -> CapabilitySnapshot:
        return snapshot_for_trigger(trigger)

    def _resolve_runtime_context(
        self,
        msg: InboundMessage,
        *,
        channel: str | None = None,
        chat_id: str | None = None,
        session_key: str | None = None,
    ) -> RuntimeContext:
        """Resolve runtime context for a message.

        Delegates to AgentRuntime when available.
        """
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._resolve_runtime_context(
                msg, channel=channel, chat_id=chat_id, session_key=session_key,
            )
        return self.actor_resolver.resolve_runtime_context(
            channel=msg.channel,
            chat_id=msg.chat_id,
            sender_id=msg.sender_id,
            metadata=msg.metadata or {},
            session_key=session_key,
            routing_channel=channel,
            routing_chat_id=chat_id,
        )

    def _tool_hint(self, tool_calls: list) -> str:
        """Format tool calls as concise hints with smart abbreviation.

        Delegates to AgentRuntime when available.
        """
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._tool_hint(tool_calls)
        from OriginAgent.utils.tool_hints import format_tool_hints
        return format_tool_hints(tool_calls, max_length=self.tool_hint_max_length)

    async def _build_bus_progress_callback(
        self, msg: InboundMessage
    ) -> Callable[..., Awaitable[None]]:
        """Build a progress callback that publishes to the message bus."""
        return await build_bus_progress_callback(self.bus, msg)

    async def _build_retry_wait_callback(
        self, msg: InboundMessage
    ) -> Callable[[str], Awaitable[None]]:
        """Build a retry-wait callback that publishes to the message bus."""
        return await build_retry_wait_callback(self.bus, msg)

    def _turn_persist_manager(self) -> TurnPersistManager:
        manager = getattr(self, "_persist", None)
        if isinstance(manager, TurnPersistManager):
            return manager
        max_chars = getattr(self, "max_tool_result_chars", AgentDefaults().max_tool_result_chars)
        manager = TurnPersistManager(max_chars, getattr(self, "sessions", None))
        try:
            self._persist = manager
        except Exception:
            pass
        return manager

    def _persist_user_message_early(self, msg: InboundMessage, session: Session,
                                     pending_ask_id: str | None, **kwargs: Any) -> bool:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._persist_user_message_early(msg, session, pending_ask_id, **kwargs)
        return AgentLoop._turn_persist_manager(self).persist_user_message_early(msg, session, pending_ask_id, **kwargs)

    def _build_initial_messages(self, msg: InboundMessage, session: Session, history: list[dict],
                                 pending_ask_id: str | None, pending_summary: str | None,
                                 internal_event=None, recovered_continuity_block=None) -> list[dict]:
        if hasattr(self, "_runtime") and self._runtime is not None:
            messages = self._runtime._build_initial_messages(
                msg, session, history, pending_ask_id, pending_summary,
                internal_event=internal_event, recovered_continuity_block=recovered_continuity_block,
            )
            return messages
        # fallback
        self_model_payload = self._build_prompt_self_model()
        if pending_ask_id:
            system_prompt = self.context.build_system_prompt(channel=msg.channel, session_summary=pending_summary, self_model_payload=self_model_payload)
            messages = ask_user_tool_result_messages(system_prompt, history, pending_ask_id, image_generation_prompt(msg.content, msg.metadata))
            if self.context._context_config.enable_phase1_continuity:
                assembled = self.context.assemble_user_content(current_message=None, media=None, channel=msg.channel, chat_id=self._runtime_chat_id(msg), sender_id=msg.sender_id, session_summary=pending_summary, session_metadata=session.metadata, internal_event=None, runtime_context=self._state_holder.get(session.key).last_runtime_context, session_key=session.key, recovered_continuity_block=recovered_continuity_block, include_current_message=False)
                self._state_holder.get(session.key).last_context_assembly = dict(assembled.audit)
                messages.append({"role": "user", "content": assembled.blocks})
                return self.context._apply_prompt_budget(messages, context_window_tokens=self.context_window_tokens, max_completion_tokens=getattr(self.provider.generation, "max_tokens", 4096))
            messages.append({"role": "user", "content": [self.context.build_runtime_context_block(msg.channel, self._runtime_chat_id(msg), self.context.timezone, sender_id=msg.sender_id, session_metadata=session.metadata)] + ([recovered_continuity_block] if recovered_continuity_block else []) + list(self.context.build_reference_context_blocks(session_summary=pending_summary, session_key=session.key, runtime_context=self._state_holder.get(session.key).last_runtime_context, current_message=msg.content))})
            self._state_holder.get(session.key).last_context_assembly = {"enabled": False, "session_key": session.key, "reason": "phase1_continuity_disabled", "block_kinds": [self.context.RUNTIME_CONTEXT_KIND] + [block.get("_meta", {}).get("kind") for block in self.context.build_reference_context_blocks(session_summary=pending_summary, session_key=session.key, runtime_context=self._state_holder.get(session.key).last_runtime_context, current_message=msg.content)]}
            return self.context._apply_prompt_budget(messages, context_window_tokens=self.context_window_tokens, max_completion_tokens=getattr(self.provider.generation, "max_tokens", 4096))
        state = self._state_holder.get(session.key)
        built = self.context.build_messages(history=history, current_message=image_generation_prompt(msg.content, msg.metadata), media=msg.media if msg.media else None, channel=msg.channel, chat_id=self._runtime_chat_id(msg), sender_id=msg.sender_id, session_summary=pending_summary, session_metadata=session.metadata, internal_event=internal_event, self_model_payload=self_model_payload, runtime_context=state.last_runtime_context, session_key=session.key, recovered_continuity_block=recovered_continuity_block, context_window_tokens=self.context_window_tokens, max_completion_tokens=getattr(self.provider.generation, "max_tokens", 4096))
        state.last_context_assembly = dict(getattr(self.context, "_last_context_assembly_audit", {}) or {})
        if not state.last_context_assembly:
            state.last_context_assembly = self._snapshot_context_assembly_from_messages(built, session_key=session.key, runtime_context=state.last_runtime_context)
        return built

    def _build_prompt_self_model(self) -> dict[str, Any]:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._build_prompt_self_model()
        snapshot = self.introspection.runtime_context_snapshot()
        return SelfModelService(
            self.workspace, audit_mode=self._tool_audit_config.mode,
            runtime_profile=self._runtime_profile,
            domain_pack_manager=self.domain_packs, skills_loader=self.context.skills,
            memory_store=self.context.memory,
            nearline_memory_config=self._nearline_memory_config,
            runtime_snapshot=snapshot,
        ).build()

    def _consume_tool_approval_reply(
        self,
        *,
        session_key: str,
        actor_id: str | None,
        reply: str,
    ) -> tuple[tuple[str, str] | None, bool]:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._consume_tool_approval_reply(
                session_key=session_key, actor_id=actor_id, reply=reply,
            )
        # fallback
        confirmation = self._confirmation_manager.latest_pending_tool_approval(session_key)
        if confirmation is None:
            return None, False
        classification = classify_confirmation_reply(reply)
        if classification not in {"confirmed", "rejected"}:
            return None, False
        result = self._confirmation_manager.resolve_user_reply(confirmation.confirmation_id, reply)
        tool_name = confirmation.metadata.get("tool_name") or confirmation.action or "tool"
        if result.decision == "confirmed":
            grant = issue_tool_approval_grant(confirmation, self._grant_store, approved_by=actor_id)
            return (("tool_approval",
                     f"Tool approval confirmed for {tool_name}. Short-lived grant {grant.grant_id} is active for this session. "
                     "Continue the pending task using the newly approved capability."), True)
        if result.decision == "rejected":
            return (("tool_approval",
                     f"Tool approval was rejected for {tool_name}. Do not use that capability unless the user asks again."), True)
        return None, False

    def _is_webui_message(self, msg: InboundMessage) -> bool:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._is_webui_message(msg)
        return msg.channel == "websocket" and msg.metadata.get("webui") is True

    def _append_webui_command_transcript(self, msg: InboundMessage, content: str) -> None:
        if hasattr(self, "_runtime") and self._runtime is not None:
            self._runtime._append_webui_command_transcript(msg, content)
            return
        if not self._is_webui_message(msg):
            return
        try:
            append_transcript_object(f"websocket:{msg.chat_id}", {"event": "message", "chat_id": msg.chat_id, "text": content})
        except (TypeError, ValueError, OSError) as e:
            logger.warning("webui command transcript append failed: {}", e)

    def _write_continuity_runtime_identity(self, session: Session, runtime_context: RuntimeContext | None) -> None:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._write_continuity_runtime_identity(session, runtime_context)
        if runtime_context is None:
            return
        session.metadata[CONTINUITY_RUNTIME_IDENTITY_KEY] = {
            "user_id": runtime_context.user_id, "device_id": runtime_context.device_id,
            "session_id": runtime_context.session_id, "scope": runtime_context.default_scope,
            "updated_at": _utcnow_iso(),
        }

    def _persist_shortcut_command_turn(self, msg: InboundMessage, session_key: str, result: OutboundMessage) -> None:
        raw = msg.content.strip()
        if raw.lower() == "/new":
            if self._is_webui_message(msg):
                delete_webui_transcript(session_key)
            return
        session = self.sessions.get_or_create(session_key)
        mark_webui_session(session, msg.metadata)
        self._persist_user_message_early(msg, session, pending_ask_id=None, _command=True)
        if result.content.strip():
            session.add_message("assistant", result.content, _command=True)
        self._clear_pending_user_turn(session)
        self.sessions.save(session)
        self._append_webui_command_transcript(msg, result.content)

    async def _dispatch_command_inline(
        self,
        msg: InboundMessage,
        key: str,
        raw: str,
        dispatch_fn: Callable[[CommandContext], Awaitable[OutboundMessage | None]],
    ) -> None:
        """Dispatch a command directly from the run() loop and publish the result."""
        session = self.sessions.get_or_create(key)
        lang = (msg.metadata or {}).get("lang", "") or os.environ.get("ORIGINAGENT_LANG", "")
        ctx = CommandContext(msg=msg, session=session, key=key, raw=raw, lang=lang, loop=self)
        result = await dispatch_fn(ctx)
        if result:
            self._persist_shortcut_command_turn(msg, key, result)
            if self._is_webui_message(msg):
                result.metadata["_webui_transcript_recorded"] = True
            await self.bus.publish_outbound(result)
            if msg.channel == "websocket":
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id, content="",
                        metadata={**dict(msg.metadata or {}), "_turn_end": True,
                                  "goal_state": goal_state_ws_blob(self.sessions.get_or_create(key).metadata)},
                    )
                )
        else:
            logger.warning("Command '{}' matched but dispatch returned None", raw)

    async def _cancel_active_tasks(self, key: str) -> int:
        tasks = self._active_tasks.pop(key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass  # Cancellation is expected during shutdown
            except BaseException as exc:
                logger.warning("Task cancellation wait failed: {}: {}", type(exc).__name__, exc)
        sub_cancelled = await self.subagents.cancel_by_session(key)
        return cancelled + sub_cancelled

    def _effective_session_key(self, msg: InboundMessage) -> str:
        """Return the session key used for task routing and mid-turn injections.

        Delegates to AgentRuntime when available.
        """
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._effective_session_key(msg)
        if self._unified_session and not msg.session_key_override:
            return "unified:default"
        return msg.session_key

    def _replay_token_budget(self) -> int:
        """Derive a token budget for session history replay from the context window.

        Delegates to AgentRuntime when available.
        """
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._replay_token_budget()
        if self.context_window_tokens <= 0:
            return 0
        max_output = getattr(getattr(self.provider, "generation", None), "max_tokens", 4096)
        try:
            reserved_output = int(max_output)
        except (TypeError, ValueError):
            reserved_output = 4096
        budget = self.context_window_tokens - max(1, reserved_output) - 1024
        return budget if budget > 0 else max(128, self.context_window_tokens // 2)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
        *,
        session: Session | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
        pending_queue: asyncio.Queue | None = None,
        actor_id: str | None = None,
        trigger: str | None = None,
        capability_snapshot: CapabilitySnapshot | None = None,
    ) -> tuple[str | None, list[str], list[dict], str, bool]:
        """Run the agent iteration loop via AgentRuntime."""
        async def _checkpoint_cb(sess: Session, payload: dict[str, Any]) -> None:
            self._set_runtime_checkpoint(sess, payload)

        result = await self._runtime._run_agent_loop(
            initial_messages,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            on_retry_wait=on_retry_wait,
            session=session,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
            metadata=metadata,
            session_key=session_key,
            pending_queue=pending_queue,
            actor_id=actor_id,
            trigger=trigger,
            capability_snapshot=capability_snapshot,
            checkpoint_cb=_checkpoint_cb,
            set_current_iteration=lambda it: setattr(self, "_current_iteration", it),
        )
        self._last_usage = getattr(result, "usage", None) if hasattr(result, "usage") else None
        return result

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._host._connect_mcp()
        self._schedule_session_search_refresh(force=self.session_search_index.rebuild_on_start)
        self._host._start_active_intent_loop()
        await self._host.start_bdi()
        logger.info("Agent loop started")
        await self._get_message_dispatcher().run_forever()

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Compatibility shell; delegates to MessageDispatcher.dispatch_message.

        Before delegating, resolves tenant identity from channel+sender_id and
        sets the tenant's unified session key as the session_key_override.
        """
        # ── Tenant resolution ──────────────────────────────────────────────
        # hasattr guard preserves backward compat for tests that bypass
        # __init__ via __new__ (no tenant registry configured).
        resolver = getattr(self, "_identity_resolver", None)
        if resolver is not None:
            tenant = resolver.resolve(
                channel=msg.channel,
                sender_id=msg.sender_id,
            )
            from OriginAgent.agent.tenant_context import set_current_tenant
            set_current_tenant(tenant)

            if not msg.session_key_override:
                msg.session_key_override = tenant.unified_session_key

        return await self._get_message_dispatcher().dispatch_message(msg)

    def expire_stale_sessions(self) -> int:
        """Periodic maintenance: expire stale SessionStateHolder entries.

        Called by ``MessageDispatcher.run_forever()`` from its idle cycle.
        """
        removed = self._state_holder.expire_stale()
        if removed > 0:
            logger.debug("SessionStateHolder: expired {} stale session(s) during idle cycle", removed)
        return removed

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections.

        Delegates infrastructure shutdown to AgentHost; keeps
        SessionStateHolder cleanup here because the holder is loop-owned.
        """
        removed = self._state_holder.expire_stale()
        if removed > 0:
            logger.debug("SessionStateHolder: expired {} stale sessions during shutdown", removed)
        await self._host.shutdown()

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown).

        Delegates to AgentHost so all background tasks live in one place.
        Falls back to direct task creation when ``_host`` is not initialised
        (e.g. tests that bypass ``__init__`` via ``__new__``).
        """
        if hasattr(self, "_host") and self._host is not None:
            self._host.schedule_background(coro)
        else:
            task = asyncio.create_task(coro)
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    def _start_active_intent_loop(self) -> None:
        """Compatibility shell delegating active-intent startup to AgentHost.

        Falls back to direct cognitive_runtime delegation when ``_host`` is
        not initialised (e.g. tests that bypass ``__init__`` via ``__new__``).
        """
        if hasattr(self, "_host") and self._host is not None:
            self._active_intent_task = self._host.start_active_intent_loop(
                self._active_intent_task
            )
        else:
            self._active_intent_task = self._cognitive_runtime.start_active_intent_loop(
                self._active_intent_task
            )

    async def _active_intent_loop(self) -> None:
        """Compatibility shell delegating the active-intent loop to AgentCognitiveRuntime."""
        await self._cognitive_runtime.active_intent_loop()

    async def _run_cognitive_pass_for_session(
        self,
        session_key: str,
        *,
        active_task_count: int,
        running_subagents: int,
    ) -> list[CognitiveDecision]:
        """Compatibility shell delegating per-session cognition to AgentCognitiveRuntime."""
        return await self._cognitive_runtime.run_cognitive_pass_for_session(
            session_key,
            active_task_count=active_task_count,
            running_subagents=running_subagents,
        )

    def _active_task_count(self, session_key: str) -> int:
        """Runtime provider used by cognitive scheduling and compatibility tests."""
        active_tasks = self._active_tasks.get(session_key, [])
        return sum(1 for task in active_tasks if not task.done())

    def _build_cognitive_runtime_context(self, session_key: str) -> RuntimeContext:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._build_cognitive_runtime_context(session_key)
        return RuntimeContext(actor_id="user", user_id="user", session_id=session_key)

    def _collect_cognitive_candidates(self, session_key: str) -> list[dict[str, Any]]:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._collect_cognitive_candidates(session_key)
        return []

    def _candidate_to_cognitive_event(self, session_key: str, item: Any) -> CognitiveEvent:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._candidate_to_cognitive_event(session_key, item)
        return CognitiveEvent(
            event_id="fallback", session_key=session_key,
            event_type="fallback", source_type="fallback",
            source_reference="", summary="", priority="low",
            payload={},
        )

    def _write_cognitive_event_to_working_memory(
        self, session: Session, *, runtime_context: RuntimeContext, event: CognitiveEvent,
    ) -> bool:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._write_cognitive_event_to_working_memory(
                session, runtime_context=runtime_context, event=event,
            )
        return False

    def stop(self) -> None:
        """Stop the agent loop."""
        self._host.stop_bdi()
        self._host.stop()
        self._running = False  # compat — mirrors _host.running
        logger.info("Agent loop stopping")

    async def _process_system_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        pending_queue: asyncio.Queue | None = None,
        capability_snapshot: CapabilitySnapshot | None = None,
    ) -> OutboundMessage | None:
        """Compatibility shell; delegates to SystemTurnHandler.process_message."""
        return await self._system_turn_handler.process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            pending_queue=pending_queue,
            capability_snapshot=capability_snapshot,
        )

    def _update_working_memory_from_turn(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        current_message: str | None,
        internal_event: str | None = None,
        media_paths: list[str] | None = None,
    ) -> None:
        pending_questions = None
        attention_items = None
        merged_attention: list[str] = []
        text = str(current_message or "").strip()
        lowered = text.lower()
        if text and ("?" in text or lowered.startswith(("how ", "what ", "why ", "can ", "should ", "do ", "is ", "are "))):
            pending_questions = [text]
        if internal_event and runtime_context.source in {"user_turn", "system"}:
            attention_items = [str(internal_event).strip()]
        if media_paths:
            self.world_state.ingest_media(
                session,
                runtime_context=runtime_context,
                media_paths=media_paths,
            )
        world_attention_items = self.world_state.current_attention_items(
            session,
            runtime_context=runtime_context,
        )
        if world_attention_items:
            max_world_items = max(
                1,
                int(getattr(self.context._context_config, "world_attention_max_items", 3) or 3),
            )
            merged_attention = [*([item for item in (attention_items or []) if item])]
            world_seen: set[str] = set()
            world_kept = 0
            for item in world_attention_items:
                text = str(item or "").strip()
                if not text:
                    continue
                normalized = text.casefold()
                if normalized in world_seen:
                    continue
                world_seen.add(normalized)
                if any(str(existing or "").strip().casefold() == normalized for existing in merged_attention):
                    continue
                merged_attention.append(text)
                world_kept += 1
                if world_kept >= max_world_items:
                    break
            attention_items = merged_attention
            state = self._state_holder.get(session.key)
            state.last_world_attention_write = {
                "world_attention_total": len(world_attention_items),
                "world_kept": world_kept,
                "world_truncated": max(0, len(world_attention_items) - world_kept),
                "world_truncated_by_limit": bool(world_kept >= max_world_items and len(world_attention_items) > world_kept),
                "attention_merged_items": list(merged_attention),
            }
        else:
            state = self._state_holder.get(session.key)
            state.last_world_attention_write = {
                "world_attention_total": 0,
                "world_kept": 0,
                "world_truncated": 0,
                "world_truncated_by_limit": False,
                "attention_merged_items": [item for item in (attention_items or []) if item],
            }
        self.working_memory.upsert(
            session,
            identity=runtime_context.identity,
            pending_questions=pending_questions,
            attention_items=attention_items,
        )

    @staticmethod
    @staticmethod
    def _pending_confirmation_ref(confirmation: Any) -> dict[str, Any]:
        return AgentRuntime._pending_confirmation_ref(confirmation)

    def _collect_pending_confirmation_refs(self, session: Session) -> list[dict[str, Any]]:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._collect_pending_confirmation_refs(session)
        return []

    def _save_continuity_checkpoint(self, session: Session, *, runtime_context: RuntimeContext | None) -> dict[str, Any]:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._save_continuity_checkpoint(session, runtime_context=runtime_context)
        return {"session_key": session.key, "current_goal": None}

    @staticmethod
    def _load_continuity_checkpoint(session: Session) -> dict[str, Any] | None:
        return AgentRuntime._load_continuity_checkpoint(session)

    def _snapshot_context_assembly_from_messages(self, messages: list[dict], *,
                                                   session_key: str | None, runtime_context: RuntimeContext | None) -> dict:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._snapshot_context_assembly_from_messages(messages, session_key=session_key, runtime_context=runtime_context)
        return {}

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        pending_queue: asyncio.Queue | None = None,
        capability_snapshot: CapabilitySnapshot | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        self._refresh_provider_snapshot()
        return await self._get_turn_orchestrator().process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            pending_queue=pending_queue,
            capability_snapshot=capability_snapshot,
        )

    def _install_meta_cognition_observer(self) -> None:
        runtime = getattr(self, "_meta_cognition_runtime", None)
        if runtime is None:
            return

        registry = self.tools
        existing = getattr(registry, "_execution_observer", None)
        loop = self

        class _MetaCognitionObserver:
            def on_tool_result(
                self,
                *,
                name: str,
                params: dict[str, Any],
                status: str,
                start: float,
                error_kind: str | None = None,
                policy_rule: str | None = None,
                result: Any = None,
            ) -> None:
                tool_runtime_context = registry.runtime_context
                session_key = str(tool_runtime_context.session_key or "").strip()
                if not session_key:
                    return

                if status in {"error", "policy_denied"}:
                    fusion = getattr(loop, "_perception_fusion", None)
                    if fusion is not None and fusion.enabled:
                        _re = fusion.bridge_tool_result(
                            session_key=session_key,
                            tool_name=name,
                            status=status,
                            params=params,
                            error_kind=error_kind,
                        )
                        if _re is not None:
                            _t = bridge_runtime_event_to_trigger(_re)
                            if _t is not None:
                                loop._record_meta_trigger(
                                    _t,
                                    turn_id=tool_runtime_context.turn_id,
                                )
                    else:
                        trigger = build_tool_failure_trigger(
                            session_key=session_key,
                            tool_name=name,
                            params=params,
                            status=status,
                            error_kind=error_kind,
                            policy_rule=policy_rule,
                        )
                        if trigger is not None:
                            loop._record_meta_trigger(
                                trigger,
                                turn_id=tool_runtime_context.turn_id,
                            )

                if name == "complete_goal" and status == "success":
                    fusion = getattr(loop, "_perception_fusion", None)
                    if fusion is not None and fusion.enabled:
                        _re = fusion.bridge_tool_result(
                            session_key=session_key,
                            tool_name=name,
                            status=status,
                            params=params,
                        )
                        if _re is not None:
                            _t = bridge_runtime_event_to_trigger(_re)
                            if _t is not None:
                                loop._record_meta_trigger(
                                    _t,
                                    turn_id=tool_runtime_context.turn_id,
                                )
                    else:
                        session = loop.sessions.get_or_create(session_key)
                        before_status = None
                        raw = goal_state_raw(session.metadata)
                        parsed = parse_goal_state(raw)
                        if isinstance(parsed, dict):
                            before_status = parsed.get("status")
                        trigger = build_task_completion_trigger(
                            session_key=session_key,
                            session_metadata=dict(session.metadata or {}),
                            params=params,
                        )
                        if trigger is not None:
                            payload = dict(trigger.payload)
                            payload["goal_status_before_completion"] = before_status or "active"
                            trigger = MetaTrigger(
                                trigger_id=trigger.trigger_id,
                                session_key=trigger.session_key,
                                trigger_type=trigger.trigger_type,
                                source_type=trigger.source_type,
                                source_reference=trigger.source_reference,
                                severity=trigger.severity,
                                created_at=trigger.created_at,
                                cooldown_key=trigger.cooldown_key,
                                evidence_refs=trigger.evidence_refs,
                                payload=payload,
                            )
                            loop._record_meta_trigger(
                                trigger,
                                turn_id=tool_runtime_context.turn_id,
                            )


                if existing is not None:
                    existing.on_tool_result(
                        name=name,
                        params=params,
                        status=status,
                        start=start,
                        error_kind=error_kind,
                        policy_rule=policy_rule,
                        result=result,
                    )

        registry._execution_observer = _MetaCognitionObserver()

    def _record_meta_trigger(self, trigger: MetaTrigger, *, turn_id: str | None = None) -> None:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._record_meta_trigger(trigger, turn_id=turn_id)

    def _scan_meta_triggers_for_turn(self, ctx: TurnContext) -> None:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._scan_meta_triggers_for_turn(ctx)

    def _maybe_apply_meta_fast_path(self, trigger: MetaTrigger) -> None:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._maybe_apply_meta_fast_path(trigger)

    def _schedule_meta_cognition_reflection(self, ctx: TurnContext) -> None:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._schedule_meta_cognition_reflection(ctx)

    async def _reflect_meta_cognition_turn(self, *, session_key: str, turn_id: str,
                                            turn_snapshot: dict, accepted_triggers: list,
                                            runtime_context: RuntimeContext | None = None) -> None:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return await self._runtime._reflect_meta_cognition_turn(
                session_key=session_key, turn_id=turn_id, turn_snapshot=turn_snapshot,
                accepted_triggers=accepted_triggers, runtime_context=runtime_context,
            )

    def _meta_world_summary_preview(self, ctx: TurnContext) -> str:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._meta_world_summary_preview(ctx)
        return ""

    def _meta_runtime_context_summary(self, runtime_context: RuntimeContext | None) -> dict[str, Any]:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._meta_runtime_context_summary(runtime_context)
        return {} if runtime_context is None else {
            "actor_id": getattr(runtime_context, "actor_id", None),
            "user_id": getattr(runtime_context, "user_id", None),
            "session_id": getattr(runtime_context, "session_id", None),
            "device_id": getattr(runtime_context, "device_id", None),
            "trigger": getattr(runtime_context, "trigger", None),
            "source": getattr(runtime_context, "source", None),
            "default_scope": getattr(runtime_context, "default_scope", None),
        }

    def _assemble_outbound(
        self,
        msg: InboundMessage,
        final_content: str,
        all_msgs: list[dict[str, Any]],
        stop_reason: str,
        had_injections: bool,
        generated_media: list[str],
        on_stream: Callable[[str], Awaitable[None]] | None,
    ) -> OutboundMessage | None:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._assemble_outbound(
                msg, final_content, all_msgs, stop_reason,
                had_injections, generated_media, on_stream=on_stream,
            )
        # fallback
        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            if not had_injections or stop_reason == "empty_final_response":
                return None
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        meta = dict(msg.metadata or {})
        content, buttons = ask_user_outbound(
            final_content,
            ask_user_options_from_messages(all_msgs) if stop_reason == "ask_user" else [],
            msg.channel,
        )
        if on_stream is not None and stop_reason not in {"ask_user", "error", "tool_error"}:
            meta["_streamed"] = True
        if msg.channel == "websocket":
            meta["goal_state"] = goal_state_ws_blob(
                self.sessions.get_or_create(self._effective_session_key(msg)).metadata
            )
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=content, media=generated_media,
            metadata=meta, buttons=buttons,
        )

    async def _state_restore(self, ctx: TurnContext) -> str:
        """Compatibility wrapper; normal flow is driven by TurnOrchestrator."""
        return await self._turn_pipeline.state_restore(ctx)

    async def _state_compact(self, ctx: TurnContext) -> str:
        """Compatibility wrapper; normal flow is driven by TurnOrchestrator."""
        return await self._turn_pipeline.state_compact(ctx)

    async def _state_command(self, ctx: TurnContext) -> str:
        """Compatibility wrapper; normal flow is driven by TurnOrchestrator."""
        return await self._turn_pipeline.state_command(ctx)

    async def _state_build(self, ctx: TurnContext) -> str:
        """Compatibility wrapper; normal flow is driven by TurnOrchestrator."""
        return await self._turn_pipeline.state_build(ctx)

    async def _state_run(self, ctx: TurnContext) -> str:
        """Compatibility wrapper; normal flow is driven by TurnOrchestrator."""
        return await self._turn_pipeline.state_run(ctx)

    async def _state_save(self, ctx: TurnContext) -> str:
        """Compatibility wrapper; normal flow is driven by TurnOrchestrator."""
        return await self._turn_pipeline.state_save(ctx)

    def _automation_enabled(self) -> bool:
        cfg = getattr(self.tools_config, "device", None)
        return bool(
            cfg is not None
            and getattr(cfg, "enabled", False)
            and getattr(cfg, "lighting_enabled", False)
            and getattr(cfg, "automation_enabled", False)
        )

    def _bind_action_resume_precheck(self) -> None:
        executors: list[Any] = []
        override_executor = self._domain_runtime_overrides.get("device_action_executor")
        if override_executor is not None:
            executors.append(override_executor)
        for contribution in self._domain_runtime_contributions:
            tool_context = getattr(contribution, "tool_context", {}) or {}
            executor = tool_context.get("device_action_executor")
            if executor is not None and executor not in executors:
                executors.append(executor)
        for executor in executors:
            safe_executor = getattr(executor, "safe_executor", None)
            if safe_executor is not None and hasattr(safe_executor, "set_resume_precheck"):
                safe_executor.set_resume_precheck(self._resume_action_confirmation_precheck)

    def _resume_action_confirmation_precheck(
        self,
        intent: Any,
        confirmation: Any,
        now: datetime,
    ) -> ActionDecision | None:
        origin = str(getattr(intent, "continuity_origin", "") or "").strip()
        if origin not in {"smart_home", "loop_owned_rule_based", "robot"}:
            return None
        session_key = str(getattr(intent, "continuity_session_ref", "") or "").strip()
        if not session_key:
            return ActionDecision(
                decision="deny",
                reason="automation continuity session reference is missing",
            )
        session = self.sessions.get_or_create(session_key)
        identity = session.metadata.get(CONTINUITY_RUNTIME_IDENTITY_KEY)
        if not isinstance(identity, dict):
            return ActionDecision(
                decision="deny",
                reason="automation continuity runtime identity is unavailable",
            )
        user_id = str(identity.get("user_id") or "").strip()
        if not user_id:
            return ActionDecision(
                decision="deny",
                reason="automation continuity user identity is unavailable",
            )
        runtime_context = RuntimeContext(
            actor_id=user_id,
            user_id=user_id,
            session_id=str(identity.get("session_id") or session_key).strip() or session_key,
            device_id=str(identity.get("device_id") or "").strip() or None,
            trigger="automation",
            channel=str(session_key.split(":", 1)[0] or "system"),
            chat_id=str(session_key.split(":", 1)[1] if ":" in session_key else session_key),
            session_key=session_key,
            source="automation",
            default_scope=str(identity.get("scope") or "session").strip() or "session",
        )
        continuity_inputs = self.context.build_action_continuity_inputs(session_key, runtime_context)
        executor = AgentTurnPipeline._resolve_executor_for_origin(
            origin,
            self._domain_runtime_contributions,
        )
        if executor is None or not hasattr(executor, "automation_preconditions"):
            return ActionDecision(
                decision="deny",
                reason="automation executor is unavailable during confirmation resume",
            )
        payload = getattr(intent, "payload", {}) if isinstance(getattr(intent, "payload", {}), dict) else {}
        if origin == "robot":
            typed_action = TypedRobotAction(
                action_type=str(getattr(intent, "action", "") or "").strip(),
                target=str(payload.get("target") or "").strip() or "unitree_g1",
                parameters={
                    key: value
                    for key, value in payload.items()
                    if key not in {"target", "domain", "action_type"}
                },
                requested_by=getattr(intent, "requested_by", None),
                trigger="automation",
                idempotency_key=getattr(intent, "idempotency_key", None),
            )
        else:
            from OriginAgent.domain_packs.smart_home.runtime.device_actions import TypedDeviceAction

            room = None
            scope_parts = [part for part in str(getattr(intent, "scope", "") or "").split(".") if part]
            if len(scope_parts) >= 4 and scope_parts[0] == "home":
                room = scope_parts[1]
            typed_action = TypedDeviceAction(
                action_type=str(getattr(intent, "action", "") or "").strip(),
                device_id=str(payload.get("device_id") or "").strip(),
                domain=str(payload.get("domain") or "").strip(),
                room=room,
                parameters={
                    key: value
                    for key, value in payload.items()
                    if key not in {"device_id", "domain", "action_type"}
                },
                requested_by=getattr(intent, "requested_by", None),
                trigger="automation",
                idempotency_key=getattr(intent, "idempotency_key", None),
            )
        precondition = executor.automation_preconditions(
            typed_action,
            continuity_inputs=continuity_inputs,
        )
        if precondition.outcome == "allow":
            return None
        if origin == "robot" and precondition.outcome == "pending_confirmation":
            return ActionDecision(
                decision="allow",
                reason="robot handoff confirmation already satisfied",
            )
        decision_map = {
            "pending_confirmation": "ask_confirmation",
            "recommended_only": "deny",
            "deny": "deny",
            "denied": "deny",
        }
        return ActionDecision(
            decision=decision_map.get(precondition.outcome, "deny"),
            reason=precondition.reason,
        )

    def _device_action_executor_for_automation(self) -> Any | None:
        executor = self._domain_runtime_overrides.get("device_action_executor")
        if executor is not None:
            return executor
        for contribution in self._domain_runtime_contributions:
            tool_context = getattr(contribution, "tool_context", {}) or {}
            executor = tool_context.get("device_action_executor")
            if executor is not None:
                return executor
        return None

    async def _state_automation(self, ctx: TurnContext) -> str:
        """Compatibility wrapper; normal flow is driven by TurnOrchestrator."""
        return await self._turn_pipeline.state_automation(ctx)

    def _schedule_session_search_refresh(
        self,
        *,
        sources: list[str] | None = None,
        force: bool = False,
    ) -> None:
        if not getattr(self.session_search_index, "enabled", False):
            return
        self._schedule_background(
            self._refresh_session_search_index(sources=sources, force=force)
        )

    async def _refresh_session_search_index(
        self,
        *,
        sources: list[str] | None = None,
        force: bool = False,
    ) -> None:
        await asyncio.to_thread(
            self.session_search_index.refresh_incremental,
            sources=sources,
            force=force,
        )

    def _schedule_background_review(self, ctx: TurnContext) -> None:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._schedule_background_review(ctx)
        if ctx.session is None:
            return
        self.background_review.refresh_config()
        if not self.background_review.enabled:
            return
        if ctx.stop_reason in {"ask_user", "error", "tool_error"}:
            return
        if ctx.msg.channel == "system" or ctx.msg.sender_id == "subagent":
            return
        if not (ctx.final_content or "").strip():
            return
        max_recent = int(getattr(self.background_review.config, "max_recent_messages", 12) or 12)
        messages = [dict(m) for m in ctx.session.messages if not m.get("_command")][-max_recent:]
        self._schedule_background(
            self.background_review.review_turn(
                session_key=ctx.session_key, turn_id=ctx.turn_id,
                channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
                message_id=ctx.msg.metadata.get("message_id"), messages=messages,
            )
        )

    def _schedule_curator_review(self, ctx: TurnContext) -> None:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._schedule_curator_review(ctx)
        if ctx.session is None:
            return
        self.curator.refresh_config()
        if not self.curator.enabled:
            return
        if ctx.stop_reason in {"ask_user", "error", "tool_error"}:
            return
        if ctx.msg.channel == "system" or ctx.msg.sender_id == "subagent":
            return
        if not (ctx.final_content or "").strip():
            return
        self._schedule_background(self.curator.review_workspace(session_key=ctx.session_key, turn_id=ctx.turn_id))

    def _schedule_nearline_memory(self, ctx: TurnContext) -> None:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._schedule_nearline_memory(ctx)
        if ctx.session is None:
            return
        service = getattr(self, "nearline_memory", None)
        if service is None or not getattr(service, "enabled", False):
            return
        if not self._nearline_turn_completed_successfully(ctx):
            return
        actor_id = ctx.runtime_context.actor_id if ctx.runtime_context is not None and getattr(ctx.runtime_context, "actor_id", None) else "user"
        self._schedule_background(service.process_turn(session=ctx.session, channel=ctx.msg.channel, chat_id=ctx.msg.chat_id, actor_id=actor_id, turn_id=ctx.turn_id))

    @staticmethod
    def _nearline_turn_completed_successfully(ctx: TurnContext) -> bool:
        if ctx.stop_reason in {"ask_user", "error", "tool_error", "max_iterations", "empty_final_response"}:
            return False
        return bool((ctx.final_content or "").strip())

    async def _state_respond(self, ctx: TurnContext) -> str:
        """Compatibility wrapper; normal flow is driven by TurnOrchestrator."""
        return await self._turn_pipeline.state_respond(ctx)

    @property
    def _message_dispatcher_ref(self) -> MessageDispatcher:
        return self._get_message_dispatcher()

    def _sanitize_persisted_blocks(self, content: list[dict[str, Any]], *,
                                    should_truncate_text: bool = False, drop_runtime: bool = False) -> list[dict[str, Any]]:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._sanitize_persisted_blocks(content, should_truncate_text=should_truncate_text, drop_runtime=drop_runtime)
        return AgentLoop._turn_persist_manager(self).sanitize_persisted_blocks(content, should_truncate_text=should_truncate_text, drop_runtime=drop_runtime)

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._save_turn(session, messages, skip)
        AgentLoop._turn_persist_manager(self).save_turn(session, messages, skip)

    def _persist_subagent_followup(self, session: Session, msg: InboundMessage) -> bool:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._persist_subagent_followup(session, msg)
        return AgentLoop._turn_persist_manager(self).persist_subagent_followup(session, msg)

    def _set_runtime_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        """Persist the latest in-flight turn state into session metadata."""
        AgentLoop._turn_persist_manager(self).set_checkpoint(session, payload)

    def _mark_pending_user_turn(self, session: Session) -> None:
        AgentLoop._turn_persist_manager(self).mark_pending_user_turn(session)

    def _clear_pending_user_turn(self, session: Session) -> None:
        AgentLoop._turn_persist_manager(self).clear_pending_user_turn(session)
        self._state_holder.drop(session.key)
        # Reset flat attributes to prevent stale reads (backward compat)
        self._last_runtime_context = None
        self._last_continuity_session_key = None
        self._last_context_assembly = {}
        self._last_governance_audit = {}
        self._last_action_continuity_audit = {}
        self._last_cognitive_scan = {}
        self._last_world_attention_write = {}

    def _clear_runtime_checkpoint(self, session: Session) -> None:
        AgentLoop._turn_persist_manager(self).clear_checkpoint(session)

    @staticmethod
    def _checkpoint_message_key(message: dict[str, Any]) -> tuple[Any, ...]:
        return TurnPersistManager.checkpoint_message_key(message)

    def _restore_runtime_checkpoint(self, session: Session) -> bool:
        """Materialize an unfinished turn into session history before a new request."""
        return AgentLoop._turn_persist_manager(self).restore_checkpoint(session)

    def _restore_pending_user_turn(self, session: Session) -> bool:
        """Close a turn that only persisted the user message before crashing."""
        return AgentLoop._turn_persist_manager(self).restore_pending_user_turn(session)

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        media: list[str] | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel, sender_id="user", chat_id=chat_id,
            content=content, media=media or [],
        )
        return await self._process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
        )
