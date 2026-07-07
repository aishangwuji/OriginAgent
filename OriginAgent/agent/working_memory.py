"""Working memory manager for continuity Phase 1."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from OriginAgent.agent.reminders import ReminderStore
from OriginAgent.agent.scope import IdentityDescriptor, ScopeResolver
from OriginAgent.session.goal_state import goal_state_raw, parse_goal_state
from OriginAgent.session.manager import Session, SessionManager


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

    def load(self, session: Session, *, identity: IdentityDescriptor | None = None) -> WorkingMemorySnapshot:
        snapshot = WorkingMemorySnapshot.from_json(
            session.key,
            session.metadata.get(WORKING_MEMORY_METADATA_KEY),
        )
        if identity is not None and not snapshot.owner_id:
            snapshot.owner_id = identity.user_id
        self._hydrate_goal(snapshot, session)
        self._hydrate_due_reminders(snapshot)
        return snapshot

    def save(self, session: Session, snapshot: WorkingMemorySnapshot) -> WorkingMemorySnapshot:
        # 冲突验证：对比现有值，避免 LLM confabulated 的内容覆盖真实记忆，
        # 形成自我强化的虚假记忆。仅在明确冲突时保守地保留现有值。
        existing_raw = session.metadata.get(WORKING_MEMORY_METADATA_KEY)
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
