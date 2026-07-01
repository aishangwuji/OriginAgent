"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import time
from contextlib import AsyncExitStack, nullcontext, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from OriginAgent.agent import model_presets as preset_helpers
from OriginAgent.agent.agent_runtime_context import (
    build_bus_progress_callback,
    build_retry_wait_callback,
    runtime_chat_id,
    set_tool_context as set_tools_runtime_context,
    snapshot_for_trigger,
)
from OriginAgent.agent.agent_tool_setup import (
    build_domain_tool_context_extras,
    build_tool_context,
    register_default_tools,
    register_domain_tools,
    register_plugin_tools,
    should_register_exec,
)
from OriginAgent.agent.action_summary import normalize_action_summary
from OriginAgent.agent.local_awareness import LocalAwarenessBackend, normalize_local_awareness_summary
from OriginAgent.agent.message_metadata import build_origin_metadata
from OriginAgent.agent.active_intents import ActiveIntentConfig, ActiveIntentService
from OriginAgent.agent.agent_cognitive_runtime import AgentCognitiveRuntime, CognitiveRuntimeDeps
from OriginAgent.agent.agent_loop_components import build_loop_components
from OriginAgent.agent.action_planning import UnifiedActionPlanner
from OriginAgent.agent.agent_turn_pipeline import (
    AgentTurnPipeline,
    StateTraceEntry,
    TURN_PIPELINE_TRANSITIONS,
    TurnContext,
    TurnEvent,
    TurnPipelineDeps,
    TurnState,
)
from OriginAgent.agent.services import AgentServiceContainer
from OriginAgent.agent.turn_orchestrator import TurnOrchestrator, TurnOrchestratorDeps
from OriginAgent.agent.error_classifier import ClassifiedError, ErrorKind, user_facing_message
from OriginAgent.agent.message_dispatcher import MessageDispatcher, MessageDispatcherDeps
from OriginAgent.agent.system_turn_handler import (
    SystemTurnHandler,
    SystemTurnHandlerDeps,
    SystemTurnLoopContext,
)
from OriginAgent.domain_packs.robot.runtime.robot_actions import TypedRobotAction
from OriginAgent.agent.agent_turn_persist import TurnPersistManager
from OriginAgent.agent.autocompact import AutoCompact
from OriginAgent.agent.auxiliary_llm import AuxiliaryLLMRouter
from OriginAgent.agent.background_review import BackgroundReviewService
from OriginAgent.agent.cognitive_audit import JsonlCognitiveAuditLedger
from OriginAgent.agent.cognitive_events import CognitiveDecision, CognitiveEvent
from OriginAgent.agent.cognitive_scheduler import CognitiveScheduler, CognitiveSchedulerConfig
from OriginAgent.agent.cognitive_loop import CognitiveLoop, CognitiveLoopConfig
from OriginAgent.agent.context import ContextBuilder
from OriginAgent.agent.action_safety import ActionDecision
from OriginAgent.agent.curator import CuratorService
from OriginAgent.agent.domain_packs import DomainPackManager
from OriginAgent.agent.hook import AgentHook, CompositeHook
from OriginAgent.agent.identity import ActorResolver, RuntimeContext
from OriginAgent.agent.introspection.service import RuntimeIntrospectionService
from OriginAgent.agent.memory import Consolidator, Dream, dream_feature_flags
from OriginAgent.agent.memory import session_summary_text
from OriginAgent.agent.memory_governance import MemoryGovernance
from OriginAgent.agent.meta_cognition_coordinator import MetaCognitionCoordinator
from OriginAgent.agent.meta_cognition_models import MetaTrigger
from OriginAgent.agent.meta_cognition_triggers import (
    bridge_runtime_event_to_trigger,
    build_task_completion_trigger,
    build_tool_failure_trigger,
    build_user_correction_trigger,
    latest_assistant_message,
)
from OriginAgent.memory.rolling import RollingEpisodeCompaction
from OriginAgent.agent.roaming_prewarm import RoamingPrewarmService
from OriginAgent.agent.progress_hook import AgentProgressHook
from OriginAgent.agent.runner import _MAX_INJECTIONS_PER_TURN, AgentRunner, AgentRunSpec
from OriginAgent.agent.self_model import SelfModelService
from OriginAgent.agent.reminders import ReminderStore
from OriginAgent.agent.session_state import SessionStateHolder, SessionScopedState
from OriginAgent.agent.working_memory import WorkingMemoryManager
from OriginAgent.agent.subagent import SubagentManager
from OriginAgent.agent.world_state import WorldStateManager
from OriginAgent.memory.pipeline import NearlineMemoryPipeline
from OriginAgent.agent.tools.ask import (
    ask_user_options_from_messages,
    ask_user_outbound,
    ask_user_tool_result_messages,
    pending_ask_user_id,
)
from OriginAgent.agent.tools.audit import JsonlToolAuditSink, ToolAuditConfig
from OriginAgent.agent.audit import AuditLogger
from OriginAgent.agent.confirmation import ConfirmationManager, PendingConfirmationStore, classify_confirmation_reply
from OriginAgent.agent.tools.file_state import FileStateStore, bind_file_states, reset_file_states
from OriginAgent.agent.tools.message import MessageTool
from OriginAgent.agent.tools.registry import ToolRegistry
from OriginAgent.agent.tools.self import MyTool
from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.command import CommandContext, CommandRouter, register_builtin_commands
from OriginAgent.config.schema import AgentDefaults
from OriginAgent.providers.base import LLMProvider
from OriginAgent.providers.transcription import (
    GroqTranscriptionProvider,
    OpenAITranscriptionProvider,
    VolcengineTranscriptionProvider,
)
from OriginAgent.providers.factory import ProviderSnapshot
from OriginAgent.security.capabilities import CapabilitySnapshot
from OriginAgent.security.grants import CapabilityGrantStore, issue_tool_approval_grant
from OriginAgent.session.cold_archive import SessionColdArchiveStore
from OriginAgent.session.goal_state import goal_state_raw, goal_state_ws_blob, parse_goal_state, runner_wall_llm_timeout_s
from OriginAgent.session.manager import Session, SessionManager
from OriginAgent.session.search_index import SessionSearchIndexService
from OriginAgent.utils.document import extract_documents
from OriginAgent.utils.image_generation_intent import image_generation_prompt
from OriginAgent.utils.webui_titles import mark_webui_session, maybe_generate_webui_title_after_turn
from OriginAgent.utils.webui_transcript import append_transcript_object, delete_webui_transcript

if TYPE_CHECKING:
    from OriginAgent.config.schema import (
        AuxiliaryConfig,
        ChannelsConfig,
        Config,
        DomainPacksConfig,
        ExecToolConfig,
        BackgroundReviewConfig,
        CuratorConfig,
        EvolutionConfig,
        ModelPresetConfig,
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
        self._local_awareness_backend = LocalAwarenessBackend(
            tts_config=dict((transcription_provider_config or {}).get("tts_config") or {}),
        )
        self._transcription_provider = self._build_transcription_provider(transcription_provider_config)
        self._last_local_awareness_summary: dict[str, Any] = normalize_local_awareness_summary(
            self.tools_config.local_awareness,
            backend=self._local_awareness_backend,
        )
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
        self._install_meta_cognition_observer()

        # ── BDI Deliberation Engine ────────────────────────────────────────
        self._desire_store: DesireStore | None = None
        self._bdi_engine: DeliberationEngine | None = None

        gw = getattr(effective_config, "gateway", None) if effective_config else None
        bdi_config: "BDIConfig | None" = getattr(gw, "bdi", None) if gw is not None else None

        if bdi_config and bdi_config.enabled:
            from OriginAgent.bdi import DesireStore, DeliberationEngine

            self._desire_store = DesireStore(self.workspace)

            async def _on_bdi_intention(intent: "DeliberationIntention") -> None:
                """Handle an intention formed by the BDI engine."""
                logger.info(
                    "BDI: executing intention — desire={} action={} scope={}",
                    intent.desire_id, intent.action, intent.scope,
                )
                if intent.action == "send_message":
                    from OriginAgent.bus.events import OutboundMessage

                    channel = intent.scope if intent.scope != "system" else "cli"
                    msg = OutboundMessage(
                        channel=channel,
                        content=intent.payload.get("text", ""),
                        chat_id="",
                        session_key="bdi:deliberation",
                    )
                    ok = await self.bus.publish_outbound(msg)
                    if not ok:
                        logger.error(
                            "BDI: Failed to publish intention message for desire={}",
                            intent.desire_id,
                        )

            self._bdi_engine = DeliberationEngine(
                workspace=self.workspace,
                store=self._desire_store,
                provider=self.provider,
                model=bdi_config.model_override or self.model,
                enabled=bdi_config.enabled,
                interval_s=bdi_config.interval_s,
                max_desires_per_cycle=bdi_config.max_desires_per_cycle,
                auto_create_from_foresight=bdi_config.auto_create_from_foresight,
                on_intention=_on_bdi_intention,
            )

            # ── InnerMonologueEngine (CS-004) ───────────────────────
            self._inner_monologue_engine: Any = None
            _ime_enabled = getattr(
                getattr(self, "_meta_cognition_config", None),
                "inner_monologue_enabled",
                True,
            )
            if _ime_enabled:
                from OriginAgent.agent.inner_monologue_engine import InnerMonologueEngine
                self._inner_monologue_engine = InnerMonologueEngine(
                    workspace=self.workspace,
                    deliberation_engine=self._bdi_engine,
                    substrate=getattr(self, "_thought_substrate", None),
                    desire_store=self._desire_store,
                    enabled=_ime_enabled,
                )
                self._bdi_engine.set_on_cycle_complete(
                    self._inner_monologue_engine.on_bdi_cycle
                )

            logger.info("BDI: DeliberationEngine initialized")

    def _build_transcription_provider(self, config: dict[str, Any] | None = None) -> Any | None:
        config = dict(config or {})
        provider_name = str(config.get("provider") or "groq").strip()
        provider_key = str(config.get("api_key") or "").strip()
        provider_base = str(config.get("api_base") or "").strip()
        language = config.get("language")
        resource_id = config.get("resource_id")
        user_id = config.get("user_id")
        if not provider_key:
            return None
        try:
            if provider_name == "openai":
                return OpenAITranscriptionProvider(api_key=provider_key, api_base=provider_base or None, language=language or None)
            if provider_name == "volcengine":
                return VolcengineTranscriptionProvider(
                    api_key=provider_key,
                    api_base=provider_base or None,
                    language=language or None,
                    resource_id=resource_id or None,
                    user_id=user_id or None,
                )
            return GroqTranscriptionProvider(api_key=provider_key, api_base=provider_base or None, language=language or None)
        except Exception:
            return None

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
        from OriginAgent.agent.loop_options import LoopOptions, ProviderOptions

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
        self._last_runtime_context = runtime_context
        self._last_continuity_session_key = session_key
        state = self._state_holder.get(session_key)
        state.last_runtime_context = runtime_context
        state.last_continuity_session_key = session_key

    def _record_continuity_session_key(self, session_key: str) -> None:
        self._last_continuity_session_key = session_key
        self._state_holder.get(session_key).last_continuity_session_key = session_key

    def _record_context_assembly(self, payload: dict[str, Any]) -> None:
        self._last_context_assembly = dict(payload)
        state_key = self._resolve_state_key()
        self._state_holder.get(state_key).last_context_assembly = dict(payload)

    def _record_recovered_continuity_checkpoint(
        self,
        checkpoint: dict[str, Any] | None,
    ) -> None:
        self._last_recovered_continuity_checkpoint = dict(checkpoint or {})
        state_key = self._resolve_state_key()
        self._state_holder.get(state_key).last_recovered_continuity_checkpoint = dict(checkpoint or {})

    def _record_governance_audit(self, audit: dict[str, Any]) -> None:
        self._last_governance_audit = dict(audit)
        self.context._last_governance_audit = dict(audit)
        state_key = self._resolve_state_key()
        self._state_holder.get(state_key).last_governance_audit = dict(audit)

    def _record_action_continuity_audit(self, audit: dict[str, Any]) -> None:
        self._last_action_continuity_audit = dict(audit)
        self._cached_action_summary = normalize_action_summary(self._last_action_continuity_audit)
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
        self._last_cognitive_scan = dict(payload)
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
        """Keep subagent runtime limits aligned with mutable loop settings."""
        self.subagents.max_iterations = self.max_iterations

    def _archive_session_file_cap(
        self,
        messages: list[dict[str, Any]],
        *,
        session_key: str,
        reason: str,
    ) -> None:
        if self.session_cold_archive is not None:
            self.session_cold_archive.archive(session_key, messages, reason=reason)
        self.context.memory.raw_archive(messages)

    def _apply_provider_snapshot(self, snapshot: ProviderSnapshot) -> None:
        """Swap model/provider for future turns without disturbing an active one."""
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
        self.runner.provider = provider
        self.subagents.set_provider(provider, model)
        self.auxiliary_router.set_primary(provider, model)
        self.background_review.set_provider(provider, model)
        self.consolidator.set_provider(provider, model, context_window_tokens)
        self.dream.set_provider(provider, model)
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

    def _refresh_provider_snapshot(self) -> None:
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
        """Switch the active runtime model preset for subsequent turns."""
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
        """Connect to configured MCP servers (one-time, lazy)."""
        if not self._mcp_servers:
            return
        while True:
            ready: asyncio.Future[bool] | None = None
            runtime_task: asyncio.Task[None] | None = None
            async with self._mcp_lifecycle_lock:
                if self._mcp_state == "connected":
                    return
                if self._mcp_state == "connecting":
                    ready = self._mcp_ready
                elif self._mcp_state == "closing":
                    runtime_task = self._mcp_runtime_task
                else:
                    ready = asyncio.get_running_loop().create_future()
                    self._mcp_state = "connecting"
                    self._mcp_connected = False
                    self._mcp_connecting = True
                    self._mcp_startup_error = None
                    self._mcp_ready = ready
                    self._mcp_shutdown_event = asyncio.Event()
                    self._mcp_runtime_task = asyncio.create_task(
                        self._run_mcp_runtime(ready, self._mcp_shutdown_event),
                        name="originagent-mcp-runtime",
                    )
            if runtime_task is not None:
                with suppress(Exception):
                    await asyncio.shield(runtime_task)
                continue
            if ready is not None:
                with suppress(Exception):
                    await asyncio.shield(ready)
                return
            return

    async def _run_mcp_runtime(
        self,
        ready: asyncio.Future[bool],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Own the MCP connection lifecycle inside a single task."""
        from OriginAgent.agent.tools.mcp import connect_mcp_servers

        stacks: dict[str, AsyncExitStack] = {}
        clear_snapshot_on_exit = False
        try:
            stacks = await connect_mcp_servers(
                self._mcp_servers,
                self.tools,
                snapshot_out=self._mcp_snapshot,
            )
            if not stacks:
                logger.warning("No MCP servers connected successfully (will retry next message)")
                async with self._mcp_lifecycle_lock:
                    self._mcp_stacks = {}
                    self._mcp_connected = False
                    self._mcp_connecting = False
                    self._mcp_state = "disconnected"
                    if not ready.done():
                        ready.set_result(False)
                return

            async with self._mcp_lifecycle_lock:
                self._mcp_stacks = stacks
                self._mcp_connected = True
                self._mcp_connecting = False
                self._mcp_state = "connected"
                self._mcp_startup_error = None
                if not ready.done():
                    ready.set_result(True)

            await shutdown_event.wait()
            clear_snapshot_on_exit = True
        except asyncio.CancelledError:
            clear_snapshot_on_exit = True
            logger.warning("MCP runtime cancelled (will retry next message)")
            async with self._mcp_lifecycle_lock:
                self._mcp_stacks.clear()
                self._mcp_snapshot.clear()
                self._mcp_connected = False
                self._mcp_connecting = False
                self._mcp_state = "disconnected"
                if not ready.done():
                    ready.set_result(False)
            raise
        except BaseException as e:
            clear_snapshot_on_exit = True
            logger.warning("Failed to connect MCP servers (will retry next message): {}", e)
            async with self._mcp_lifecycle_lock:
                self._mcp_stacks.clear()
                self._mcp_snapshot.clear()
                self._mcp_connected = False
                self._mcp_connecting = False
                self._mcp_state = "disconnected"
                self._mcp_startup_error = e
                if not ready.done():
                    ready.set_result(False)
            return
        finally:
            for name, stack in stacks.items():
                try:
                    await stack.aclose()
                except (RuntimeError, BaseExceptionGroup):
                    logger.debug("MCP server '{}' cleanup error (can be ignored)", name)
            async with self._mcp_lifecycle_lock:
                if self._mcp_runtime_task is asyncio.current_task():
                    self._mcp_stacks.clear()
                    if clear_snapshot_on_exit:
                        self._mcp_snapshot.clear()
                    self._mcp_connected = False
                    self._mcp_connecting = False
                    self._mcp_state = "disconnected"
                    self._mcp_runtime_task = None
                    self._mcp_ready = None
                    self._mcp_shutdown_event = None

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
        """Update context for all tools that need routing info."""
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
        """Format tool calls as concise hints with smart abbreviation."""
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

    def _persist_user_message_early(
        self,
        msg: InboundMessage,
        session: Session,
        pending_ask_id: str | None,
        **kwargs: Any,
    ) -> bool:
        """Persist the triggering user message before the turn starts.

        Returns True if the message was persisted.
        """
        return AgentLoop._turn_persist_manager(self).persist_user_message_early(
            msg,
            session,
            pending_ask_id,
            **kwargs,
        )

    def _build_initial_messages(
        self,
        msg: InboundMessage,
        session: Session,
        history: list[dict[str, Any]],
        pending_ask_id: str | None,
        pending_summary: str | None,
        internal_event: tuple[str, str] | None = None,
        recovered_continuity_block: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the initial message list for the LLM turn."""
        self_model_payload = self._build_prompt_self_model()
        if pending_ask_id:
            system_prompt = self.context.build_system_prompt(
                channel=msg.channel,
                session_summary=pending_summary,
                self_model_payload=self_model_payload,
            )
            messages = ask_user_tool_result_messages(
                system_prompt,
                history,
                pending_ask_id,
                image_generation_prompt(msg.content, msg.metadata),
            )
            if self.context._context_config.enable_phase1_continuity:
                assembled = self.context.assemble_user_content(
                    current_message=None,
                    media=None,
                    channel=msg.channel,
                    chat_id=self._runtime_chat_id(msg),
                    sender_id=msg.sender_id,
                    session_summary=pending_summary,
                    session_metadata=session.metadata,
                    internal_event=None,
                    runtime_context=self._state_holder.get(session.key).last_runtime_context,
                    session_key=session.key,
                    recovered_continuity_block=recovered_continuity_block,
                    include_current_message=False,
                )
                self._state_holder.get(session.key).last_context_assembly = dict(assembled.audit)
                self._last_context_assembly = dict(assembled.audit)  # compat
                messages.append({
                    "role": "user",
                    "content": assembled.blocks,
                })
                return self.context._apply_prompt_budget(
                    messages,
                    context_window_tokens=self.context_window_tokens,
                    max_completion_tokens=getattr(self.provider.generation, "max_tokens", 4096),
                )
            messages.append({
                "role": "user",
                "content": [
                    self.context.build_runtime_context_block(
                        msg.channel,
                        self._runtime_chat_id(msg),
                        self.context.timezone,
                        sender_id=msg.sender_id,
                        session_metadata=session.metadata,
                    ),
                    *([recovered_continuity_block] if recovered_continuity_block is not None else []),
                    *self.context.build_reference_context_blocks(
                        session_summary=pending_summary,
                        session_key=session.key,
                        runtime_context=self._state_holder.get(session.key).last_runtime_context,
                        current_message=msg.content,
                    ),
                ],
            })
            self._state_holder.get(session.key).last_context_assembly = {
                "enabled": False,
                "session_key": session.key,
                "reason": "phase1_continuity_disabled",
                "block_kinds": [
                    self.context.RUNTIME_CONTEXT_KIND,
                    *[
                        block.get("_meta", {}).get("kind")
                        for block in self.context.build_reference_context_blocks(
                            session_summary=pending_summary,
                            session_key=session.key,
                            runtime_context=self._state_holder.get(session.key).last_runtime_context,
                            current_message=msg.content,
                        )
                    ],
                ],
            }
            self._last_context_assembly = self._state_holder.get(session.key).last_context_assembly  # compat
            return self.context._apply_prompt_budget(
                messages,
                context_window_tokens=self.context_window_tokens,
                max_completion_tokens=getattr(self.provider.generation, "max_tokens", 4096),
            )
        built = self.context.build_messages(
            history=history,
            current_message=image_generation_prompt(msg.content, msg.metadata),
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=self._runtime_chat_id(msg),
            sender_id=msg.sender_id,
            session_summary=pending_summary,
            session_metadata=session.metadata,
            internal_event=internal_event,
            self_model_payload=self_model_payload,
            runtime_context=self._last_runtime_context,
            session_key=session.key,
            recovered_continuity_block=recovered_continuity_block,
            context_window_tokens=self.context_window_tokens,
            max_completion_tokens=getattr(self.provider.generation, "max_tokens", 4096),
        )
        state = self._state_holder.get(session.key)
        state.last_context_assembly = dict(getattr(self.context, "_last_context_assembly_audit", {}) or {})
        if not state.last_context_assembly:
            state.last_context_assembly = self._snapshot_context_assembly_from_messages(
                built,
                session_key=session.key,
                runtime_context=state.last_runtime_context,
            )
        self._last_context_assembly = dict(state.last_context_assembly)  # compat
        return built

    def _build_prompt_self_model(self) -> dict[str, Any]:
        snapshot = self.introspection.runtime_context_snapshot()
        return SelfModelService(
            self.workspace,
            audit_mode=self._tool_audit_config.mode,
            runtime_profile=self._runtime_profile,
            domain_pack_manager=self.domain_packs,
            skills_loader=self.context.skills,
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
        confirmation = self._confirmation_manager.latest_pending_tool_approval(session_key)
        if confirmation is None:
            return None, False
        classification = classify_confirmation_reply(reply)
        if classification not in {"confirmed", "rejected"}:
            return None, False
        result = self._confirmation_manager.resolve_user_reply(
            confirmation.confirmation_id,
            reply,
        )
        tool_name = confirmation.metadata.get("tool_name") or confirmation.action or "tool"
        if result.decision == "confirmed":
            grant = issue_tool_approval_grant(
                confirmation,
                self._grant_store,
                approved_by=actor_id,
            )
            return (
                (
                    "tool_approval",
                    (
                        f"Tool approval confirmed for {tool_name}. "
                        f"Short-lived grant {grant.grant_id} is active for this session. "
                        "Continue the pending task using the newly approved capability."
                    ),
                ),
                True,
            )
        if result.decision == "rejected":
            return (
                (
                    "tool_approval",
                    f"Tool approval was rejected for {tool_name}. Do not use that capability unless the user asks again.",
                ),
                True,
            )
        return None, False

    def _is_webui_message(self, msg: InboundMessage) -> bool:
        return msg.channel == "websocket" and msg.metadata.get("webui") is True

    def _append_webui_command_transcript(
        self,
        msg: InboundMessage,
        content: str,
    ) -> None:
        """Persist a command response to the WebUI transcript."""
        if not self._is_webui_message(msg):
            return
        try:
            append_transcript_object(
                f"websocket:{msg.chat_id}",
                {"event": "message", "chat_id": msg.chat_id, "text": content},
            )
        except (TypeError, ValueError, OSError) as e:
            logger.warning("webui command transcript append failed: {}", e)

    def _write_continuity_runtime_identity(
        self,
        session: Session,
        runtime_context: RuntimeContext | None,
    ) -> None:
        if runtime_context is None:
            return
        session.metadata[CONTINUITY_RUNTIME_IDENTITY_KEY] = {
            "user_id": runtime_context.user_id,
            "device_id": runtime_context.device_id,
            "session_id": runtime_context.session_id,
            "scope": runtime_context.default_scope,
            "updated_at": _utcnow_iso(),
        }

    def _persist_shortcut_command_turn(
        self,
        msg: InboundMessage,
        session_key: str,
        result: OutboundMessage,
    ) -> None:
        """Persist slash-command turns that bypass the normal RUN/SAVE states."""
        raw = msg.content.strip()
        if raw.lower() == "/new":
            if self._is_webui_message(msg):
                delete_webui_transcript(session_key)
            return
        session = self.sessions.get_or_create(session_key)
        mark_webui_session(session, msg.metadata)
        self._persist_user_message_early(
            msg,
            session,
            pending_ask_id=None,
            _command=True,
        )
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
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="",
                        metadata={
                            **dict(msg.metadata or {}),
                            "_turn_end": True,
                            "goal_state": goal_state_ws_blob(
                                self.sessions.get_or_create(key).metadata
                            ),
                        },
                    )
                )
        else:
            logger.warning("Command '{}' matched but dispatch returned None", raw)

    async def _cancel_active_tasks(self, key: str) -> int:
        """Cancel and await all active tasks and subagents for *key*.

        Returns the total number of cancelled tasks + subagents.
        """
        tasks = self._active_tasks.pop(key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await t
        sub_cancelled = await self.subagents.cancel_by_session(key)
        return cancelled + sub_cancelled

    def _effective_session_key(self, msg: InboundMessage) -> str:
        """Return the session key used for task routing and mid-turn injections."""
        if self._unified_session and not msg.session_key_override:
            return UNIFIED_SESSION_KEY
        return msg.session_key

    def _replay_token_budget(self) -> int:
        """Derive a token budget for session history replay from the context window."""
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
        """Run the agent iteration loop.

        *on_stream*: called with each content delta during streaming.
        *on_stream_end(resuming)*: called when a streaming session finishes.
        ``resuming=True`` means tool calls follow (spinner should restart);
        ``resuming=False`` means this is the final response.

        Returns (final_content, tools_used, messages, stop_reason, had_injections).
        """
        self._sync_subagent_runtime_limits()
        self._capability_snapshot = capability_snapshot or self._snapshot_for_trigger(trigger)
        if hasattr(self.tools, "set_capability_snapshot"):
            self.tools.set_capability_snapshot(self._capability_snapshot)

        loop_hook = AgentProgressHook(
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
            metadata=metadata,
            session_key=session_key,
            tool_hint_max_length=self.tool_hint_max_length,
            set_tool_context=self._set_tool_context,
            on_iteration=lambda iteration: setattr(self, "_current_iteration", iteration),
            actor_id=actor_id,
            trigger=trigger,
            capability_snapshot=self._capability_snapshot,
            sensitive_tool_log_names=_SENSITIVE_TOOL_LOG_FALLBACK_NAMES,
            sensitive_tool_log_prefixes=_SENSITIVE_TOOL_LOG_FALLBACK_PREFIXES,
        )
        hook: AgentHook = (
            CompositeHook([loop_hook] + self._extra_hooks) if self._extra_hooks else loop_hook
        )

        async def _checkpoint(payload: dict[str, Any]) -> None:
            if session is None:
                return
            self._set_runtime_checkpoint(session, payload)

        async def _drain_pending(*, limit: int = _MAX_INJECTIONS_PER_TURN) -> list[dict[str, Any]]:
            """Drain follow-up messages from the pending queue.

            When no messages are immediately available but sub-agents
            spawned in this dispatch are still running, blocks until at
            least one result arrives (or timeout).  This keeps the runner
            loop alive so subsequent sub-agent completions are consumed
            in-order rather than dispatched separately.
            """
            if pending_queue is None:
                return []

            def _to_user_message(pending_msg: InboundMessage) -> dict[str, Any]:
                content = pending_msg.content
                media = pending_msg.media if pending_msg.media else None
                if media:
                    content, media = extract_documents(content, media)
                    media = media or None
                runtime_block = self.context.build_runtime_context_block(
                    pending_msg.channel,
                    self._runtime_chat_id(pending_msg),
                    self.context.timezone,
                )
                if (
                    pending_msg.sender_id == "subagent"
                    or pending_msg.metadata.get("injected_event") == "subagent_result"
                ):
                    merged: list[dict[str, Any]] = [
                        runtime_block,
                        self.context.build_internal_event_block("subagent_result", content),
                    ]
                elif pending_msg.metadata.get("injected_event") == "active_intent":
                    merged = [
                        runtime_block,
                        self.context.build_internal_event_block("active_intent", content),
                    ]
                else:
                    merged = [
                        runtime_block,
                        *self.context._build_user_content(content, media),
                    ]
                return {"role": "user", "content": merged}

            items: list[dict[str, Any]] = []
            while len(items) < limit:
                try:
                    items.append(_to_user_message(pending_queue.get_nowait()))
                except asyncio.QueueEmpty:
                    break

            # Block if nothing drained but sub-agents spawned in this dispatch
            # are still running.  Keeps the runner loop alive so subsequent
            # completions are injected in-order rather than dispatched separately.
            if (not items
                    and session is not None
                    and self.subagents.get_running_count_by_session(session.key) > 0):
                try:
                    msg = await asyncio.wait_for(pending_queue.get(), timeout=300)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Timeout waiting for sub-agent completion in session {}",
                        session.key,
                    )
                    return items
                items.append(_to_user_message(msg))
                while len(items) < limit:
                    try:
                        items.append(_to_user_message(pending_queue.get_nowait()))
                    except asyncio.QueueEmpty:
                        break

            return items

        active_session_key = session.key if session else session_key
        file_state_token = bind_file_states(self._file_state_store.for_session(active_session_key))
        try:
            result = await self.runner.run(AgentRunSpec(
                initial_messages=initial_messages,
                tools=self.tools,
                model=self.model,
                max_iterations=self.max_iterations,
                max_tool_result_chars=self.max_tool_result_chars,
                hook=hook,
                error_message=user_facing_message(
                    ClassifiedError(kind=ErrorKind.INTERNAL, technical_detail="agent loop error", retryable=False)
                ),
                concurrent_tools=True,
                tool_concurrency_limit=self._tool_concurrency_limit,
                workspace=self.workspace,
                session_key=session.key if session else None,
                context_window_tokens=self.context_window_tokens,
                context_block_limit=self.context_block_limit,
                provider_retry_mode=self.provider_retry_mode,
                progress_callback=on_progress,
                stream_progress_deltas=on_stream is not None,
                retry_wait_callback=on_retry_wait,
                checkpoint_callback=_checkpoint,
                injection_callback=_drain_pending,
                llm_timeout_s=runner_wall_llm_timeout_s(
                    self.sessions,
                    session_key,
                    metadata=session.metadata if session is not None else None,
                ),
            ))
        finally:
            reset_file_states(file_state_token)
        self._last_usage = result.usage
        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            # Push final content through stream so streaming channels (e.g. Feishu)
            # update the card instead of leaving it empty.
            if on_stream and on_stream_end:
                await on_stream(result.final_content or "")
                await on_stream_end(resuming=False)
        elif result.stop_reason == "error":
            logger.error("LLM returned error: {}", (result.final_content or "")[:200])
        return result.final_content, result.tools_used, result.messages, result.stop_reason, result.had_injections

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        self._schedule_session_search_refresh(force=self.session_search_index.rebuild_on_start)
        self._start_active_intent_loop()
        if self._bdi_engine:
            await self._bdi_engine.start()
        logger.info("Agent loop started")
        await self._get_message_dispatcher().run_forever()

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Compatibility shell; delegates to MessageDispatcher.dispatch_message."""
        return await self._get_message_dispatcher().dispatch_message(msg)

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        if self._active_intent_task is not None:
            self._active_intent_task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.shield(self._active_intent_task)
            self._active_intent_task = None
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        runtime_task: asyncio.Task[None] | None = None
        async with self._mcp_lifecycle_lock:
            runtime_task = self._mcp_runtime_task
            shutdown_event = self._mcp_shutdown_event
            if runtime_task is None:
                self._mcp_connected = False
                self._mcp_connecting = False
                self._mcp_state = "disconnected"
                self._mcp_stacks.clear()
                self._mcp_snapshot.clear()
                return
            self._mcp_connected = False
            self._mcp_connecting = False
            self._mcp_state = "closing"
            if shutdown_event is not None:
                shutdown_event.set()
        with suppress(Exception):
            await asyncio.shield(runtime_task)

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _start_active_intent_loop(self) -> None:
        """Compatibility shell delegating active-intent startup to AgentCognitiveRuntime."""
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
        channel, chat_id = (
            session_key.split(":", 1)
            if ":" in session_key
            else ("cli", session_key)
        )
        msg = InboundMessage(
            channel="system",
            sender_id="agent_cognitive",
            chat_id=session_key,
            content="",
            metadata=build_origin_metadata(
                {
                    "injected_event": "cognitive_event",
                    "user_id": "agent_cognitive",
                    "scope": "session",
                },
                origin_kind="cognitive_event",
                is_inferred=True,
                confidence=0.6,
                trigger_reason="runtime_scan",
            ),
            session_key_override=session_key,
        )
        return self._resolve_runtime_context(
            msg,
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
        )

    def _collect_cognitive_candidates(self, session_key: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for candidate in self.active_intents.collect_candidates(session_key):
            items.append({
                "event": self._candidate_to_cognitive_event(session_key, {
                    "kind": "active_intent",
                    "candidate": candidate,
                    "cooldown_key": candidate.intent_id,
                    "message": self.active_intents.build_message(session_key, candidate),
                }),
                "message": self.active_intents.build_message(session_key, candidate),
                "cooldown_key": candidate.intent_id,
                "working_memory_attention": candidate.summary or candidate.content,
                "working_memory_question": (
                    "Should this pending item be confirmed now?"
                    if candidate.intent_type == "pending_confirmation_nudge"
                    else None
                ),
                "raw_candidate": candidate,
            })
        for record in self._reminder_store.list_due():
            if record.session_key != session_key:
                continue
            content = (
                "Scheduled reminder follow-up: a previously scheduled reminder is now due.\n"
                f"Reminder: {record.content}\n"
                "If helpful, continue from this due reminder and keep the follow-up bounded."
            )
            message = InboundMessage(
                channel="system",
                sender_id="agent_cognitive",
                chat_id=record.chat_id or session_key,
                content=content,
                session_key_override=session_key,
                metadata=build_origin_metadata(
                    {
                        "injected_event": "cognitive_event",
                        "_from_active": True,
                        "cognitive_event_type": "scheduled_reminder",
                        "cognitive_event_id": f"reminder:{record.reminder_id}",
                        "reminder_id": record.reminder_id,
                    },
                    origin_kind="cognitive_event",
                    is_inferred=True,
                    confidence=0.8,
                    trigger_reason="scheduled_reminder",
                ),
            )
            event = CognitiveEvent(
                event_id=f"reminder:{record.reminder_id}",
                session_key=session_key,
                event_type="scheduled_reminder",
                source_type="reminder_store",
                source_reference=record.reminder_id,
                summary=_trim_text(record.content, max_chars=160),
                priority="high",
                payload={
                    "due_at": record.due_at,
                    "channel": record.channel,
                    "chat_id": record.chat_id,
                },
            )
            items.append({
                "event": event,
                "message": message,
                "cooldown_key": event.event_id,
                "working_memory_attention": record.content,
                "working_memory_question": "Is this reminder still relevant and ready to act on?",
                "raw_candidate": record,
            })
        priority_order = {"high": 0, "medium": 1, "low": 2}
        items.sort(key=lambda item: (priority_order.get(item["event"].priority, 9), item["event"].created_at))
        return items

    def _candidate_to_cognitive_event(self, session_key: str, item: Any) -> CognitiveEvent:
        if isinstance(item, dict) and "event" in item and isinstance(item["event"], CognitiveEvent):
            return item["event"]
        candidate = item["candidate"] if isinstance(item, dict) and "candidate" in item else item
        priority = "medium"
        if getattr(candidate, "intent_type", "") in {"pending_confirmation_nudge", "goal_nudge"}:
            priority = "high"
        return CognitiveEvent(
            event_id=str(getattr(candidate, "intent_id", "")),
            session_key=session_key,
            event_type=str(getattr(candidate, "intent_type", "goal_nudge")),
            source_type=str(getattr(candidate, "source_type", "active_intent")),
            source_reference=str(getattr(candidate, "source_reference", "")),
            summary=_trim_text(getattr(candidate, "summary", "") or getattr(candidate, "content", ""), max_chars=160),
            priority=priority,
            payload={
                "content": str(getattr(candidate, "content", "")),
            },
        )

    def _write_cognitive_event_to_working_memory(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        event: CognitiveEvent,
    ) -> bool:
        written = False
        if event.summary:
            self.working_memory.append_attention_item(
                session,
                event.summary,
                identity=runtime_context.identity,
            )
            written = True
        if event.event_type in {"pending_confirmation_nudge", "scheduled_reminder"}:
            question = (
                "Should this pending confirmation be resolved now?"
                if event.event_type == "pending_confirmation_nudge"
                else "Is this due reminder still relevant and ready to act on?"
            )
            self.working_memory.append_pending_question(
                session,
                question,
                identity=runtime_context.identity,
            )
            written = True
        if written:
            self.sessions.save(session)
        return written

    def stop(self) -> None:
        """Stop the agent loop."""
        if self._bdi_engine:
            self._bdi_engine.stop()
        self._running = False
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
            self._last_world_attention_write = state.last_world_attention_write  # compat
        else:
            state = self._state_holder.get(session.key)
            state.last_world_attention_write = {
                "world_attention_total": 0,
                "world_kept": 0,
                "world_truncated": 0,
                "world_truncated_by_limit": False,
                "attention_merged_items": [item for item in (attention_items or []) if item],
            }
            self._last_world_attention_write = state.last_world_attention_write  # compat
        self.working_memory.upsert(
            session,
            identity=runtime_context.identity,
            pending_questions=pending_questions,
            attention_items=attention_items,
        )

    @staticmethod
    def _pending_confirmation_ref(confirmation: Any) -> dict[str, Any]:
        return {
            "confirmation_id": str(getattr(confirmation, "confirmation_id", "") or "").strip(),
            "scope": str(getattr(confirmation, "scope", "") or "").strip(),
            "status": str(getattr(confirmation, "status", "") or "").strip(),
            "risk": str(getattr(confirmation, "risk", "") or "").strip(),
            "updated_at": str(
                getattr(confirmation, "consumed_at", None)
                or getattr(confirmation, "created_at", None)
                or ""
            ).strip(),
        }

    def _collect_pending_confirmation_refs(self, session: Session) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        try:
            confirmations = self._confirmation_store.read_all()
        except Exception:
            return refs
        for confirmation in confirmations:
            scope = str(getattr(confirmation, "scope", "") or "").strip()
            metadata = getattr(confirmation, "metadata", {}) or {}
            session_ref = str(metadata.get("arc_session") or "").strip() if isinstance(metadata, dict) else ""
            if scope and session.key not in scope and session_ref != session.key:
                continue
            ref = self._pending_confirmation_ref(confirmation)
            if ref["confirmation_id"]:
                refs.append(ref)
        return refs[:8]

    def _save_continuity_checkpoint(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext | None,
    ) -> dict[str, Any]:
        working = self.working_memory.load(
            session,
            identity=runtime_context.identity if runtime_context is not None else None,
        )
        profile_ref = None
        try:
            profiles = self.nearline_memory.store.read_profiles(limit=1)
            if profiles:
                profile = profiles[-1]
                profile_ref = {
                    "profile_id": profile.profile_id,
                    "updated_at": profile.updated_at,
                }
        except Exception:
            profile_ref = None
        # Phase 5: simplified checkpoint — episode summaries, tone notes, and
        # key quotes are handled by the episode system, not the checkpoint.
        checkpoint = {
            "session_key": session.key,
            "current_goal": working.current_goal,
            "current_plan": list(working.current_plan or []),
            "open_loops": list(working.open_loops or []),
            "active_constraints": list(working.active_constraints or []),
            "pending_confirmation_refs": self._collect_pending_confirmation_refs(session),
            "updated_at": _utcnow_iso(),
        }
        session.metadata[CONTINUITY_CHECKPOINT_KEY] = checkpoint
        return checkpoint

    @staticmethod
    def _load_continuity_checkpoint(session: Session) -> dict[str, Any] | None:
        raw = session.metadata.get(CONTINUITY_CHECKPOINT_KEY)
        if not isinstance(raw, dict):
            return None
        return {
            "session_key": str(raw.get("session_key") or session.key),
            "current_goal": _trim_text(raw.get("current_goal"), max_chars=1000),
            "current_plan": [str(item).strip() for item in raw.get("current_plan", []) if str(item).strip()][:8],
            "open_loops": [str(item).strip() for item in raw.get("open_loops", []) if str(item).strip()][:8],
            "active_constraints": [
                str(item).strip() for item in raw.get("active_constraints", []) if str(item).strip()
            ][:8],
            "pending_confirmation_refs": [
                dict(item) for item in raw.get("pending_confirmation_refs", []) if isinstance(item, dict)
            ][:8],
            "updated_at": str(raw.get("updated_at") or "").strip(),
        }

    def _snapshot_context_assembly_from_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        session_key: str | None,
        runtime_context: RuntimeContext | None,
    ) -> dict[str, Any]:
        user_blocks: list[dict[str, Any]] = []
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, list):
                user_blocks = [block for block in content if isinstance(block, dict)]
                break
        return {
            "enabled": bool(self.context._context_config.enable_phase1_continuity),
            "session_key": session_key,
            "runtime_context": (
                {
                    "actor_id": runtime_context.actor_id,
                    "user_id": runtime_context.user_id,
                    "session_id": runtime_context.session_id,
                    "device_id": runtime_context.device_id,
                    "trigger": runtime_context.trigger,
                    "source": runtime_context.source,
                    "scope": runtime_context.default_scope,
                }
                if runtime_context is not None
                else {}
            ),
            "block_kinds": [
                block.get("_meta", {}).get("kind")
                for block in user_blocks
            ],
            "reference_sources": [
                block.get("_meta", {}).get("source")
                for block in user_blocks
                if block.get("_meta", {}).get("kind") == self.context.REFERENCE_CONTEXT_KIND
            ],
            "current_message_preview": next(
                (
                    str(block.get("text") or "")[:200]
                    for block in reversed(user_blocks)
                    if block.get("type") == "text" and not block.get("_meta")
                ),
                "",
            ),
        }

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
        result = self._meta_coordinator.record_trigger(trigger, turn_id=turn_id)
        if result is not None and result.accepted and trigger.trigger_type == "user_correction":
            self._maybe_apply_meta_fast_path(trigger)

    def _scan_meta_triggers_for_turn(self, ctx: TurnContext) -> None:
        runtime = getattr(self, "_meta_cognition_runtime", None)
        if runtime is None:
            return
        try:
            self._meta_coordinator.reset_fast_path()
            fusion = getattr(self, "_perception_fusion", None)
            if fusion is not None and fusion.enabled:
                _re = fusion.bridge_user_message(
                    session_key=ctx.session_key,
                    text=ctx.msg.content,
                )
                if _re is not None:
                    _t = bridge_runtime_event_to_trigger(_re)
                    if _t is not None:
                        self._record_meta_trigger(_t, turn_id=ctx.turn_id)
            else:
                trigger = build_user_correction_trigger(
                    session_key=ctx.session_key,
                    user_message=ctx.msg.content,
                    last_assistant_message=latest_assistant_message(ctx.all_messages),
                )
                if trigger is not None:
                    self._record_meta_trigger(trigger, turn_id=ctx.turn_id)
            self._meta_coordinator.runtime_reset_turn(ctx.turn_id)
        except Exception:
            logger.debug("Meta-cognition turn-end scan failed", exc_info=True)

    def _maybe_apply_meta_fast_path(self, trigger: MetaTrigger) -> None:
        session_key = str(trigger.session_key or "").strip()
        if not session_key or trigger.trigger_type != "user_correction":
            return
        sessions = getattr(self, "sessions", None)
        working_memory = getattr(self, "working_memory", None)
        runtime_context = self._state_holder.get(session_key).last_runtime_context
        if sessions is None or working_memory is None:
            return
        try:
            session = sessions.get_or_create(session_key)
            snapshot = working_memory.load(
                session,
                identity=getattr(runtime_context, "identity", None) if runtime_context is not None else None,
            )
            payload = trigger.payload if isinstance(trigger.payload, dict) else {}
            preview_source = payload.get("user_message_preview")
            preview = _trim_text(preview_source, max_chars=160)
            text = f"user_correction: {preview or 'correction recorded'}".strip()
            if text in list(getattr(snapshot, "attention_items", []) or []):
                self._meta_coordinator.add_fast_path_ref(trigger.source_reference)
                self._meta_coordinator.record_fast_path_decision("fast_path_duplicate_skipped")
                return
            working_memory.append_attention_item(
                session,
                text,
                identity=getattr(runtime_context, "identity", None) if runtime_context is not None else None,
            )
            self._meta_coordinator.add_fast_path_ref(trigger.source_reference)
            self._meta_coordinator.record_fast_path_decision("fast_path_working_memory_written")
        except Exception:
            logger.debug("Meta-cognition fast path failed", exc_info=True)

    def _schedule_meta_cognition_reflection(self, ctx: TurnContext) -> None:
        reflector = getattr(self, "_meta_cognition_reflector", None)
        runtime = getattr(self, "_meta_cognition_runtime", None)
        if reflector is None or runtime is None:
            return
        if ctx.session is None:
            return
        if ctx.stop_reason in {"ask_user", "error", "tool_error", "system", "subagent"}:
            return
        if ctx.msg.channel == "system" or ctx.msg.sender_id == "subagent":
            return
        if not (ctx.final_content or "").strip():
            return
        accepted = runtime.take_accepted_triggers_for_turn(ctx.turn_id)
        if not accepted:
            return
        snapshot = {
            "user_message": ctx.msg.content,
            "assistant_final_content": ctx.final_content or "",
            "previous_assistant_message": latest_assistant_message(ctx.session.messages[:-1]),
            "world_summary_preview": self._meta_world_summary_preview(ctx),
            "runtime_context": self._meta_runtime_context_summary(ctx.runtime_context),
        }
        if ctx.runtime_context is not None:
            object.__setattr__(ctx.runtime_context, "meta_cognition_fast_path_refs", set(self._meta_coordinator.fast_path_refs))
        self._schedule_background(
            self._reflect_meta_cognition_turn(
                session_key=ctx.session_key,
                turn_id=ctx.turn_id,
                turn_snapshot=snapshot,
                accepted_triggers=accepted,
                runtime_context=ctx.runtime_context,
            )
        )

    async def _reflect_meta_cognition_turn(
        self,
        *,
        session_key: str,
        turn_id: str,
        turn_snapshot: dict[str, Any],
        accepted_triggers: list[MetaTrigger],
        runtime_context: RuntimeContext | None,
    ) -> None:
        reflector = getattr(self, "_meta_cognition_reflector", None)
        if reflector is None:
            return
        result = await reflector.reflect_turn(
            session_key=session_key,
            turn_id=turn_id,
            turn_snapshot=turn_snapshot,
            accepted_triggers=accepted_triggers,
            runtime_context=runtime_context,
        )
        self._meta_coordinator.on_reflection_complete(reflector)
        logger.debug(
            "Meta cognition reflection finished for turn {} with status {} ({})",
            turn_id,
            result.status,
            result.reason,
        )

    def _meta_world_summary_preview(self, ctx: TurnContext) -> str:
        session = ctx.session
        if session is None:
            return ""
        world_state = getattr(self, "world_state", None)
        if world_state is None:
            return ""
        try:
            snapshot = world_state.load(
                session,
                identity=ctx.runtime_context if ctx.runtime_context is not None else None,
            )
        except Exception:
            return ""
        world_summary = getattr(snapshot, "world_summary", None)
        if world_summary is None:
            return ""
        try:
            data = world_summary.to_json()
        except Exception:
            return ""
        focus = data.get("focus") if isinstance(data, dict) else []
        if isinstance(focus, list):
            return _trim_text(" | ".join(str(item or "").strip() for item in focus if str(item or "").strip()), max_chars=400)
        return ""

    def _meta_runtime_context_summary(self, runtime_context: RuntimeContext | None) -> dict[str, Any]:
        if runtime_context is None:
            return {}
        return {
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
        """Assemble the final outbound message from turn results."""
        # MessageTool suppression
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
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
            media=generated_media,
            metadata=meta,
            buttons=buttons,
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
        """Schedule a controlled learning review for successful foreground turns."""
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

        max_recent = int(
            getattr(self.background_review.config, "max_recent_messages", 12) or 12
        )
        messages = [
            dict(message)
            for message in ctx.session.messages
            if not message.get("_command")
        ][-max_recent:]
        self._schedule_background(
            self.background_review.review_turn(
                session_key=ctx.session_key,
                turn_id=ctx.turn_id,
                channel=ctx.msg.channel,
                chat_id=ctx.msg.chat_id,
                message_id=ctx.msg.metadata.get("message_id"),
                messages=messages,
            )
        )

    def _schedule_curator_review(self, ctx: TurnContext) -> None:
        """Schedule deterministic curator review after successful foreground turns."""
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
        self._schedule_background(
            self.curator.review_workspace(
                session_key=ctx.session_key,
                turn_id=ctx.turn_id,
            )
        )

    def _schedule_nearline_memory(self, ctx: TurnContext) -> None:
        """Schedule nearline sidecar extraction without blocking the foreground reply."""
        if ctx.session is None:
            return
        service = getattr(self, "nearline_memory", None)
        if service is None or not getattr(service, "enabled", False):
            return
        if not self._nearline_turn_completed_successfully(ctx):
            return
        actor_id = (
            ctx.runtime_context.actor_id
            if ctx.runtime_context is not None and getattr(ctx.runtime_context, "actor_id", None)
            else "user"
        )
        self._schedule_background(
            service.process_turn(
                session=ctx.session,
                channel=ctx.msg.channel,
                chat_id=ctx.msg.chat_id,
                actor_id=actor_id,
                turn_id=ctx.turn_id,
            )
        )

    @staticmethod
    def _nearline_turn_completed_successfully(ctx: TurnContext) -> bool:
        if ctx.stop_reason in {
            "ask_user",
            "error",
            "tool_error",
            "max_iterations",
            "empty_final_response",
        }:
            return False
        return bool((ctx.final_content or "").strip())

    async def _state_respond(self, ctx: TurnContext) -> str:
        """Compatibility wrapper; normal flow is driven by TurnOrchestrator."""
        return await self._turn_pipeline.state_respond(ctx)

    @property
    def _message_dispatcher_ref(self) -> MessageDispatcher:
        return self._get_message_dispatcher()

    def _sanitize_persisted_blocks(
        self,
        content: list[dict[str, Any]],
        *,
        should_truncate_text: bool = False,
        drop_runtime: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip volatile multimodal payloads before writing session history."""
        return AgentLoop._turn_persist_manager(self).sanitize_persisted_blocks(
            content,
            should_truncate_text=should_truncate_text,
            drop_runtime=drop_runtime,
        )

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        AgentLoop._turn_persist_manager(self).save_turn(session, messages, skip)

    def _persist_subagent_followup(self, session: Session, msg: InboundMessage) -> bool:
        """Persist subagent follow-ups before prompt assembly so history stays durable.

        Returns True if a new entry was appended; False if the follow-up was
        deduped (same ``subagent_task_id`` already in session) or carries no
        content worth persisting.
        """
        return AgentLoop._turn_persist_manager(self).persist_subagent_followup(session, msg)

    def _set_runtime_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        """Persist the latest in-flight turn state into session metadata."""
        AgentLoop._turn_persist_manager(self).set_checkpoint(session, payload)

    def _mark_pending_user_turn(self, session: Session) -> None:
        AgentLoop._turn_persist_manager(self).mark_pending_user_turn(session)

    def _clear_pending_user_turn(self, session: Session) -> None:
        AgentLoop._turn_persist_manager(self).clear_pending_user_turn(session)

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
