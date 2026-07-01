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

    # ── Core turn pipeline ───────────────────────────────────────

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Any = None,
        on_stream: Any = None,
        on_stream_end: Any = None,
        on_retry_wait: Any = None,
        *,
        session: Any = None,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        metadata: dict | None = None,
        session_key: str | None = None,
        pending_queue: Any = None,
        actor_id: str | None = None,
        trigger: str | None = None,
        capability_snapshot: Any = None,
        # ── injected callbacks ────────────────────────────────
        checkpoint_cb: Any = None,
        set_current_iteration: Any = None,
    ) -> tuple:
        """Run the agent iteration loop (stateless — all context via parameters).

        Returns (final_content, tools_used, messages, stop_reason, had_injections).
        """
        import asyncio as _asyncio
        from contextlib import suppress

        from OriginAgent.agent.agent_runtime_context import snapshot_for_trigger
        from OriginAgent.agent.error_classifier import ClassifiedError, ErrorKind, user_facing_message
        from OriginAgent.agent.hook import AgentHook, CompositeHook
        from OriginAgent.agent.progress_hook import AgentProgressHook
        from OriginAgent.agent.runner import _MAX_INJECTIONS_PER_TURN, AgentRunSpec
        from OriginAgent.agent.tools.file_state import bind_file_states, reset_file_states
        from OriginAgent.bus.events import InboundMessage
        from OriginAgent.session.goal_state import runner_wall_llm_timeout_s
        from OriginAgent.utils.document import extract_documents

        _SENSITIVE_NAMES: frozenset[str] = frozenset({"exec", "message", "web_fetch"})
        _SENSITIVE_PREFIXES: tuple[str, ...] = ("originagent_device_",)

        d = self._deps
        self._sync_subagent_runtime_limits()

        _cap_snapshot = capability_snapshot or snapshot_for_trigger(trigger)
        if hasattr(d.tools, "set_capability_snapshot"):
            d.tools.set_capability_snapshot(_cap_snapshot)

        _current_iteration = 0

        def _on_iteration(iteration: int) -> None:
            nonlocal _current_iteration
            _current_iteration = iteration
            if set_current_iteration is not None:
                set_current_iteration(iteration)

        loop_hook = AgentProgressHook(
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
            metadata=metadata,
            session_key=session_key,
            tool_hint_max_length=d.tool_hint_max_length,
            set_tool_context=self._set_tool_context,
            on_iteration=_on_iteration,
            actor_id=actor_id,
            trigger=trigger,
            capability_snapshot=_cap_snapshot,
            sensitive_tool_log_names=_SENSITIVE_NAMES,
            sensitive_tool_log_prefixes=_SENSITIVE_PREFIXES,
        )
        hook: AgentHook = (
            CompositeHook([loop_hook] + (d.extra_hooks or []))
            if d.extra_hooks else loop_hook
        )

        async def _checkpoint(payload: dict) -> None:
            if session is None:
                return
            if checkpoint_cb is not None:
                await checkpoint_cb(session, payload)

        async def _drain_pending(*, limit: int = _MAX_INJECTIONS_PER_TURN) -> list[dict]:
            if pending_queue is None:
                return []

            def _to_user_message(pending_msg: InboundMessage) -> dict:
                content = pending_msg.content
                media = pending_msg.media if pending_msg.media else None
                if media:
                    content, media = extract_documents(content, media)
                    media = media or None
                runtime_block = d.context.build_runtime_context_block(
                    pending_msg.channel,
                    self._runtime_chat_id(pending_msg),
                    d.context.timezone,
                )
                if (pending_msg.sender_id == "subagent"
                        or pending_msg.metadata.get("injected_event") == "subagent_result"):
                    merged = [runtime_block, d.context.build_internal_event_block("subagent_result", content)]
                elif pending_msg.metadata.get("injected_event") == "active_intent":
                    merged = [runtime_block, d.context.build_internal_event_block("active_intent", content)]
                else:
                    merged = [runtime_block, *d.context._build_user_content(content, media)]
                return {"role": "user", "content": merged}

            items: list[dict] = []
            while len(items) < limit:
                try:
                    items.append(_to_user_message(pending_queue.get_nowait()))
                except _asyncio.QueueEmpty:
                    break
            if (not items and session is not None and d.subagents is not None
                    and d.subagents.get_running_count_by_session(session.key) > 0):
                try:
                    msg = await _asyncio.wait_for(pending_queue.get(), timeout=300)
                except _asyncio.TimeoutError:
                    logger.warning("Timeout waiting for sub-agent completion in session {}", session.key)
                    return items
                items.append(_to_user_message(msg))
                while len(items) < limit:
                    try:
                        items.append(_to_user_message(pending_queue.get_nowait()))
                    except _asyncio.QueueEmpty:
                        break
            return items

        active_session_key = session.key if session else session_key
        file_state_token = bind_file_states(d.file_state_store.for_session(active_session_key))
        try:
            result = await d.runner.run(AgentRunSpec(
                initial_messages=initial_messages,
                tools=d.tools,
                model=d.model,
                max_iterations=d.max_iterations,
                max_tool_result_chars=d.max_tool_result_chars,
                hook=hook,
                error_message=user_facing_message(
                    ClassifiedError(kind=ErrorKind.INTERNAL, technical_detail="agent loop error", retryable=False)
                ),
                concurrent_tools=True,
                tool_concurrency_limit=getattr(d.tools_config, "tool_concurrency_limit", None) if d.tools_config else None,
                workspace=d.workspace,
                session_key=session.key if session else None,
                context_window_tokens=d.context_window_tokens,
                context_block_limit=d.context_block_limit,
                provider_retry_mode=d.provider_retry_mode,
                progress_callback=on_progress,
                stream_progress_deltas=on_stream is not None,
                retry_wait_callback=on_retry_wait,
                checkpoint_callback=_checkpoint,
                injection_callback=_drain_pending,
                llm_timeout_s=runner_wall_llm_timeout_s(
                    d.sessions, session_key,
                    metadata=session.metadata if session is not None else None,
                ),
            ))
        finally:
            reset_file_states(file_state_token)

        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", d.max_iterations)
            if on_stream and on_stream_end:
                await on_stream(result.final_content or "")
                await on_stream_end(resuming=False)
        elif result.stop_reason == "error":
            logger.error("LLM returned error: {}", (result.final_content or "")[:200])
        return result.final_content, result.tools_used, result.messages, result.stop_reason, result.had_injections
