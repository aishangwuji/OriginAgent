"""Turn pipeline extracted from AgentLoop."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
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
from OriginAgent.utils.helpers import safe_filename
from OriginAgent.agent.topic_detection import detect_topic_shift
from OriginAgent.agent.warm_store import WarmStore
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

    # Populated by state_run when it catches an exception (Task A2.1); read by
    # the HANDLE_ERROR / HANDLE_TIMEOUT handlers to record an audit log.
    error: str | None = None

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
    # Phase 3 Task 6: 温区总结器 getter；None 表示未配置，不触发温区总结
    get_warm_summarizer: Callable[[], Any] | None = None


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


def _split_messages_by_turn(
    messages: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """将消息列表按 user turn 拆分为 ``(user_msg, assistant_msgs)`` 元组列表。

    一轮 = 一条 user 消息开始，到下一条 user 消息之前的全部内容
    （含 assistant / tool / tool_result 等任意条数）。

    若开头出现孤儿 assistant/tool 消息（理论上不应发生，因热区边界
    对齐到 user turn），直接忽略，避免污染温区的首轮结构。
    """
    turns: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    current_user: dict[str, Any] | None = None
    current_assistants: list[dict[str, Any]] = []

    for msg in messages:
        if msg.get("role") == "user":
            if current_user is not None:
                turns.append((current_user, current_assistants))
            current_user = msg
            current_assistants = []
        else:
            # user 还未出现时，开头的非 user 消息视为孤儿，跳过。
            if current_user is None:
                continue
            current_assistants.append(msg)

    if current_user is not None:
        turns.append((current_user, current_assistants))
    return turns


def _spill_overflow_into_warm_store(session: Session, max_turns: int = 50) -> None:
    """温区填补：把热区超出 ``max_turns`` 轮的前缀按 turn 追加到温区。

    设计意图（Phase 2 Task 4）：
    - 热区（``session.messages``）只保留最近 ``max_turns`` 轮对话，
      控制 LLM 上下文长度，避免 token 膨胀；
    - 超出部分按"轮"为单位追加到温区（``session.metadata['warm_buffer']``），
      比逐条归档更高效，且保留跨轮次的局部连续性；
    - 边界对齐复用 ``Session._find_hot_start_idx``，确保不截断 tool_call
      序列，与 ``get_hot_history`` 行为一致。

    本函数只负责 append；温区达到上限后的 drain（归档到冷区）由后续
    阶段处理。任何异常都吞掉并记录日志，避免影响 turn 主流程。
    """
    try:
        hot_start_idx = session._find_hot_start_idx(max_turns=max_turns)
        if hot_start_idx <= 0:
            return

        overflow_messages = session.messages[:hot_start_idx]
        if not overflow_messages:
            return

        warm_store = WarmStore()
        turns = _split_messages_by_turn(overflow_messages)
        for user_msg, assistant_msgs in turns:
            # 即使 assistant 序列为空也 append，保持温区轮次计数与
            # 实际溢出轮次一致；空 assistant 序列在 drain 时自然无影响。
            warm_store.append(session, user_msg, assistant_msgs)

        # 热区只保留最后 max_turns 轮（已按 user turn 边界对齐）。
        session.messages = session.messages[hot_start_idx:]
    except Exception:
        logger.exception("Warm buffer fill failed during turn save")


def _utcnow_iso() -> str:
    """返回 UTC 时间的 ISO8601 字符串，用于温区归档时间戳。"""
    return datetime.now(timezone.utc).isoformat()


def _warm_archive_filename(session_key: str) -> str:
    """将 session_key 转换为安全的归档文件名（与 SessionManager.safe_key 一致）。"""
    return safe_filename(session_key.replace(":", "_"))


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
            # Phase 1 热区注入：改用 get_hot_history 按“用户轮次”边界截取最近热区，
            # 避免按原始消息数切片时把同一轮的 tool_call / tool_result 拦腰截断，
            # 同时让刚被合并的旧轮次仍可进入热区窗口，保证短期上下文连续性。
            ctx.history = session.get_hot_history(
                max_turns=50,
                max_tokens=hist_kwargs.get("max_tokens", 0),
            )

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
        except TimeoutError as exc:
            # Route to HANDLE_TIMEOUT via (RUN, TIMEOUT) instead of crashing the turn.
            ctx.error = f"{type(exc).__name__}: {exc}"
            return TurnEvent.TIMEOUT
        except Exception as exc:
            # Route to HANDLE_ERROR via (RUN, ERROR) instead of crashing the turn.
            # CancelledError derives from BaseException, so it is NOT swallowed here.
            ctx.error = f"{type(exc).__name__}: {exc}"
            return TurnEvent.ERROR
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
        # Phase 2 Task 4: turn 结束后将热区超出 50 轮的部分追加到温区，
        # 把热区上下文长度控制在最近 50 轮内，溢出部分按 turn 批量进入
        # 温区缓冲（session.metadata['warm_buffer']）等待后续 drain。
        _spill_overflow_into_warm_store(session, max_turns=50)
        # Phase 3 Task 6: 温区满 50 轮触发异步总结。
        # 异步执行的设计意图：温区总结需调用辅助 LLM（耗时秒级），若在
        # turn 主流程同步等待会阻塞用户响应。通过 schedule_background 调度
        # 到后台执行，用户立即得到 turn 响应，总结在后台完成后写入归档。
        if WarmStore().is_full(session):
            self._schedule_warm_summary(session, ctx.session_key)
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

    # ── Phase 3 Task 6: 温区总结触发与执行 ────────────────────────────

    def _schedule_warm_summary(self, session: Session, session_key: str) -> None:
        """调度温区总结后台任务（不阻塞 turn 主流程）。

        若未配置 warm_summarizer（``get_warm_summarizer`` 为 None），直接跳过，
        温区消息保留在缓冲区等待后续配置后再触发。
        """
        getter = self._deps.get_warm_summarizer
        if getter is None:
            return
        summarizer = getter()
        if summarizer is None:
            return
        self._deps.schedule_background(
            self._run_warm_summary(session, session_key, summarizer)
        )

    async def _run_warm_summary(
        self,
        session: Session,
        session_key: str,
        summarizer: Any,
    ) -> None:
        """温区总结后台任务：drain → summarize → 归档 → 同步 working_memory。

        执行顺序的设计：先 drain 清空温区（同步，不 yield），再调用 LLM 总结
        （异步，耗时）。这保证在 LLM 等待期间下一轮 turn 可以安全向空温区
        追加，不会因温区满而重复触发总结。若进程在 LLM 调用期间崩溃，已
        持久化的 state_save 快照仍保留满温区，重启后会重新触发总结。
        """
        try:
            warm_store = WarmStore()
            # 1. 先 drain 清空温区（同步），避免 LLM 等待期间重复触发
            warm_messages = warm_store.drain(session)
            if not warm_messages:
                return

            # 2. 计算本批次的 turn_range（基于已有总结条数）
            turn_range = self._compute_warm_turn_range(session_key)

            # 3. 获取热区上下文（最近 50 轮），确保总结结合热区连贯性
            hot_messages = session.get_hot_history(max_turns=50)

            # 4. 调用辅助 LLM 生成结构化总结（异步，耗时秒级）
            summary = await summarizer.summarize(
                warm_messages,
                hot_messages,
                turn_range=turn_range,
                session_key=session_key,
            )

            # 5. 归档：原始消息写入 warm_archive，总结写入 warm_summaries
            self._archive_warm_messages(
                session_key, warm_messages, summary, turn_range
            )

            # 6. 同步结构化字段到 working_memory
            if summary is not None:
                self._sync_summary_to_working_memory(session, summary)

            # 7. 持久化 drain 后的温区状态 + working_memory 更新
            self._deps.sessions.save(session)
        except Exception:
            logger.exception("Warm summary background task failed")

    def _compute_warm_turn_range(self, session_key: str) -> str:
        """根据已有总结数量计算本批次的 turn_range。

        每批 50 轮：第 1 批为 1-50，第 2 批为 51-100，以此类推。
        通过读取 warm_summaries.jsonl 中该 session 的已有条数确定批次序号。
        """
        summaries_path = Path(self._deps.workspace) / "warm_summaries.jsonl"
        count = 0
        if summaries_path.exists():
            for line in summaries_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("session_key") == session_key:
                        count += 1
                except json.JSONDecodeError:
                    continue
        start = count * 50 + 1
        end = (count + 1) * 50
        return f"{start}-{end}"

    def _archive_warm_messages(
        self,
        session_key: str,
        warm_messages: list[dict[str, Any]],
        summary: dict[str, Any] | None,
        turn_range: str,
    ) -> None:
        """将原始消息与总结结果写入归档文件。

        - ``warm_archive/{session_key}.jsonl``：原始消息（每行一条 JSON）
        - ``warm_summaries.jsonl``：结构化总结索引（每行一条 JSON）

        两个文件均以追加模式写入，支持同一 session 多批总结累积归档。
        """
        workspace = Path(self._deps.workspace)
        safe_key = _warm_archive_filename(session_key)

        # 原始消息归档
        archive_dir = workspace / "warm_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{safe_key}.jsonl"
        msg_count = 0
        with archive_path.open("a", encoding="utf-8") as f:
            for msg in warm_messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                msg_count += 1

        # 总结索引归档（仅在总结成功时写入）
        if summary is not None:
            timestamp_range = summary.get("timestamp_range") or {}
            if not isinstance(timestamp_range, dict):
                timestamp_range = {}
            entry = {
                "session_key": session_key,
                "turn_range": turn_range,
                "summary": summary.get("summary", ""),
                "commitments": summary.get("commitments", []),
                "decisions": summary.get("decisions", []),
                "open_questions": summary.get("open_questions", []),
                "key_entities": summary.get("key_entities", []),
                "timestamp_range": {
                    "start": timestamp_range.get("start", ""),
                    "end": timestamp_range.get("end", ""),
                },
                "locator": f"warm_archive/{safe_key}.jsonl:0:{msg_count}",
                "created_at": _utcnow_iso(),
            }
            summaries_path = workspace / "warm_summaries.jsonl"
            with summaries_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _sync_summary_to_working_memory(
        self,
        session: Session,
        summary: dict[str, Any],
    ) -> None:
        """将总结的结构化字段同步到 working_memory。

        - ``commitments`` → ``open_loops``（Agent 答应过的事 = 待办事项）
        - ``open_questions`` → ``priority_facts``（悬而未决的问题 = 需关注的事实）

        采用追加去重策略：只加入尚未记录的条目，避免丢失此前累积的待办与
        关注事项，也避免同一承诺被重复记录。
        """
        working_memory = self._deps.get_working_memory()
        snapshot = working_memory.load(session)

        commitments = summary.get("commitments") or []
        open_questions = summary.get("open_questions") or []

        # 追加去重：只加入尚未记录的条目
        new_open_loops = [
            str(c) for c in commitments
            if str(c) not in snapshot.open_loops
        ]
        new_priority_facts = [
            str(q) for q in open_questions
            if str(q) not in snapshot.priority_facts
        ]

        if new_open_loops or new_priority_facts:
            working_memory.upsert(
                session,
                open_loops=[*snapshot.open_loops, *new_open_loops],
                priority_facts=[*snapshot.priority_facts, *new_priority_facts],
            )

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

    async def state_handle_error(self, ctx: TurnContext) -> TurnEvent:
        """Recovery handler reached via ``(RUN, ERROR)`` (and other ERROR transitions).

        Records an audit log of the failure captured in ``ctx.error`` by ``state_run``,
        then returns ``OK`` so the ``(HANDLE_ERROR, OK) -> RESPOND`` transition still
        lets the user receive a response instead of crashing the turn.
        """
        logger.error(
            "[turn {}] Turn entered HANDLE_ERROR: {}",
            ctx.turn_id,
            ctx.error or "unknown error",
        )
        return TurnEvent.OK

    async def state_handle_timeout(self, ctx: TurnContext) -> TurnEvent:
        """Recovery handler reached via ``(RUN, TIMEOUT)``.

        Records an audit log of the timeout captured in ``ctx.error`` by ``state_run``,
        then returns ``OK`` so the ``(HANDLE_TIMEOUT, OK) -> RESPOND`` transition still
        lets the user receive a response instead of crashing the turn.
        """
        logger.warning(
            "[turn {}] Turn entered HANDLE_TIMEOUT: {}",
            ctx.turn_id,
            ctx.error or "unknown timeout",
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
