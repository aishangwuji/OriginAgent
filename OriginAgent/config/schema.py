"""Configuration schema using Pydantic."""

from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings

from OriginAgent.cron.types import CronSchedule

# Single source of truth for supported transcription providers (spec 3.6, rule 6).
# Consumed by config.doctor (validation) and gateway.rest_api (UI options) to keep
# them in sync — previously these were defined independently and drifted (2 vs 3).
TRANSCRIPTION_PROVIDERS = {"groq", "openai", "volcengine"}

# Single source of truth for supported device backends (spec 3.5, rule 6).
# Consumed by gateway.rest_api (settings_update validation). The Literal type on
# DeviceToolsConfig.backend is a *static* type-check constraint and cannot be
# derived from this runtime tuple — both must be kept in sync by hand when a
# backend is added. Previously channels/websocket.py and gateway/rest_api.py each
# maintained an independent copy, risking drift on schema changes.
DEVICE_BACKEND_OPTIONS = ("none", "fake", "lighting_client")


class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

class ChannelsConfig(Base):
    """Configuration for chat channels.

    Built-in and plugin channel configs are stored as extra fields (dicts).
    Each channel parses its own config in __init__.
    Per-channel "streaming": true enables streaming output (requires send_delta impl).
    """

    model_config = ConfigDict(extra="allow")

    send_progress: bool = True  # stream agent's text progress to the channel
    send_tool_hints: bool = False  # stream tool-call hints (e.g. read_file("…"))
    show_reasoning: bool = True  # surface model reasoning when channel implements it
    send_max_retries: int = Field(default=3, ge=0, le=10)  # Max delivery attempts (initial send included)
    transcription_provider: str = "groq"  # Voice transcription backend: "groq" or "openai"
    transcription_language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")  # Optional ISO-639-1 hint for audio transcription


class DreamConfig(Base):
    """Dream memory consolidation configuration."""

    _HOUR_MS = 3_600_000

    interval_h: int = Field(default=2, ge=1)  # Every 2 hours by default
    cron: str | None = Field(default=None, exclude=True)  # Legacy compatibility override
    model_override: str | None = Field(
        default=None,
        validation_alias=AliasChoices("modelOverride", "model", "model_override"),
    )  # Optional Dream-specific model override
    max_batch_size: int = Field(default=20, ge=1)  # Max history entries per run
    # Bumped from 10 to 15 in #3212 (exp002: +30% dedup, no accuracy loss; >15 plateaus).
    max_iterations: int = Field(default=15, ge=1)  # Max tool calls per Phase 2
    # Per-line git-blame age annotation in Phase 1 prompt (see #3212). Default
    # on — set to False to feed MEMORY.md raw if a specific LLM reacts poorly
    # to the `← Nd` suffix or you want deterministic, git-independent prompts.
    annotate_line_ages: bool = True
    semantic_retrieval_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "semanticRetrievalEnabled",
            "semantic_retrieval_enabled",
        ),
        serialization_alias="semanticRetrievalEnabled",
    )
    semantic_merge_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "semanticMergeEnabled",
            "semantic_merge_enabled",
        ),
        serialization_alias="semanticMergeEnabled",
    )
    fact_graph_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "factGraphEnabled",
            "fact_graph_enabled",
        ),
        serialization_alias="factGraphEnabled",
    )
    confidence_v2_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "confidenceV2Enabled",
            "confidence_v2_enabled",
        ),
        serialization_alias="confidenceV2Enabled",
    )
    contradiction_auto_flip_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "contradictionAutoFlipEnabled",
            "contradiction_auto_flip_enabled",
        ),
        serialization_alias="contradictionAutoFlipEnabled",
    )
    fact_audit_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "factAuditEnabled",
            "fact_audit_enabled",
        ),
        serialization_alias="factAuditEnabled",
    )
    lazy_snapshot_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "lazySnapshotEnabled",
            "lazy_snapshot_enabled",
        ),
        serialization_alias="lazySnapshotEnabled",
    )

    def build_schedule(self, timezone: str) -> CronSchedule:
        """Build the runtime schedule, preferring the legacy cron override if present."""
        if self.cron:
            return CronSchedule(kind="cron", expr=self.cron, tz=timezone)
        return CronSchedule(kind="every", every_ms=self.interval_h * self._HOUR_MS)

    def describe_schedule(self) -> str:
        """Return a human-readable summary for logs and startup output."""
        if self.cron:
            return f"cron {self.cron} (legacy)"
        hours = self.interval_h
        return f"every {hours}h"


class NearlineMemoryConfig(Base):
    """Layered nearline memory scaffolding configuration."""

    enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("enabled"),
    )
    pipeline_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "pipelineEnabled",
            "pipeline_enabled",
        ),
        serialization_alias="pipelineEnabled",
    )
    profile_shadow_write_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "profileShadowWriteEnabled",
            "profile_shadow_write_enabled",
        ),
        serialization_alias="profileShadowWriteEnabled",
    )
    retrieval_top_k: int = Field(
        default=8,
        ge=1,
        le=50,
        validation_alias=AliasChoices(
            "retrievalTopK",
            "retrieval_top_k",
        ),
        serialization_alias="retrievalTopK",
    )
    max_messages_per_memcell: int = Field(
        default=12,
        ge=1,
        le=100,
        validation_alias=AliasChoices(
            "maxMessagesPerMemcell",
            "max_messages_per_memcell",
        ),
        serialization_alias="maxMessagesPerMemcell",
    )
    idle_gap_seconds: int = Field(
        default=900,
        ge=30,
        le=86_400,
        validation_alias=AliasChoices(
            "idleGapSeconds",
            "idle_gap_seconds",
        ),
        serialization_alias="idleGapSeconds",
    )
    profile_refresh_min_memcells: int = Field(
        default=1,
        ge=1,
        le=100,
        validation_alias=AliasChoices(
            "profileRefreshMinMemcells",
            "profile_refresh_min_memcells",
        ),
        serialization_alias="profileRefreshMinMemcells",
    )
    event_batch_size: int = Field(
        default=10,
        ge=1,
        le=200,
        validation_alias=AliasChoices(
            "eventBatchSize",
            "event_batch_size",
        ),
        serialization_alias="eventBatchSize",
    )
    episode_compaction_interval_turns: int = Field(
        default=20,
        ge=1,
        le=500,
        validation_alias=AliasChoices(
            "episodeCompactionIntervalTurns",
            "episode_compaction_interval_turns",
        ),
        serialization_alias="episodeCompactionIntervalTurns",
    )


class InlineFallbackConfig(Base):
    """Inline fallback model candidate."""

    model: str
    provider: str = "auto"
    max_tokens: int | None = None
    context_window_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None


class ModelPresetConfig(Base):
    """Named runtime model + provider configuration."""

    model: str
    provider: str = "auto"
    max_tokens: int | None = None
    context_window_tokens: int | None = Field(default=None, ge=4096, le=1_000_000)
    temperature: float | None = None
    reasoning_effort: str | None = None
    fallback_models: list[str | InlineFallbackConfig] = Field(default_factory=list)

    def to_generation_settings(self):
        from OriginAgent.providers.base import GenerationSettings

        return GenerationSettings(
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=self.reasoning_effort,
        )


FallbackCandidate = str | InlineFallbackConfig


class AuxiliaryTaskConfig(Base):
    """Per-background-task auxiliary LLM routing settings."""

    model_override: str | None = Field(
        default=None,
        validation_alias=AliasChoices("modelOverride", "model", "model_override"),
    )
    timeout_s: float | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("timeoutS", "timeout", "timeout_s"),
    )
    fallback_models: list[FallbackCandidate] = Field(default_factory=list)


class AuxiliaryConfig(Base):
    """Background LLM routing and fallback configuration."""

    enabled: bool = True
    payment_cooldown_s: int = Field(
        default=1800,
        ge=0,
        validation_alias=AliasChoices("paymentCooldownS", "payment_cooldown_s"),
    )
    transient_cooldown_s: int = Field(
        default=60,
        ge=0,
        validation_alias=AliasChoices("transientCooldownS", "transient_cooldown_s"),
    )
    tasks: dict[str, AuxiliaryTaskConfig] = Field(default_factory=dict)


class ModelTierConfig(Base):
    """A named tier representing one cost/capability level for tiered routing.

    Each tier maps to a specific (provider, model) pair with optional token
    and generation limits.  Used by :class:`TieredRouterConfig` to assign
    background tasks to the cheapest adequate model.
    """

    provider: str
    model: str
    max_tokens: int = 4096
    context_window_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None


class TieredRouterConfig(Base):
    """Tier-based LLM routing configuration.

    When *enabled*, background tasks are assigned to a model *tier*
    (e.g. ``economy`` → DeepSeek, ``standard`` → Sonnet) instead of
    always using the primary agent model.  This decouples capability from
    cost: high-volume / low-cognitive-load tasks (search, consolidation,
    title generation) run on cheap models while the main agent loop keeps
    the expensive flagship model for reasoning and decisions.

    The config is **off by default** — setting ``enabled: true`` is an
    explicit opt-in.  All fields have safe defaults so a minimal config
    just needs ``tiers`` and ``task_tier_mapping``.
    """

    enabled: bool = False
    default_tier: str = Field(default="economy")
    tiers: dict[str, ModelTierConfig] = Field(default_factory=dict)
    task_tier_mapping: dict[str, str] = Field(default_factory=dict)


class DomainPacksConfig(Base):
    """Domain pack discovery and prompt injection configuration."""

    enabled: bool = True
    disabled: list[str] = Field(default_factory=list)
    active: list[str] = Field(default_factory=list)
    max_capability_chars: int = Field(
        default=4000,
        ge=0,
        validation_alias=AliasChoices("maxCapabilityChars", "max_capability_chars"),
        serialization_alias="maxCapabilityChars",
    )


class RobotG1Config(Base):
    """Disabled-by-default Unitree G1 integration placeholder for P5B+."""

    enabled: bool = False
    mcp_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("mcpEndpoint", "mcp_endpoint"),
        serialization_alias="mcpEndpoint",
    )
    tool_timeout_seconds: int = Field(
        default=30,
        ge=1,
        validation_alias=AliasChoices("toolTimeoutSeconds", "tool_timeout_seconds"),
        serialization_alias="toolTimeoutSeconds",
    )
    perception_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("perceptionEnabled", "perception_enabled"),
        serialization_alias="perceptionEnabled",
    )
    perception_mode: Literal["disabled", "on_demand", "always", "interval", "motion"] = Field(
        default="disabled",
        validation_alias=AliasChoices("perceptionMode", "perception_mode"),
        serialization_alias="perceptionMode",
    )


class BackgroundReviewConfig(Base):
    """Controlled background learning proposal generation."""

    enabled: bool = True
    max_recent_messages: int = Field(
        default=12,
        ge=1,
        le=100,
        validation_alias=AliasChoices("maxRecentMessages", "max_recent_messages"),
        serialization_alias="maxRecentMessages",
    )
    max_prompt_chars: int = Field(
        default=16000,
        ge=1000,
        validation_alias=AliasChoices("maxPromptChars", "max_prompt_chars"),
        serialization_alias="maxPromptChars",
    )
    max_proposals_per_turn: int = Field(
        default=8,
        ge=1,
        le=50,
        validation_alias=AliasChoices("maxProposalsPerTurn", "max_proposals_per_turn"),
        serialization_alias="maxProposalsPerTurn",
    )
    max_concurrent_reviews: int = Field(
        default=1,
        ge=1,
        le=8,
        validation_alias=AliasChoices("maxConcurrentReviews", "max_concurrent_reviews"),
        serialization_alias="maxConcurrentReviews",
    )
    allowed_proposal_types: list[str] = Field(
        default_factory=lambda: ["memory", "fact", "skill", "workflow"],
        validation_alias=AliasChoices("allowedProposalTypes", "allowed_proposal_types"),
        serialization_alias="allowedProposalTypes",
    )
    title_max_chars: int = Field(
        default=160,
        ge=1,
        le=1000,
        validation_alias=AliasChoices("titleMaxChars", "title_max_chars"),
        serialization_alias="titleMaxChars",
    )
    content_max_chars: int = Field(
        default=2400,
        ge=100,
        le=20_000,
        validation_alias=AliasChoices("contentMaxChars", "content_max_chars"),
        serialization_alias="contentMaxChars",
    )
    rationale_max_chars: int = Field(
        default=1200,
        ge=100,
        le=10_000,
        validation_alias=AliasChoices("rationaleMaxChars", "rationale_max_chars"),
        serialization_alias="rationaleMaxChars",
    )
    evidence_max_items: int = Field(
        default=5,
        ge=1,
        le=50,
        validation_alias=AliasChoices("evidenceMaxItems", "evidence_max_items"),
        serialization_alias="evidenceMaxItems",
    )
    evidence_max_chars: int = Field(
        default=500,
        ge=50,
        le=5000,
        validation_alias=AliasChoices("evidenceMaxChars", "evidence_max_chars"),
        serialization_alias="evidenceMaxChars",
    )
    message_max_chars: int = Field(
        default=1600,
        ge=100,
        le=20_000,
        validation_alias=AliasChoices("messageMaxChars", "message_max_chars"),
        serialization_alias="messageMaxChars",
    )
    review_reason_max_chars: int = Field(
        default=1000,
        ge=100,
        le=10_000,
        validation_alias=AliasChoices("reviewReasonMaxChars", "review_reason_max_chars"),
        serialization_alias="reviewReasonMaxChars",
    )
    transient_retry_count: int = Field(
        default=1,
        ge=0,
        le=5,
        validation_alias=AliasChoices("transientRetryCount", "transient_retry_count"),
        serialization_alias="transientRetryCount",
    )


class CuratorConfig(Base):
    """Deterministic curator proposal generation."""

    enabled: bool = True
    max_proposals_per_run: int = Field(
        default=12,
        ge=1,
        le=50,
        validation_alias=AliasChoices("maxProposalsPerRun", "max_proposals_per_run"),
        serialization_alias="maxProposalsPerRun",
    )
    title_max_chars: int = Field(
        default=160,
        ge=1,
        le=1000,
        validation_alias=AliasChoices("titleMaxChars", "title_max_chars"),
        serialization_alias="titleMaxChars",
    )
    body_max_chars: int = Field(
        default=2400,
        ge=100,
        le=20_000,
        validation_alias=AliasChoices("bodyMaxChars", "body_max_chars"),
        serialization_alias="bodyMaxChars",
    )
    rationale_max_chars: int = Field(
        default=1200,
        ge=100,
        le=10_000,
        validation_alias=AliasChoices("rationaleMaxChars", "rationale_max_chars"),
        serialization_alias="rationaleMaxChars",
    )
    evidence_max_chars: int = Field(
        default=500,
        ge=50,
        le=5000,
        validation_alias=AliasChoices("evidenceMaxChars", "evidence_max_chars"),
        serialization_alias="evidenceMaxChars",
    )
    max_evidence: int = Field(
        default=5,
        ge=1,
        le=50,
        validation_alias=AliasChoices("maxEvidence", "max_evidence"),
        serialization_alias="maxEvidence",
    )
    transient_retry_count: int = Field(
        default=1,
        ge=0,
        le=5,
        validation_alias=AliasChoices("transientRetryCount", "transient_retry_count"),
        serialization_alias="transientRetryCount",
    )


class ContextConfig(Base):
    """Prompt context construction limits."""

    enable_phase1_continuity: bool = Field(
        default=True,
        validation_alias=AliasChoices("enablePhase1Continuity", "enable_phase1_continuity"),
        serialization_alias="enablePhase1Continuity",
    )

    enable_episode_context: bool = Field(
        default=True,
        validation_alias=AliasChoices("enableEpisodeContext", "enable_episode_context"),
        serialization_alias="enableEpisodeContext",
    )

    max_recent_history: int = Field(
        default=50,
        ge=1,
        le=500,
        validation_alias=AliasChoices("maxRecentHistory", "max_recent_history"),
        serialization_alias="maxRecentHistory",
    )
    max_history_chars: int = Field(
        default=32_000,
        ge=1000,
        le=500_000,
        validation_alias=AliasChoices("maxHistoryChars", "max_history_chars"),
        serialization_alias="maxHistoryChars",
    )
    max_retrieval_blocks: int = Field(
        default=4,
        ge=1,
        le=32,
        validation_alias=AliasChoices("maxRetrievalBlocks", "max_retrieval_blocks"),
        serialization_alias="maxRetrievalBlocks",
    )
    max_retrieval_chars: int = Field(
        default=8_000,
        ge=200,
        le=100_000,
        validation_alias=AliasChoices("maxRetrievalChars", "max_retrieval_chars"),
        serialization_alias="maxRetrievalChars",
    )
    max_retrieval_hits_per_source: int = Field(
        default=4,
        ge=1,
        le=32,
        validation_alias=AliasChoices("maxRetrievalHitsPerSource", "max_retrieval_hits_per_source"),
        serialization_alias="maxRetrievalHitsPerSource",
    )
    world_summary_ttl_minutes: int = Field(
        default=5,
        ge=1,
        le=60,
        validation_alias=AliasChoices("worldSummaryTtlMinutes", "world_summary_ttl_minutes"),
        serialization_alias="worldSummaryTtlMinutes",
    )
    snapshot_freshness_minutes: int = Field(
        default=30,
        ge=5,
        le=1440,
        validation_alias=AliasChoices("snapshotFreshnessMinutes", "snapshot_freshness_minutes"),
        serialization_alias="snapshotFreshnessMinutes",
    )
    world_attention_max_items: int = Field(
        default=3,
        ge=1,
        le=10,
        validation_alias=AliasChoices("worldAttentionMaxItems", "world_attention_max_items"),
        serialization_alias="worldAttentionMaxItems",
    )
    governance_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("governanceEnabled", "governance_enabled"),
        serialization_alias="governanceEnabled",
    )
    prewarm_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("prewarmEnabled", "prewarm_enabled"),
        serialization_alias="prewarmEnabled",
    )
    promotion_confidence_threshold: float = Field(
        default=0.8,
        ge=0.5,
        le=1.0,
        validation_alias=AliasChoices("promotionConfidenceThreshold", "promotion_confidence_threshold"),
        serialization_alias="promotionConfidenceThreshold",
    )
    promotion_min_confirmations: int = Field(
        default=2,
        ge=1,
        le=20,
        validation_alias=AliasChoices("promotionMinConfirmations", "promotion_min_confirmations"),
        serialization_alias="promotionMinConfirmations",
    )
    promotion_require_user_confirmation_for_sensitive: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "promotionRequireUserConfirmationForSensitive",
            "promotion_require_user_confirmation_for_sensitive",
        ),
        serialization_alias="promotionRequireUserConfirmationForSensitive",
    )
    prewarm_max_items: int = Field(
        default=8,
        ge=1,
        le=32,
        validation_alias=AliasChoices("prewarmMaxItems", "prewarm_max_items"),
        serialization_alias="prewarmMaxItems",
    )
    max_media_files: int = Field(
        default=8,
        ge=1,
        le=32,
        validation_alias=AliasChoices("maxMediaFiles", "max_media_files"),
        serialization_alias="maxMediaFiles",
    )
    max_media_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=1024,
        le=256 * 1024 * 1024,
        validation_alias=AliasChoices("maxMediaBytes", "max_media_bytes"),
        serialization_alias="maxMediaBytes",
    )
    home_situation_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("homeSituationEnabled", "home_situation_enabled"),
        serialization_alias="homeSituationEnabled",
    )
    home_attention_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("homeAttentionEnabled", "home_attention_enabled"),
        serialization_alias="homeAttentionEnabled",
    )
    home_attention_max_notices: int = Field(
        default=50,
        ge=1,
        le=200,
        validation_alias=AliasChoices("homeAttentionMaxNotices", "home_attention_max_notices"),
        serialization_alias="homeAttentionMaxNotices",
    )
    proactive_home_suggestions: bool = Field(
        default=False,
        validation_alias=AliasChoices("proactiveHomeSuggestions", "proactive_home_suggestions"),
        serialization_alias="proactiveHomeSuggestions",
    )
    # continuity checkpoint 中 recent_turns_summary 每条消息的截断长度。
    # 原硬编码 500 字符导致跨 session 恢复时长对话关键信息丢失，改为可配置。
    recent_turns_summary_max_chars: int = Field(
        default=800,
        ge=100,
        le=10_000,
        validation_alias=AliasChoices("recentTurnsSummaryMaxChars", "recent_turns_summary_max_chars"),
        serialization_alias="recentTurnsSummaryMaxChars",
    )


class ConfirmationConfig(Base):
    """Runtime confirmation sanitization and TTL settings."""

    prompt_max_chars: int = Field(
        default=1000,
        ge=50,
        le=20_000,
        validation_alias=AliasChoices("promptMaxChars", "prompt_max_chars"),
        serialization_alias="promptMaxChars",
    )
    reason_max_chars: int = Field(
        default=2000,
        ge=50,
        le=20_000,
        validation_alias=AliasChoices("reasonMaxChars", "reason_max_chars"),
        serialization_alias="reasonMaxChars",
    )
    notify_ttl_seconds: int = Field(
        default=24 * 60 * 60,
        ge=60,
        le=7 * 24 * 60 * 60,
        validation_alias=AliasChoices("notifyTtlSeconds", "notify_ttl_seconds"),
        serialization_alias="notifyTtlSeconds",
    )
    confirm_ttl_by_risk: dict[str, int] = Field(
        default_factory=lambda: {
            "low": 10 * 60,
            "medium": 5 * 60,
            "high": 2 * 60,
        },
        validation_alias=AliasChoices("confirmTtlByRisk", "confirm_ttl_by_risk"),
        serialization_alias="confirmTtlByRisk",
    )


class TaskRuntimeConfig(Base):
    """Shared background task runtime policy."""

    default_transient_retry_count: int = Field(
        default=1,
        ge=0,
        le=5,
        validation_alias=AliasChoices("defaultTransientRetryCount", "default_transient_retry_count"),
        serialization_alias="defaultTransientRetryCount",
    )
    retry_backoff_ms: int = Field(
        default=0,
        ge=0,
        le=60_000,
        validation_alias=AliasChoices("retryBackoffMs", "retry_backoff_ms"),
        serialization_alias="retryBackoffMs",
    )
    status_history_limit: int = Field(
        default=20,
        ge=1,
        le=500,
        validation_alias=AliasChoices("statusHistoryLimit", "status_history_limit"),
        serialization_alias="statusHistoryLimit",
    )


class EvolutionSandboxConfig(Base):
    """Read-only sandbox evaluation settings for governed evolution."""

    enabled: bool = True
    read_only_tools: list[str] = Field(
        default_factory=lambda: ["read_file", "glob", "grep"],
        validation_alias=AliasChoices("readOnlyTools", "read_only_tools"),
        serialization_alias="readOnlyTools",
    )
    timeout_seconds: int = Field(
        default=10,
        ge=1,
        le=120,
        validation_alias=AliasChoices("timeoutSeconds", "timeout_seconds"),
        serialization_alias="timeoutSeconds",
    )
    max_output_chars: int = Field(
        default=8000,
        ge=100,
        le=100_000,
        validation_alias=AliasChoices("maxOutputChars", "max_output_chars"),
        serialization_alias="maxOutputChars",
    )
    max_replay_samples: int = Field(
        default=3,
        ge=0,
        le=20,
        validation_alias=AliasChoices("maxReplaySamples", "max_replay_samples"),
        serialization_alias="maxReplaySamples",
    )
    cache_ttl_hours: int = Field(
        default=24,
        ge=0,
        le=168,
        validation_alias=AliasChoices("cacheTtlHours", "cache_ttl_hours"),
        serialization_alias="cacheTtlHours",
    )


class EvolutionTrialConfig(Base):
    """Isolated trial-mode settings for verified evolution artifacts."""

    enabled: bool = True
    isolated_workspace: bool = Field(
        default=True,
        validation_alias=AliasChoices("isolatedWorkspace", "isolated_workspace"),
        serialization_alias="isolatedWorkspace",
    )
    read_only_tools_only: bool = Field(
        default=True,
        validation_alias=AliasChoices("readOnlyToolsOnly", "read_only_tools_only"),
        serialization_alias="readOnlyToolsOnly",
    )
    blocked_tools: list[str] = Field(
        default_factory=lambda: ["write_file", "edit_file", "exec", "message", "cron", "spawn"],
        validation_alias=AliasChoices("blockedTools", "blocked_tools"),
        serialization_alias="blockedTools",
    )
    temp_dir: str = Field(
        default="",
        validation_alias=AliasChoices("tempDir", "temp_dir"),
        serialization_alias="tempDir",
    )
    max_step_output_chars: int = Field(
        default=2000,
        ge=100,
        le=100_000,
        validation_alias=AliasChoices("maxStepOutputChars", "max_step_output_chars"),
        serialization_alias="maxStepOutputChars",
    )
    max_retained_trial_logs: int = Field(
        default=10,
        ge=0,
        le=1000,
        validation_alias=AliasChoices("maxRetainedTrialLogs", "max_retained_trial_logs"),
        serialization_alias="maxRetainedTrialLogs",
    )
    trial_log_retention_days: int = Field(
        default=30,
        ge=1,
        le=3650,
        validation_alias=AliasChoices("trialLogRetentionDays", "trial_log_retention_days"),
        serialization_alias="trialLogRetentionDays",
    )


class EvolutionConfig(Base):
    """Governed self-evolution observability settings."""

    mode: Literal["conservative", "curated", "exploratory", "aggressive"] = "curated"
    ledger_backend: Literal["jsonl", "sqlite"] = Field(
        default="sqlite",
        validation_alias=AliasChoices("ledgerBackend", "ledger_backend"),
        serialization_alias="ledgerBackend",
    )
    allow_manual_override: bool = Field(
        default=False,
        validation_alias=AliasChoices("allowManualOverride", "allow_manual_override"),
        serialization_alias="allowManualOverride",
    )
    require_manual_approval: bool = Field(
        default=True,
        validation_alias=AliasChoices("requireManualApproval", "require_manual_approval"),
        serialization_alias="requireManualApproval",
    )
    dry_run: bool = Field(
        default=True,
        validation_alias=AliasChoices("dryRun", "dry_run"),
        serialization_alias="dryRun",
    )
    signal_retention_days: int = Field(
        default=30,
        ge=1,
        le=365,
        validation_alias=AliasChoices("signalRetentionDays", "signal_retention_days"),
        serialization_alias="signalRetentionDays",
    )
    outcome_retention_days: int = Field(
        default=90,
        ge=1,
        le=3650,
        validation_alias=AliasChoices("outcomeRetentionDays", "outcome_retention_days"),
        serialization_alias="outcomeRetentionDays",
    )
    outcome_archive_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("outcomeArchiveEnabled", "outcome_archive_enabled"),
        serialization_alias="outcomeArchiveEnabled",
    )
    dependency_stale_cleanup_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("dependencyStaleCleanupEnabled", "dependency_stale_cleanup_enabled"),
        serialization_alias="dependencyStaleCleanupEnabled",
    )
    health_history_retention_days: int = Field(
        default=90,
        ge=1,
        le=3650,
        validation_alias=AliasChoices("healthHistoryRetentionDays", "health_history_retention_days"),
        serialization_alias="healthHistoryRetentionDays",
    )
    max_health_history_snapshots: int = Field(
        default=100,
        ge=0,
        le=10_000,
        validation_alias=AliasChoices("maxHealthHistorySnapshots", "max_health_history_snapshots"),
        serialization_alias="maxHealthHistorySnapshots",
    )
    workflow_min_seen_count: int = Field(
        default=3,
        ge=1,
        le=100,
        validation_alias=AliasChoices("workflowMinSeenCount", "workflow_min_seen_count"),
        serialization_alias="workflowMinSeenCount",
    )
    workflow_min_evidence_sources: int = Field(
        default=2,
        ge=1,
        le=50,
        validation_alias=AliasChoices("workflowMinEvidenceSources", "workflow_min_evidence_sources"),
        serialization_alias="workflowMinEvidenceSources",
    )
    workflow_priority_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices("workflowPriorityThreshold", "workflow_priority_threshold"),
        serialization_alias="workflowPriorityThreshold",
    )
    max_high_score_signals: int = Field(
        default=5,
        ge=0,
        le=20,
        validation_alias=AliasChoices("maxHighScoreSignals", "max_high_score_signals"),
        serialization_alias="maxHighScoreSignals",
    )
    max_proposals_per_cycle: int = Field(
        default=3,
        ge=0,
        le=20,
        validation_alias=AliasChoices("maxProposalsPerCycle", "max_proposals_per_cycle"),
        serialization_alias="maxProposalsPerCycle",
    )
    static_gate_allowed_workflow_tools: list[str] = Field(
        default_factory=lambda: ["read_file", "glob", "grep", "web_fetch"],
        validation_alias=AliasChoices(
            "staticGateAllowedWorkflowTools",
            "static_gate_allowed_workflow_tools",
        ),
        serialization_alias="staticGateAllowedWorkflowTools",
    )
    static_gate_max_workflow_steps: int = Field(
        default=10,
        ge=1,
        le=25,
        validation_alias=AliasChoices("staticGateMaxWorkflowSteps", "static_gate_max_workflow_steps"),
        serialization_alias="staticGateMaxWorkflowSteps",
    )
    auto_verify_workflows: bool = Field(
        default=False,
        validation_alias=AliasChoices("autoVerifyWorkflows", "auto_verify_workflows"),
        serialization_alias="autoVerifyWorkflows",
    )
    workflow_auto_verify_threshold: float = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices("workflowAutoVerifyThreshold", "workflow_auto_verify_threshold"),
        serialization_alias="workflowAutoVerifyThreshold",
    )
    workflow_auto_verify_min_seen_count: int = Field(
        default=5,
        ge=1,
        le=100,
        validation_alias=AliasChoices("workflowAutoVerifyMinSeenCount", "workflow_auto_verify_min_seen_count"),
        serialization_alias="workflowAutoVerifyMinSeenCount",
    )
    skill_candidates_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("skillCandidatesEnabled", "skill_candidates_enabled"),
        serialization_alias="skillCandidatesEnabled",
    )
    skill_min_seen_count: int = Field(
        default=5,
        ge=1,
        le=100,
        validation_alias=AliasChoices("skillMinSeenCount", "skill_min_seen_count"),
        serialization_alias="skillMinSeenCount",
    )
    skill_priority_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices("skillPriorityThreshold", "skill_priority_threshold"),
        serialization_alias="skillPriorityThreshold",
    )
    static_gate_allowed_skill_tools: list[str] = Field(
        default_factory=lambda: ["read_file", "glob", "grep"],
        validation_alias=AliasChoices(
            "staticGateAllowedSkillTools",
            "static_gate_allowed_skill_tools",
        ),
        serialization_alias="staticGateAllowedSkillTools",
    )
    max_skill_proposals_per_cycle: int = Field(
        default=1,
        ge=0,
        le=10,
        validation_alias=AliasChoices("maxSkillProposalsPerCycle", "max_skill_proposals_per_cycle"),
        serialization_alias="maxSkillProposalsPerCycle",
    )
    sandbox: EvolutionSandboxConfig = Field(
        default_factory=EvolutionSandboxConfig,
        validation_alias=AliasChoices("sandbox"),
        serialization_alias="sandbox",
    )
    trial: EvolutionTrialConfig = Field(
        default_factory=EvolutionTrialConfig,
        validation_alias=AliasChoices("trial"),
        serialization_alias="trial",
    )
    feedback_calibration_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("feedbackCalibrationEnabled", "feedback_calibration_enabled"),
        serialization_alias="feedbackCalibrationEnabled",
    )
    feedback_reject_multiplier: float = Field(
        default=0.8,
        ge=0.05,
        le=1.0,
        validation_alias=AliasChoices("feedbackRejectMultiplier", "feedback_reject_multiplier"),
        serialization_alias="feedbackRejectMultiplier",
    )
    feedback_rollback_multiplier: float = Field(
        default=0.6,
        ge=0.05,
        le=1.0,
        validation_alias=AliasChoices("feedbackRollbackMultiplier", "feedback_rollback_multiplier"),
        serialization_alias="feedbackRollbackMultiplier",
    )
    feedback_rollback_delta: float = Field(
        default=-0.1,
        ge=-1.0,
        le=0.0,
        validation_alias=AliasChoices("feedbackRollbackDelta", "feedback_rollback_delta"),
        serialization_alias="feedbackRollbackDelta",
    )
    feedback_positive_multiplier: float = Field(
        default=1.02,
        ge=1.0,
        le=2.0,
        validation_alias=AliasChoices("feedbackPositiveMultiplier", "feedback_positive_multiplier"),
        serialization_alias="feedbackPositiveMultiplier",
    )
    feedback_suppress_after_negative_count: int = Field(
        default=3,
        ge=1,
        le=20,
        validation_alias=AliasChoices(
            "feedbackSuppressAfterNegativeCount",
            "feedback_suppress_after_negative_count",
        ),
        serialization_alias="feedbackSuppressAfterNegativeCount",
    )
    feedback_cooldown_days: int = Field(
        default=14,
        ge=1,
        le=365,
        validation_alias=AliasChoices("feedbackCooldownDays", "feedback_cooldown_days"),
        serialization_alias="feedbackCooldownDays",
    )
    feedback_trend_window_days: int = Field(
        default=14,
        ge=1,
        le=365,
        validation_alias=AliasChoices("feedbackTrendWindowDays", "feedback_trend_window_days"),
        serialization_alias="feedbackTrendWindowDays",
    )

class SubagentDefaultsConfig(Base):
    """Subagent delegation policy configuration."""

    mode: Literal["normal", "restricted"] = "normal"


class MetaCognitionConfig(Base):
    """Sidecar meta-cognition trigger collection configuration."""

    enabled: bool = True
    inner_monologue_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "innerMonologueEnabled",
            "inner_monologue_enabled",
        ),
        serialization_alias="innerMonologueEnabled",
    )
    trigger_collection_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "triggerCollectionEnabled",
            "trigger_collection_enabled",
        ),
        serialization_alias="triggerCollectionEnabled",
    )
    max_accepted_triggers_per_turn: int = Field(
        default=2,
        ge=1,
        le=20,
        validation_alias=AliasChoices(
            "maxAcceptedTriggersPerTurn",
            "max_accepted_triggers_per_turn",
        ),
        serialization_alias="maxAcceptedTriggersPerTurn",
    )
    session_cooldown_seconds: int = Field(
        default=300,
        ge=0,
        le=86_400,
        validation_alias=AliasChoices(
            "sessionCooldownSeconds",
            "session_cooldown_seconds",
        ),
        serialization_alias="sessionCooldownSeconds",
    )
    trigger_type_cooldown_seconds: int = Field(
        default=300,
        ge=0,
        le=86_400,
        validation_alias=AliasChoices(
            "triggerTypeCooldownSeconds",
            "trigger_type_cooldown_seconds",
        ),
        serialization_alias="triggerTypeCooldownSeconds",
    )
    queue_max_items: int = Field(
        default=200,
        ge=1,
        le=10_000,
        validation_alias=AliasChoices("queueMaxItems", "queue_max_items"),
        serialization_alias="queueMaxItems",
    )
    capture_user_correction_explicit_only: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "captureUserCorrectionExplicitOnly",
            "capture_user_correction_explicit_only",
        ),
        serialization_alias="captureUserCorrectionExplicitOnly",
    )
    capture_task_completion_from_complete_goal_only: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "captureTaskCompletionFromCompleteGoalOnly",
            "capture_task_completion_from_complete_goal_only",
        ),
        serialization_alias="captureTaskCompletionFromCompleteGoalOnly",
    )
    structured_reflection_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "structuredReflectionEnabled",
            "structured_reflection_enabled",
        ),
        serialization_alias="structuredReflectionEnabled",
    )
    working_memory_bridge_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "workingMemoryBridgeEnabled",
            "working_memory_bridge_enabled",
        ),
        serialization_alias="workingMemoryBridgeEnabled",
    )
    memory_candidate_bridge_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "memoryCandidateBridgeEnabled",
            "memory_candidate_bridge_enabled",
        ),
        serialization_alias="memoryCandidateBridgeEnabled",
    )
    memory_candidate_min_confidence: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices(
            "memoryCandidateMinConfidence",
            "memory_candidate_min_confidence",
        ),
        serialization_alias="memoryCandidateMinConfidence",
    )
    pattern_consolidation_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "patternConsolidationEnabled",
            "pattern_consolidation_enabled",
        ),
        serialization_alias="patternConsolidationEnabled",
    )
    evolution_bridge_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "evolutionBridgeEnabled",
            "evolution_bridge_enabled",
        ),
        serialization_alias="evolutionBridgeEnabled",
    )
    pattern_window_days: int = Field(
        default=14,
        ge=1,
        le=365,
        validation_alias=AliasChoices("patternWindowDays", "pattern_window_days"),
        serialization_alias="patternWindowDays",
    )
    pattern_window_max_reflections: int = Field(
        default=200,
        ge=1,
        le=5000,
        validation_alias=AliasChoices(
            "patternWindowMaxReflections",
            "pattern_window_max_reflections",
        ),
        serialization_alias="patternWindowMaxReflections",
    )
    pattern_min_frequency: int = Field(
        default=3,
        ge=1,
        le=100,
        validation_alias=AliasChoices("patternMinFrequency", "pattern_min_frequency"),
        serialization_alias="patternMinFrequency",
    )
    pattern_min_distinct_turns: int = Field(
        default=2,
        ge=1,
        le=100,
        validation_alias=AliasChoices("patternMinDistinctTurns", "pattern_min_distinct_turns"),
        serialization_alias="patternMinDistinctTurns",
    )
    pattern_max_example_refs: int = Field(
        default=5,
        ge=1,
        le=20,
        validation_alias=AliasChoices("patternMaxExampleRefs", "pattern_max_example_refs"),
        serialization_alias="patternMaxExampleRefs",
    )
    signal_max_evidence_refs: int = Field(
        default=6,
        ge=1,
        le=20,
        validation_alias=AliasChoices("signalMaxEvidenceRefs", "signal_max_evidence_refs"),
        serialization_alias="signalMaxEvidenceRefs",
    )
    max_signal_upserts_per_turn: int = Field(
        default=1,
        ge=1,
        le=10,
        validation_alias=AliasChoices("maxSignalUpsertsPerTurn", "max_signal_upserts_per_turn"),
        serialization_alias="maxSignalUpsertsPerTurn",
    )
    allowed_evolution_target_types: tuple[str, ...] = Field(
        default=("workflow_candidate", "skill_candidate"),
        validation_alias=AliasChoices(
            "allowedEvolutionTargetTypes",
            "allowed_evolution_target_types",
        ),
        serialization_alias="allowedEvolutionTargetTypes",
    )
    thought_substrate_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "thoughtSubstrateEnabled",
            "thought_substrate_enabled",
        ),
        serialization_alias="thoughtSubstrateEnabled",
    )
    thought_substrate_max_frames_per_session: int = Field(
        default=500,
        ge=10,
        le=10_000,
        validation_alias=AliasChoices(
            "thoughtSubstrateMaxFramesPerSession",
            "thought_substrate_max_frames_per_session",
        ),
        serialization_alias="thoughtSubstrateMaxFramesPerSession",
    )
    thought_substrate_sampling_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices(
            "thoughtSubstrateSamplingRate",
            "thought_substrate_sampling_rate",
        ),
        serialization_alias="thoughtSubstrateSamplingRate",
    )
    perception_fusion_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "perceptionFusionEnabled",
            "perception_fusion_enabled",
        ),
        serialization_alias="perceptionFusionEnabled",
    )
    regulator_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "regulatorEnabled",
            "regulator_enabled",
        ),
        serialization_alias="regulatorEnabled",
    )
    skill_bootstrapper_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "skillBootstrapperEnabled",
            "skill_bootstrapper_enabled",
        ),
        serialization_alias="skillBootstrapperEnabled",
    )
    skill_bootstrapper_min_repeats: int = Field(
        default=3,
        ge=2,
        le=100,
        validation_alias=AliasChoices(
            "skillBootstrapperMinRepeats",
            "skill_bootstrapper_min_repeats",
        ),
        serialization_alias="skillBootstrapperMinRepeats",
    )
    skill_bootstrapper_min_confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices(
            "skillBootstrapperMinConfidence",
            "skill_bootstrapper_min_confidence",
        ),
        serialization_alias="skillBootstrapperMinConfidence",
    )


class LearningConfig(Base):
    """Agent self-improvement and review configuration."""

    background_review: BackgroundReviewConfig = Field(
        default_factory=BackgroundReviewConfig,
        validation_alias=AliasChoices("backgroundReview", "background_review"),
        serialization_alias="backgroundReview",
    )
    curator: CuratorConfig = Field(
        default_factory=CuratorConfig,
        validation_alias=AliasChoices("curator"),
        serialization_alias="curator",
    )
    evolution: EvolutionConfig = Field(
        default_factory=EvolutionConfig,
        validation_alias=AliasChoices("evolution"),
        serialization_alias="evolution",
    )
    meta_cognition: MetaCognitionConfig = Field(
        default_factory=MetaCognitionConfig,
        validation_alias=AliasChoices("metaCognition", "meta_cognition"),
        serialization_alias="metaCognition",
    )


class AgentDefaults(Base):
    """Default agent configuration."""

    workspace: str = "~/.originagent/workspace"
    model: str = "deepseek-chat"
    model_preset: str | None = Field(
        default=None,
        validation_alias=AliasChoices("modelPreset", "model_preset"),
        serialization_alias="modelPreset",
    )
    provider: str = (
        "deepseek"  # Provider name (e.g. "deepseek", "openrouter") or "auto" for auto-detection
    )
    max_tokens: int = 8192
    context_window_tokens: int = 65_536
    context_block_limit: int | None = None
    temperature: float = 0.1
    max_tool_iterations: int = 200
    max_concurrent_subagents: int = Field(default=1, ge=1)
    max_tool_result_chars: int = 16_000
    provider_retry_mode: Literal["standard", "persistent"] = "standard"
    tool_hint_max_length: int = Field(
        default=40,
        ge=20,
        le=500,
        validation_alias=AliasChoices("toolHintMaxLength"),
        serialization_alias="toolHintMaxLength",
    )  # Max characters for tool hint display (e.g. "$ cd …/project && npm test")
    reasoning_effort: str | None = None  # low / medium / high / adaptive / none — LLM thinking effort; None preserves the provider default
    fallback_models: list[FallbackCandidate] = Field(default_factory=list)
    auxiliary: AuxiliaryConfig = Field(default_factory=AuxiliaryConfig)
    domain_packs: DomainPacksConfig = Field(
        default_factory=DomainPacksConfig,
        validation_alias=AliasChoices("domainPacks", "domain_packs"),
        serialization_alias="domainPacks",
    )
    robot_g1: RobotG1Config = Field(
        default_factory=RobotG1Config,
        validation_alias=AliasChoices("robotG1", "robot_g1"),
        serialization_alias="robotG1",
    )
    subagent_policy: SubagentDefaultsConfig = Field(
        default_factory=SubagentDefaultsConfig,
        validation_alias=AliasChoices("subagentPolicy", "subagent_policy"),
        serialization_alias="subagentPolicy",
    )
    learning: LearningConfig = Field(default_factory=LearningConfig)
    timezone: str = "Asia/Shanghai"  # IANA timezone, e.g. "Asia/Shanghai", "America/New_York"
    output_language: str | None = Field(
        default=None,
        validation_alias=AliasChoices("outputLanguage", "output_language"),
        serialization_alias="outputLanguage",
    )  # Agent 产出语言（BCP-47 标签，如 "zh-CN"、"en"、"ja"）；None 表示交由 LLM 自决
    bot_name: str = "OriginAgent"  # Display name shown in CLI prompts (e.g. "{name} is thinking...")
    bot_icon: str = "OA"  # Short icon (emoji or text) shown next to the bot name in CLI; "" to omit
    unified_session: bool = False  # Share one session across all channels (single-user multi-device)
    disabled_skills: list[str] = Field(default_factory=list)  # Skill names to exclude from loading (e.g. ["summarize", "skill-creator"])
    session_ttl_minutes: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("idleCompactAfterMinutes", "sessionTtlMinutes"),
        serialization_alias="idleCompactAfterMinutes",
    )  # Auto-compact idle threshold in minutes (0 = disabled)
    cold_archive_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("coldArchiveEnabled", "cold_archive_enabled"),
        serialization_alias="coldArchiveEnabled",
    )  # Preserve trimmed persisted session messages in a local cold archive.
    max_messages: int = Field(
        default=120,
        ge=0,
    )  # Max messages to replay from session history (0 = use default 120, respects token budget)
    allow_agent_initiated_messages: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "allowAgentInitiatedMessages",
            "allow_agent_initiated_messages",
        ),
        serialization_alias="allowAgentInitiatedMessages",
    )
    enable_backend_cognition: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "enableBackendCognition",
            "enable_backend_cognition",
        ),
        serialization_alias="enableBackendCognition",
    )
    active_intent_interval_seconds: int = Field(
        default=15,
        ge=5,
        le=3600,
        validation_alias=AliasChoices(
            "activeIntentIntervalSeconds",
            "active_intent_interval_seconds",
        ),
        serialization_alias="activeIntentIntervalSeconds",
    )
    active_intent_session_cooldown_seconds: int = Field(
        default=600,
        ge=0,
        le=86400,
        validation_alias=AliasChoices(
            "activeIntentSessionCooldownSeconds",
            "active_intent_session_cooldown_seconds",
        ),
        serialization_alias="activeIntentSessionCooldownSeconds",
    )
    active_intent_intent_cooldown_seconds: int = Field(
        default=300,
        ge=0,
        le=86400,
        validation_alias=AliasChoices(
            "activeIntentIntentCooldownSeconds",
            "active_intent_intent_cooldown_seconds",
        ),
        serialization_alias="activeIntentIntentCooldownSeconds",
    )
    active_intent_max_messages_per_session_per_pass: int = Field(
        default=1,
        ge=1,
        le=10,
        validation_alias=AliasChoices(
            "activeIntentMaxMessagesPerSessionPerPass",
            "active_intent_max_messages_per_session_per_pass",
        ),
        serialization_alias="activeIntentMaxMessagesPerSessionPerPass",
    )
    consolidation_ratio: float = Field(
        default=0.5,
        ge=0.1,
        le=0.95,
        validation_alias=AliasChoices("consolidationRatio"),
        serialization_alias="consolidationRatio",
    )  # Consolidation target ratio (0.5 = 50% of budget retained after compression)
    context: ContextConfig = Field(default_factory=ContextConfig)
    confirmation: ConfirmationConfig = Field(default_factory=ConfirmationConfig)
    task_runtime: TaskRuntimeConfig = Field(
        default_factory=TaskRuntimeConfig,
        validation_alias=AliasChoices("taskRuntime", "task_runtime"),
        serialization_alias="taskRuntime",
    )
    dream: DreamConfig = Field(default_factory=DreamConfig)
    nearline_memory: NearlineMemoryConfig = Field(
        default_factory=NearlineMemoryConfig,
        validation_alias=AliasChoices("nearlineMemory", "nearline_memory"),
        serialization_alias="nearlineMemory",
    )


class AgentsConfig(Base):
    """Agent configuration."""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(Base):
    """LLM provider configuration."""

    api_key: str | None = None
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)
    extra_body: dict[str, Any] | None = None  # Extra fields merged into every request body


class BedrockProviderConfig(ProviderConfig):
    """AWS Bedrock Runtime provider configuration."""

    region: str | None = None  # AWS region, falls back to AWS_REGION/AWS_DEFAULT_REGION/profile
    profile: str | None = None  # Optional AWS shared config profile


class ProvidersConfig(Base):
    """Configuration for LLM providers."""

    custom: ProviderConfig = Field(default_factory=ProviderConfig)  # Any OpenAI-compatible endpoint
    azure_openai: ProviderConfig = Field(default_factory=ProviderConfig)  # Azure OpenAI (model = deployment name)
    bedrock: BedrockProviderConfig = Field(default_factory=BedrockProviderConfig)  # AWS Bedrock Converse
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    huggingface: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    ollama: ProviderConfig = Field(default_factory=ProviderConfig)  # Ollama local models
    lm_studio: ProviderConfig = Field(default_factory=ProviderConfig)  # LM Studio local models
    ovms: ProviderConfig = Field(default_factory=ProviderConfig)  # OpenVINO Model Server (OVMS)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax_anthropic: ProviderConfig = Field(default_factory=ProviderConfig)  # MiniMax Anthropic endpoint (thinking)
    mistral: ProviderConfig = Field(default_factory=ProviderConfig)
    stepfun: ProviderConfig = Field(default_factory=ProviderConfig)  # Step Fun (阶跃星辰)
    xiaomi_mimo: ProviderConfig = Field(default_factory=ProviderConfig)  # Xiaomi MIMO (小米)
    longcat: ProviderConfig = Field(default_factory=ProviderConfig)  # LongCat
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)  # AiHubMix API gateway
    siliconflow: ProviderConfig = Field(default_factory=ProviderConfig)  # SiliconFlow (硅基流动)
    volcengine: ProviderConfig = Field(default_factory=ProviderConfig)  # VolcEngine (火山引擎)
    volcengine_coding_plan: ProviderConfig = Field(default_factory=ProviderConfig)  # VolcEngine Coding Plan
    byteplus: ProviderConfig = Field(default_factory=ProviderConfig)  # BytePlus (VolcEngine international)
    byteplus_coding_plan: ProviderConfig = Field(default_factory=ProviderConfig)  # BytePlus Coding Plan
    openai_codex: ProviderConfig = Field(default_factory=ProviderConfig, exclude=True)  # OpenAI Codex (OAuth)
    github_copilot: ProviderConfig = Field(default_factory=ProviderConfig, exclude=True)  # Github Copilot (OAuth)
    qianfan: ProviderConfig = Field(default_factory=ProviderConfig)  # Qianfan (百度千帆)
    nvidia: ProviderConfig = Field(default_factory=ProviderConfig)  # NVIDIA NIM (nvapi- keys)
    atomic_chat: ProviderConfig = Field(default_factory=ProviderConfig)  # Atomic Chat local models


class HeartbeatConfig(Base):
    """Heartbeat service configuration."""

    enabled: bool = True
    interval_s: int = 30 * 60  # 30 minutes
    keep_recent_messages: int = 8


class BDIConfig(Base):
    """BDI Deliberation Engine configuration."""

    enabled: bool = True
    interval_s: int = Field(
        default=120,
        ge=30,
        le=86_400,
        description="Seconds between deliberation cycles (default 2 min)",
    )
    model_override: str | None = Field(
        default=None,
        validation_alias=AliasChoices("modelOverride", "model", "model_override"),
        description="Optional model override for deliberation (defaults to main agent model)",
    )
    max_desires_per_cycle: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Max desires to evaluate in one cycle",
    )
    auto_create_from_foresight: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "autoCreateFromForesight",
            "auto_create_from_foresight",
        ),
        serialization_alias="autoCreateFromForesight",
        description="Auto-create Desires from ForesightRecords during deliberation",
    )
    notify_on_intention: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "notifyOnIntention",
            "notify_on_intention",
        ),
        serialization_alias="notifyOnIntention",
        description="Notify user via their primary channel when intentions are formed",
    )


class TenantChannelBinding(Base):
    """Map a channel+sender pair to a tenant."""
    channel: str                                    # "telegram", "websocket", "cli"
    sender_id: str                                  # raw sender from channel
    label: str = ""                                 # "爸爸的手机", "姐姐的手表"


class TenantConfig(Base):
    """A person in the household."""
    tenant_id: str                                  # "dad", "mom", "sister"
    display_name: str = ""                          # "爸爸", "妈妈", "姐姐"
    bindings: list[TenantChannelBinding] = Field(default_factory=list)
    bdi_enabled: bool = True
    permissions: dict[str, bool] = Field(default_factory=lambda: {
        "exec": False, "write_files": False, "device_control": True,
    })


class TenantsConfig(Base):
    """All tenants known to this OriginAgent instance."""
    tenants: list[TenantConfig] = Field(default_factory=list)
    default_tenant_id: str = ""                     # fallback for unmatched senders
    guest_tenant_enabled: bool = True               # unmatched → "guest" tenant?


class SpeakerRecognitionConfig(Base):
    """Reserved interface for speaker recognition (voice/face)."""
    enabled: bool = False
    plugin: str = ""                                # fully qualified Python class path
    config: dict[str, Any] = Field(default_factory=dict)
    confidence_threshold: float = 0.7


class ApiConfig(Base):
    """OpenAI-compatible API server configuration."""

    host: str = "127.0.0.1"  # Safer default: local-only bind.
    port: int = 8900
    timeout: float = 120.0  # Per-request timeout in seconds.


class GatewayConfig(Base):
    """Gateway/server configuration."""

    host: str = "127.0.0.1"  # Safer default: local-only bind.
    port: int = 18790
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    bdi: BDIConfig = Field(default_factory=BDIConfig)
    tenants: TenantsConfig = Field(default_factory=TenantsConfig)
    speaker_recognition: SpeakerRecognitionConfig = Field(
        default_factory=SpeakerRecognitionConfig
    )
    tiered_router: TieredRouterConfig = Field(default_factory=TieredRouterConfig)


RuntimeProfile = Literal["default", "safe", "household_safe", "local_dev", "automation"]


class RuntimeConfig(Base):
    """Runtime profile configuration."""

    profile: RuntimeProfile = "default"


class PairingConfig(Base):
    """Opt-in DM pairing for approving channel senders."""

    enabled: bool = False
    ttl_seconds: int = Field(
        default=600,
        ge=60,
        le=86_400,
        validation_alias=AliasChoices("ttlSeconds", "ttl_seconds"),
        serialization_alias="ttlSeconds",
    )
    allow_self_approve: bool = Field(
        default=False,
        validation_alias=AliasChoices("allowSelfApprove", "allow_self_approve"),
        serialization_alias="allowSelfApprove",
    )
    approval_channels: list[str] = Field(
        default_factory=lambda: ["cli", "websocket"],
        validation_alias=AliasChoices("approvalChannels", "approval_channels"),
        serialization_alias="approvalChannels",
    )


class SecurityConfig(Base):
    """Security-related runtime controls."""

    pairing: PairingConfig = Field(default_factory=PairingConfig)


class WebSearchConfig(Base):
    """Web search tool configuration."""

    provider: str = "duckduckgo"  # brave, tavily, duckduckgo, searxng, jina, kagi, olostep
    api_key: str = ""
    base_url: str = ""  # SearXNG base URL
    max_results: int = 5
    timeout: int = 30  # Wall-clock timeout (seconds) for search operations

    # ── Intelligent search features ──────────────────────────────────────
    # All are enabled by default; set to False to disable individual features.

    # Query Reformulation: LLM generates 3-5 query variants for higher recall.
    query_reformulation_enabled: bool = True
    reformulation_variants: int = Field(default=3, ge=2, le=5)

    # Relevance Filtering: removes low-quality / irrelevant results.
    relevance_filtering_enabled: bool = True
    # Minimum RRF score fraction (0.0-1.0) to keep a result.
    # Lower = more results kept; higher = stricter filter.
    relevance_min_score: float = Field(default=0.05, ge=0.0, le=1.0)

    # Multi-Source Fusion: search through multiple providers in parallel.
    multi_source_enabled: bool = True
    # Provider names to use in multi-source mode. Empty = use primary only.
    # e.g. ["brave", "tavily", "jina"] — each falls back to duckduckgo if
    # its API key is missing, just like the single-provider path.
    search_providers: list[str] = Field(default_factory=list)

    # Search Planning: LLM decomposes complex queries into sub-queries.
    search_planning_enabled: bool = True

    # Context-Aware Search: LLM enriches queries with session context.
    context_aware_enabled: bool = True

    # Execution limits
    parallel_search_limit: int = Field(default=4, ge=1, le=10)
    max_merged_results: int = Field(default=10, ge=1, le=20)
    # RRF constant (k). Standard value is 60.
    rrf_k: int = Field(default=60, ge=1)


class WebFetchConfig(Base):
    """Web fetch tool configuration."""

    use_jina_reader: bool = True


class SessionSearchConfig(Base):
    """Local session_search retrieval configuration."""

    enabled: bool = True
    backend: Literal["auto", "literal", "sqlite_fts"] = "auto"
    semantic_enabled: bool = True
    max_tool_refresh_ms: int = Field(
        default=500,
        ge=0,
        le=10_000,
        validation_alias=AliasChoices("maxToolRefreshMs", "max_tool_refresh_ms"),
        serialization_alias="maxToolRefreshMs",
    )
    rebuild_on_start: bool = Field(
        default=False,
        validation_alias=AliasChoices("rebuildOnStart", "rebuild_on_start"),
        serialization_alias="rebuildOnStart",
    )


class WebToolsConfig(Base):
    """Web tools configuration."""

    enable: bool = True
    proxy: str | None = (
        None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    )
    user_agent: str | None = None
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    fetch: WebFetchConfig = Field(default_factory=WebFetchConfig)


class ContentReadToolConfig(Base):
    """Platform-aware content_read tool configuration."""

    enabled: bool = False
    providers: list[str] = Field(
        default_factory=lambda: ["generic", "rss", "github", "hackernews"]
    )
    max_chars: int = Field(default=50_000, ge=100)
    use_jina_reader: bool = True
    rss_entry_limit: int = Field(default=10, ge=1, le=50)
    hackernews_comment_limit: int = Field(default=20, ge=0, le=100)


class ExecToolConfig(Base):
    """Shell exec tool configuration."""

    enable: bool = True
    profile: Literal["secure", "local_dev", "disabled"] = "local_dev"
    allow_unsafe_exec: bool = True
    shell_syntax_policy: Literal["restricted", "shell"] = "restricted"
    timeout: int = 60
    path_append: str = ""
    sandbox: str = ""  # sandbox backend: "" (none) or "bwrap"
    allowed_env_keys: list[str] = Field(default_factory=list)  # Env var names to pass through to subprocess (e.g. ["GOPATH", "JAVA_HOME"])
    allow_patterns: list[str] = Field(default_factory=list)  # Regex patterns that bypass deny_patterns (e.g. [r"rm\s+-rf\s+/tmp/"])
    deny_patterns: list[str] = Field(default_factory=list)  # Extra regex patterns to block (appended to built-in list)

    @model_validator(mode="after")
    def _normalize_unsafe_exec(self) -> "ExecToolConfig":
        if self.profile != "local_dev":
            self.allow_unsafe_exec = False
        return self

class MCPServerConfig(Base):
    """MCP server connection configuration (stdio or HTTP)."""

    type: Literal["stdio", "sse", "streamableHttp"] | None = None  # auto-detected if omitted
    command: str = ""  # Stdio: command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: extra env vars
    url: str = ""  # HTTP/SSE: endpoint URL
    headers: dict[str, str] = Field(default_factory=dict)  # HTTP/SSE: custom headers
    tool_timeout: int = 30  # seconds before a tool call is cancelled
    enabled_tools: list[str] = Field(default_factory=lambda: ["*"])  # Only register these tools; accepts raw MCP names or wrapped mcp_<server>_<tool> names; ["*"] = all tools; [] = no tools

class MyToolConfig(Base):
    """Self-inspection tool configuration."""

    enable: bool = True  # register the `my` tool (agent runtime state inspection)
    allow_set: bool = False  # let `my` modify loop state (read-only if False)


class ImageGenerationToolConfig(Base):
    """Image generation tool configuration."""

    enabled: bool = False
    provider: str = "openrouter"
    model: str = "openai/gpt-5.4-image-2"
    default_aspect_ratio: str = "1:1"
    default_image_size: str = "1K"
    max_images_per_turn: int = Field(default=4, ge=1, le=8)
    save_dir: str = "generated"


class DeviceToolsConfig(Base):
    """Real-world device tool gateway configuration."""

    enabled: bool = False
    lighting_enabled: bool = False
    mode: Literal["dry_run", "real"] = "dry_run"
    backend: Literal["none", "fake", "lighting_client"] = "none"
    automation_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("automationEnabled", "automation_enabled"),
        serialization_alias="automationEnabled",
    )
    automation_allowed_domains: list[str] = Field(
        default_factory=lambda: ["lighting"],
        validation_alias=AliasChoices("automationAllowedDomains", "automation_allowed_domains"),
        serialization_alias="automationAllowedDomains",
    )
    automation_max_actions_per_pass: int = Field(
        default=1,
        ge=1,
        le=8,
        validation_alias=AliasChoices("automationMaxActionsPerPass", "automation_max_actions_per_pass"),
        serialization_alias="automationMaxActionsPerPass",
    )
    automation_dry_run_only: bool = Field(
        default=True,
        validation_alias=AliasChoices("automationDryRunOnly", "automation_dry_run_only"),
        serialization_alias="automationDryRunOnly",
    )
    real_execution_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("realExecutionEnabled", "real_execution_enabled"),
        serialization_alias="realExecutionEnabled",
    )
    lighting_client_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("lightingClientEndpoint", "lighting_client_endpoint"),
        serialization_alias="lightingClientEndpoint",
    )
    lighting_client_timeout_seconds: int = Field(
        default=5,
        ge=1,
        le=60,
        validation_alias=AliasChoices("lightingClientTimeoutSeconds", "lighting_client_timeout_seconds"),
        serialization_alias="lightingClientTimeoutSeconds",
    )


class ToolAuditConfig(Base):
    """Privacy-preserving tool call audit configuration."""

    mode: Literal["off", "minimal", "security"] = "minimal"
    security_tools: tuple[str, ...] = (
        "exec",
        "message",
        "web_fetch",
        "content_read",
        "cron",
        "spawn",
        "originagent_device_*",
        "mcp_*",
    )
    security_on_policy_denial: bool = True


class ToolsConfig(Base):
    """Tools configuration."""

    class LocalAwarenessCameraConfig(Base):
        enabled: bool = False
        require_confirmation: bool = True
        save_dir: str = "uploads/perception"
        max_frames_per_call: int = Field(default=1, ge=1, le=8)
        device_id: str | None = None

    class LocalAwarenessScreenConfig(Base):
        enabled: bool = False
        require_confirmation: bool = True
        save_dir: str = "uploads/perception"
        screen_id: str | None = None

    class LocalAwarenessAudioConfig(Base):
        input_enabled: bool = False
        output_enabled: bool = False
        require_confirmation: bool = True
        save_dir: str = "uploads/perception"
        max_record_seconds: int = Field(default=5, ge=1, le=60)
        device_id: str | None = None
        voice: str | None = None
        transcription_enabled: bool = Field(
            default=False,
            validation_alias=AliasChoices("transcriptionEnabled", "transcription_enabled"),
            serialization_alias="transcriptionEnabled",
        )
        transcription_provider: str | None = Field(
            default=None,
            validation_alias=AliasChoices("transcriptionProvider", "transcription_provider"),
            serialization_alias="transcriptionProvider",
        )
        tts_enabled: bool = Field(
            default=False,
            validation_alias=AliasChoices("ttsEnabled", "tts_enabled"),
            serialization_alias="ttsEnabled",
        )

    class VoicePipelineConfig(Base):
        """Voice pipeline runtime configuration.

        Separate from LocalAwarenessAudioConfig (which feeds the WebUI
        settings panel). This controls the voice processing behavior:
        provider selection, audio parameters, and response mode.
        """
        enabled: bool = False
        stt_provider: str = "volcengine"
        tts_provider: str = "volcengine"
        tts_voice: str = "zh_female_santong"
        tts_sample_rate: int = Field(default=24000, ge=8000, le=48000)
        auto_play_response: bool = True
        voice_activity_timeout_ms: int = Field(default=1500, ge=100, le=10000)
        max_record_seconds: int = Field(default=60, ge=1, le=300)

    class LocalAwarenessMediaInspectionConfig(Base):
        enabled: bool = True

    class LocalAwarenessMediaConfig(Base):
        enabled: bool = False
        workspace_roots: list[str] = Field(
            default_factory=lambda: ["uploads/perception"],
            validation_alias=AliasChoices("workspaceRoots", "workspace_roots"),
            serialization_alias="workspaceRoots",
        )
        max_files: int = Field(
            default=100,
            ge=1,
            le=5000,
            validation_alias=AliasChoices("maxFiles", "max_files"),
            serialization_alias="maxFiles",
        )
        max_file_bytes: int = Field(
            default=10 * 1024 * 1024,
            ge=1,
            validation_alias=AliasChoices("maxFileBytes", "max_file_bytes"),
            serialization_alias="maxFileBytes",
        )
        auto_inspect_after_capture: bool = Field(
            default=False,
            validation_alias=AliasChoices("autoInspectAfterCapture", "auto_inspect_after_capture"),
            serialization_alias="autoInspectAfterCapture",
        )
        supported_mime_types: list[str] = Field(
            default_factory=lambda: ["image/png", "image/jpeg", "image/webp", "audio/wav", "audio/mpeg", "audio/mp4"],
            validation_alias=AliasChoices("supportedMimeTypes", "supported_mime_types"),
            serialization_alias="supportedMimeTypes",
        )

    class LocalAwarenessHardwareDiscoveryConfig(Base):
        enabled: bool = True
        posture: Literal["strong"] = "strong"
        allowed_cidrs: list[str] = Field(default_factory=list)
        max_hosts: int = Field(default=256, ge=1, le=4096)
        max_concurrency: int = Field(default=64, ge=1, le=256)
        timeout_ms: int = Field(default=500, ge=50, le=5000)
        active_probe_enabled: bool = True
        service_ports: list[int] = Field(
            default_factory=lambda: [22, 53, 80, 443, 445, 554, 1883, 1900, 5353, 5683, 8008, 8080, 8123, 8883, 9000]
        )

    class LocalAwarenessConfig(Base):
        enabled: bool = True
        device_discovery_enabled: bool = True
        lan_discovery_enabled: bool = True
        camera: "ToolsConfig.LocalAwarenessCameraConfig" = Field(
            default_factory=lambda: ToolsConfig.LocalAwarenessCameraConfig()
        )
        screen: "ToolsConfig.LocalAwarenessScreenConfig" = Field(
            default_factory=lambda: ToolsConfig.LocalAwarenessScreenConfig()
        )
        audio: "ToolsConfig.LocalAwarenessAudioConfig" = Field(
            default_factory=lambda: ToolsConfig.LocalAwarenessAudioConfig()
        )
        media_inspection: "ToolsConfig.LocalAwarenessMediaInspectionConfig" = Field(
            default_factory=lambda: ToolsConfig.LocalAwarenessMediaInspectionConfig()
        )
        media: "ToolsConfig.LocalAwarenessMediaConfig" = Field(
            default_factory=lambda: ToolsConfig.LocalAwarenessMediaConfig()
        )
        hardware_discovery: "ToolsConfig.LocalAwarenessHardwareDiscoveryConfig" = Field(
            default_factory=lambda: ToolsConfig.LocalAwarenessHardwareDiscoveryConfig(),
            validation_alias=AliasChoices("hardwareDiscovery", "hardware_discovery"),
            serialization_alias="hardwareDiscovery",
        )

    session_search: SessionSearchConfig = Field(
        default_factory=SessionSearchConfig,
        validation_alias=AliasChoices("sessionSearch", "session_search"),
        serialization_alias="sessionSearch",
    )
    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    content_read: ContentReadToolConfig = Field(default_factory=ContentReadToolConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    my: MyToolConfig = Field(default_factory=MyToolConfig)
    image_generation: ImageGenerationToolConfig = Field(default_factory=ImageGenerationToolConfig)
    device: DeviceToolsConfig = Field(default_factory=DeviceToolsConfig)
    audit: ToolAuditConfig = Field(default_factory=ToolAuditConfig)
    local_awareness: "ToolsConfig.LocalAwarenessConfig" = Field(
        default_factory=lambda: ToolsConfig.LocalAwarenessConfig(),
        validation_alias=AliasChoices("localAwareness", "local_awareness"),
        serialization_alias="localAwareness",
    )
    voice: "ToolsConfig.VoicePipelineConfig" = Field(
        default_factory=lambda: ToolsConfig.VoicePipelineConfig()
    )
    restrict_to_workspace: bool = False  # restrict all tool access to workspace directory
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    ssrf_whitelist: list[str] = Field(default_factory=list)  # CIDR ranges to exempt from SSRF blocking (e.g. ["100.64.0.0/10"] for Tailscale)


class StorageConfig(Base):
    """Storage backend configuration.

    ``jsonl_fallback_enabled`` controls the JSONL cold-backup dual-write path.
    SQLite is the primary source of truth (WAL mode, busy_timeout=10000,
    synchronous=NORMAL — see ``storage/sqlite_helpers.py``). When this flag is
    ``False`` (production default), stores write ONLY to SQLite. When ``True``,
    stores additionally append a buffered-I/O JSONL copy as an emergency
    rollback artifact.

    收口时间表 (rule 25): JSONL fallback is retained solely as a safety net
    during the SQLite cutover window. Planned full removal: after two full
    release cycles of stable production operation without any SQLite write
    failures that required JSONL recovery. Track in the tech-debt board.
    """

    jsonl_fallback_enabled: bool = False


class TimeoutConfig(Base):
    """Centralized timeout configuration (spec 3.4, rule 17).

    Replaces scattered inline magic values (10.0/15.0/20.0/30.0/300 etc.)
    with a single source of truth so the same operation class uses the
    same timeout across files.
    """

    http_download_timeout: float = Field(
        default=30.0,
        ge=1.0,
        validation_alias=AliasChoices("httpDownloadTimeout", "http_download_timeout"),
        serialization_alias="httpDownloadTimeout",
    )  # HTTP download/stream timeout (channels: telegram, email, msteams, mochat)
    http_api_timeout: float = Field(
        default=30.0,
        ge=1.0,
        validation_alias=AliasChoices("httpApiTimeout", "http_api_timeout"),
        serialization_alias="httpApiTimeout",
    )  # HTTP API call timeout (agent/tools/web.py search & fetch calls)
    db_busy_timeout: int = Field(
        default=5000,
        ge=0,
        validation_alias=AliasChoices("dbBusyTimeout", "db_busy_timeout"),
        serialization_alias="dbBusyTimeout",
    )  # SQLite PRAGMA busy_timeout in milliseconds
    tool_default_timeout: float = Field(
        default=30.0,
        ge=1.0,
        validation_alias=AliasChoices("toolDefaultTimeout", "tool_default_timeout"),
        serialization_alias="toolDefaultTimeout",
    )  # Default tool execution timeout (gateway tool dispatch, pending queue wait)


class Config(BaseSettings):
    """Root configuration for OriginAgent."""

    # Schema version for migration tracking (spec 3.15, rule 2). v0 = legacy
    # unversioned configs; bumped on each breaking schema change so loader.py
    # can run the forward migration chain. See MIGRATIONS in config/loader.py.
    config_version: int = 1
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    timeouts: TimeoutConfig = Field(
        default_factory=TimeoutConfig,
        validation_alias=AliasChoices("timeouts"),
        serialization_alias="timeouts",
    )
    model_presets: dict[str, ModelPresetConfig] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("modelPresets", "model_presets"),
        serialization_alias="modelPresets",
    )

    @model_validator(mode="after")
    def _validate_model_presets(self) -> "Config":
        if "default" in self.model_presets:
            raise ValueError("model_presets must not define reserved preset 'default'")
        name = self.agents.defaults.model_preset
        if name and name != "default" and name not in self.model_presets:
            raise ValueError(f"model_preset {name!r} not found in model_presets")
        for fallback in self.agents.defaults.fallback_models:
            if isinstance(fallback, str) and fallback != "default" and fallback not in self.model_presets:
                raise ValueError(f"fallback_models entry {fallback!r} not found in model_presets")
        for preset_name, preset in self.model_presets.items():
            for fallback in preset.fallback_models:
                if isinstance(fallback, str) and fallback != "default" and fallback not in self.model_presets:
                    raise ValueError(
                        f"model_presets.{preset_name}.fallback_models entry {fallback!r} not found in model_presets"
                    )
        for task_name, task in self.agents.defaults.auxiliary.tasks.items():
            for fallback in task.fallback_models:
                if isinstance(fallback, str) and fallback != "default" and fallback not in self.model_presets:
                    raise ValueError(
                        f"auxiliary.tasks.{task_name}.fallback_models entry {fallback!r} not found in model_presets"
                    )
        return self

    def resolve_default_preset(self) -> ModelPresetConfig:
        defaults = self.agents.defaults
        return ModelPresetConfig(
            model=defaults.model,
            provider=defaults.provider,
            max_tokens=defaults.max_tokens,
            context_window_tokens=defaults.context_window_tokens,
            temperature=defaults.temperature,
            reasoning_effort=defaults.reasoning_effort,
            fallback_models=defaults.fallback_models,
        )

    def resolve_preset(self, name: str | None = None) -> ModelPresetConfig:
        name = name or self.agents.defaults.model_preset or "default"
        if name == "default":
            return self.resolve_default_preset()
        if name not in self.model_presets:
            raise KeyError(f"model_preset {name!r} not found in model_presets")
        return self.model_presets[name]

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path."""
        return Path(self.agents.defaults.workspace).expanduser()

    def _match_provider(
        self, model: str | None = None
    ) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config and its registry name. Returns (config, spec_name)."""
        from OriginAgent.providers.match import best_provider_match
        from OriginAgent.providers.registry import PROVIDERS, find_by_name

        forced = self.agents.defaults.provider
        if forced != "auto":
            spec = find_by_name(forced)
            if spec:
                p = getattr(self.providers, spec.name, None)
                if p and (spec.is_oauth or spec.is_local or spec.is_direct or p.api_key):
                    return p, spec.name
            # Forced provider not available; fall through to auto-detection
            # rather than returning None (preserves backward compat).

        model_to_match = model or self.agents.defaults.model

        def _get_provider_config(name: str):
            return getattr(self.providers, name, None)

        result = best_provider_match(model_to_match, _get_provider_config, PROVIDERS)
        if result is not None:
            config, spec, name = result
            return config, name

        # Final fallback: any configured (non-OAuth) provider
        for spec in PROVIDERS:
            if spec.is_oauth:
                continue
            p = getattr(self.providers, spec.name, None)
            if p and p.api_key:
                return p, spec.name
        return None, None

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """Get matched provider config (api_key, api_base, extra_headers). Falls back to first available."""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """Get the registry name of the matched provider (e.g. "deepseek", "openrouter")."""
        _, name = self._match_provider(model)
        return name

    def get_api_key(self, model: str | None = None) -> str | None:
        """Get API key for the given model. Falls back to first available key."""
        p = self.get_provider(model)
        return p.api_key if p else None

    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for the given model, falling back to the provider default when present."""
        from OriginAgent.providers.registry import find_by_name

        p, name = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        if name:
            spec = find_by_name(name)
            if spec and spec.default_api_base:
                return spec.default_api_base
        return None

    model_config = ConfigDict(env_prefix="ORIGINAGENT_", env_nested_delimiter="__")
