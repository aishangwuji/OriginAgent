"""Rolling structured episode compaction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from OriginAgent.memory.models import EpisodeRecord
from OriginAgent.memory.store import NearlineMemoryStore
from OriginAgent.session.manager import Session
from OriginAgent.utils.helpers import truncate_text

EPISODE_COMPACTION_TURN_COUNT_KEY = "episode_compaction_turn_count_v1"
EPISODE_COMPACTION_LAST_MARKER_KEY = "episode_compaction_last_marker_v1"


@dataclass(frozen=True)
class RollingEpisodeResult:
    created: bool
    reason: str
    episode_id: str | None = None


class RollingEpisodeCompaction:
    """Emit structured nearline episode records on a rolling cadence."""

    def __init__(
        self,
        workspace: Path,
        *,
        store: NearlineMemoryStore | None = None,
        interval_turns: int = 20,
    ) -> None:
        self.workspace = Path(workspace)
        self.store = store or NearlineMemoryStore(self.workspace)
        self.interval_turns = max(1, int(interval_turns or 20))

    def maybe_compact(
        self,
        session: Session,
        *,
        working_snapshot: Any,
        force: bool = False,
        reason: str = "interval",
    ) -> RollingEpisodeResult:
        turn_count = int(session.metadata.get(EPISODE_COMPACTION_TURN_COUNT_KEY, 0) or 0) + 1
        session.metadata[EPISODE_COMPACTION_TURN_COUNT_KEY] = turn_count
        if not force and turn_count % self.interval_turns != 0:
            return RollingEpisodeResult(created=False, reason="interval_not_due")

        marker = self._latest_marker(session, turn_count=turn_count)
        if marker and marker == str(session.metadata.get(EPISODE_COMPACTION_LAST_MARKER_KEY) or "").strip():
            return RollingEpisodeResult(created=False, reason="already_compacted")

        episode = self._build_episode(
            session,
            working_snapshot=working_snapshot,
            reason=reason,
            turn_count=turn_count,
        )
        if episode is None:
            return RollingEpisodeResult(created=False, reason="no_content")
        self.store.append_episodes([episode])
        session.metadata[EPISODE_COMPACTION_LAST_MARKER_KEY] = marker
        return RollingEpisodeResult(created=True, reason=reason, episode_id=episode.episode_id)

    def _build_episode(
        self,
        session: Session,
        *,
        working_snapshot: Any,
        reason: str,
        turn_count: int,
    ) -> EpisodeRecord | None:
        messages = list(session.messages[-12:])
        if not messages:
            return None
        goal_summary = str(getattr(working_snapshot, "current_goal", "") or "").strip()
        current_plan = list(getattr(working_snapshot, "current_plan", []) or [])
        constraints = list(getattr(working_snapshot, "active_constraints", []) or [])
        open_loops = list(getattr(working_snapshot, "open_loops", []) or [])
        key_events = self._key_events(messages)
        if not goal_summary and not current_plan and not constraints and not open_loops and not key_events:
            return None

        started_at = self._message_timestamp(messages[0]) or self._utcnow_iso()
        ended_at = self._message_timestamp(messages[-1]) or started_at
        content = "\n".join(key_events[:6]).strip()
        summary = goal_summary or (key_events[0] if key_events else "Rolling continuity checkpoint")
        owner_id = str(getattr(working_snapshot, "owner_id", "") or "user").strip() or "user"
        source_refs = self._source_refs(messages)
        digest = hashlib.sha1(
            f"{session.key}|{ended_at}|{turn_count}|{reason}".encode("utf-8")
        ).hexdigest()[:12]
        return EpisodeRecord(
            episode_id=f"rolling_episode_{digest}",
            memcell_id=f"rolling:{session.key}:{turn_count}",
            session_key=session.key,
            owner_id=owner_id,
            summary=truncate_text(summary, 220).replace("\n... (truncated)", " ..."),
            content=truncate_text(content, 1200).replace("\n... (truncated)", " ..."),
            timestamp=ended_at,
            source_message_ids=[
                str(message.get("message_id") or "").strip()
                for message in messages
                if str(message.get("message_id") or "").strip()
            ],
            goal_summary=truncate_text(goal_summary, 240).replace("\n... (truncated)", " ..."),
            decisions=[self._compact_text(item, 180) for item in current_plan if self._compact_text(item, 180)],
            constraints=[self._compact_text(item, 180) for item in constraints if self._compact_text(item, 180)],
            open_loops=[self._compact_text(item, 180) for item in open_loops if self._compact_text(item, 180)],
            key_events=key_events[:6],
            source_refs=source_refs[:8],
            time_range={"start": started_at, "end": ended_at},
            metadata={
                "kind": "rolling_compaction",
                "reason": reason,
                "turn_count": turn_count,
            },
        )

    @staticmethod
    def _compact_text(value: Any, limit: int) -> str:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return ""
        return truncate_text(text, limit).replace("\n... (truncated)", " ...")

    def _key_events(self, messages: list[dict[str, Any]]) -> list[str]:
        events: list[str] = []
        for message in messages[-8:]:
            role = str(message.get("role") or "").strip()
            content = message.get("content")
            if not isinstance(content, str):
                continue
            compact = self._compact_text(content, 180)
            if not compact:
                continue
            events.append(f"{role}: {compact}")
        return events

    def _source_refs(self, messages: list[dict[str, Any]]) -> list[str]:
        refs: list[str] = []
        for index, message in enumerate(messages):
            message_id = str(message.get("message_id") or "").strip()
            if message_id:
                refs.append(message_id)
                continue
            timestamp = self._message_timestamp(message)
            role = str(message.get("role") or "").strip() or "message"
            refs.append(f"{role}:{timestamp or index}")
        return refs

    @staticmethod
    def _message_timestamp(message: dict[str, Any]) -> str | None:
        value = message.get("timestamp")
        text = str(value or "").strip()
        return text or None

    def _latest_marker(self, session: Session, *, turn_count: int) -> str:
        last = session.messages[-1] if session.messages else {}
        timestamp = self._message_timestamp(last) or self._utcnow_iso()
        return f"{turn_count}:{timestamp}"

    @staticmethod
    def _utcnow_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
