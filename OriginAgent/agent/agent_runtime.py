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

    # ── Cognitive ───────────────────────────────────────────────

    def _build_cognitive_runtime_context(self, session_key: str) -> Any:
        channel, chat_id = (
            session_key.split(":", 1)
            if ":" in session_key
            else ("cli", session_key)
        )
        from OriginAgent.bus.events import InboundMessage
        from OriginAgent.agent.message_metadata import build_origin_metadata

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
            msg, channel=channel, chat_id=chat_id, session_key=session_key,
        )

    def _collect_cognitive_candidates(self, session_key: str) -> list[dict]:
        d = self._deps
        items: list[dict] = []
        for candidate in d.active_intents.collect_candidates(session_key):
            items.append({
                "event": self._candidate_to_cognitive_event(session_key, {
                    "kind": "active_intent",
                    "candidate": candidate,
                    "cooldown_key": candidate.intent_id,
                    "message": d.active_intents.build_message(session_key, candidate),
                }),
                "message": d.active_intents.build_message(session_key, candidate),
                "cooldown_key": candidate.intent_id,
                "working_memory_attention": candidate.summary or candidate.content,
                "working_memory_question": (
                    "Should this pending item be confirmed now?"
                    if candidate.intent_type == "pending_confirmation_nudge"
                    else None
                ),
                "raw_candidate": candidate,
            })
        for record in d.reminder_store.list_due():
            if record.session_key != session_key:
                continue
            content = (
                "Scheduled reminder follow-up: a previously scheduled reminder is now due.\n"
                f"Reminder: {record.content}\n"
                "If helpful, continue from this due reminder and keep the follow-up bounded."
            )
            from OriginAgent.bus.events import InboundMessage
            from OriginAgent.agent.cognitive_events import CognitiveEvent
            from OriginAgent.agent.message_metadata import build_origin_metadata

            message = InboundMessage(
                channel="system", sender_id="agent_cognitive",
                chat_id=record.chat_id or session_key, content=content,
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
                    is_inferred=True, confidence=0.8,
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
                payload={"due_at": record.due_at, "channel": record.channel, "chat_id": record.chat_id},
            )
            items.append({
                "event": event, "message": message,
                "cooldown_key": event.event_id,
                "working_memory_attention": record.content,
                "working_memory_question": "Is this reminder still relevant and ready to act on?",
                "raw_candidate": record,
            })
        priority_order = {"high": 0, "medium": 1, "low": 2}
        items.sort(key=lambda item: (priority_order.get(item["event"].priority, 9), item["event"].created_at))
        return items

    def _candidate_to_cognitive_event(self, session_key: str, item: Any) -> Any:
        from OriginAgent.agent.cognitive_events import CognitiveEvent
        if isinstance(item, dict) and isinstance(item.get("event"), CognitiveEvent):
            return item["event"]
        candidate = item.get("candidate") if isinstance(item, dict) and "candidate" in item else item
        priority = "medium"
        if getattr(candidate, "intent_type", "") in {"pending_confirmation_nudge", "goal_nudge"}:
            priority = "high"
        from OriginAgent.agent.cognitive_events import CognitiveEvent
        return CognitiveEvent(
            event_id=str(getattr(candidate, "intent_id", "")),
            session_key=session_key,
            event_type=str(getattr(candidate, "intent_type", "goal_nudge")),
            source_type=str(getattr(candidate, "source_type", "active_intent")),
            source_reference=str(getattr(candidate, "source_reference", "")),
            summary=_trim_text(getattr(candidate, "summary", "") or getattr(candidate, "content", ""), max_chars=160),
            priority=priority,
            payload={"content": str(getattr(candidate, "content", ""))},
        )

    def _write_cognitive_event_to_working_memory(
        self, session: Any, *, runtime_context: Any, event: Any,
    ) -> bool:
        d = self._deps
        written = False
        if event.summary:
            d.working_memory.append_attention_item(session, event.summary, identity=runtime_context.identity)
            written = True
        if event.event_type in {"pending_confirmation_nudge", "scheduled_reminder"}:
            question = (
                "Should this pending confirmation be resolved now?"
                if event.event_type == "pending_confirmation_nudge"
                else "Is this due reminder still relevant and ready to act on?"
            )
            d.working_memory.append_pending_question(session, question, identity=runtime_context.identity)
            written = True
        if written:
            d.sessions.save(session)
        return written

    # ── Tool approval ───────────────────────────────────────────

    def _consume_tool_approval_reply(
        self,
        *,
        session_key: str,
        actor_id: str | None,
        reply: str,
    ) -> tuple:
        d = self._deps
        confirmation = d.confirmation_manager.latest_pending_tool_approval(session_key) if d.confirmation_manager else None
        if confirmation is None:
            return None, False
        from OriginAgent.agent.confirmation import classify_confirmation_reply
        classification = classify_confirmation_reply(reply)
        if classification not in {"confirmed", "rejected"}:
            return None, False
        result = d.confirmation_manager.resolve_user_reply(confirmation.confirmation_id, reply)
        tool_name = confirmation.metadata.get("tool_name") or confirmation.action or "tool"
        if result.decision == "confirmed":
            from OriginAgent.security.grants import issue_tool_approval_grant
            grant = issue_tool_approval_grant(confirmation, d.grant_store, approved_by=actor_id)
            return (("tool_approval",
                     f"Tool approval confirmed for {tool_name}. "
                     f"Short-lived grant {grant.grant_id} is active for this session. "
                     "Continue the pending task using the newly approved capability."), True)
        if result.decision == "rejected":
            return (("tool_approval",
                     f"Tool approval was rejected for {tool_name}. Do not use that capability unless the user asks again."), True)
        return None, False

    # ── WebUI helpers ───────────────────────────────────────────

    @staticmethod
    def _is_webui_message(msg: Any) -> bool:
        return msg.channel == "websocket" and msg.metadata.get("webui") is True

    def _append_webui_command_transcript(self, msg: Any, content: str) -> None:
        if not self._is_webui_message(msg):
            return
        from OriginAgent.utils.webui_transcript import append_transcript_object
        try:
            append_transcript_object(
                f"websocket:{msg.chat_id}",
                {"event": "message", "chat_id": msg.chat_id, "text": content},
            )
        except (TypeError, ValueError, OSError) as e:
            logger.warning("webui command transcript append failed: {}", e)

    # ── Turn persistence ────────────────────────────────────────

    def _sanitize_persisted_blocks(
        self, content: list[dict], *, should_truncate_text: bool = False, drop_runtime: bool = False,
    ) -> list[dict]:
        from OriginAgent.agent.agent_turn_persist import TurnPersistManager
        max_chars = self._deps.max_tool_result_chars
        return TurnPersistManager(max_chars, self._deps.sessions).sanitize_persisted_blocks(
            content, should_truncate_text=should_truncate_text, drop_runtime=drop_runtime,
        )

    def _save_turn(self, session: Any, messages: list[dict], skip: int) -> None:
        from OriginAgent.agent.agent_turn_persist import TurnPersistManager
        max_chars = self._deps.max_tool_result_chars
        TurnPersistManager(max_chars, self._deps.sessions).save_turn(session, messages, skip)

    def _persist_subagent_followup(self, session: Any, msg: Any) -> bool:
        from OriginAgent.agent.agent_turn_persist import TurnPersistManager
        max_chars = self._deps.max_tool_result_chars
        return TurnPersistManager(max_chars, self._deps.sessions).persist_subagent_followup(session, msg)

    # ── Continuity / Checkpoint ──────────────────────────────────

    @staticmethod
    def _pending_confirmation_ref(confirmation: Any) -> dict:
        return {
            "confirmation_id": str(getattr(confirmation, "confirmation_id", "") or "").strip(),
            "scope": str(getattr(confirmation, "scope", "") or "").strip(),
            "status": str(getattr(confirmation, "status", "") or "").strip(),
            "risk": str(getattr(confirmation, "risk", "") or "").strip(),
            "updated_at": str(getattr(confirmation, "consumed_at", None) or getattr(confirmation, "created_at", None) or "").strip(),
        }

    def _collect_pending_confirmation_refs(self, session: Any) -> list[dict]:
        d = self._deps
        refs: list[dict] = []
        try:
            confirmations = d.confirmation_store.read_all()
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

    def _save_continuity_checkpoint(self, session: Any, *, runtime_context: Any = None) -> dict:
        d = self._deps
        working = d.working_memory.load(session, identity=runtime_context.identity if runtime_context is not None else None)
        profile_ref = None
        try:
            profiles = d.nearline_memory.store.read_profiles(limit=1) if d.nearline_memory else []
            if profiles:
                profile = profiles[-1]
                profile_ref = {"profile_id": profile.profile_id, "updated_at": profile.updated_at}
        except Exception:
            profile_ref = None
        checkpoint = {
            "session_key": session.key,
            "current_goal": working.current_goal,
            "current_plan": list(working.current_plan or []),
            "open_loops": list(working.open_loops or []),
            "active_constraints": list(working.active_constraints or []),
            "pending_confirmation_refs": self._collect_pending_confirmation_refs(session),
            "updated_at": _utcnow_iso(),
        }
        session.metadata.setdefault("continuity_checkpoint_v1", checkpoint)
        return checkpoint

    @staticmethod
    def _load_continuity_checkpoint(session: Any) -> dict | None:
        raw = session.metadata.get("continuity_checkpoint_v1")
        if not isinstance(raw, dict):
            return None
        return {
            "session_key": str(raw.get("session_key") or session.key),
            "current_goal": _trim_text(raw.get("current_goal"), max_chars=1000),
            "current_plan": [str(item).strip() for item in raw.get("current_plan", []) if str(item).strip()][:8],
            "open_loops": [str(item).strip() for item in raw.get("open_loops", []) if str(item).strip()][:8],
            "active_constraints": [str(item).strip() for item in raw.get("active_constraints", []) if str(item).strip()][:8],
            "pending_confirmation_refs": [dict(item) for item in raw.get("pending_confirmation_refs", []) if isinstance(item, dict)][:8],
            "updated_at": str(raw.get("updated_at") or "").strip(),
        }

    # ── Post-turn effects ───────────────────────────────────────

    @staticmethod
    def _nearline_turn_completed_successfully(ctx: Any) -> bool:
        if ctx.stop_reason in {"ask_user", "error", "tool_error", "max_iterations", "empty_final_response"}:
            return False
        return bool((ctx.final_content or "").strip())

    def _schedule_background_review(self, ctx: Any) -> None:
        d = self._deps
        if ctx.session is None:
            return
        d.background_review.refresh_config()
        if not d.background_review.enabled:
            return
        if ctx.stop_reason in {"ask_user", "error", "tool_error"}:
            return
        if ctx.msg.channel == "system" or ctx.msg.sender_id == "subagent":
            return
        if not (ctx.final_content or "").strip():
            return
        max_recent = int(getattr(d.background_review.config, "max_recent_messages", 12) or 12)
        messages = [dict(m) for m in ctx.session.messages if not m.get("_command")][-max_recent:]
        d.host.schedule_background(
            d.background_review.review_turn(
                session_key=ctx.session_key, turn_id=ctx.turn_id,
                channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
                message_id=ctx.msg.metadata.get("message_id"),
                messages=messages,
            )
        )

    def _schedule_curator_review(self, ctx: Any) -> None:
        d = self._deps
        if ctx.session is None:
            return
        d.curator.refresh_config()
        if not d.curator.enabled:
            return
        if ctx.stop_reason in {"ask_user", "error", "tool_error"}:
            return
        if ctx.msg.channel == "system" or ctx.msg.sender_id == "subagent":
            return
        if not (ctx.final_content or "").strip():
            return
        d.host.schedule_background(
            d.curator.review_workspace(session_key=ctx.session_key, turn_id=ctx.turn_id)
        )

    def _schedule_nearline_memory(self, ctx: Any) -> None:
        d = self._deps
        if ctx.session is None:
            return
        service = d.nearline_memory
        if service is None or not getattr(service, "enabled", False):
            return
        if not self._nearline_turn_completed_successfully(ctx):
            return
        actor_id = (ctx.runtime_context.actor_id
                    if ctx.runtime_context is not None and getattr(ctx.runtime_context, "actor_id", None)
                    else "user")
        d.host.schedule_background(
            service.process_turn(
                session=ctx.session, channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
                actor_id=actor_id, turn_id=ctx.turn_id,
            )
        )

    # ── Outbound assembly ───────────────────────────────────────

    def _assemble_outbound(
        self,
        msg: Any,
        final_content: str,
        all_msgs: list[dict],
        stop_reason: str,
        had_injections: bool,
        generated_media: list[str],
        on_stream: Any = None,
    ) -> Any | None:
        """Assemble the final outbound message from turn results."""
        from OriginAgent.agent.tools.message import MessageTool
        from OriginAgent.agent.tools.ask import ask_user_options_from_messages, ask_user_outbound
        from OriginAgent.bus.events import OutboundMessage
        from OriginAgent.session.goal_state import goal_state_ws_blob

        d = self._deps
        if (mt := d.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
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
                d.sessions.get_or_create(self._effective_session_key(msg)).metadata
            )

        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=content, media=generated_media,
            metadata=meta, buttons=buttons,
        )

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

    # ── Meta-cognition ──────────────────────────────────────────

    def _record_meta_trigger(self, trigger: Any, *, turn_id: str | None = None) -> None:
        d = self._deps
        if d.meta_coordinator is None:
            return
        result = d.meta_coordinator.record_trigger(trigger, turn_id=turn_id)
        if result is not None and result.accepted and trigger.trigger_type == "user_correction":
            self._maybe_apply_meta_fast_path(trigger)

    def _scan_meta_triggers_for_turn(self, ctx: Any) -> None:
        d = self._deps
        runtime = d.meta_cognition_runtime
        if runtime is None:
            return
        try:
            d.meta_coordinator.reset_fast_path()
            fusion = d.perception_fusion
            from OriginAgent.agent.meta_cognition_triggers import (
                bridge_runtime_event_to_trigger,
                build_user_correction_trigger,
                latest_assistant_message,
            )
            if fusion is not None and fusion.enabled:
                _re = fusion.bridge_user_message(session_key=ctx.session_key, text=ctx.msg.content)
                if _re is not None:
                    _t = bridge_runtime_event_to_trigger(_re)
                    if _t is not None:
                        self._record_meta_trigger(_t, turn_id=ctx.turn_id)
            else:
                trigger = build_user_correction_trigger(
                    session_key=ctx.session_key, user_message=ctx.msg.content,
                    last_assistant_message=latest_assistant_message(ctx.all_messages),
                )
                if trigger is not None:
                    self._record_meta_trigger(trigger, turn_id=ctx.turn_id)
            d.meta_coordinator.runtime_reset_turn(ctx.turn_id)
        except Exception:
            logger.debug("Meta-cognition turn-end scan failed", exc_info=True)

    def _maybe_apply_meta_fast_path(self, trigger: Any) -> None:
        d = self._deps
        session_key = str(trigger.session_key or "").strip()
        if not session_key or trigger.trigger_type != "user_correction":
            return
        runtime_context = d.state_holder.get(session_key).last_runtime_context
        try:
            session = d.sessions.get_or_create(session_key)
            snapshot = d.working_memory.load(
                session, identity=getattr(runtime_context, "identity", None) if runtime_context is not None else None,
            )
            payload = trigger.payload if isinstance(trigger.payload, dict) else {}
            preview = _trim_text(payload.get("user_message_preview"), max_chars=160)
            text = f"user_correction: {preview or 'correction recorded'}".strip()
            if text in list(getattr(snapshot, "attention_items", []) or []):
                d.meta_coordinator.add_fast_path_ref(trigger.source_reference)
                d.meta_coordinator.record_fast_path_decision("fast_path_duplicate_skipped")
                return
            d.working_memory.append_attention_item(
                session, text,
                identity=getattr(runtime_context, "identity", None) if runtime_context is not None else None,
            )
            d.meta_coordinator.add_fast_path_ref(trigger.source_reference)
            d.meta_coordinator.record_fast_path_decision("fast_path_working_memory_written")
        except Exception:
            logger.debug("Meta-cognition fast path failed", exc_info=True)

    def _schedule_meta_cognition_reflection(self, ctx: Any) -> None:
        from OriginAgent.agent.meta_cognition_triggers import latest_assistant_message
        d = self._deps
        if d.meta_cognition_reflector is None or d.meta_cognition_runtime is None:
            return
        if ctx.session is None:
            return
        if ctx.stop_reason in {"ask_user", "error", "tool_error", "system", "subagent"}:
            return
        if ctx.msg.channel == "system" or ctx.msg.sender_id == "subagent":
            return
        if not (ctx.final_content or "").strip():
            return
        accepted = d.meta_cognition_runtime.take_accepted_triggers_for_turn(ctx.turn_id)
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
            object.__setattr__(ctx.runtime_context, "meta_cognition_fast_path_refs",
                               set(d.meta_coordinator.fast_path_refs))
        d.host.schedule_background(
            self._reflect_meta_cognition_turn(
                session_key=ctx.session_key, turn_id=ctx.turn_id,
                turn_snapshot=snapshot, accepted_triggers=accepted,
                runtime_context=ctx.runtime_context,
            )
        )

    async def _reflect_meta_cognition_turn(
        self, *, session_key: str, turn_id: str, turn_snapshot: dict,
        accepted_triggers: list, runtime_context: Any = None,
    ) -> None:
        d = self._deps
        reflector = d.meta_cognition_reflector
        if reflector is None:
            return
        result = await reflector.reflect_turn(
            session_key=session_key, turn_id=turn_id,
            turn_snapshot=turn_snapshot, accepted_triggers=accepted_triggers,
            runtime_context=runtime_context,
        )
        d.meta_coordinator.on_reflection_complete(reflector)
        logger.debug("Meta cognition reflection finished for turn {} with status {} ({})",
                     turn_id, result.status, result.reason)

    def _meta_world_summary_preview(self, ctx: Any) -> str:
        d = self._deps
        session = ctx.session
        if session is None:
            return ""
        world_state = d.world_state
        if world_state is None:
            return ""
        try:
            snapshot = world_state.load(session, identity=ctx.runtime_context if ctx.runtime_context is not None else None)
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

    def _meta_runtime_context_summary(self, runtime_context: Any = None) -> dict:
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
