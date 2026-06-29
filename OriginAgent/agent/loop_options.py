"""Grouped parameter objects for ``AgentLoop`` constructor.

Replaces 60+ flat constructor parameters with concern-specific dataclasses
so that adding a new option touches only one group rather than the entire
constructor signature.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields as dataclass_fields
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from OriginAgent.agent.hook import AgentHook
    from OriginAgent.agent.identity import ActorResolver
    from OriginAgent.agent.subagent import SubagentManager
    from OriginAgent.bus.queue import MessageBus
    from OriginAgent.config.schema import (
        AuxiliaryConfig,
        ChannelsConfig,
        Config,
        DomainPacksConfig,
        EvolutionConfig,
        ExecToolConfig,
        LearningConfig,
        ModelPresetConfig,
        ProviderConfig,
        ToolsConfig,
        WebToolsConfig,
    )
    from OriginAgent.cron.service import CronService
    from OriginAgent.domain_packs import DomainPackManager
    from OriginAgent.providers.base import LLMProvider
    from OriginAgent.providers.factory import ProviderSnapshot
    from OriginAgent.security.capabilities import CapabilitySnapshot
    from OriginAgent.session.manager import SessionManager


@dataclass
class ProviderOptions:
    """LLM provider and agent iteration settings."""

    model: str | None = None
    max_iterations: int | None = None
    context_window_tokens: int | None = None
    context_block_limit: int | None = None
    max_tool_result_chars: int | None = None
    provider_retry_mode: str = "standard"
    provider_snapshot_loader: Callable[..., "ProviderSnapshot"] | None = None
    provider_signature: tuple[object, ...] | None = None
    model_presets: dict[str, "ModelPresetConfig"] | None = None
    model_preset: str | None = None
    preset_snapshot_loader: Any = None
    runtime_model_publisher: Callable[[str, str | None], None] | None = None
    primary_provider_name: str | None = None
    auxiliary_config: Any = None
    auxiliary_source_config: "Config | None" = None
    auxiliary_provider_factory: Callable[..., "LLMProvider"] | None = None
    enable_backend_cognition: bool | None = None


@dataclass
class ToolOptions:
    """Tool registration and execution settings."""

    tools_config: "ToolsConfig | None" = None
    web_config: "WebToolsConfig | None" = None
    exec_config: "ExecToolConfig | None" = None
    tool_hint_max_length: int | None = None
    restrict_to_workspace: bool = False
    mcp_servers: dict[str, Any] | None = None
    tool_audit_config: Any = None
    tool_concurrency_limit: int | None = None
    image_generation_provider_config: "ProviderConfig | None" = None
    image_generation_provider_configs: dict[str, "ProviderConfig"] | None = None
    device_action_executor: Any | None = None
    device_tools_real_mode: bool = False
    device_registry: Any | None = None


@dataclass
class ChannelOptions:
    """Multi-channel and session settings."""

    channels_config: "ChannelsConfig | None" = None
    session_manager: "SessionManager | None" = None
    unified_session: bool = False
    session_ttl_minutes: int = 0
    max_messages: int = 120
    cold_archive_enabled: bool = True
    timezone: str | None = None
    transcription_provider_config: dict[str, Any] | None = None


@dataclass
class LearningOptions:
    """Self-improvement and memory consolidation settings."""

    consolidation_ratio: float = 0.5
    learning_config: Any = None
    learning_config_loader: Any = None
    curator_config: Any = None
    curator_config_loader: Any = None
    evolution_config: Any = None
    evolution_config_loader: Any = None
    meta_cognition_config: Any = None
    dream_config: Any = None
    nearline_memory_config: Any = None
    background_review_config: Any = None
    allow_agent_initiated_messages: bool | None = None
    enable_backend_cognition: bool | None = None
    active_intent_interval_seconds: int | None = None
    active_intent_session_cooldown_seconds: int | None = None
    active_intent_intent_cooldown_seconds: int | None = None
    active_intent_max_messages_per_session_per_pass: int | None = None


@dataclass
class RuntimeOptions:
    """Runtime profile, hooks, domain packs, and cross-cutting services."""

    runtime_profile: str = "default"
    hooks: list[Any] | None = None
    disabled_skills: list[str] | None = None
    domain_runtime_overrides: dict[str, Any] | None = None
    actor_resolver: "ActorResolver | None" = None
    pairing_config: Any = None
    effective_config: Any = None
    tiered_config: Any = None
    cron_service: "CronService | None" = None
    domain_packs_config: "DomainPacksConfig | None" = None
    domain_pack_manager: "DomainPackManager | None" = None


@dataclass
class LoopOptions:
    """Grouped parameter object for ``AgentLoop`` constructor.

    Each sub-group maps to a subsystem concern.  Call ``to_kwargs()``
    for backward-compatible flat dict conversion, or pass the object
    to ``AgentLoop.from_options()``.
    """

    provider: ProviderOptions = field(default_factory=ProviderOptions)
    tools: ToolOptions = field(default_factory=ToolOptions)
    channels: ChannelOptions = field(default_factory=ChannelOptions)
    learning: LearningOptions = field(default_factory=LearningOptions)
    runtime: RuntimeOptions = field(default_factory=RuntimeOptions)

    # Cross-cutting shared services
    workspace: Any = None
    bus: Any = None

    def to_kwargs(self) -> dict[str, Any]:
        """Flatten all sub-groups into a single keyword-arg dict.

        Only includes fields whose value is not ``None`` so that callers
        can override specific options without defaulting unrelated ones.
        """
        kwargs: dict[str, Any] = {}
        for group in (self.provider, self.tools, self.channels, self.learning, self.runtime):
            for f in dataclass_fields(group):
                value = getattr(group, f.name)
                if value is not None:
                    kwargs[f.name] = value
        return kwargs
