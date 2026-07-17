"""Working memory manager for continuity Phase 1."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from OriginAgent.agent.reminders import ReminderStore
from OriginAgent.agent.scope import IdentityDescriptor, ScopeResolver
from OriginAgent.session.goal_state import goal_state_raw, parse_goal_state
from OriginAgent.session.manager import Session, SessionManager
from OriginAgent.utils.tracing import log_event


WORKING_MEMORY_METADATA_KEY = "working_memory_v1"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_items(values: Any, *, limit: int = 8, max_chars: int = 240) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if not text:
            continue
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "..."
        out.append(text)
        if len(out) >= limit:
            break
    return out


@dataclass
class WorkingMemorySnapshot:
    session_key: str
    scope: str = "session"
    owner_id: str | None = None
    current_goal: str = ""
    current_plan: list[str] = field(default_factory=list)
    open_loops: list[str] = field(default_factory=list)
    active_constraints: list[str] = field(default_factory=list)
    pending_questions: list[str] = field(default_factory=list)
    priority_facts: list[str] = field(default_factory=list)
    attention_items: list[str] = field(default_factory=list)
    tool_residue: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=_utcnow_iso)
    expires_at: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, session_key: str, raw: Any) -> "WorkingMemorySnapshot":
        if not isinstance(raw, dict):
            return cls(session_key=session_key)
        return cls(
            session_key=str(raw.get("session_key") or session_key),
            scope=ScopeResolver.normalize_scope(raw.get("scope")),
            owner_id=str(raw.get("owner_id")).strip() if raw.get("owner_id") else None,
            current_goal=str(raw.get("current_goal") or "").strip()[:1000],
            current_plan=_normalize_items(raw.get("current_plan")),
            open_loops=_normalize_items(raw.get("open_loops")),
            active_constraints=_normalize_items(raw.get("active_constraints")),
            pending_questions=_normalize_items(raw.get("pending_questions")),
            priority_facts=_normalize_items(raw.get("priority_facts")),
            attention_items=_normalize_items(raw.get("attention_items")),
            tool_residue=_normalize_items(raw.get("tool_residue")),
            updated_at=str(raw.get("updated_at") or _utcnow_iso()),
            expires_at=str(raw.get("expires_at")).strip() if raw.get("expires_at") else None,
        )


class WorkingMemoryManager:
    """Session-backed structured working set.

    Working memory is a session-bound runtime surface in continuity v1. Reads
    and writes are keyed by the current session and do not perform any
    cross-session visibility propagation on their own.
    """

    def __init__(
        self,
        sessions: SessionManager,
        *,
        reminder_store: ReminderStore | None = None,
    ) -> None:
        self._sessions = sessions
        self._reminder_store = reminder_store
        # turn-scoped cache：同一 session.key 的连续 load 调用复用同一份
        # deserialized snapshot，避免每轮 8 次 load 重复触发反序列化 + 衰减 +
        # hydration + log_event。save / clear 主动失效缓存。
        self._cache: dict[str, WorkingMemorySnapshot] = {}
        # 状态签名去重：跨 turn 缓存失效但 snapshot 内容未变时，抑制
        # working_memory.loaded/saved 事件，避免日志噪音。键为 session.key，
        # 值为最近一次已发射事件对应的 snapshot 签名。
        self._last_emitted_signature: dict[str, str] = {}

    def load(self, session: Session, *, identity: IdentityDescriptor | None = None) -> WorkingMemorySnapshot:
        cached = self._cache.get(session.key)
        if cached is not None:
            return cached
        snapshot = WorkingMemorySnapshot.from_json(
            session.key,
            session.metadata.get(WORKING_MEMORY_METADATA_KEY),
        )
        if identity is not None and not snapshot.owner_id:
            snapshot.owner_id = identity.user_id
        # 时间衰减：在 goal 注入前清空陈旧的易失真字段，
        # 避免 LLM confabulate 的内容被每轮反复注入形成自我强化的虚假记忆。
        self._apply_field_decay(snapshot)
        self._hydrate_goal(snapshot, session)
        self._hydrate_due_reminders(snapshot)
        signature = self._compute_signature(snapshot)
        last_sig = self._last_emitted_signature.get(session.key)
        if last_sig != signature:
            log_event(
                "working_memory.loaded",
                session_key=session.key,
                has_goal=bool(snapshot.current_goal),
                open_loops_count=len(snapshot.open_loops or []),
                attention_items_count=len(snapshot.attention_items or []),
                priority_facts_count=len(snapshot.priority_facts or []),
                owner_id=snapshot.owner_id,
            )
            self._last_emitted_signature[session.key] = signature
        else:
            logger.debug(
                "working_memory.loaded suppressed (state unchanged) for session {}",
                session.key,
            )
        self._cache[session.key] = snapshot
        return snapshot

    def save(self, session: Session, snapshot: WorkingMemorySnapshot) -> WorkingMemorySnapshot:
        # 冲突验证：对比现有值，避免 LLM confabulated 的内容覆盖真实记忆，
        # 形成自我强化的虚假记忆。仅在明确冲突时保守地保留现有值。
        existing_raw = session.metadata.get(WORKING_MEMORY_METADATA_KEY)
        existing: WorkingMemorySnapshot | None = None
        if isinstance(existing_raw, dict):
            existing = WorkingMemorySnapshot.from_json(session.key, existing_raw)
            # current_goal 冲突：现有值非空且新值完全不同（非子串关系）时保留现有值
            if (
                existing.current_goal
                and snapshot.current_goal
                and existing.current_goal != snapshot.current_goal
                and existing.current_goal not in snapshot.current_goal
                and snapshot.current_goal not in existing.current_goal
            ):
                logger.warning(
                    "Working memory current_goal conflict: existing='{}', new='{}'. Keeping existing value.",
                    existing.current_goal[:100],
                    snapshot.current_goal[:100],
                )
                snapshot.current_goal = existing.current_goal
            # attention_items 冲突：现有列表非空且新列表完全无交集时保留现有值
            if (
                existing.attention_items
                and snapshot.attention_items
                and set(existing.attention_items) != set(snapshot.attention_items)
                and not any(item in existing.attention_items for item in snapshot.attention_items)
            ):
                logger.warning(
                    "Working memory attention_items conflict: keeping existing {} items.",
                    len(existing.attention_items),
                )
                snapshot.attention_items = existing.attention_items

        snapshot.updated_at = _utcnow_iso()
        session.metadata[WORKING_MEMORY_METADATA_KEY] = snapshot.to_json()
        signature = self._compute_signature(snapshot)
        last_sig = self._last_emitted_signature.get(session.key)
        if last_sig != signature:
            log_event(
                "working_memory.saved",
                session_key=session.key,
                goal_changed=(existing.current_goal != snapshot.current_goal) if existing else True,
                attention_items_count=len(snapshot.attention_items or []),
            )
            self._last_emitted_signature[session.key] = signature
        else:
            logger.debug(
                "working_memory.saved suppressed (state unchanged) for session {}",
                session.key,
            )
        self._cache[session.key] = snapshot
        return snapshot

    def upsert(
        self,
        session: Session,
        *,
        identity: IdentityDescriptor | None = None,
        current_goal: str | None = None,
        current_plan: list[str] | None = None,
        open_loops: list[str] | None = None,
        active_constraints: list[str] | None = None,
        pending_questions: list[str] | None = None,
        priority_facts: list[str] | None = None,
        attention_items: list[str] | None = None,
        tool_residue: list[str] | None = None,
        scope: str | None = None,
    ) -> WorkingMemorySnapshot:
        snapshot = self.load(session, identity=identity)
        if identity is not None:
            snapshot.owner_id = identity.user_id
        if current_goal is not None:
            snapshot.current_goal = str(current_goal).strip()[:1000]
        if current_plan is not None:
            snapshot.current_plan = _normalize_items(current_plan)
        if open_loops is not None:
            snapshot.open_loops = _normalize_items(open_loops)
        if active_constraints is not None:
            snapshot.active_constraints = _normalize_items(active_constraints)
        if pending_questions is not None:
            snapshot.pending_questions = _normalize_items(pending_questions)
        if priority_facts is not None:
            snapshot.priority_facts = _normalize_items(priority_facts)
        if attention_items is not None:
            snapshot.attention_items = _normalize_items(attention_items)
        if tool_residue is not None:
            snapshot.tool_residue = _normalize_items(tool_residue)
        if scope is not None:
            snapshot.scope = ScopeResolver.normalize_scope(scope)
        return self.save(session, snapshot)

    def inspect(self, session: Session, *, identity: IdentityDescriptor | None = None) -> dict[str, Any]:
        return self.load(session, identity=identity).to_json()

    def clear(self, session: Session) -> None:
        session.metadata.pop(WORKING_MEMORY_METADATA_KEY, None)
        self._cache.pop(session.key, None)
        # 清除签名基线：clear 后再次 load 会得到空 snapshot，
        # 其签名可能与已清空的旧 snapshot 不同，应重新发射事件。
        self._last_emitted_signature.pop(session.key, None)

    def append_attention_item(
        self,
        session: Session,
        item: str,
        *,
        identity: IdentityDescriptor | None = None,
    ) -> WorkingMemorySnapshot:
        text = str(item or "").strip()
        snapshot = self.load(session, identity=identity)
        if text:
            snapshot.attention_items = _normalize_items([*snapshot.attention_items, text])
        return self.save(session, snapshot)

    def append_pending_question(
        self,
        session: Session,
        question: str,
        *,
        identity: IdentityDescriptor | None = None,
    ) -> WorkingMemorySnapshot:
        text = str(question or "").strip()
        snapshot = self.load(session, identity=identity)
        if text:
            snapshot.pending_questions = _normalize_items([*snapshot.pending_questions, text])
        return self.save(session, snapshot)

    @staticmethod
    def _compute_signature(snapshot: WorkingMemorySnapshot) -> str:
        """基于易变字段计算稳定签名，用于日志去重。

        仅覆盖 current_goal / open_loops / attention_items / priority_facts，
        不包含 updated_at 等每次 save 都会变化的字段，否则去重失效。
        """
        payload = json.dumps({
            "g": snapshot.current_goal,
            "ol": snapshot.open_loops,
            "ai": snapshot.attention_items,
            "pf": snapshot.priority_facts,
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _apply_field_decay(snapshot: WorkingMemorySnapshot) -> None:
        # 基于 updated_at 的时间衰减：易失真字段（attention_items 等）一旦写入
        # 即被每轮注入，LLM confabulate 的内容会形成自我强化的虚假记忆。
        # 超过 30 分钟则清空，current_goal 由 _hydrate_goal 独立判断，
        # priority_facts 相对稳定不清空。时间解析失败时容错降级不清空。
        updated_at = str(snapshot.updated_at or "").strip()
        if not updated_at:
            # 缺失时间字段时不阻塞加载，保留原值
            return
        try:
            updated_time = datetime.fromisoformat(updated_at)
            if updated_time.tzinfo is None:
                updated_time = updated_time.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            # 格式错误时不阻塞加载，保留原值（容错降级）
            return
        age = datetime.now(timezone.utc) - updated_time
        if age <= timedelta(minutes=30):
            return
        # 衰减作用域：这些字段易被 LLM confabulate，超时即清空
        snapshot.attention_items = []
        snapshot.open_loops = []
        snapshot.active_constraints = []
        snapshot.pending_questions = []
        snapshot.tool_residue = []
        logger.debug(
            "Working memory fields decayed (updated_at={}, age={:.0f}min > 30min)",
            updated_at,
            age.total_seconds() / 60,
        )

    @staticmethod
    def _hydrate_goal(snapshot: WorkingMemorySnapshot, session: Session) -> None:
        goal = parse_goal_state(goal_state_raw(session.metadata))
        if not isinstance(goal, dict) or goal.get("status") != "active":
            return

        # 过期检查：goal_state 的 started_at 超过 30 分钟则不再注入，
        # 避免陈旧 goal 被每轮重复注入形成自我强化的虚假记忆。
        # 容错：时间字段缺失或解析失败时不阻塞，继续注入。
        started_at = str(goal.get("started_at") or "").strip()
        if started_at:
            try:
                goal_time = datetime.fromisoformat(started_at)
                if goal_time.tzinfo is None:
                    goal_time = goal_time.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - goal_time > timedelta(minutes=30):
                    logger.debug(
                        "Goal state expired (started_at={}), skipping hydration",
                        started_at,
                    )
                    return
            except (ValueError, TypeError):
                # 解析失败时不阻塞，继续注入
                pass

        if not snapshot.current_goal:
            snapshot.current_goal = str(goal.get("objective") or "").strip()[:1000]
        summary = str(goal.get("ui_summary") or "").strip()
        if summary:
            line = f"goal_summary: {summary}"
            if line not in snapshot.priority_facts:
                snapshot.priority_facts = [line, *snapshot.priority_facts][:8]

    def _hydrate_due_reminders(self, snapshot: WorkingMemorySnapshot) -> None:
        if self._reminder_store is None:
            return
        try:
            due = self._reminder_store.list_due()
        except Exception:
            return
        pending = list(snapshot.attention_items)
        for record in due:
            if record.session_key != snapshot.session_key:
                continue
            # 加 [reminder] 前缀，与 LLM confabulated 的 attention_items 区分，
            # 避免 reminder 内容被误认为 LLM 生成内容而自我强化。
            reminder_item = f"[reminder] {record.content}"
            if reminder_item not in pending and record.content not in pending:
                pending.append(reminder_item)
        snapshot.attention_items = _normalize_items(pending)
