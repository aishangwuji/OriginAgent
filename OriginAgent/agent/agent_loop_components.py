"""Sequential component assembly for AgentLoop."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from OriginAgent.agent import model_presets as preset_helpers
from OriginAgent.agent.action_planning import UnifiedActionPlanner
from OriginAgent.agent.action_summary import normalize_action_summary
from OriginAgent.agent.active_intents import ActiveIntentConfig, ActiveIntentService
from OriginAgent.agent.agent_turn_persist import TurnPersistManager
from OriginAgent.agent.audit import AuditLogger
from OriginAgent.agent.autocompact import AutoCompact
from OriginAgent.agent.auxiliary_llm import AuxiliaryLLMRouter
from OriginAgent.agent.background_review import BackgroundReviewService
from OriginAgent.agent.cognitive_audit import JsonlCognitiveAuditLedger
from OriginAgent.agent.cognitive_loop import CognitiveLoop, CognitiveLoopConfig
from OriginAgent.agent.cognitive_scheduler import CognitiveScheduler, CognitiveSchedulerConfig
from OriginAgent.agent.confirmation import ConfirmationManager, PendingConfirmationStore
from OriginAgent.agent.curator import CuratorService
from OriginAgent.agent.domain_packs import DomainPackManager
from OriginAgent.agent.introspection.service import RuntimeIntrospectionService
from OriginAgent.agent.memory import Consolidator, Dream, dream_feature_flags
from OriginAgent.agent.memory_governance import MemoryGovernance
from OriginAgent.agent.meta_cognition_audit import JsonlMetaCognitionAuditLedger
from OriginAgent.agent.meta_cognition_reflector import MetaCognitionReflector
from OriginAgent.agent.meta_cognition_regulator import MetaCognitionRegulator
from OriginAgent.agent.meta_cognition_runtime import MetaCognitionRuntime
from OriginAgent.agent.perception_event_fusion import PerceptionEventFusion
from OriginAgent.agent.reminders import ReminderStore
from OriginAgent.agent.roaming_prewarm import RoamingPrewarmService
from OriginAgent.agent.runner import AgentRunner
from OriginAgent.agent.skill_bootstrapper import (
    SkillBootstrapperScanner,
    SkillCandidateCompiler,
)
from OriginAgent.agent.thought_substrate_store import ThoughtSubstrate
from OriginAgent.agent.tools.audit import JsonlToolAuditSink, ToolAuditConfig
from OriginAgent.agent.tools.file_state import FileStateStore
from OriginAgent.agent.tools.registry import ToolRegistry
from OriginAgent.agent.working_memory import WorkingMemoryManager
from OriginAgent.agent.world_state import WorldStateManager
from OriginAgent.memory.pipeline import NearlineMemoryPipeline
from OriginAgent.memory.rolling import RollingEpisodeCompaction
from OriginAgent.security.grants import CapabilityGrantStore
from OriginAgent.session.cold_archive import SessionColdArchiveStore
from OriginAgent.session.search_index import SessionSearchIndexService
from OriginAgent.storage.sqlite_stores import SqliteStoreFactory


@dataclass
class LoopComponents:
    values: dict[str, Any]


@dataclass
class AgentLoopAttributes:
    """Typed container for all dynamically-injected AgentLoop attributes (D4).

    This mirrors LoopComponents.values but with proper type annotations,
    enabling progressive migration away from build_loop_components()'s
    setattr injection pattern.
    """

    # Step 1 — basic runtime inputs
    bus: Any = None  # type: ignore[assignment]
    channels_config: Any = None  # type: ignore[assignment]
    provider: Any = None  # type: ignore[assignment]
    _provider_snapshot_loader: Any = None  # type: ignore[assignment]
    _preset_snapshot_loader: Any = None  # type: ignore[assignment]
    _runtime_model_publisher: Any = None  # type: ignore[assignment]
    _provider_signature: Any = None  # type: ignore[assignment]
    _default_selection_signature: Any = None  # type: ignore[assignment]
    workspace: str = ""
    model: str = ""
    auxiliary_router: Any = None  # type: ignore[assignment]
    model_presets: Any = None  # type: ignore[assignment]
    model_preset: str | None = None
    max_iterations: int = 100
    context_window_tokens: int | None = None
    context_block_limit: int | None = None
    max_tool_result_chars: int = 5000
    provider_retry_mode: str = "standard"
    tool_hint_max_length: int = 120
    web_config: Any = None  # type: ignore[assignment]
    exec_config: Any = None  # type: ignore[assignment]
    tools_config: Any = None  # type: ignore[assignment]
    evolution_config: Any = None  # type: ignore[assignment]
    _meta_cognition_config: Any = None  # type: ignore[assignment]
    _dream_config: Any = None  # type: ignore[assignment]
    _nearline_memory_config: Any = None  # type: ignore[assignment]
    _memory_feature_flags: Any = None  # type: ignore[assignment]
    session_search_index: Any = None  # type: ignore[assignment]
    pairing_config: Any = None  # type: ignore[assignment]
    _image_generation_provider_configs: Any = None  # type: ignore[assignment]
    cron_service: Any = None  # type: ignore[assignment]
    restrict_to_workspace: bool = True
    _runtime_profile: str = "default"
    _start_time: float = 0.0
    _last_usage: float = 0.0
    _extra_hooks: Any = None  # type: ignore[assignment]

    # Step 2 — storage and persistence
    domain_packs: Any = None  # type: ignore[assignment]
    background_review: Any = None  # type: ignore[assignment]
    curator: Any = None  # type: ignore[assignment]
    sessions: Any = None  # type: ignore[assignment]
    session_cold_archive: Any = None  # type: ignore[assignment]
    _persist: Any = None  # type: ignore[assignment]
    _tool_audit_config: Any = None  # type: ignore[assignment]
    _grant_store: Any = None  # type: ignore[assignment]
    _audit_logger: Any = None  # type: ignore[assignment]
    _confirmation_store: Any = None  # type: ignore[assignment]
    _confirmation_manager: Any = None  # type: ignore[assignment]
    _reminder_store: Any = None  # type: ignore[assignment]

    # Step 3 — registry, subagents, runtime contributions
    working_memory: Any = None  # type: ignore[assignment]
    world_state: Any = None  # type: ignore[assignment]
    action_planner: Any = None  # type: ignore[assignment]
    tools: Any = None  # type: ignore[assignment]
    _domain_runtime_overrides: Any = None  # type: ignore[assignment]
    _domain_runtime_contributions: Any = None  # type: ignore[assignment]
    actor_resolver: Any = None  # type: ignore[assignment]
    _file_state_store: Any = None  # type: ignore[assignment]
    runner: Any = None  # type: ignore[assignment]
    subagents: Any = None  # type: ignore[assignment]

    # Step 4 — loop runtime scaffolding
    _unified_session: bool = False
    _max_messages: int = 200
    _running: bool = False
    _mcp_servers: Any = None  # type: ignore[assignment]
    _mcp_stacks: Any = None  # type: ignore[assignment]
    _mcp_snapshot: Any = None  # type: ignore[assignment]
    _mcp_state: str = "disconnected"
    _mcp_connected: bool = False
    _mcp_connecting: bool = False
    _mcp_lifecycle_lock: Any = None  # type: ignore[assignment]
    _mcp_ready: Any = None  # type: ignore[assignment]
    _mcp_shutdown_event: Any = None  # type: ignore[assignment]
    _mcp_runtime_task: Any = None  # type: ignore[assignment]
    _active_intent_task: Any = None  # type: ignore[assignment]
    _mcp_startup_error: str | None = None
    _active_tasks: Any = None  # type: ignore[assignment]
    _background_tasks: Any = None  # type: ignore[assignment]
    _session_locks: Any = None  # type: ignore[assignment]
    _pending_queues: Any = None  # type: ignore[assignment]
    context: Any = None  # type: ignore[assignment]
    memory_governance: Any = None  # type: ignore[assignment]
    roaming_prewarm: Any = None  # type: ignore[assignment]

    # Step 5 — active intent, cognitive, nearline, introspection, memory
    _active_intent_config: Any = None  # type: ignore[assignment]
    _cognitive_audit: Any = None  # type: ignore[assignment]
    active_intents: Any = None  # type: ignore[assignment]
    _meta_cognition_audit: Any = None  # type: ignore[assignment]
    _meta_cognition_runtime: Any = None  # type: ignore[assignment]
    _meta_cognition_reflector: Any = None  # type: ignore[assignment]
    _cognitive_loop_enabled: bool = False
    cognitive_scheduler: Any = None  # type: ignore[assignment]
    cognitive_loop: Any = None  # type: ignore[assignment]
    nearline_memory: Any = None  # type: ignore[assignment]
    rolling_episode_compaction: Any = None  # type: ignore[assignment]
    introspection: Any = None  # type: ignore[assignment]
    _concurrency_gate: Any = None  # type: ignore[assignment]
    _tool_concurrency_limit: int | None = None
    consolidator: Any = None  # type: ignore[assignment]
    auto_compact: Any = None  # type: ignore[assignment]
    dream: Any = None  # type: ignore[assignment]

    # Step 6 — runtime snapshots
    _runtime_vars: dict[str, Any] = field(default_factory=dict)
    _capability_snapshot: Any = None  # type: ignore[assignment]
    _current_iteration: int = 0
    _last_runtime_context: Any = None  # type: ignore[assignment]
    _last_continuity_session_key: Any = None  # type: ignore[assignment]
    _last_context_assembly: dict[str, Any] = field(default_factory=dict)
    _last_recovered_continuity_checkpoint: dict[str, Any] = field(default_factory=dict)
    _last_governance_audit: dict[str, Any] = field(default_factory=dict)
    _last_action_continuity_audit: dict[str, Any] = field(default_factory=dict)
    _cached_action_summary: dict[str, Any] = field(default_factory=dict)
    _last_cognitive_scan: dict[str, Any] = field(default_factory=dict)


def build_loop_components(
    *,
    loop: Any,
    context_builder_cls: Any,
    session_manager_cls: Any,
    subagent_manager_cls: Any,
    domain_pack_manager_cls: Any,
    bus: Any,
    provider: Any,
    workspace: Path,
    defaults: Any,
    tools_config: Any,
    web_config: Any,
    exec_config: Any,
    channels_config: Any,
    model: str | None,
    max_iterations: int | None,
    context_window_tokens: int | None,
    context_block_limit: int | None,
    max_tool_result_chars: int | None,
    provider_retry_mode: str,
    tool_hint_max_length: int | None,
    cron_service: Any,
    restrict_to_workspace: bool,
    session_manager: Any,
    mcp_servers: dict | None,
    timezone: str | None,
    runtime_profile: str,
    session_ttl_minutes: int,
    consolidation_ratio: float,
    max_messages: int,
    hooks: list[Any] | None,
    unified_session: bool,
    disabled_skills: list[str] | None,
    image_generation_provider_config: Any,
    image_generation_provider_configs: dict[str, Any] | None,
    provider_snapshot_loader: Callable[..., Any] | None,
    provider_signature: tuple[object, ...] | None,
    model_presets: dict[str, Any] | None,
    model_preset: str | None,
    preset_snapshot_loader: Any,
    runtime_model_publisher: Callable[[str, str | None], None] | None,
    device_action_executor: Any,
    device_registry: Any,
    domain_runtime_overrides: dict[str, Any] | None,
    actor_resolver: Any,
    tool_audit_config: Any,
    pairing_config: Any,
    auxiliary_config: Any,
    auxiliary_source_config: Any,
    auxiliary_provider_factory: Callable[[Any], Any] | None,
    primary_provider_name: str | None,
    domain_packs_config: Any,
    domain_pack_manager: DomainPackManager | None,
    learning_config: Any,
    learning_config_loader: Callable[[], Any] | None,
    meta_cognition_config: Any | None,
    curator_config: Any,
    curator_config_loader: Callable[[], Any] | None,
    evolution_config: Any,
    evolution_config_loader: Callable[[], Any] | None,
    dream_config: Any,
    nearline_memory_config: Any,
    cold_archive_enabled: bool,
    tool_concurrency_limit: int | None,
    allow_agent_initiated_messages: bool | None,
    enable_backend_cognition: bool | None,
    active_intent_interval_seconds: int | None,
    active_intent_session_cooldown_seconds: int | None,
    active_intent_intent_cooldown_seconds: int | None,
    active_intent_max_messages_per_session_per_pass: int | None,
    effective_config: Any | None = None,
    tiered_config: Any | None = None,
) -> LoopComponents:
    values: dict[str, Any] = {}

    # Step 1: basic runtime inputs and provider/router.
    values["bus"] = bus
    values["channels_config"] = channels_config

    # If tiered routing is enabled, upgrade the primary provider to the
    # "premium" tier — the flagship model handles reasoning and decisions
    # while the auxiliary router dispatches background tasks to cheaper tiers.
    _tiered = tiered_config if tiered_config and getattr(tiered_config, "enabled", False) else None
    if _tiered:
        premium_cfg = _tiered.tiers.get("premium")
        if premium_cfg and auxiliary_provider_factory is not None:
            from OriginAgent.config.schema import ModelPresetConfig as _Preset

            premium_preset = _Preset(
                model=premium_cfg.model,
                provider=premium_cfg.provider,
                max_tokens=premium_cfg.max_tokens,
                context_window_tokens=premium_cfg.context_window_tokens,
            )
            premium_provider = auxiliary_provider_factory(premium_preset)
            values["provider"] = premium_provider
            values["model"] = premium_cfg.model
        else:
            _tiered = None  # downgrade: premium tier not available

    values["_provider_snapshot_loader"] = provider_snapshot_loader
    values["_preset_snapshot_loader"] = preset_snapshot_loader
    values["_runtime_model_publisher"] = runtime_model_publisher
    values["_provider_signature"] = provider_signature
    values["_default_selection_signature"] = preset_helpers.default_selection_signature(
        provider_signature
    )
    values["workspace"] = workspace
    values.setdefault("provider", provider)
    values.setdefault("model", model or provider.get_default_model())
    values["auxiliary_router"] = AuxiliaryLLMRouter(
        primary_provider=values["provider"],
        primary_model=values["model"],
        auxiliary_config=auxiliary_config or defaults.auxiliary,
        config=auxiliary_source_config,
        provider_factory=auxiliary_provider_factory,
        primary_provider_name=primary_provider_name,
        tiered_config=_tiered,
    )
    values["model_presets"] = model_presets or {}
    values["model_preset"] = model_preset or ("default" if values["model_presets"] else None)
    values["max_iterations"] = (
        max_iterations if max_iterations is not None else defaults.max_tool_iterations
    )
    values["context_window_tokens"] = (
        context_window_tokens if context_window_tokens is not None else defaults.context_window_tokens
    )
    values["context_block_limit"] = context_block_limit
    values["max_tool_result_chars"] = (
        max_tool_result_chars if max_tool_result_chars is not None else defaults.max_tool_result_chars
    )
    values["provider_retry_mode"] = provider_retry_mode
    values["tool_hint_max_length"] = (
        tool_hint_max_length if tool_hint_max_length is not None else defaults.tool_hint_max_length
    )
    values["web_config"] = web_config
    values["exec_config"] = exec_config
    values["tools_config"] = tools_config
    values["evolution_config"] = evolution_config or defaults.learning.evolution
    values["_meta_cognition_config"] = (
        meta_cognition_config
        if meta_cognition_config is not None
        else getattr(defaults.learning, "meta_cognition", None)
    )
    values["_dream_config"] = dream_config or defaults.dream
    values["_nearline_memory_config"] = (
        nearline_memory_config if nearline_memory_config is not None else defaults.nearline_memory
    )
    values["_memory_feature_flags"] = dream_feature_flags(values["_dream_config"])
    values["session_search_index"] = SessionSearchIndexService(
        workspace,
        backend=tools_config.session_search.backend,
        semantic_enabled=bool(
            tools_config.session_search.semantic_enabled and tools_config.session_search.enabled
        ),
        rebuild_on_start=tools_config.session_search.rebuild_on_start,
        nearline_memory_config=values["_nearline_memory_config"],
    )
    values["pairing_config"] = pairing_config
    values["_image_generation_provider_configs"] = dict(image_generation_provider_configs or {})
    if (
        image_generation_provider_config is not None
        and "openrouter" not in values["_image_generation_provider_configs"]
    ):
        values["_image_generation_provider_configs"]["openrouter"] = image_generation_provider_config
    values["cron_service"] = cron_service
    values["restrict_to_workspace"] = restrict_to_workspace
    values["_runtime_profile"] = runtime_profile
    values["_start_time"] = time.time()
    values["_last_usage"] = {}
    values["_extra_hooks"] = hooks or []

    # Step 2: storage and persistence services.
    values["domain_packs"] = domain_pack_manager or domain_pack_manager_cls(
        workspace,
        config=domain_packs_config or defaults.domain_packs,
    )
    values["background_review"] = BackgroundReviewService(
        workspace=workspace,
        provider=provider,
        model=values["model"],
        router=values["auxiliary_router"],
        config=learning_config or defaults.learning.background_review,
        config_loader=learning_config_loader,
        domain_pack_manager=values["domain_packs"],
        memory_feature_flags=values["_memory_feature_flags"],
        task_runtime_config=defaults.task_runtime,
    )
    values["curator"] = CuratorService(
        workspace=workspace,
        config=curator_config or defaults.learning.curator,
        config_loader=curator_config_loader,
        evolution_config=values["evolution_config"],
        evolution_config_loader=evolution_config_loader,
        meta_cognition_config=values["_meta_cognition_config"],
        domain_pack_manager=values["domain_packs"],
    )
    values["sessions"] = session_manager or session_manager_cls(workspace)
    values["session_cold_archive"] = (
        SessionColdArchiveStore(workspace) if cold_archive_enabled else None
    )
    values["_persist"] = TurnPersistManager(values["max_tool_result_chars"], values["sessions"])
    values["_tool_audit_config"] = ToolAuditConfig.from_config(tool_audit_config or tools_config.audit)
    values["_grant_store"] = CapabilityGrantStore(workspace)
    values["_audit_logger"] = AuditLogger(workspace)
    values["_confirmation_store"] = PendingConfirmationStore(workspace)
    values["_confirmation_manager"] = ConfirmationManager(
        workspace,
        store=values["_confirmation_store"],
        audit_logger=values["_audit_logger"],
        config=defaults.confirmation,
    )
    values["_reminder_store"] = ReminderStore(workspace)

    # ── SQLite stores (create + migrate from JSONL) ────────────────
    values["_sqlite_stores"] = SqliteStoreFactory.create_all(workspace)

    # Step 3: registry, subagents, runtime contributions.
    values["working_memory"] = WorkingMemoryManager(
        values["sessions"],
        reminder_store=values["_reminder_store"],
    )
    values["world_state"] = WorldStateManager(
        workspace,
        values["sessions"],
        context_config=defaults.context,
    )
    values["action_planner"] = UnifiedActionPlanner()
    values["tools"] = ToolRegistry(
        audit_sink=JsonlToolAuditSink(workspace),
        audit_config=values["_tool_audit_config"],
        confirmation_manager=values["_confirmation_manager"],
        grant_store=values["_grant_store"],
    )
    values["_domain_runtime_overrides"] = dict(domain_runtime_overrides or {})
    if device_action_executor is not None:
        values["_domain_runtime_overrides"].setdefault("device_action_executor", device_action_executor)
    if device_registry is not None:
        values["_domain_runtime_overrides"].setdefault("device_registry", device_registry)
    values["_domain_runtime_contributions"] = values["domain_packs"].active_runtime_contributions(
        workspace=workspace,
        config=tools_config,
        overrides=values["_domain_runtime_overrides"]
        | {
            "confirmation_manager": values["_confirmation_manager"],
            "world_state": values["world_state"],
            "sessions": values["sessions"],
            "timezone_name": timezone,
        },
    )
    values["actor_resolver"] = actor_resolver or loop.actor_resolver.__class__() if actor_resolver is None and hasattr(loop, "actor_resolver") else actor_resolver
    if values["actor_resolver"] is None:
        from OriginAgent.agent.identity import ActorResolver

        values["actor_resolver"] = ActorResolver()
    values["_file_state_store"] = FileStateStore()
    values["runner"] = AgentRunner(provider)
    values["subagents"] = subagent_manager_cls(
        provider=provider,
        workspace=workspace,
        bus=bus,
        model=values["model"],
        web_config=web_config,
        content_read_config=tools_config.content_read,
        max_tool_result_chars=values["max_tool_result_chars"],
        exec_config=exec_config,
        restrict_to_workspace=restrict_to_workspace,
        disabled_skills=disabled_skills,
        max_iterations=values["max_iterations"],
        subagent_policy_mode=defaults.subagent_policy.mode,
        grant_store=values["_grant_store"],
        preset_snapshot_loader=preset_snapshot_loader,
    )

    # Step 4: loop runtime scaffolding and context wiring.
    values["_unified_session"] = unified_session
    values["_max_messages"] = max_messages if max_messages > 0 else 120
    values["_running"] = False
    values["_mcp_servers"] = mcp_servers or {}
    values["_mcp_stacks"] = {}
    values["_mcp_snapshot"] = {}
    values["_mcp_state"] = "disconnected"
    values["_mcp_connected"] = False
    values["_mcp_connecting"] = False
    values["_mcp_lifecycle_lock"] = asyncio.Lock()
    values["_mcp_ready"] = None
    values["_mcp_shutdown_event"] = None
    values["_mcp_runtime_task"] = None
    values["_active_intent_task"] = None
    values["_mcp_startup_error"] = None
    values["_active_tasks"] = {}
    values["_background_tasks"] = set()
    values["_session_locks"] = {}
    values["_pending_queues"] = {}
    values["context"] = context_builder_cls(
        workspace,
        timezone=timezone,
        disabled_skills=disabled_skills,
        memory_feature_flags=values["_memory_feature_flags"],
        context_config=defaults.context,
        domain_pack_manager=values["domain_packs"],
        audit_mode=values["_tool_audit_config"].mode,
        runtime_profile=runtime_profile,
        registry=values["tools"],
        sessions=values["sessions"],
        pending_queues=values["_pending_queues"],
        nearline_memory_config=values["_nearline_memory_config"],
        session_search_index_service=values["session_search_index"],
        cron_service=cron_service,
        confirmation_store=values["_confirmation_store"],
        background_review_service=values["background_review"],
        curator_service=values["curator"],
    )
    values["context"].working_memory = values["working_memory"]
    values["context"].world_state = values["world_state"]
    values["memory_governance"] = MemoryGovernance(
        workspace=workspace,
        memory=values["context"].memory,
        context_config=defaults.context,
        working_memory=values["working_memory"],
        world_state=values["world_state"],
    )
    values["roaming_prewarm"] = RoamingPrewarmService(
        workspace=workspace,
        sessions=values["sessions"],
        memory=values["context"].memory,
        nearline_memory=values["context"].nearline_memory,
        context_config=defaults.context,
        world_state=values["world_state"],
    )
    values["context"].memory_governance = values["memory_governance"]
    values["context"]._roaming_prewarm = values["roaming_prewarm"]

    # Step 5: active intent, cognitive, nearline, introspection, memory services.
    values["_active_intent_config"] = ActiveIntentConfig(
        enabled=(
            defaults.allow_agent_initiated_messages
            if allow_agent_initiated_messages is None
            else allow_agent_initiated_messages
        ),
        interval_seconds=(
            defaults.active_intent_interval_seconds
            if active_intent_interval_seconds is None
            else active_intent_interval_seconds
        ),
        session_cooldown_seconds=(
            defaults.active_intent_session_cooldown_seconds
            if active_intent_session_cooldown_seconds is None
            else active_intent_session_cooldown_seconds
        ),
        intent_cooldown_seconds=(
            defaults.active_intent_intent_cooldown_seconds
            if active_intent_intent_cooldown_seconds is None
            else active_intent_intent_cooldown_seconds
        ),
        max_messages_per_session_per_pass=(
            defaults.active_intent_max_messages_per_session_per_pass
            if active_intent_max_messages_per_session_per_pass is None
            else active_intent_max_messages_per_session_per_pass
        ),
    )
    cognition_enabled = (
        defaults.enable_backend_cognition
        if enable_backend_cognition is None
        else enable_backend_cognition
    )
    values["_cognitive_audit"] = JsonlCognitiveAuditLedger(workspace)
    values["active_intents"] = ActiveIntentService(
        workspace=workspace,
        bus=bus,
        sessions=values["sessions"],
        confirmation_store=values["_confirmation_store"],
        fact_store=values["context"].memory.fact_store,
        config=values["_active_intent_config"],
        cognitive_audit=values["_cognitive_audit"],
        nearline_memory_config=values["_nearline_memory_config"],
    )
    values["_meta_cognition_audit"] = JsonlMetaCognitionAuditLedger(
        workspace,
        sqlite=values["_sqlite_stores"],
    )
    # ── MetaCognitionRegulator (CS-007) ─────────────────────────────
    _regulator_enabled = getattr(values.get("_meta_cognition_config"), "regulator_enabled", True)
    values["_meta_cognition_regulator"] = MetaCognitionRegulator() if _regulator_enabled else None
    # ── SkillBootstrapper (CS-009) ─────────────────────────────────
    _bs_enabled = getattr(values.get("_meta_cognition_config"), "skill_bootstrapper_enabled", True)
    _scanner: SkillBootstrapperScanner | None = None
    _compiler: SkillCandidateCompiler | None = None
    if _bs_enabled:
        _scanner = SkillBootstrapperScanner()
        _compiler = SkillCandidateCompiler()
    values["_skill_bootstrapper_scanner"] = _scanner
    values["_skill_bootstrapper_compiler"] = _compiler
    values["_meta_cognition_runtime"] = MetaCognitionRuntime(
        config=values["_meta_cognition_config"],
        audit=values["_meta_cognition_audit"],
    )
    # ── ThoughtSubstrate (CogniSphere CS-002) ──────────────────────
    meta_config = values["_meta_cognition_config"]
    substrate_enabled = getattr(meta_config, "thought_substrate_enabled", True)
    _thought_substrate: ThoughtSubstrate | None = None
    if substrate_enabled:
        _sqlite = values["_sqlite_stores"]
        _thought_substrate = ThoughtSubstrate(
            workspace=workspace,
            max_frames_per_session=getattr(meta_config, "thought_substrate_max_frames_per_session", 500),
            sampling_rate=getattr(meta_config, "thought_substrate_sampling_rate", 1.0),
            audit=values["_meta_cognition_audit"],
            sqlite_frames=_sqlite.thought_frames,
            sqlite_journals=_sqlite.thought_journals,
        )
    values["_thought_substrate"] = _thought_substrate
    values["_meta_cognition_reflector"] = MetaCognitionReflector(
        workspace=workspace,
        config=meta_config,
        audit=values["_meta_cognition_audit"],
        auxiliary_router=values["auxiliary_router"],
        provider=provider,
        model=values["model"],
        sessions=values["sessions"],
        working_memory=values["working_memory"],
        context_config=defaults.context,
        substrate=_thought_substrate,
    )
    # ── PerceptionEventFusion (CS-003) ─────────────────────────────
    _fusion_config = SimpleNamespace(
        enabled=getattr(meta_config, "perception_fusion_enabled", True),
    )
    values["_perception_fusion"] = PerceptionEventFusion(config=_fusion_config)
    values["_cognitive_loop_enabled"] = bool(cognition_enabled)
    values["cognitive_scheduler"] = CognitiveScheduler(
        workspace=workspace,
        config=CognitiveSchedulerConfig(
            enabled=bool(cognition_enabled),
            interval_seconds=values["_active_intent_config"].interval_seconds,
        ),
        cron_service=cron_service,
        session_keys_provider=values["active_intents"].session_keys,
        active_task_count_provider=loop._active_task_count,
        running_subagents_provider=values["subagents"].get_running_count_by_session,
        session_processor=loop._run_cognitive_pass_for_session,
    )
    values["cognitive_loop"] = CognitiveLoop(
        config=CognitiveLoopConfig(
            enabled=bool(cognition_enabled),
            interval_seconds=values["_active_intent_config"].interval_seconds,
        ),
        session_keys_provider=values["active_intents"].session_keys,
        active_task_count_provider=loop._active_task_count,
        running_subagents_provider=values["subagents"].get_running_count_by_session,
        session_processor=loop._run_cognitive_pass_for_session,
    )
    values["nearline_memory"] = NearlineMemoryPipeline(
        workspace=workspace,
        config=values["_nearline_memory_config"],
    )
    values["rolling_episode_compaction"] = RollingEpisodeCompaction(
        workspace,
        store=values["nearline_memory"].store,
        interval_turns=getattr(
            values["_nearline_memory_config"],
            "episode_compaction_interval_turns",
            20,
        ),
    )
    values["introspection"] = RuntimeIntrospectionService(
        loop=loop,
        workspace=workspace,
        registry=values["tools"],
        sessions=values["sessions"],
        pending_queues=values["_pending_queues"],
        cron_service=cron_service,
        confirmation_store=values["_confirmation_store"],
        audit_mode=values["_tool_audit_config"].mode,
        runtime_profile=runtime_profile,
        domain_pack_manager=values["domain_packs"],
        background_review_service=values["background_review"],
        curator_service=values["curator"],
        nearline_memory_service=values["nearline_memory"],
        nearline_memory_config=values["_nearline_memory_config"],
        session_search_index_service=values["session_search_index"],
        evolution_config=values["evolution_config"],
        effective_config=effective_config,
    )
    max_concurrent_requests = int(os.environ.get("ORIGINAGENT_MAX_CONCURRENT_REQUESTS", "3"))
    values["_concurrency_gate"] = (
        asyncio.Semaphore(max_concurrent_requests) if max_concurrent_requests > 0 else None
    )
    values["_tool_concurrency_limit"] = (
        tool_concurrency_limit
        if tool_concurrency_limit is not None
        else max(1, int(os.environ.get("ORIGINAGENT_MAX_CONCURRENT_TOOLS", "4")))
    )
    values["consolidator"] = Consolidator(
        store=values["context"].memory,
        provider=provider,
        model=values["model"],
        auxiliary_router=values["auxiliary_router"],
        sessions=values["sessions"],
        context_window_tokens=values["context_window_tokens"],
        build_messages=values["context"].build_messages,
        get_tool_definitions=values["tools"].get_definitions,
        max_completion_tokens=provider.generation.max_tokens,
        consolidation_ratio=consolidation_ratio,
    )
    values["auto_compact"] = AutoCompact(
        sessions=values["sessions"],
        consolidator=values["consolidator"],
        session_ttl_minutes=session_ttl_minutes,
        cold_archive=values["session_cold_archive"],
    )
    values["dream"] = Dream(
        store=values["context"].memory,
        provider=provider,
        model=values["model"],
        auxiliary_router=values["auxiliary_router"],
        evolution_config=values["evolution_config"],
        feature_flags=values["_memory_feature_flags"],
    )

    # Step 6: final runtime snapshots and command/tool registration host state.
    values["_runtime_vars"] = {}
    values["_capability_snapshot"] = None
    values["_current_iteration"] = 0
    values["_last_runtime_context"] = None
    values["_last_continuity_session_key"] = None
    values["_last_context_assembly"] = {}
    values["_last_recovered_continuity_checkpoint"] = {}
    values["_last_governance_audit"] = {}
    values["_last_action_continuity_audit"] = {}
    values["_cached_action_summary"] = normalize_action_summary({})
    values["_last_cognitive_scan"] = {}

    return LoopComponents(values=values)


__all__ = ["AgentLoopAttributes", "LoopComponents", "build_loop_components"]
