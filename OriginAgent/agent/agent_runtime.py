"""AgentRuntime — stateless message router extracted from AgentLoop.

Receives all context (session_key, session, messages, etc.) as explicit
method parameters.  Depends on SessionStateHolder for session-scoped
scratchpad and on AgentHost for infrastructure lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import time as _time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from OriginAgent.agent.agent_runtime_context import (
    runtime_chat_id,
    set_tool_context as set_tools_runtime_context,
    snapshot_for_trigger,
)
from OriginAgent.agent.agent_turn_persist import TurnPersistManager
from OriginAgent.agent.cognitive_events import CognitiveEvent
from OriginAgent.agent.confirmation import classify_confirmation_reply
from OriginAgent.agent.error_classifier import (
    ClassifiedError,
    ErrorKind,
    user_facing_message,
)
from OriginAgent.agent.hook import AgentHook, CompositeHook
from OriginAgent.agent.message_metadata import build_origin_metadata
from OriginAgent.agent.meta_cognition_triggers import (
    bridge_runtime_event_to_trigger,
    build_user_correction_trigger,
    latest_assistant_message,
)
from OriginAgent.agent.progress_hook import AgentProgressHook
from OriginAgent.agent.runner import _MAX_INJECTIONS_PER_TURN, AgentRunSpec
from OriginAgent.agent.self_model import SelfModelService
from OriginAgent.agent.tools.ask import (
    ask_user_options_from_messages,
    ask_user_outbound,
    ask_user_tool_result_messages,
)
from OriginAgent.agent.tools.file_state import bind_file_states, reset_file_states
from OriginAgent.agent.tools.message import MessageTool
from OriginAgent.agent.warm_store import WarmStore
from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.security.grants import issue_tool_approval_grant
from OriginAgent.session.goal_state import goal_state_ws_blob, runner_wall_llm_timeout_s
from OriginAgent.utils.constants import RoleConstants
from OriginAgent.utils.document import extract_documents
from OriginAgent.utils.helpers import strip_think
from OriginAgent.utils.image_generation_intent import image_generation_prompt
from OriginAgent.utils.tool_hints import format_tool_hints
from OriginAgent.utils.tracing import log_event, new_trace as _new_trace, set_session, span
from OriginAgent.utils.webui_transcript import append_transcript_object


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
class RuntimeConfig:
    """Scalar runtime configuration (already typed)."""
    model: str | None = None
    max_iterations: int = 10
    context_window_tokens: int = 0
    context_block_limit: int = 0
    max_tool_result_chars: int = 0
    provider_retry_mode: str = "standard"
    tool_hint_max_length: int | None = None
    restrict_to_workspace: bool = False
    unified_session: bool = False
    runtime_profile: str = "default"
    consolidation_ratio: float = 0.5
    max_messages: int = 120


@dataclass(frozen=True)
class CoreServices:
    """Typed subcontainer for core agent services (Strangler Fig migration target)."""
    tools: Any = None
    provider: Any = None
    runner: Any = None
    context: Any = None
    sessions: Any = None
    bus: Any = None
    workspace: Any = None
    subagents: Any = None
    working_memory: Any = None
    commands: Any = None
    action_planner: Any = None
    active_intents: Any = None
    reminder_store: Any = None
    world_state: Any = None


@dataclass(frozen=True)
class MetaCognitionServices:
    """Typed subcontainer for meta-cognition services."""
    meta_cognition_runtime: Any = None
    meta_cognition_reflector: Any = None
    meta_cognition_regulator: Any = None
    meta_cognition_config: Any = None
    meta_coordinator: Any = None
    perception_fusion: Any = None


@dataclass(frozen=True)
class MemoryServices:
    """Typed subcontainer for memory pipeline services."""
    nearline_memory: Any = None
    session_search_index: Any = None
    consolidator: Any = None
    dream: Any = None
    memory_governance: Any = None


@dataclass(frozen=True)
class BackgroundServices:
    """Typed subcontainer for background/async services."""
    background_review: Any = None
    curator: Any = None
    cognitive_loop: Any = None
    cognitive_scheduler: Any = None
    cognitive_audit: Any = None
    session_cold_archive: Any = None
    rolling_episode_compaction: Any = None
    auto_compact: Any = None


@dataclass(frozen=True)
class StoreServices:
    """Typed subcontainer for persistent stores."""
    file_state_store: Any = None
    confirmation_store: Any = None
    confirmation_manager: Any = None
    grant_store: Any = None
    cron_service: Any = None


@dataclass(frozen=True)
class RuntimeDependencies:
    """Immutable dependency bundle for AgentRuntime.

    Strangler Fig migration: flat fields coexist with typed subcontainers.
    During migration, both ``deps.tools`` and ``deps.core.tools`` return
    the same object. Phase 6 will remove the flat fields.

    All dependencies are injected at construction time.  AgentRuntime
    never reaches back to AgentLoop — every method receives context
    explicitly.
    """

    config: RuntimeConfig = RuntimeConfig()

    # ── Existing flat fields (keep for backward compat) ────────────────────
    # Infrastructure
    state_holder: Any = None  # SessionStateHolder
    host: Any = None  # AgentHost

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
    turn_orchestrator: Any = None
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
    sqlite_stores: Any = None  # SqliteStoreRegistry

    # ── Strangler Fig typed subcontainers (Phase 0: dual-path coexistence) ─
    # During migration, both flat fields and subcontainers return the same
    # objects. Phase 6 will remove the flat fields above.
    core: CoreServices | None = None
    meta_cognition_svc: MetaCognitionServices | None = None
    memory_svc: MemoryServices | None = None
    background_svc: BackgroundServices | None = None
    stores: StoreServices | None = None


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
        return format_tool_hints(tool_calls, max_length=self._deps.tool_hint_max_length or 40)

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
        return strip_think(text) or None

    @staticmethod
    def _runtime_chat_id(msg: Any) -> str:
        """Return the chat id shown in runtime metadata for the model."""
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
        return snapshot_for_trigger(trigger)

    # ── Cognitive ───────────────────────────────────────────────

    def _build_cognitive_runtime_context(self, session_key: str) -> Any:
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
            message = InboundMessage(
                channel="system", sender_id="agent_cognitive",
                chat_id=record.chat_id or session_key, content=content,
                session_key_override=session_key,
                # P4 (方案 A): 系统触发的 scheduled reminder 走 inbound_internal
                # 队列，与用户消息隔离。
                is_internal=True,
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
        if isinstance(item, dict) and isinstance(item.get("event"), CognitiveEvent):
            return item["event"]
        candidate = item.get("candidate") if isinstance(item, dict) and "candidate" in item else item
        priority = "medium"
        if getattr(candidate, "intent_type", "") in {"pending_confirmation_nudge", "goal_nudge"}:
            priority = "high"
        return CognitiveEvent(
            event_id=str(getattr(candidate, "intent_id", "")),
            session_key=session_key,
            event_type=getattr(candidate, "intent_type", "goal_nudge"),  # type: ignore[arg-type]
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
        classification = classify_confirmation_reply(reply)
        if classification not in {"confirmed", "rejected"}:
            return None, False
        result = d.confirmation_manager.resolve_user_reply(confirmation.confirmation_id, reply)
        tool_name = confirmation.metadata.get("tool_name") or confirmation.action or "tool"
        if result.decision == "confirmed":
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
        max_chars = self._deps.max_tool_result_chars
        return TurnPersistManager(max_chars, self._deps.sessions).sanitize_persisted_blocks(
            content, should_truncate_text=should_truncate_text, drop_runtime=drop_runtime,
        )

    def _save_turn(self, session: Any, messages: list[dict], skip: int) -> None:
        max_chars = self._deps.max_tool_result_chars
        TurnPersistManager(max_chars, self._deps.sessions).save_turn(session, messages, skip)

    def _persist_subagent_followup(self, session: Any, msg: Any) -> bool:
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
            "profile_ref": profile_ref,
            # 截断长度由 ContextConfig.recent_turns_summary_max_chars 控制（默认 800），
            # 配置访问路径：self._deps.context._context_config（与 enable_phase1_continuity 等同源）
            "recent_turns_summary": self._extract_recent_turns_summary(
                session,
                max_chars=self._deps.context._context_config.recent_turns_summary_max_chars,
            ),
            # 冷区索引：渐进式暴露 warm 区归档总结的索引视图，
            # 只保留 turn_range/summary/key_entities，完整结构化字段通过 locator 回查，
            # 避免 checkpoint 膨胀为"缓慢的庞然大物"
            "cold_indices": self._collect_cold_indices(session.key),
            "updated_at": _utcnow_iso(),
        }
        session.metadata["continuity_checkpoint_v1"] = checkpoint
        log_event(
            "continuity.checkpoint.saved",
            session_key=session.key,
            field_count=len([k for k, v in checkpoint.items() if v]),
            recent_turns_count=len(checkpoint.get("recent_turns_summary") or []),
            cold_indices_count=len(checkpoint.get("cold_indices") or []),
            current_goal_present=bool(checkpoint.get("current_goal")),
            open_loops_count=len(checkpoint.get("open_loops") or []),
        )
        return checkpoint

    def _collect_cold_indices(self, session_key: str) -> list[dict[str, Any]]:
        """从 warm_summaries.jsonl 读取当前 session 的最近 5 条冷区索引。

        渐进式暴露设计：checkpoint 只承载索引视图（turn_range/summary/key_entities），
        完整的 commitments/decisions/open_questions 等结构化字段保留在 warm_summaries.jsonl
        原始归档中，Agent 需要细节时通过 locator 回查 warm_archive，避免一次性把所有
        历史总结塞入 checkpoint 导致上下文膨胀。
        """
        d = self._deps
        if not d.workspace:
            return []
        summaries_path = Path(d.workspace) / "warm_summaries.jsonl"
        if not summaries_path.exists():
            return []
        matched: list[dict[str, Any]] = []
        try:
            for line in summaries_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if entry.get("session_key") != session_key:
                    continue
                matched.append(entry)
        except Exception:
            return []
        # 文件以追加模式写入，末尾即最新；倒序后取最近 5 条
        matched.reverse()
        return [
            {
                "turn_range": str(item.get("turn_range", "")),
                "summary": str(item.get("summary", "")),
                "key_entities": [str(e) for e in (item.get("key_entities") or [])],
            }
            for item in matched[:5]
        ]

    @staticmethod
    def _extract_recent_turns_summary(session: Any, max_chars: int = 800) -> list[dict[str, str]]:
        """从 session 历史中提取最近 2 轮对话摘要（2 user + 2 assistant）

        max_chars 控制每条消息的截断长度，默认 800（向后兼容）。
        配置来源：ContextConfig.recent_turns_summary_max_chars，由
        _save_continuity_checkpoint 调用时传入。
        """
        # Session 类使用 messages 属性；兼容可能使用 history 的 mock
        history = getattr(session, "messages", None)
        if history is None:
            history = getattr(session, "history", None) or []
        # 取最后 4 条消息（2 轮 = 2 user + 2 assistant）
        recent = history[-4:] if len(history) >= 4 else history
        summary: list[dict[str, str]] = []
        for msg in recent:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "")
            content = msg.get("content", "")
            # content 可能是 list（多模态）或 str
            if isinstance(content, list):
                text_parts: list[str] = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        text_parts.append(part)
                content = " ".join(text_parts)
            # 截断到 max_chars 字符（可配置，避免长对话关键信息丢失）
            content = str(content)[:max_chars]
            if role in (RoleConstants.USER, RoleConstants.ASSISTANT) and content.strip():
                summary.append({"role": role, "content": content})
        return summary[-4:]  # 最多 4 条（2 轮）

    @staticmethod
    def _load_continuity_checkpoint(session: Any, workspace: Any = None) -> dict | None:
        """加载 continuity checkpoint 并执行三级状态重建（Phase 6 Task 9）。

        三级重建设计意图：
        - 热区：由 ``session.get_hot_history`` 在 ``state_build`` 阶段从
          ``session.messages`` 尾部取 50 轮，此处不重复处理；
        - 温区：从 ``session.metadata["warm_buffer"]`` 恢复，``WarmStore``
          无状态地读取 session.metadata，重启后天然可用；
        - 冷区：优先从 ``workspace/warm_summaries.jsonl`` 读取最近 5 条索引
          （按 session_key 过滤），确保后台总结任务生成的新条目能被及时感知；
          workspace 不可用时回退到 checkpoint 中已保存的索引。
        """
        raw = session.metadata.get("continuity_checkpoint_v1")
        if not isinstance(raw, dict):
            return None
        # 温区状态恢复：WarmStore 从 session.metadata 读取，无需额外持久化
        warm_buffer = WarmStore().load(session)
        # 冷区索引恢复：优先从 warm_summaries.jsonl 读取最新条目
        cold_indices = AgentRuntime._read_cold_indices(workspace, session.key)
        # 记录冷区索引来源：是否由 workspace 的 warm_summaries.jsonl 提供
        # （用于日志区分 warm_summaries 与 checkpoint_embedded 两条恢复路径）
        cold_indices_from_workspace = bool(cold_indices)
        if not cold_indices:
            # workspace 不可用或文件不存在时，回退到 checkpoint 中已保存的索引
            cold_indices = [
                {
                    "turn_range": str(item.get("turn_range", "")),
                    "summary": str(item.get("summary", "")),
                    "key_entities": [str(e) for e in (item.get("key_entities") or [])],
                }
                for item in raw.get("cold_indices", [])
                if isinstance(item, dict)
            ][:5]
        checkpoint = {
            "session_key": str(raw.get("session_key") or session.key),
            "current_goal": _trim_text(raw.get("current_goal"), max_chars=1000),
            "current_plan": [str(item).strip() for item in raw.get("current_plan", []) if str(item).strip()][:8],
            "open_loops": [str(item).strip() for item in raw.get("open_loops", []) if str(item).strip()][:8],
            "active_constraints": [str(item).strip() for item in raw.get("active_constraints", []) if str(item).strip()][:8],
            "pending_confirmation_refs": [dict(item) for item in raw.get("pending_confirmation_refs", []) if isinstance(item, dict)][:8],
            "recent_turns_summary": [dict(item) for item in raw.get("recent_turns_summary", []) if isinstance(item, dict)][:4],
            # 温区状态摘要（三级重建 - 温区）：只保留计数字段，避免上下文膨胀
            "warm_buffer": {
                "turn_count": int(warm_buffer.get("turn_count", 0)),
                "message_count": len(warm_buffer.get("messages", [])),
                "updated_at": str(warm_buffer.get("updated_at", "")),
            },
            # 冷区索引（三级重建 - 冷区）：最近 5 条温区总结的索引条目
            "cold_indices": cold_indices,
            "updated_at": str(raw.get("updated_at") or "").strip(),
        }
        log_event(
            "continuity.checkpoint.loaded",
            session_key=session.key,
            source=("warm_summaries" if cold_indices_from_workspace else "checkpoint_embedded"),
            field_count=len([k for k, v in checkpoint.items() if v]) if checkpoint else 0,
            recent_turns_count=len(checkpoint.get("recent_turns_summary") or []) if checkpoint else 0,
            cold_indices_count=len(checkpoint.get("cold_indices") or []) if checkpoint else 0,
        )
        return checkpoint

    @staticmethod
    def _read_cold_indices(workspace: Any, session_key: str) -> list[dict[str, Any]]:
        """从 ``warm_summaries.jsonl`` 读取最近 5 条冷区索引（按 session_key 过滤）。

        冷区索引是温区总结归档后生成的结构化条目，用于在 session 恢复时
        让 Agent 感知历史长程对话的存在。文件以追加模式写入，末尾即最新。
        """
        if workspace is None:
            return []
        try:
            summaries_path = Path(workspace) / "warm_summaries.jsonl"
            if not summaries_path.exists():
                return []
        except Exception:
            return []
        entries: list[dict[str, Any]] = []
        try:
            for line in summaries_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if entry.get("session_key") != session_key:
                    continue
                entries.append({
                    "turn_range": str(entry.get("turn_range", "")),
                    "summary": str(entry.get("summary", "")),
                    "key_entities": [str(e) for e in (entry.get("key_entities") or [])],
                })
        except Exception:
            logger.exception("Failed to read cold indices from warm_summaries.jsonl")
            return []
        # 取最近 5 条（文件追加写入，末尾即最新）
        return entries[-5:]

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
        d = self._deps
        if (mt := d.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            if not had_injections or stop_reason == "empty_final_response":
                return None

        # Cron 触发的 turn 不通过 channel 系统回流产出——cron 通道是 inbound-only
        # 的内部触发源，没有实现类，OutboundMessage 发到这里只会被静默丢弃。
        # 真正的用户通知由 on_cron_job 回调（cli/commands.py）通过
        # job.payload.deliver/to 投递到真实通道（如 telegram）。
        # 在此处短路返回 None，避免产生 "Response to cron:cron" 日志和被丢弃的
        # OutboundMessage，消除路径 A 的浪费与身份污染。
        if getattr(msg, "channel", None) == "cron":
            logger.debug(
                "cron session turn completed; outbound suppressed (deliver via on_cron_job payload): {}",
                getattr(msg, "chat_id", "?"),
            )
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
            content=content or "", media=generated_media,
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
        _sensitive_names: frozenset[str] = frozenset({"exec", "message", "web_fetch"})
        _sensitive_prefixes: tuple[str, ...] = ("originagent_device_",)

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
            tool_hint_max_length=d.tool_hint_max_length or 40,
            set_tool_context=self._set_tool_context,
            on_iteration=_on_iteration,
            actor_id=actor_id,
            trigger=trigger,
            capability_snapshot=_cap_snapshot,
            sensitive_tool_log_names=_sensitive_names,
            sensitive_tool_log_prefixes=_sensitive_prefixes,
        )
        hook: AgentHook = (
            CompositeHook([loop_hook] + (d.extra_hooks or []))  # type: ignore[arg-type]
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
                merged = [runtime_block, *d.context._build_user_content(content, media)]
                return {"role": RoleConstants.USER, "content": merged}

            def _to_system_event(pending_msg: InboundMessage) -> dict:
                """将内部事件包装为 system role，避免 LLM 误解为用户指令"""
                content = pending_msg.content
                runtime_block = d.context.build_runtime_context_block(
                    pending_msg.channel,
                    self._runtime_chat_id(pending_msg),
                    d.context.timezone,
                )
                event_type = pending_msg.metadata.get("injected_event") or "subagent_result"
                merged = [runtime_block, d.context.build_internal_event_block(event_type, content)]
                return {"role": RoleConstants.SYSTEM, "content": merged}

            def _is_internal_event(pending_msg: InboundMessage) -> bool:
                """判断是否为内部事件（不应伪装为 user role）"""
                return (
                    pending_msg.metadata.get("injected_event") in ("active_intent", "subagent_result")
                    or pending_msg.sender_id == "subagent"
                )

            items: list[dict] = []
            while len(items) < limit:
                try:
                    pending_msg = pending_queue.get_nowait()
                    # 内部事件使用 system role，避免 LLM 误解为用户指令
                    if _is_internal_event(pending_msg):
                        items.append(_to_system_event(pending_msg))
                    else:
                        items.append(_to_user_message(pending_msg))
                except asyncio.QueueEmpty:
                    break
            if (not items and session is not None and d.subagents is not None
                    and d.subagents.get_running_count_by_session(session.key) > 0):
                try:
                    # Sub-agent completion wait (5 min) — not a tool/HTTP timeout, not in TimeoutConfig (spec 3.4)
                    msg = await asyncio.wait_for(pending_queue.get(), timeout=300)
                except asyncio.TimeoutError:
                    logger.warning("Timeout waiting for sub-agent completion in session {}", session.key)
                    return items
                if _is_internal_event(msg):
                    items.append(_to_system_event(msg))
                else:
                    items.append(_to_user_message(msg))
                while len(items) < limit:
                    try:
                        pending_msg = pending_queue.get_nowait()
                        if _is_internal_event(pending_msg):
                            items.append(_to_system_event(pending_msg))
                        else:
                            items.append(_to_user_message(pending_msg))
                    except asyncio.QueueEmpty:
                        break
            return items

        active_session_key = session.key if session else session_key
        file_state_token = bind_file_states(d.file_state_store.for_session(active_session_key))
        try:
            result = await d.runner.run(AgentRunSpec(
                initial_messages=initial_messages,
                tools=d.tools,
                model=d.model or "",
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
                # Phase 1: pass SessionManager so _run_tool_core can persist
                # denied_tools to session.metadata (session-scoped short-circuit).
                # Only the main conversation path needs this; dream/subagent
                # paths leave sessions=None (no session-level persistence).
                sessions=d.sessions,
            ))
        finally:
            reset_file_states(file_state_token)

        # Refresh state.last_context_assembly so it reflects the messages
        # actually sent to the LLM (after runner governance such as
        # _snip_history), not just the pre-governance snapshot written by
        # ContextBudgetManager in _build_initial_messages.
        # 规则5: this cached audit must reflect the final sent state.
        # 规则7: single write path here — the audit no longer goes stale.
        if session is not None and result.last_sent_messages:
            state = d.state_holder.get(session.key)
            if state is not None:
                existing = dict(state.last_context_assembly or {})
                budget_audit = dict(existing.get("budget") or {})
                budget_audit["final_sent_message_count"] = len(result.last_sent_messages)
                budget_audit["runner_governance_applied"] = (
                    len(result.last_sent_messages) != len(initial_messages)
                )
                existing["budget"] = budget_audit
                state.last_context_assembly = existing

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
        try:
            result = await reflector.reflect_turn(
                session_key=session_key, turn_id=turn_id,
                turn_snapshot=turn_snapshot, accepted_triggers=accepted_triggers,
                runtime_context=runtime_context,
            )
        except Exception:
            logger.exception("Meta cognition reflection crashed for turn {}", turn_id)
            return
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

    # ── Message dispatch ────────────────────────────────────────

    async def _process_message(
        self,
        msg: Any,
        session_key: str | None = None,
        on_progress: Any = None,
        on_stream: Any = None,
        on_stream_end: Any = None,
        pending_queue: Any = None,
        capability_snapshot: Any = None,
    ) -> Any | None:
        """Process a single inbound message and return the response."""
        sk = session_key or getattr(msg, "session_key", None) or "unknown"
        _new_trace()
        set_session(sk)
        turn_id = f"{getattr(msg, 'channel', 'msg')}:{_time.time_ns()}"
        with span("turn.process", attrs={"session_key": sk, "turn_id": turn_id}):
            return await self._deps.turn_orchestrator.process_message(
                msg, session_key=session_key, on_progress=on_progress,
                on_stream=on_stream, on_stream_end=on_stream_end,
                pending_queue=pending_queue, capability_snapshot=capability_snapshot,
            )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        media: list[str] | None = None,
        on_progress: Any = None,
        on_stream: Any = None,
        on_stream_end: Any = None,
    ) -> Any | None:
        """Process a message directly and return the outbound payload."""
        _new_trace()
        set_session(session_key)
        await self._deps.host._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id,
                             content=content, media=media or [])
        return await self._process_message(msg, session_key=session_key,
                                           on_progress=on_progress, on_stream=on_stream,
                                           on_stream_end=on_stream_end)

    def _write_continuity_runtime_identity(self, session: Any, runtime_context: Any) -> None:
        if runtime_context is None:
            return
        _continuity_runtime_identity_key = "continuity_runtime_identity_v1"
        session.metadata[_continuity_runtime_identity_key] = {
            "user_id": runtime_context.user_id, "device_id": runtime_context.device_id,
            "session_id": runtime_context.session_id, "scope": runtime_context.default_scope,
            "updated_at": _utcnow_iso(),
        }

    def _archive_session_file_cap(self, messages: list[dict], *, session_key: str, reason: str) -> None:
        d = self._deps
        if d.session_cold_archive is not None:
            d.session_cold_archive.archive(session_key, messages, reason=reason)
        d.context.memory.raw_archive(messages)

    # ── Message construction ────────────────────────────────────

    def _build_prompt_self_model(self) -> dict:
        d = self._deps
        introspection = d.introspection
        snapshot = introspection.runtime_context_snapshot() if introspection is not None else {}
        return SelfModelService(
            d.workspace,
            audit_mode=getattr(d.tool_audit_config, "mode", "standard"),
            runtime_profile=d.runtime_profile,
            domain_pack_manager=d.domain_packs,
            skills_loader=d.context.skills,
            memory_store=d.context.memory,
            nearline_memory_config=d.nearline_memory,
            runtime_snapshot=snapshot,
        ).build()

    def _persist_user_message_early(
        self, msg: Any, session: Any, pending_ask_id: str | None, **kwargs: Any
    ) -> bool:
        return TurnPersistManager(
            self._deps.max_tool_result_chars, self._deps.sessions
        ).persist_user_message_early(msg, session, pending_ask_id, **kwargs)

    def _snapshot_context_assembly_from_messages(
        self, messages: list[dict], *, session_key: str | None, runtime_context: Any = None,
    ) -> dict:
        user_blocks: list[dict] = []
        for message in reversed(messages):
            if message.get("role") != RoleConstants.USER:
                continue
            content = message.get("content")
            if isinstance(content, list):
                user_blocks = [block for block in content if isinstance(block, dict)]
                break
        return {
            "enabled": bool(self._deps.context._context_config.enable_phase1_continuity),
            "session_key": session_key,
            "runtime_context": (
                {
                    "actor_id": runtime_context.actor_id, "user_id": runtime_context.user_id,
                    "session_id": runtime_context.session_id, "device_id": runtime_context.device_id,
                    "trigger": runtime_context.trigger, "source": runtime_context.source,
                    "scope": runtime_context.default_scope,
                } if runtime_context is not None else {}
            ),
            "block_kinds": [block.get("_meta", {}).get("kind") for block in user_blocks],
            "reference_sources": [
                block.get("_meta", {}).get("source") for block in user_blocks
                if block.get("_meta", {}).get("kind") == "reference_context"
            ],
            "current_message_preview": next(
                (str(block.get("text") or "")[:200] for block in reversed(user_blocks)
                 if block.get("type") == "text" and not block.get("_meta")),
                "",
            ),
        }

    def _build_initial_messages(
        self,
        msg: Any,
        session: Any,
        history: list[dict],
        pending_ask_id: str | None,
        pending_summary: str | None,
        internal_event: tuple | None = None,
        recovered_continuity_block: dict | None = None,
    ) -> list[dict]:
        """Build the initial message list for the LLM turn."""
        d = self._deps
        self_model_payload = self._build_prompt_self_model()
        # capability_snapshot 已在 state_build 阶段由 set_tool_context 设置到 tools 上
        # （agent_turn_pipeline.py:489-498），此处读取用于注入 system prompt 的能力边界声明
        capability_snapshot = getattr(d.tools, "_capability_snapshot", None)
        if pending_ask_id:
            system_prompt = d.context.build_system_prompt(
                channel=msg.channel, session_summary=pending_summary,
                self_model_payload=self_model_payload,
                capability_snapshot=capability_snapshot,
            )
            messages = ask_user_tool_result_messages(
                system_prompt, history, pending_ask_id,
                image_generation_prompt(msg.content, msg.metadata),
            )
            if d.context._context_config.enable_phase1_continuity:
                assembled = d.context.assemble_user_content(
                    current_message=None, media=None,
                    channel=msg.channel, chat_id=self._runtime_chat_id(msg),
                    sender_id=msg.sender_id, session_summary=pending_summary,
                    session_metadata=session.metadata, internal_event=None,
                    runtime_context=d.state_holder.get(session.key).last_runtime_context,
                    session_key=session.key,
                    recovered_continuity_block=recovered_continuity_block,
                    include_current_message=False,
                )
                d.state_holder.get(session.key).last_context_assembly = dict(assembled.audit)
                messages.append({"role": RoleConstants.USER, "content": assembled.blocks})
                return d.context._apply_prompt_budget(
                    messages,
                    context_window_tokens=d.context_window_tokens,
                    max_completion_tokens=getattr(d.provider.generation, "max_tokens", 4096),
                )
            messages.append({
                "role": RoleConstants.USER,
                "content": [
                    d.context.build_runtime_context_block(
                        msg.channel, self._runtime_chat_id(msg),
                        d.context.timezone, sender_id=msg.sender_id,
                        session_metadata=session.metadata,
                    ),
                    *([recovered_continuity_block] if recovered_continuity_block is not None else []),
                    *d.context.build_reference_context_blocks(
                        session_summary=pending_summary, session_key=session.key,
                        runtime_context=d.state_holder.get(session.key).last_runtime_context,
                        current_message=msg.content,
                    ),
                ],
            })
            d.state_holder.get(session.key).last_context_assembly = {
                "enabled": False,
                "session_key": session.key,
                "reason": "phase1_continuity_disabled",
                "block_kinds": [d.context.RUNTIME_CONTEXT_KIND] + [
                    block.get("_meta", {}).get("kind")
                    for block in d.context.build_reference_context_blocks(
                        session_summary=pending_summary, session_key=session.key,
                        runtime_context=d.state_holder.get(session.key).last_runtime_context,
                        current_message=msg.content,
                    )
                ],
            }
            return d.context._apply_prompt_budget(
                messages,
                context_window_tokens=d.context_window_tokens,
                max_completion_tokens=getattr(d.provider.generation, "max_tokens", 4096),
            )
        built = d.context.build_messages(
            history=history,
            current_message=image_generation_prompt(msg.content, msg.metadata),
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=self._runtime_chat_id(msg),
            sender_id=msg.sender_id, session_summary=pending_summary,
            session_metadata=session.metadata, internal_event=internal_event,
            self_model_payload=self_model_payload,
            runtime_context=d.state_holder.get(session.key).last_runtime_context,
            session_key=session.key,
            recovered_continuity_block=recovered_continuity_block,
            context_window_tokens=d.context_window_tokens,
            max_completion_tokens=getattr(d.provider.generation, "max_tokens", 4096),
            capability_snapshot=capability_snapshot,
        )
        state = d.state_holder.get(session.key)
        state.last_context_assembly = dict(
            getattr(d.context, "_last_context_assembly_audit", {}) or {}
        )
        if not state.last_context_assembly:
            state.last_context_assembly = self._snapshot_context_assembly_from_messages(
                built, session_key=session.key,
                runtime_context=state.last_runtime_context,
            )
        return built
