"""Turn pipeline extracted from AgentLoop."""

from __future__ import annotations

import asyncio
import dataclasses
import os
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Awaitable, Callable

from loguru import logger

from OriginAgent.agent.action_continuity import ActionProposal
from OriginAgent.agent.tools.message import MessageTool
from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.command import CommandContext
from OriginAgent.security.capabilities import CapabilitySnapshot
from OriginAgent.agent.identity import RuntimeContext
from OriginAgent.session.manager import Session
from OriginAgent.utils.artifacts import generated_image_paths_from_messages
from OriginAgent.utils.document import extract_documents
from OriginAgent.agent.topic_detection import detect_topic_shift
from OriginAgent.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE
from OriginAgent.utils.session_attachments import merge_turn_media_into_last_assistant
from OriginAgent.utils.webui_turn_helpers import publish_turn_run_status, websocket_turn_latency_ms


class TurnState(Enum):
    RESTORE = auto()
    COMPACT = auto()
    COMMAND = auto()
    BUILD = auto()
    RUN = auto()
    SAVE = auto()
    AUTOMATION = auto()
    RESPOND = auto()
    DONE = auto()
    # Error / recovery states added for explicit transition modelling (D5).
    HANDLE_ERROR = auto()
    HANDLE_TIMEOUT = auto()


@dataclass
class StateTraceEntry:
    state: TurnState
    started_at: float
    duration_ms: float
    event: str
    error: str | None = None


@dataclass
class TurnContext:
    msg: InboundMessage
    session_key: str
    state: TurnState
    turn_id: str
    session: Session | None = None

    history: list[dict[str, Any]] = field(default_factory=list)
    initial_messages: list[dict[str, Any]] = field(default_factory=list)

    final_content: str | None = None
    tools_used: list[str] = field(default_factory=list)
    all_messages: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    had_injections: bool = False

    user_persisted_early: bool = False
    save_skip: int = 0

    outbound: OutboundMessage | None = None
    generated_media: list[str] = field(default_factory=list)
    automation_appendix: list[str] = field(default_factory=list)

    on_progress: Callable[..., Awaitable[None]] | None = None
    on_stream: Callable[[str], Awaitable[None]] | None = None
    on_stream_end: Callable[..., Awaitable[None]] | None = None
    on_retry_wait: Callable[[str], Awaitable[None]] | None = None

    pending_queue: asyncio.Queue | None = None
    pending_summary: str | None = None
    internal_event: tuple[str, str] | None = None
    runtime_context: RuntimeContext | None = None
    capability_snapshot: CapabilitySnapshot | None = None
    recovered_continuity_checkpoint: dict[str, Any] | None = None

    trace: list[StateTraceEntry] = field(default_factory=list)


class TurnEvent(Enum):
    """Typed events driving the Turn state machine.

    Replaces plain-string events for compile-time safety.
    """
    OK = auto()
    DISPATCH = auto()
    SHORTCUT = auto()
    SKIP = auto()
    ERROR = auto()
    TIMEOUT = auto()
    FATAL_ERROR = auto()
    MAX_ITERATIONS = auto()
    FATAL = auto()


TURN_PIPELINE_TRANSITIONS: dict[tuple[TurnState, TurnEvent], TurnState] = {
    # Happy path
    (TurnState.RESTORE, TurnEvent.OK): TurnState.COMPACT,
    (TurnState.COMPACT, TurnEvent.OK): TurnState.COMMAND,
    (TurnState.COMMAND, TurnEvent.DISPATCH): TurnState.BUILD,
    (TurnState.COMMAND, TurnEvent.SHORTCUT): TurnState.DONE,
    (TurnState.BUILD, TurnEvent.OK): TurnState.RUN,
    (TurnState.RUN, TurnEvent.OK): TurnState.SAVE,
    (TurnState.SAVE, TurnEvent.OK): TurnState.AUTOMATION,
    (TurnState.AUTOMATION, TurnEvent.OK): TurnState.RESPOND,
    (TurnState.AUTOMATION, TurnEvent.SKIP): TurnState.RESPOND,
    (TurnState.RESPOND, TurnEvent.OK): TurnState.DONE,
    # Error / recovery paths (D5)
    (TurnState.RESTORE, TurnEvent.ERROR): TurnState.HANDLE_ERROR,
    (TurnState.COMPACT, TurnEvent.ERROR): TurnState.HANDLE_ERROR,
    (TurnState.BUILD, TurnEvent.ERROR): TurnState.HANDLE_ERROR,
    (TurnState.RUN, TurnEvent.ERROR): TurnState.HANDLE_ERROR,
    (TurnState.RUN, TurnEvent.MAX_ITERATIONS): TurnState.SAVE,
    (TurnState.RUN, TurnEvent.FATAL_ERROR): TurnState.HANDLE_ERROR,
    (TurnState.RUN, TurnEvent.TIMEOUT): TurnState.HANDLE_TIMEOUT,
    (TurnState.SAVE, TurnEvent.ERROR): TurnState.HANDLE_ERROR,
    (TurnState.AUTOMATION, TurnEvent.ERROR): TurnState.RESPOND,
    (TurnState.HANDLE_ERROR, TurnEvent.OK): TurnState.RESPOND,
    (TurnState.HANDLE_ERROR, TurnEvent.FATAL): TurnState.DONE,
    (TurnState.HANDLE_TIMEOUT, TurnEvent.OK): TurnState.RESPOND,
    (TurnState.HANDLE_TIMEOUT, TurnEvent.FATAL): TurnState.DONE,
}


@dataclass
class TurnPipelineDeps:
    auto_compact: Any
    commands: Any
    command_loop: Any
    get_consolidator: Callable[[], Any]
    get_tools: Callable[[], Any]
    get_context: Callable[[], Any]
    sessions: Any
    bus: Any
    get_working_memory: Callable[[], Any]
    get_memory_governance: Callable[[], Any]
    get_rolling_episode_compaction: Callable[[], Any]
    workspace: Any
    tools_config: Any
    domain_runtime_contributions: list[Any]
    domain_runtime_overrides: dict[str, Any]
    archive_session_file_cap: Callable[[Any], None]
    restore_runtime_checkpoint: Callable[[Session], bool]
    restore_pending_user_turn: Callable[[Session], bool]
    load_continuity_checkpoint: Callable[[Session], dict[str, Any] | None]
    record_recovered_continuity_checkpoint: Callable[[dict[str, Any] | None], None]
    mark_webui_session: Callable[[Session, dict[str, Any] | None], None]
    persist_shortcut_command_turn: Callable[[InboundMessage, str, OutboundMessage], None]
    is_webui_message: Callable[[InboundMessage], bool]
    resolve_runtime_context: Callable[..., RuntimeContext]
    record_runtime_context: Callable[[str, RuntimeContext], None]
    write_continuity_runtime_identity: Callable[[Session, RuntimeContext], None]
    snapshot_for_trigger: Callable[[str | None], CapabilitySnapshot]
    update_working_memory_from_turn: Callable[..., None]
    set_tool_context: Callable[..., None]
    replay_token_budget: Callable[[], int]
    build_initial_messages: Callable[..., list[dict[str, Any]]]
    persist_user_message_early: Callable[..., bool]
    schedule_session_search_refresh: Callable[..., None]
    build_progress_callback: Callable[[InboundMessage], Awaitable[Callable[..., Awaitable[None]]]]
    build_retry_wait_callback: Callable[[InboundMessage], Awaitable[Callable[[str], Awaitable[None]]]]
    pending_ask_user_id: Callable[[list[dict[str, Any]]], str | None]
    consume_tool_approval_reply: Callable[..., tuple[tuple[str, str] | None, bool]]
    build_recovered_continuity_context: Callable[[dict[str, Any]], dict[str, Any]]
    run_agent_loop: Callable[..., Awaitable[tuple[str | None, list[str], list[dict[str, Any]], str, bool]]]
    clear_pending_user_turn: Callable[[Session], None]
    clear_runtime_checkpoint: Callable[[Session], None]
    save_turn: Callable[[Session, list[dict], int], None]
    record_governance_audit: Callable[[dict[str, Any]], None]
    save_continuity_checkpoint: Callable[..., dict[str, Any]]
    schedule_background: Callable[[Awaitable[Any]], None]
    schedule_nearline_memory: Callable[[TurnContext], None]
    schedule_background_review: Callable[[TurnContext], None]
    schedule_curator_review: Callable[[TurnContext], None]
    automation_enabled: Callable[[], bool]
    action_planner: Any
    record_action_continuity_audit: Callable[[dict[str, Any]], None]
    assemble_outbound: Callable[
        [InboundMessage, str, list[dict[str, Any]], str, bool, list[str], Callable[[str], Awaitable[None]] | None],
        OutboundMessage | None,
    ]
    get_max_messages: Callable[[], int]


_CONTEXT_INSUFFICIENCY_MIN_MESSAGES = 20


def _build_context_insufficiency_hint(session: Session) -> str | None:
    """If the active episode is long, hint that the agent can retrieve more context.

    Returns a hint string or None.
    """
    active_ep = session.active_episode
    if active_ep is None:
        return None
    count = active_ep.msg_end - active_ep.msg_start
    if count < _CONTEXT_INSUFFICIENCY_MIN_MESSAGES:
        return None
    return (
        f"The current episode has {count} messages, but only a subset is "
        f"loaded in your context window. Use episode_context(episode_id=\"{active_ep.episode_id}\") "
        f"to retrieve the full episode transcript if you need more context."
    )


def _maybe_auto_detect_topic_shift(
    context_builder: Any,
    session: Session,
    current_message: str,
    *,
    working_memory: dict[str, Any] | None = None,
) -> None:
    """Run lightweight topic-shift detection and auto-start a new episode if needed.

    Uses Jaccard similarity on content words — no LLM call, no external dependency.
    If a shift is detected, any *working_memory* snapshot is archived into the
    closed episode's summary metadata.
    """
    active_ep = session.active_episode
    if active_ep is None:
        return

    # Gather recent user messages from the active episode for comparison.
    msgs = session.messages[active_ep.msg_start:active_ep.msg_end]
    recent_user_texts: list[str] = [
        m.get("content", "")
        for m in msgs
        if m.get("role") == "user" and isinstance(m.get("content"), str)
    ]

    # Need at least 2 prior user messages to detect a shift.
    if len(recent_user_texts) < 2:
        return

    # Use the last 5 user messages as the rolling window.
    window = recent_user_texts[-5:]

    if detect_topic_shift(current_message, window):
        session.start_new_episode(working_memory=working_memory)


class AgentTurnPipeline:
    """Encapsulate the non-system AgentLoop turn pipeline."""

    def __init__(self, deps: TurnPipelineDeps) -> None:
        self._deps = deps

    @staticmethod
    def _select_automation_proposal(
        proposals: list[ActionProposal],
        *,
        preferred_origin: str | None = None,
    ) -> ActionProposal | None:
        executable = [
            proposal
            for proposal in proposals
            if not getattr(proposal, "preview_only", False)
        ]
        if not executable:
            return None
        if preferred_origin:
            preferred = [
                proposal
                for proposal in executable
                if proposal.automation_origin == preferred_origin
            ]
            if preferred:
                if len(preferred) > 1:
                    logger.warning(
                        "Multiple executable proposals found for preferred origin %s; selecting the first candidate",
                        preferred_origin,
                    )
                return preferred[0]
        for origin in ("smart_home", "robot"):
            candidates = [
                proposal
                for proposal in executable
                if proposal.automation_origin == origin
            ]
            if not candidates:
                continue
            if len(candidates) > 1:
                logger.warning(
                    "Multiple executable proposals found for origin %s; selecting the first candidate",
                    origin,
                )
            return candidates[0]
        return executable[0]

    @staticmethod
    def _resolve_executor_for_origin(origin: str | None, domain_contributions: list[Any]) -> Any | None:
        if not origin:
            return None
        for contribution in list(domain_contributions or []):
            provider = getattr(contribution, "action_continuity_provider", None)
            if provider is None:
                continue
            if getattr(provider, "automation_origin", None) != origin:
                continue
            tool_context = getattr(contribution, "tool_context", {}) or {}
            executor = tool_context.get("device_action_executor")
            if executor is not None:
                return executor
        return None

    @staticmethod
    def _resolve_adapter_for_origin(origin: str | None, domain_contributions: list[Any]) -> Any | None:
        if not origin:
            return None
        for contribution in list(domain_contributions or []):
            provider = getattr(contribution, "action_continuity_provider", None)
            if provider is None:
                continue
            if getattr(provider, "automation_origin", None) == origin:
                return getattr(contribution, "action_continuity_writeback_adapter", None)
        return None

    async def state_restore(self, ctx: TurnContext) -> TurnEvent:
        """Restore checkpoint / pending user turn; extract documents."""
        msg = ctx.msg

        if msg.media:
            new_content, image_only = extract_documents(msg.content, msg.media)
            ctx.msg = dataclasses.replace(msg, content=new_content, media=image_only)
            msg = ctx.msg

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        if ctx.session is None:
            ctx.session = self._deps.sessions.get_or_create(ctx.session_key)
        session = ctx.session
        self._deps.mark_webui_session(session, msg.metadata)

        if self._deps.restore_runtime_checkpoint(session):
            self._deps.sessions.save(session)
        if self._deps.restore_pending_user_turn(session):
            self._deps.sessions.save(session)
        ctx.recovered_continuity_checkpoint = self._deps.load_continuity_checkpoint(session)
        self._deps.record_recovered_continuity_checkpoint(ctx.recovered_continuity_checkpoint)

        return TurnEvent.OK

    async def state_compact(self, ctx: TurnContext) -> TurnEvent:
        ctx.session, pending = self._deps.auto_compact.prepare_session(ctx.session, ctx.session_key)
        ctx.pending_summary = pending
        return TurnEvent.OK

    async def state_command(self, ctx: TurnContext) -> TurnEvent:
        raw = ctx.msg.content.strip()
        lang = (ctx.msg.metadata or {}).get("lang", "") or os.environ.get("ORIGINAGENT_LANG", "")
        cmd_ctx = CommandContext(
            msg=ctx.msg,
            session=ctx.session,
            key=ctx.session_key,
            raw=raw,
            lang=lang,
            loop=self._deps.command_loop,
        )
        result = await self._deps.commands.dispatch(cmd_ctx)
        if result is not None:
            ctx.outbound = result
            self._deps.persist_shortcut_command_turn(ctx.msg, ctx.session_key, result)
            if self._deps.is_webui_message(ctx.msg):
                result.metadata["_webui_transcript_recorded"] = True
            return TurnEvent.SHORTCUT
        return TurnEvent.DISPATCH

    async def state_build(self, ctx: TurnContext) -> TurnEvent:
        consolidator = self._deps.get_consolidator()
        tools = self._deps.get_tools()
        await consolidator.maybe_consolidate_by_tokens(
            ctx.session,
            replay_max_messages=self._deps.get_max_messages(),
        )
        runtime_context = self._deps.resolve_runtime_context(ctx.msg, session_key=ctx.session_key)
        ctx.runtime_context = runtime_context
        self._deps.record_runtime_context(ctx.session_key, runtime_context)
        session = ctx.session
        self._deps.write_continuity_runtime_identity(session, runtime_context)
        snapshot = ctx.capability_snapshot or self._deps.snapshot_for_trigger(runtime_context.trigger)
        self._deps.update_working_memory_from_turn(
            session,
            runtime_context=runtime_context,
            current_message=ctx.msg.content,
            media_paths=ctx.msg.media if ctx.msg.media else None,
        )
        self._deps.set_tool_context(
            ctx.msg.channel,
            ctx.msg.chat_id,
            ctx.msg.metadata.get("message_id"),
            ctx.msg.metadata,
            session_key=ctx.session_key,
            capability_snapshot=snapshot,
            runtime_context=runtime_context,
            turn_id=ctx.turn_id,
        )
        if message_tool := tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        hist_kwargs: dict[str, Any] = {
            "max_messages": self._deps.get_max_messages(),
            "max_tokens": self._deps.replay_token_budget(),
            "include_timestamps": True,
        }

        # Tag the inbound message with the active episode_id.
        active_ep = session.active_episode
        if active_ep is not None and hasattr(ctx.msg, "episode_id"):
            ctx.msg.episode_id = active_ep.episode_id

        # Phase 5: always use episode-scoped history and auto topic detection.
        context_builder = self._deps.get_context()
        wm_snapshot: dict[str, Any] | None = None
        try:
            wm_mgr = self._deps.get_working_memory()
            if wm_mgr is not None:
                wm_snapshot = wm_mgr.inspect(
                    session,
                    identity=runtime_context.identity if runtime_context is not None else None,
                )
        except Exception:
            pass
        _maybe_auto_detect_topic_shift(
            context_builder, session, ctx.msg.content,
            working_memory=wm_snapshot,
        )
        active_ep = session.active_episode
        if active_ep is not None:
            ctx.history = session.get_episode_history(
                active_ep.episode_id,
                max_tokens=hist_kwargs["max_tokens"],
                include_timestamps=hist_kwargs["include_timestamps"],
            )
        else:
            ctx.history = session.get_history(**hist_kwargs)

        pending_ask_id = self._deps.pending_ask_user_id(ctx.history)
        tool_approval_event = None
        if pending_ask_id is None:
            tool_approval_event, consumed = self._deps.consume_tool_approval_reply(
                session_key=ctx.session_key,
                actor_id=runtime_context.actor_id,
                reply=ctx.msg.content,
            )
            if consumed:
                ctx.internal_event = tool_approval_event

        # Context insufficiency hint (Phase 5): if the active episode is long
        # and no tool_approval is pending, hint the agent that more context
        # may be available via episode_context tool.
        if ctx.internal_event is None:
            hint = _build_context_insufficiency_hint(session)
            if hint:
                ctx.internal_event = ("episode_context_hint", hint)

        recovered_block = None
        if ctx.recovered_continuity_checkpoint:
            recovered_block = self._deps.build_recovered_continuity_context(
                ctx.recovered_continuity_checkpoint
            )
        ctx.initial_messages = self._deps.build_initial_messages(
            ctx.msg,
            session,
            ctx.history,
            pending_ask_id,
            ctx.pending_summary,
            ctx.internal_event,
            recovered_block,
        )
        ctx.user_persisted_early = self._deps.persist_user_message_early(
            ctx.msg, session, pending_ask_id
        )
        if ctx.user_persisted_early:
            self._deps.schedule_session_search_refresh(sources=["sessions"])

        if ctx.on_progress is None:
            ctx.on_progress = await self._deps.build_progress_callback(ctx.msg)
        if ctx.on_retry_wait is None:
            ctx.on_retry_wait = await self._deps.build_retry_wait_callback(ctx.msg)

        return TurnEvent.OK

    async def state_run(self, ctx: TurnContext) -> TurnEvent:
        runtime_context = ctx.runtime_context or self._deps.resolve_runtime_context(
            ctx.msg,
            session_key=ctx.session_key,
        )
        ctx.runtime_context = runtime_context
        snapshot = ctx.capability_snapshot or self._deps.snapshot_for_trigger(runtime_context.trigger)
        await publish_turn_run_status(self._deps.bus, ctx.msg, "running")
        try:
            result = await self._deps.run_agent_loop(
                ctx.initial_messages,
                on_progress=ctx.on_progress,
                on_stream=ctx.on_stream,
                on_stream_end=ctx.on_stream_end,
                on_retry_wait=ctx.on_retry_wait,
                session=ctx.session,
                channel=ctx.msg.channel,
                chat_id=ctx.msg.chat_id,
                message_id=ctx.msg.metadata.get("message_id"),
                metadata=ctx.msg.metadata,
                session_key=ctx.session_key,
                pending_queue=ctx.pending_queue,
                actor_id=runtime_context.actor_id,
                trigger=runtime_context.trigger,
                capability_snapshot=snapshot,
            )
        finally:
            if ctx.msg.channel == "websocket":
                latency = websocket_turn_latency_ms(str(ctx.msg.chat_id))
                if latency is not None:
                    ctx.msg.metadata["webui_turn_latency_ms"] = latency
            await publish_turn_run_status(self._deps.bus, ctx.msg, "idle")
        final_content, tools_used, all_msgs, stop_reason, had_injections = result
        ctx.final_content = final_content
        ctx.tools_used = tools_used
        ctx.all_messages = all_msgs
        ctx.stop_reason = stop_reason
        ctx.had_injections = had_injections
        return TurnEvent.OK

    async def state_save(self, ctx: TurnContext) -> TurnEvent:
        if ctx.final_content is None or not ctx.final_content.strip():
            ctx.final_content = EMPTY_FINAL_RESPONSE_MESSAGE

        tools = self._deps.get_tools()
        context = self._deps.get_context()
        memory_governance = self._deps.get_memory_governance()
        working_memory = self._deps.get_working_memory()
        rolling_episode_compaction = self._deps.get_rolling_episode_compaction()
        consolidator = self._deps.get_consolidator()
        ctx.save_skip = 1 + len(ctx.history) + (1 if ctx.user_persisted_early else 0)
        skip_msgs = ctx.all_messages[ctx.save_skip:]
        ctx.generated_media = generated_image_paths_from_messages(skip_msgs)
        message_tool = tools.get("message")
        extra_media = (
            message_tool.turn_delivered_media_paths()
            if hasattr(message_tool, "turn_delivered_media_paths")
            else []
        )
        merge_turn_media_into_last_assistant(
            ctx.all_messages,
            ctx.generated_media,
            extra_media,
            workspace=self._deps.workspace,
        )

        session = ctx.session
        self._deps.save_turn(session, ctx.all_messages, ctx.save_skip)
        session.enforce_file_cap(on_archive=self._deps.archive_session_file_cap)
        self._deps.clear_pending_user_turn(session)
        self._deps.clear_runtime_checkpoint(session)
        governance_audit: dict[str, Any] = {
            "governance_enabled": bool(getattr(context._context_config, "governance_enabled", False)),
            "promotion_candidates": [],
            "promotion_applied_count": 0,
            "promotion_conflict_count": 0,
            "forgetting_actions": [],
        }
        if governance_audit["governance_enabled"]:
            decision = memory_governance.evaluate_turn(
                session,
                runtime_context=ctx.runtime_context,
                turn_id=ctx.turn_id,
                current_message=ctx.msg.content,
            )
            governance_audit = memory_governance.apply_turn(session, decision)
        self._deps.record_governance_audit(governance_audit)
        self._deps.save_continuity_checkpoint(
            session,
            runtime_context=ctx.runtime_context,
        )
        try:
            working_snapshot = working_memory.load(
                session,
                identity=ctx.runtime_context.identity if ctx.runtime_context is not None else None,
            )
            rolling_episode_compaction.maybe_compact(
                session,
                working_snapshot=working_snapshot,
                reason="turn_save",
            )
        except Exception:
            logger.exception("Rolling episode compaction failed during save")
        self._deps.sessions.save(session)
        self._deps.schedule_session_search_refresh(sources=["sessions", "history"])
        self._deps.schedule_background(
            consolidator.maybe_consolidate_by_tokens(
                session,
                replay_max_messages=self._deps.get_max_messages(),
            )
        )
        self._deps.schedule_nearline_memory(ctx)
        self._deps.schedule_background_review(ctx)
        self._deps.schedule_curator_review(ctx)
        return TurnEvent.OK

    async def state_automation(self, ctx: TurnContext) -> TurnEvent:
        if ctx.session is None or ctx.runtime_context is None:
            self._deps.record_action_continuity_audit({"status": "skipped", "reason": "missing_context"})
            return TurnEvent.SKIP
        if not self._deps.automation_enabled():
            self._deps.record_action_continuity_audit({"status": "skipped", "reason": "automation_disabled"})
            return TurnEvent.SKIP
        context = self._deps.get_context()
        working_memory = self._deps.get_working_memory()
        automation_runtime_context = dataclasses.replace(
            ctx.runtime_context,
            trigger="automation",
            source="automation",
        )
        automation_snapshot = self._deps.snapshot_for_trigger("automation")
        continuity_inputs = context.build_action_continuity_inputs(
            ctx.session_key,
            automation_runtime_context,
        )
        max_actions = int(
            getattr(getattr(self._deps.tools_config, "device", None), "automation_max_actions_per_pass", 1) or 1
        )
        planner_result = self._deps.action_planner.plan_action(
            continuity_inputs,
            session_key=ctx.session_key,
            domain_contributions=self._deps.domain_runtime_contributions,
            max_actions=max_actions,
        )
        proposal = self._select_automation_proposal(
            planner_result.proposals,
            preferred_origin=continuity_inputs.user_goal_domain,
        )
        if proposal is None:
            self._deps.record_action_continuity_audit({
                "status": "skipped",
                "reason": "no_executable_proposal",
                "planning_inputs": continuity_inputs.to_dict(),
                "planning_evidence": {},
                "automation_origin": None,
                "planner_result": planner_result.to_dict(),
                "selected_proposal_digest": None,
                "skipped_reasons": list(planner_result.skipped_reasons),
            })
            return TurnEvent.SKIP
        executor = self._resolve_executor_for_origin(
            proposal.automation_origin,
            self._deps.domain_runtime_contributions,
        )
        if executor is None:
            self._deps.record_action_continuity_audit({
                "status": "skipped",
                "reason": "executor_missing_for_selected_proposal",
                "planning_inputs": continuity_inputs.to_dict(),
                "planning_evidence": proposal.to_dict(),
                "automation_origin": proposal.automation_origin,
                "planner_result": planner_result.to_dict(),
                "selected_proposal_digest": proposal.proposal_digest,
                "skipped_reasons": list(planner_result.skipped_reasons),
            })
            return TurnEvent.SKIP
        adapter = self._resolve_adapter_for_origin(
            proposal.automation_origin,
            self._deps.domain_runtime_contributions,
        )
        if adapter is None:
            self._deps.record_action_continuity_audit({
                "status": "skipped",
                "reason": "adapter_missing_for_selected_proposal",
                "planning_inputs": continuity_inputs.to_dict(),
                "planning_evidence": proposal.to_dict(),
                "automation_origin": proposal.automation_origin,
                "planner_result": planner_result.to_dict(),
                "selected_proposal_digest": proposal.proposal_digest,
                "skipped_reasons": list(planner_result.skipped_reasons),
            })
            return TurnEvent.SKIP
        typed_action = proposal.typed_action
        if getattr(getattr(self._deps.tools_config, "device", None), "automation_dry_run_only", True):
            typed_action = dataclasses.replace(
                typed_action,
                trigger="automation",
                idempotency_key=proposal.proposal_digest,
            )
        self._deps.set_tool_context(
            ctx.msg.channel,
            ctx.msg.chat_id,
            ctx.msg.metadata.get("message_id"),
            ctx.msg.metadata,
            session_key=ctx.session_key,
            capability_snapshot=automation_snapshot,
            runtime_context=automation_runtime_context,
            turn_id=ctx.turn_id,
        )
        result, precondition = executor.submit_automation(
            typed_action,
            continuity_inputs=continuity_inputs,
            proposal=proposal,
        )
        writeback = adapter.writeback(
            session=ctx.session,
            continuity_inputs=continuity_inputs,
            proposal=proposal,
            result=result,
            working_memory=working_memory,
        )
        ctx.automation_appendix.extend(list(writeback.get("appendix") or []))
        self._deps.sessions.save(ctx.session)
        self._deps.record_action_continuity_audit({
            "status": "ok",
            "planning_inputs": continuity_inputs.to_dict(),
            "planning_evidence": proposal.to_dict(),
            "automation_origin": proposal.automation_origin,
            "planner_result": planner_result.to_dict(),
            "selected_proposal_digest": proposal.proposal_digest,
            "skipped_reasons": list(planner_result.skipped_reasons),
            "selection_reason": (
                "preferred_origin"
                if continuity_inputs.user_goal_domain
                and continuity_inputs.user_goal_domain == proposal.automation_origin
                else "domain_priority_first_executable"
            ),
            "preconditions": {
                "outcome": precondition.outcome,
                "reason": precondition.reason,
                "audit": dict(precondition.audit),
            },
            "execution_result": {
                "status": result.status,
                "action_id": result.action_id,
                "reason": result.reason,
                "confirmation_id": result.confirmation_id,
                "backend_called": result.backend_called,
                "permission_status": result.permission_status,
                "is_real_execution": result.is_real_execution,
                "backend_kind": result.backend_kind,
                "physical_target_domain": result.physical_target_domain,
            },
            "continuity_writeback": dict(writeback),
        })
        return TurnEvent.OK

    async def state_respond(self, ctx: TurnContext) -> TurnEvent:
        if ctx.automation_appendix:
            appendix = "\n\n".join(str(item).strip() for item in ctx.automation_appendix if str(item).strip())
            if appendix:
                ctx.final_content = f"{ctx.final_content or ''}\n\n{appendix}".strip()
        ctx.outbound = self._deps.assemble_outbound(
            ctx.msg,
            ctx.final_content,
            ctx.all_messages,
            ctx.stop_reason,
            ctx.had_injections,
            ctx.generated_media,
            ctx.on_stream,
        )
        return TurnEvent.OK


__all__ = [
    "AgentTurnPipeline",
    "StateTraceEntry",
    "TURN_PIPELINE_TRANSITIONS",
    "TurnContext",
    "TurnPipelineDeps",
    "TurnState",
]
