from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from loguru import logger

from OriginAgent.agent.agent_turn_pipeline import TurnContext, TurnState
from OriginAgent.agent.identity import RuntimeContext
from OriginAgent.agent.services import AgentServiceContainer
from OriginAgent.agent.tools.ask import ask_user_options_from_messages, ask_user_outbound
from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.security.capabilities import CapabilitySnapshot
from OriginAgent.session.goal_state import goal_state_ws_blob
from OriginAgent.session.manager import Session


@dataclass(frozen=True)
class SystemTurnLoopContext:
    restore_runtime_checkpoint: Callable[[Session], bool]
    restore_pending_user_turn: Callable[[Session], bool]
    persist_subagent_followup: Callable[[Session, InboundMessage], bool]
    resolve_runtime_context: Callable[..., RuntimeContext]
    snapshot_for_trigger: Callable[[str | None], CapabilitySnapshot]
    update_working_memory_from_turn: Callable[..., None]
    set_tool_context: Callable[..., None]
    replay_token_budget: Callable[[], int]
    snapshot_context_assembly_from_messages: Callable[..., dict[str, Any]]
    run_agent_loop: Callable[..., Awaitable[tuple[str | None, list[str], list[dict[str, Any]], str, bool]]]
    save_turn: Callable[[Session, list[dict[str, Any]], int], None]
    archive_session_file_cap: Callable[..., None]
    clear_runtime_checkpoint: Callable[[Session], None]
    schedule_background: Callable[[Awaitable[Any]], None]
    schedule_nearline_memory: Callable[[TurnContext], None]
    get_max_messages: Callable[[], int]
    get_context_window_tokens: Callable[[], int]
    record_runtime_context: Callable[[str, RuntimeContext], None]
    record_continuity_session_key: Callable[[str], None]
    record_context_assembly: Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class SystemTurnHandlerDeps:
    services: AgentServiceContainer
    loop_context: SystemTurnLoopContext


class SystemTurnHandler:
    """Handle AgentLoop system turns without owning the outer loop lifecycle."""

    def __init__(self, deps: SystemTurnHandlerDeps) -> None:
        self._deps = deps

    @property
    def services(self) -> AgentServiceContainer:
        return self._deps.services

    @property
    def loop_context(self) -> SystemTurnLoopContext:
        return self._deps.loop_context

    async def process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        pending_queue: asyncio.Queue | None = None,
        capability_snapshot: CapabilitySnapshot | None = None,
    ) -> OutboundMessage | None:
        del session_key, on_progress, on_stream, on_stream_end

        channel, chat_id = (
            msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
        )
        logger.info("Processing system message from {}", msg.sender_id)
        key = msg.session_key_override or f"{channel}:{chat_id}"
        session = self.services.sessions.get_or_create(key)
        if self.loop_context.restore_runtime_checkpoint(session):
            self.services.sessions.save(session)
        if self.loop_context.restore_pending_user_turn(session):
            self.services.sessions.save(session)

        session, pending = self.services.auto_compact.prepare_session(session, key)
        if pending:
            logger.info("Memory compact triggered for session {}", key)

        await self.services.consolidator.maybe_consolidate_by_tokens(
            session,
            replay_max_messages=self.loop_context.get_max_messages(),
        )

        event_kind = str(msg.metadata.get("injected_event") or "").strip()
        is_subagent = msg.sender_id == "subagent" or event_kind == "subagent_result"
        is_active_intent = event_kind == "active_intent"
        is_cognitive_event = event_kind == "cognitive_event"

        persisted_subagent = False
        if is_subagent and self.loop_context.persist_subagent_followup(session, msg):
            persisted_subagent = True
            logger.debug("Subagent result persisted for session {}", key)
            self.services.sessions.save(session)

        runtime_context = self.loop_context.resolve_runtime_context(
            msg,
            channel=channel,
            chat_id=chat_id,
            session_key=key,
        )
        self.loop_context.record_runtime_context(key, runtime_context)
        self.loop_context.record_continuity_session_key(key)

        snapshot = capability_snapshot or self.loop_context.snapshot_for_trigger(
            runtime_context.trigger
        )
        self.loop_context.update_working_memory_from_turn(
            session,
            runtime_context=runtime_context,
            current_message=None
            if (is_subagent or is_active_intent or is_cognitive_event)
            else msg.content,
            internal_event=msg.content
            if (is_subagent or is_active_intent or is_cognitive_event)
            else None,
            media_paths=msg.media if msg.media else None,
        )
        self.loop_context.set_tool_context(
            channel,
            chat_id,
            msg.metadata.get("message_id"),
            msg.metadata,
            session_key=key,
            capability_snapshot=snapshot,
            runtime_context=runtime_context,
        )

        history = session.get_history(
            max_messages=self.loop_context.get_max_messages(),
            max_tokens=self.loop_context.replay_token_budget(),
            include_timestamps=True,
        )
        history_for_model = list(history)
        if is_subagent and persisted_subagent:
            for index in range(len(history_for_model) - 1, -1, -1):
                candidate = history_for_model[index]
                if (
                    candidate.get("role") == "assistant"
                    and candidate.get("content") == msg.content
                ):
                    history_for_model.pop(index)
                    break

        messages = self.services.context.build_messages(
            history=history_for_model,
            current_message=None
            if (is_subagent or is_active_intent or is_cognitive_event)
            else msg.content,
            media=msg.media if msg.media else None,
            channel=channel,
            chat_id=chat_id,
            current_role="user",
            sender_id=msg.sender_id,
            session_summary=pending,
            session_metadata=session.metadata,
            internal_event=(
                ("subagent_result", msg.content)
                if is_subagent
                else ("active_intent", msg.content)
                if is_active_intent
                else ("cognitive_event", msg.content)
                if is_cognitive_event
                else None
            ),
            runtime_context=runtime_context,
            session_key=key,
            context_window_tokens=self.loop_context.get_context_window_tokens(),
            max_completion_tokens=getattr(
                self.services.provider.generation,
                "max_tokens",
                4096,
            ),
        )
        context_assembly = self.loop_context.snapshot_context_assembly_from_messages(
            messages,
            session_key=key,
            runtime_context=runtime_context,
        )
        self.loop_context.record_context_assembly(context_assembly)

        final_content, _, all_msgs, stop_reason, _ = await self.loop_context.run_agent_loop(
            messages,
            session=session,
            channel=channel,
            chat_id=chat_id,
            message_id=msg.metadata.get("message_id"),
            metadata=msg.metadata,
            session_key=key,
            pending_queue=pending_queue,
            actor_id=runtime_context.actor_id,
            trigger=runtime_context.trigger,
            capability_snapshot=snapshot,
        )
        save_skip = 1 + len(history_for_model) + (
            1 if (is_subagent or is_active_intent or is_cognitive_event) else 0
        )
        self.loop_context.save_turn(session, all_msgs, save_skip)
        session.enforce_file_cap(on_archive=self.loop_context.archive_session_file_cap)
        self.loop_context.clear_runtime_checkpoint(session)
        self.services.sessions.save(session)
        self.loop_context.schedule_background(
            self.services.consolidator.maybe_consolidate_by_tokens(
                session,
                replay_max_messages=self.loop_context.get_max_messages(),
            )
        )

        system_ctx = TurnContext(
            msg=msg,
            session_key=key,
            state=TurnState.SAVE,
            turn_id=f"{key}:{time.time_ns()}",
            session=session,
            final_content=final_content,
            all_messages=all_msgs,
            stop_reason=stop_reason,
            runtime_context=runtime_context,
        )
        self.loop_context.schedule_nearline_memory(system_ctx)

        options = ask_user_options_from_messages(all_msgs) if stop_reason == "ask_user" else []
        content, buttons = ask_user_outbound(
            final_content or "Background task completed.",
            options,
            channel,
        )
        outbound_metadata: dict[str, Any] = {}
        if channel == "slack" and key.startswith("slack:") and key.count(":") >= 2:
            outbound_metadata["slack"] = {"thread_ts": key.split(":", 2)[2]}
        if origin_message_id := msg.metadata.get("origin_message_id"):
            outbound_metadata["origin_message_id"] = origin_message_id
        if channel == "websocket":
            outbound_metadata["goal_state"] = goal_state_ws_blob(session.metadata)
        return OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            buttons=buttons,
            metadata=outbound_metadata,
        )
