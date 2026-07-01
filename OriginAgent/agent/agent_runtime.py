"""AgentRuntime — stateless message router extracted from AgentLoop.

Receives all context (session_key, session, messages, etc.) as explicit
method parameters.  Depends on SessionStateHolder for session-scoped
scratchpad and on AgentHost for infrastructure lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from loguru import logger


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim_text(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


@dataclass(frozen=True)
class RuntimeDependencies:
    """Immutable dependency bundle for AgentRuntime.

    All dependencies are injected at construction time.  AgentRuntime
    never reaches back to AgentLoop — every method receives context
    explicitly.  Fields are added incrementally as methods are moved.
    """

    # Infrastructure
    state_holder: Any  # SessionStateHolder
    host: Any  # AgentHost

    # Core services (Task 3)
    tools: Any = None  # ToolRegistry
    provider: Any = None  # LLMProvider
    runner: Any = None  # AgentRunner
    context: Any = None  # ContextBuilder
    sessions: Any = None  # SessionManager
    bus: Any = None  # MessageBus
    workspace: Any = None  # Path

    # Sub-services
    subagents: Any = None
    working_memory: Any = None
    commands: Any = None
    action_planner: Any = None
    active_intents: Any = None
    reminder_store: Any = None
    world_state: Any = None

    # Config
    model: str | None = None
    max_iterations: int = 10
    context_window_tokens: int = 0
    context_block_limit: int = 0
    max_tool_result_chars: int = 0
    provider_retry_mode: str = "standard"
    tool_hint_max_length: int | None = None
    tools_config: Any = None
    web_config: Any = None
    exec_config: Any = None
    restrict_to_workspace: bool = False
    unified_session: bool = False
    runtime_profile: str = "default"
    consolidation_ratio: float = 0.5
    domain_packs: Any = None
    max_messages: int = 120

    # Background services
    background_review: Any = None
    curator: Any = None
    nearline_memory: Any = None
    session_search_index: Any = None
    consolidator: Any = None
    dream: Any = None
    cognitive_loop: Any = None
    cognitive_scheduler: Any = None
    cognitive_audit: Any = None
    session_cold_archive: Any = None
    rolling_episode_compaction: Any = None
    memory_governance: Any = None
    auto_compact: Any = None

    # Meta-cognition
    meta_cognition_runtime: Any = None
    meta_cognition_reflector: Any = None
    meta_cognition_regulator: Any = None
    meta_cognition_config: Any = None
    meta_coordinator: Any = None
    perception_fusion: Any = None

    # Stores
    file_state_store: Any = None
    confirmation_store: Any = None
    confirmation_manager: Any = None
    grant_store: Any = None
    cron_service: Any = None

    # Misc
    introspection: Any = None
    actor_resolver: Any = None
    auxiliary_router: Any = None
    tool_audit_config: Any = None
    evolution_config: Any = None
    extra_hooks: list | None = None
    pending_queues: dict | None = None
    runtime_model_publisher: Any = None
    model_presets: dict | None = None
    model_preset: str | None = None
    provider_snapshot_loader: Any = None
    preset_snapshot_loader: Any = None
    provider_signature: Any = None
    domain_runtime_overrides: dict | None = None
    domain_runtime_contributions: list | None = None
    bdi_engine: Any = None


class AgentRuntime:
    """Stateless message router — processes inbound messages through
    the turn pipeline and returns outbound responses.

    Every public method receives ``session_key`` and other context
    explicitly.  No mutable state is stored on ``self`` beyond the
    injected ``_deps``.
    """

    def __init__(self, deps: RuntimeDependencies) -> None:
        self._deps = deps

    @property
    def deps(self) -> RuntimeDependencies:
        return self._deps

    # ── Leaf helpers (moved from AgentLoop) ──────────────────────

    def _sync_subagent_runtime_limits(self) -> None:
        """Keep subagent runtime limits aligned with mutable loop settings."""
        if self._deps.subagents is not None:
            self._deps.subagents.max_iterations = self._deps.max_iterations

    def _effective_session_key(self, msg: Any) -> str:
        """Return the session key used for task routing and mid-turn injections."""
        if self._deps.unified_session and not getattr(msg, "session_key_override", None):
            return "unified:default"
        return msg.session_key

    def _replay_token_budget(self) -> int:
        """Derive a token budget for session history replay from the context window."""
        if self._deps.context_window_tokens <= 0:
            return 0
        provider = self._deps.provider
        max_output = getattr(getattr(provider, "generation", None), "max_tokens", 4096)
        try:
            reserved_output = int(max_output)
        except (TypeError, ValueError):
            reserved_output = 4096
        budget = self._deps.context_window_tokens - max(1, reserved_output) - 1024
        return budget if budget > 0 else max(128, self._deps.context_window_tokens // 2)

    def _tool_hint(self, tool_calls: list) -> str:
        """Format tool calls as concise hints with smart abbreviation."""
        from OriginAgent.utils.tool_hints import format_tool_hints
        return format_tool_hints(tool_calls, max_length=self._deps.tool_hint_max_length)

    def _set_tool_context(
        self, channel: str, chat_id: str,
        message_id: str | None = None, metadata: dict | None = None,
        session_key: str | None = None,
        actor_id: str | None = None,
        trigger: str | None = None,
        capability_snapshot: Any = None,
        runtime_context: Any = None,
        turn_id: str | None = None,
    ) -> None:
        """Update context for all tools that need routing info."""
        from OriginAgent.agent.agent_runtime_context import (
            set_tool_context as set_tools_runtime_context,
        )
        _cap_snapshot = capability_snapshot  # turn-scoped, passed in
        set_tools_runtime_context(
            self._deps.tools,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
            metadata=metadata,
            session_key=session_key,
            actor_id=actor_id,
            trigger=trigger,
            capability_snapshot=_cap_snapshot,
            runtime_context=runtime_context,
            unified_session=self._deps.unified_session,
            unified_session_key="unified:default",
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
    def _runtime_chat_id(msg: Any) -> str:
        """Return the chat id shown in runtime metadata for the model."""
        from OriginAgent.agent.agent_runtime_context import runtime_chat_id
        return runtime_chat_id(msg)

    def _resolve_runtime_context(
        self,
        msg: Any,
        *,
        channel: str | None = None,
        chat_id: str | None = None,
        session_key: str | None = None,
    ) -> Any:
        """Resolve runtime context for a message."""
        if self._deps.actor_resolver is not None:
            return self._deps.actor_resolver.resolve_runtime_context(
                channel=msg.channel,
                chat_id=msg.chat_id,
                sender_id=msg.sender_id,
                metadata=msg.metadata or {},
                session_key=session_key,
                routing_channel=channel,
                routing_chat_id=chat_id,
            )
        return None

    @staticmethod
    def _snapshot_for_trigger(trigger: str | None) -> Any:
        """Return a capability snapshot for the given trigger."""
        from OriginAgent.agent.agent_runtime_context import snapshot_for_trigger
        return snapshot_for_trigger(trigger)
