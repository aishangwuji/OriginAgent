"""Working memory manager for continuity Phase 1."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

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
            pending_questions=_normalize_items(raw.get("pending_questions")),
            priority_facts=_normalize_items(raw.get("priority_facts")),
            attention_items=_normalize_items(raw.get("attention_items")),
            tool_residue=_normalize_items(raw.get("tool_residue")),
            updated_at=str(raw.get("updated_at") or _utcnow_iso()),
            expires_at=str(raw.get("expires_at")).strip() if raw.get("expires_at") else None,
        )


class WorkingMemoryManager:
    """Session-backed structured working set."""

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
            if record.content not in pending:
                pending.append(record.content)
        snapshot.attention_items = _normalize_items(pending)
