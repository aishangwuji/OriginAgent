"""Bounded proactive active-intent support for idle sessions."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from OriginAgent.agent.cognitive_audit import JsonlCognitiveAuditLedger
from OriginAgent.agent.cognitive_events import CognitiveDecision
from OriginAgent.agent.confirmation import PendingConfirmationStore
from OriginAgent.agent.facts import FactStore
from OriginAgent.agent.message_metadata import build_origin_metadata
from OriginAgent.bus.events import InboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.memory.policy import nearline_runtime_enabled
from OriginAgent.memory.store import NearlineMemoryStore
from OriginAgent.session.goal_state import goal_state_raw, parse_goal_state
from OriginAgent.session.manager import Session, SessionManager
from OriginAgent.utils.helpers import ensure_dir, truncate_text

ActiveIntentOutcome = Literal["emitted", "suppressed", "skipped"]
ActiveIntentType = Literal["goal_nudge", "pending_confirmation_nudge", "foresight_nudge"]

_SUMMARY_MAX_CHARS = 240
_RECENT_SCAN_LIMIT = 200
# Maximum nudge attempts for the same confirmation before giving up.
# Prevents infinite re-nudging when the user never responds (e.g., away
# from keyboard). After this many attempts, the confirmation is left
# pending for manual user interaction rather than continuously consuming
# LLM calls. 3 attempts balances "give the agent a few tries" with
# "don't burn tokens on an unresponsive user".
_MAX_NUDGES_PER_CONFIRMATION = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _summarize_text(value: Any, max_chars: int = _SUMMARY_MAX_CHARS) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return truncate_text(text, max_chars).replace("\n... (truncated)", " ...")


def _is_confirmation_expired(confirmation: Any, *, now: datetime | None = None) -> bool:
    """Check if a confirmation has expired.

    Returns False if ``expires_at`` is not set or unparseable — we err on
    the side of NOT filtering (a live confirmation should not be silently
    dropped due to a parse error). Only returns True when ``expires_at``
    is a valid ISO timestamp in the past.
    """
    expires_at = getattr(confirmation, "expires_at", None)
    if not expires_at or not isinstance(expires_at, str):
        return False
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        current_time = now or _utcnow()
        return parsed <= current_time
    except (ValueError, AttributeError):
        return False


@dataclass(frozen=True)
class ActiveIntentConfig:
    enabled: bool = True
    interval_seconds: int = 15
    session_cooldown_seconds: int = 600
    intent_cooldown_seconds: int = 300
    max_messages_per_session_per_pass: int = 1


@dataclass(frozen=True)
class ActiveIntentCandidate:
    intent_type: ActiveIntentType
    intent_id: str
    content: str
    source_type: str
    source_reference: str
    summary: str = ""


@dataclass(frozen=True)
class ActiveIntentRecord:
    timestamp: str
    session_key: str
    intent_type: str
    intent_id: str
    source_type: str
    source_reference: str
    outcome: ActiveIntentOutcome
    summary: str = ""
    suppression_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JsonlActiveIntentLedger:
    """Append-only ledger for proactive emission and suppression decisions."""

    def __init__(self, workspace: Path, *, sqlite_store: Any = None):
        root = Path(workspace) / "memory" / "active_intents"
        self._records_path = root / "records.jsonl"
        self._lock = threading.Lock()
        self._sqlite = sqlite_store

    def append(self, record: ActiveIntentRecord) -> None:
        payload = record.to_dict()
        if self._sqlite is not None:
            try:
                self._sqlite.append(payload)
            except Exception:
                logger.opt(exception=True).warning("active_intents: sqlite append failed, falling back to JSONL")
                self._jsonl_append(payload)
                return
        self._jsonl_append(payload)

    def _jsonl_append(self, payload: dict[str, Any]) -> None:
        with self._lock:
            ensure_dir(self._records_path.parent)
            with self._records_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

    def recent(self, limit: int = _RECENT_SCAN_LIMIT) -> list[dict[str, Any]]:
        if self._sqlite is not None:
            try:
                return self._sqlite.recent(limit=limit)
            except Exception:
                pass
        if not self._records_path.exists():
            return []
        items: list[dict[str, Any]] = []
        try:
            with self._records_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        items.append(payload)
        except Exception:
            return []
        if limit <= 0:
            return items
        return items[-limit:]


class ActiveIntentService:
    """Produce bounded internal proactive messages for eligible idle sessions."""

    def __init__(
        self,
        *,
        workspace: Path,
        bus: MessageBus,
        sessions: SessionManager,
        confirmation_store: PendingConfirmationStore,
        fact_store: FactStore,
        config: ActiveIntentConfig,
        cognitive_audit: JsonlCognitiveAuditLedger,
        nearline_memory_config: Any | None = None,
        sqlite_store: Any = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.bus = bus
        self.sessions = sessions
        self.confirmation_store = confirmation_store
        self.fact_store = fact_store
        self.nearline_store = NearlineMemoryStore(workspace)
        self.config = config
        self._cognitive_audit = cognitive_audit
        self._nearline_memory_config = nearline_memory_config
        self.ledger = JsonlActiveIntentLedger(workspace, sqlite_store=sqlite_store)
        # In-memory nudge counter per intent_id. Prevents infinite
        # re-nudging of the same confirmation when the user is
        # unresponsive. Reset on process restart (acceptable: restart
        # gives a fresh chance, and long-stale confirmations should be
        # handled by expires_at, not by a persistent counter).
        self._nudge_counts: dict[str, int] = {}

    def session_keys(self) -> list[str]:
        seen: set[str] = set()
        keys: list[str] = []
        for item in self.sessions.list_sessions():
            key = str(item.get("key") or "").strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
        return keys

    def collect_candidates(self, session_key: str) -> list[ActiveIntentCandidate]:
        """Return bounded proactive candidates without publishing them."""

        session = self.sessions.get_or_create(session_key)
        return self._build_candidates(session)

    def build_message(self, session_key: str, candidate: ActiveIntentCandidate) -> InboundMessage:
        """Build the internal inbound message for a proactive candidate."""

        session = self.sessions.get_or_create(session_key)
        return self._build_message(session, candidate)

    def eligible_session(
        self,
        session_key: str,
        *,
        active_task_count: int,
        running_subagents: int,
    ) -> tuple[bool, str | None]:
        if not self.config.enabled:
            return False, "disabled"
        if active_task_count > 0:
            return False, "active_tasks"
        if running_subagents > 0:
            return False, "running_subagents"
        return True, None

    async def process_session(
        self,
        session_key: str,
        *,
        active_task_count: int,
        running_subagents: int,
    ) -> list[ActiveIntentCandidate]:
        eligible, reason = self.eligible_session(
            session_key,
            active_task_count=active_task_count,
            running_subagents=running_subagents,
        )
        if not eligible:
            self._record_skip(session_key, reason or "ineligible")
            return []

        session = self.sessions.get_or_create(session_key)
        candidates = self._build_candidates(session)
        emitted: list[ActiveIntentCandidate] = []
        for candidate in candidates:
            allowed, suppression_reason = self._passes_cooldown(session_key, candidate.intent_id)
            if not allowed:
                self._append_decision(
                    session_key=session_key,
                    candidate=candidate,
                    outcome="suppressed",
                    action="suppress",
                    suppression_reason=suppression_reason,
                    published_internal_event=False,
                )
                continue
            await self.bus.publish_inbound(self._build_message(session, candidate))
            self._append_decision(
                session_key=session_key,
                candidate=candidate,
                outcome="emitted",
                action="emit",
                published_internal_event=True,
            )
            # Track nudge count per intent_id to cap re-nudging.
            self._nudge_counts[candidate.intent_id] = (
                self._nudge_counts.get(candidate.intent_id, 0) + 1
            )
            emitted.append(candidate)
            if len(emitted) >= self.config.max_messages_per_session_per_pass:
                break
        return emitted

    def _build_candidates(self, session: Session) -> list[ActiveIntentCandidate]:
        candidates: list[ActiveIntentCandidate] = []
        goal_candidate = self._goal_candidate(session)
        if goal_candidate is not None:
            candidates.append(goal_candidate)
        confirmation_candidate = self._pending_confirmation_candidate(session)
        if confirmation_candidate is not None:
            candidates.append(confirmation_candidate)
        foresight_candidate = self._foresight_candidate(session)
        if foresight_candidate is not None:
            candidates.append(foresight_candidate)
        return candidates

    def _goal_candidate(self, session: Session) -> ActiveIntentCandidate | None:
        goal = parse_goal_state(goal_state_raw(session.metadata))
        if not isinstance(goal, dict) or goal.get("status") != "active":
            return None
        objective = str(goal.get("objective") or "").strip()
        if not objective:
            return None
        summary = str(goal.get("ui_summary") or "").strip() or _summarize_text(objective, max_chars=80)
        started_at = str(goal.get("started_at") or "").strip()
        content = (
            "Active goal follow-up: there is still an unfinished sustained goal in this chat.\n"
            f"Goal: {summary}\n"
            "If work should continue, resume it. If the goal is no longer relevant, clarify or close it."
        )
        return ActiveIntentCandidate(
            intent_type="goal_nudge",
            intent_id=f"goal_nudge:{session.key}:{started_at or summary}",
            content=content,
            source_type="goal_state",
            source_reference=started_at or summary,
            summary=summary,
        )

    def _pending_confirmation_candidate(self, session: Session) -> ActiveIntentCandidate | None:
        pending = [
            item for item in self.confirmation_store.read_all()
            if item.status in {"pending", "notified"}
            and item.metadata.get("session_key") == session.key
            and not _is_confirmation_expired(item)
        ]
        if pending:
            confirmation = pending[0]
            intent_id = f"pending_confirmation:{session.key}:{confirmation.confirmation_id}"
            # Nudge count cap: stop re-nudging after _MAX_NUDGES_PER_CONFIRMATION
            # attempts. This prevents the "inner monologue death loop" where
            # the agent keeps waking up, calling close_episode (which doesn't
            # resolve the confirmation), and going back to sleep — burning
            # LLM tokens on an unresponsive user.
            nudge_count = self._nudge_counts.get(intent_id, 0)
            if nudge_count >= _MAX_NUDGES_PER_CONFIRMATION:
                logger.info(
                    "pending_confirmation nudge cap reached for {} "
                    "({}/{} attempts), stopping until user responds",
                    confirmation.confirmation_id,
                    nudge_count,
                    _MAX_NUDGES_PER_CONFIRMATION,
                )
                return None
            prompt = _summarize_text(confirmation.prompt, max_chars=160)
            return ActiveIntentCandidate(
                intent_type="pending_confirmation_nudge",
                intent_id=intent_id,
                content=(
                    "Pending confirmation follow-up: there is a safety or fact confirmation still waiting.\n"
                    f"Pending item: {prompt}\n"
                    "If the user is ready, ask for confirmation or help them resolve it."
                ),
                source_type="pending_confirmation",
                source_reference=confirmation.confirmation_id,
                summary=prompt,
            )

        pending_facts = [
            record for record in self.fact_store.list_active(include_pending=True)
            if record.status == "pending_confirmation" and record.scope == session.key
        ]
        if not pending_facts:
            return None
        fact = pending_facts[0]
        summary = _summarize_text(fact.content, max_chars=160)
        return ActiveIntentCandidate(
            intent_type="pending_confirmation_nudge",
            intent_id=f"pending_fact:{session.key}:{fact.fact_id}",
            content=(
                "Pending fact follow-up: there is an unconfirmed fact associated with this chat.\n"
                f"Fact: {summary}\n"
                "If useful, ask the user to confirm, correct, or dismiss it."
            ),
            source_type="pending_fact",
            source_reference=fact.fact_id,
            summary=summary,
        )

    def _foresight_candidate(self, session: Session) -> ActiveIntentCandidate | None:
        if not self._nearline_memory_enabled():
            return None
        now = _utcnow()
        foresights = [
            item
            for item in self.nearline_store.read_foresights(limit=80)
            if item.session_key == session.key and item.start_at
        ]
        due: list[tuple[datetime, Any]] = []
        for item in foresights:
            start_at = _parse_iso(item.start_at)
            if start_at is None:
                continue
            if start_at > now:
                continue
            due.append((start_at, item))
        if not due:
            return None
        due.sort(key=lambda pair: pair[0])
        _start_at, foresight = due[0]
        summary = _summarize_text(foresight.content, max_chars=160)
        return ActiveIntentCandidate(
            intent_type="foresight_nudge",
            intent_id=f"foresight:{foresight.foresight_id}",
            content=(
                "Foresight follow-up: a previously stated future plan or commitment is now due.\n"
                f"Foresight: {summary}\n"
                "If helpful, gently nudge the user about this due plan and keep the follow-up bounded."
            ),
            source_type="foresight",
            source_reference=foresight.foresight_id,
            summary=summary,
        )

    def _nearline_memory_enabled(self) -> bool:
        return nearline_runtime_enabled(self._nearline_memory_config)

    def passes_cooldown(self, session_key: str, intent_id: str) -> tuple[bool, str | None]:
        """Public wrapper for cooldown checks reused by the cognitive runtime."""

        return self._passes_cooldown(session_key, intent_id)

    def _passes_cooldown(self, session_key: str, intent_id: str) -> tuple[bool, str | None]:
        now = _utcnow()
        session_cutoff = self.config.session_cooldown_seconds
        intent_cutoff = self.config.intent_cooldown_seconds
        for record in reversed(self._merged_cooldown_records()):
            outcome = str(record.get("outcome") or "")
            if outcome != "emitted":
                continue
            ts = _parse_iso(str(record.get("timestamp") or ""))
            if ts is None:
                continue
            delta = (now - ts).total_seconds()
            if str(record.get("session_key") or "") == session_key and delta < session_cutoff:
                return False, "session_cooldown"
            if str(record.get("intent_id") or "") == intent_id and delta < intent_cutoff:
                return False, "intent_cooldown"
        return True, None

    def _record_skip(self, session_key: str, reason: str) -> None:
        candidate = ActiveIntentCandidate(
            intent_type="goal_nudge",
            intent_id=f"skip:{session_key}:{reason}",
            content="",
            source_type="runtime",
            source_reference="eligibility",
            summary=f"Skipped active intent processing: {reason}",
        )
        self._append_decision(
            session_key=session_key,
            candidate=candidate,
            outcome="skipped",
            action="skip",
            suppression_reason=reason,
            published_internal_event=False,
        )

    def eligibility_for_cognition(
        self,
        session_key: str,
        *,
        active_task_count: int,
        running_subagents: int,
    ) -> tuple[bool, str | None]:
        _ = session_key
        if active_task_count > 0:
            return False, "active_tasks"
        if running_subagents > 0:
            return False, "running_subagents"
        return True, None

    def _append_decision(
        self,
        *,
        session_key: str,
        candidate: ActiveIntentCandidate,
        outcome: ActiveIntentOutcome,
        action: Literal["emit", "suppress", "skip"],
        suppression_reason: str | None = None,
        published_internal_event: bool,
    ) -> None:
        self._cognitive_audit.append_decision(CognitiveDecision(
            decision_id=f"decision:{candidate.intent_id}:{_utcnow_iso()}",
            event_id=candidate.intent_id,
            session_key=session_key,
            action=action,
            outcome=outcome,
            suppression_reason=suppression_reason,
            cooldown_key=candidate.intent_id,
            published_internal_event=published_internal_event,
            payload={
                "event_type": candidate.intent_type,
                "source_type": candidate.source_type,
                "source_reference": candidate.source_reference,
                "intent_id": candidate.intent_id,
                "summary": candidate.summary,
            },
        ))

    def _merged_cooldown_records(self) -> list[dict[str, Any]]:
        cognitive_records = [
            self._normalize_cooldown_record(record, source="cognitive")
            for record in self._cognitive_audit.recent_decisions()
        ]
        legacy_records = [
            self._normalize_cooldown_record(record, source="legacy")
            for record in self.ledger.recent()
        ]
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for record in reversed(cognitive_records):
            if record is None:
                continue
            key = (
                str(record.get("session_key") or ""),
                str(record.get("intent_id") or ""),
                str(record.get("timestamp") or ""),
                str(record.get("outcome") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(record)
        for record in reversed(legacy_records):
            if record is None:
                continue
            key = (
                str(record.get("session_key") or ""),
                str(record.get("intent_id") or ""),
                str(record.get("timestamp") or ""),
                str(record.get("outcome") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(record)
        merged.reverse()
        return merged

    @staticmethod
    def _normalize_cooldown_record(record: dict[str, Any], *, source: str) -> dict[str, Any] | None:
        timestamp = str(record.get("created_at") or record.get("timestamp") or "").strip()
        intent_id = str(record.get("cooldown_key") or record.get("intent_id") or "").strip()
        session_key = str(record.get("session_key") or "").strip()
        outcome = str(record.get("outcome") or "").strip()
        if not session_key or not intent_id or not timestamp or not outcome:
            return None
        return {
            "source": source,
            "timestamp": timestamp,
            "intent_id": intent_id,
            "session_key": session_key,
            "outcome": outcome,
        }

    @staticmethod
    def _build_message(session: Session, candidate: ActiveIntentCandidate) -> InboundMessage:
        channel, chat_id = (
            session.key.split(":", 1)
            if ":" in session.key
            else ("cli", session.key)
        )
        return InboundMessage(
            channel="system",
            sender_id="agent_active",
            chat_id=f"{channel}:{chat_id}",
            content=candidate.content,
            session_key_override=session.key,
            metadata=build_origin_metadata(
                {
                    "injected_event": "active_intent",
                    "_from_active": True,
                    "active_intent_type": candidate.intent_type,
                    "active_intent_id": candidate.intent_id,
                },
                origin_kind="active_intent",
                is_inferred=True,
                confidence=0.7,
                trigger_reason=candidate.intent_type,
            ),
        )
