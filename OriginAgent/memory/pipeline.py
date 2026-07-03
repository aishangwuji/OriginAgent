"""Lightweight nearline memory pipeline for event-driven sidecar updates."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock
from loguru import logger

from OriginAgent.agent.runtime_models import TaskRunReport, now_iso
from OriginAgent.agent.task_runtime import build_task_report, remember_report, report_to_status_payload
from OriginAgent.config.schema import NearlineMemoryConfig
from OriginAgent.memory.events import (
    AgentCaseExtracted,
    EpisodeExtracted,
    ForesightExtracted,
    MemCellCreated,
    MemoryEvent,
    ProfileRefreshRequested,
)
from OriginAgent.memory.models import AgentCaseRecord, EpisodeRecord, ForesightRecord, ProfileSnapshot
from OriginAgent.memory.profile import NearlineProfileService
from OriginAgent.memory.segmenter import canonicalize_session_messages, segment_memcells
from OriginAgent.memory.store import NearlineMemoryStore
from OriginAgent.session.manager import Session

_INTENT_PATTERNS = (
    "will ",
    "going to ",
    "plan to ",
    "i'll ",
    "i will ",
    "准备",
    "计划",
    "打算",
    "会在",
    "我要",
)
_TIME_PATTERN = re.compile(
    r"(?i)\b(?:tomorrow|tonight|next\s+\w+|this\s+(?:week|month)|on\s+\d{4}-\d{2}-\d{2}|"
    r"\d{4}-\d{2}-\d{2}|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|(?:明天|后天|今晚|下周[一二三四五六日天]?|这周|本周|下个月|\d{4}[-/]\d{1,2}[-/]\d{1,2}|"
    r"\d{1,2}月\d{1,2}日)"
)
_DATE_ISO_PATTERN = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_SLASH_PATTERN = re.compile(r"\b(\d{4})/(\d{1,2})/(\d{1,2})\b")
_CHINESE_DATE_PATTERN = re.compile(r"\b(\d{1,2})月(\d{1,2})日\b")
_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_CHINESE_WEEKDAY_INDEX = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}


@dataclass(frozen=True)
class NearlinePipelineResult:
    status: str
    session_key: str
    memcells_written: int = 0
    episodes_written: int = 0
    foresights_written: int = 0
    agent_cases_written: int = 0
    profiles_written: int = 0
    events_written: int = 0
    cursor_before: int = 0
    cursor_after: int = 0
    reason: str = ""
    started_at: str = ""
    finished_at: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class NearlineMemoryPipeline:
    """Event-driven sidecar pipeline for MemCell-derived memory objects."""

    def __init__(
        self,
        workspace: Path,
        *,
        config: NearlineMemoryConfig | None = None,
        store: NearlineMemoryStore | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.config = config or NearlineMemoryConfig()
        self.store = store or NearlineMemoryStore(self.workspace)
        self.profile_service = NearlineProfileService(
            self.workspace,
            store=self.store,
        )
        self.events_path = self.workspace / "memory" / "nearline" / "events.jsonl"
        self._events_lock_path = self.events_path.parent / ".events.lock"
        self._last_report: TaskRunReport | None = None
        self._consecutive_failures = 0

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled and self.config.pipeline_enabled)

    def runtime_status(self) -> dict[str, Any]:
        return {
            "nearline_enabled": bool(self.config.enabled),
            "nearline_pipeline_enabled": bool(self.config.pipeline_enabled),
            "profile_consumer_last_run": dict(self.profile_service.last_consumer_result()),
            **report_to_status_payload(
                self._last_report,
                consecutive_failures=self._consecutive_failures,
            ),
        }

    async def process_turn(
        self,
        *,
        session: Session,
        channel: str,
        chat_id: str,
        actor_id: str,
        turn_id: str,
    ) -> NearlinePipelineResult:
        started_at = now_iso()
        if not self.enabled:
            result = NearlinePipelineResult(
                status="skipped",
                session_key=session.key,
                reason="disabled",
                started_at=started_at,
                finished_at=now_iso(),
            )
            self._remember_report(build_task_report(
                task_name="nearline_memory",
                status="skipped",
                phase="preflight",
                fault_class="config",
                reason="disabled",
                started_at=started_at,
                finished_at=result.finished_at,
            ))
            return result

        session_key = session.key
        cursor_before = self.store.read_session_cursor(session_key)
        messages = list(session.messages[cursor_before:])
        if not messages:
            result = NearlinePipelineResult(
                status="skipped",
                session_key=session_key,
                cursor_before=cursor_before,
                cursor_after=cursor_before,
                reason="no_new_messages",
                started_at=started_at,
                finished_at=now_iso(),
            )
            self._remember_report(build_task_report(
                task_name="nearline_memory",
                status="skipped",
                phase="preflight",
                fault_class="invariant",
                reason="no_new_messages",
                started_at=started_at,
                finished_at=result.finished_at,
            ))
            return result

        try:
            canonical = canonicalize_session_messages(
                session_key,
                messages,
                start_index=cursor_before,
                channel=channel,
                chat_id=chat_id,
            )
            memcells = segment_memcells(
                canonical,
                idle_gap_seconds=self.config.idle_gap_seconds,
                max_messages_per_memcell=self.config.max_messages_per_memcell,
            )
            if not memcells:
                cursor_after = cursor_before + len(messages)
                self.store.advance_session_cursor(session_key, cursor_after)
                self.store.advance_cursor(cursor_after)
                result = NearlinePipelineResult(
                    status="skipped",
                    session_key=session_key,
                    cursor_before=cursor_before,
                    cursor_after=cursor_after,
                    reason="no_memcells",
                    started_at=started_at,
                    finished_at=now_iso(),
                )
                self._remember_report(build_task_report(
                    task_name="nearline_memory",
                    status="skipped",
                    phase="segment",
                    fault_class="invariant",
                    reason="no_memcells",
                    started_at=started_at,
                    finished_at=result.finished_at,
                ))
                return result

            events: list[MemoryEvent] = []
            memcell_events = [
                MemCellCreated(
                    session_key=session_key,
                    timestamp=memcell.ended_at,
                    memcell=memcell,
                    metadata={"turn_id": turn_id},
                )
                for memcell in memcells
            ]
            events.extend(memcell_events)

            episodes: list[EpisodeRecord] = []
            foresights: list[ForesightRecord] = []
            agent_cases: list[AgentCaseRecord] = []
            for memcell in memcells:
                episode = self._extract_episode(memcell)
                if episode is not None:
                    episodes.append(episode)
                    events.append(EpisodeExtracted(
                        session_key=session_key,
                        timestamp=episode.timestamp,
                        episode=episode,
                        metadata={"turn_id": turn_id},
                    ))

                foresight = self._extract_foresight(memcell)
                if foresight is not None:
                    foresights.append(foresight)
                    events.append(ForesightExtracted(
                        session_key=session_key,
                        timestamp=foresight.timestamp,
                        foresight=foresight,
                        metadata={"turn_id": turn_id},
                    ))

                agent_case = self._extract_agent_case(memcell, actor_id=actor_id)
                if agent_case is not None:
                    agent_cases.append(agent_case)
                    events.append(AgentCaseExtracted(
                        session_key=session_key,
                        timestamp=agent_case.timestamp,
                        agent_case=agent_case,
                        metadata={"turn_id": turn_id},
                    ))

            profiles: list[ProfileSnapshot] = []
            if len(memcells) >= max(1, int(self.config.profile_refresh_min_memcells or 1)):
                refresh_event = ProfileRefreshRequested(
                    session_key=session_key,
                    timestamp=memcells[-1].ended_at,
                    owner_id=actor_id or "user",
                    reason="nearline_turn_complete",
                    source_memcell_ids=[memcell.memcell_id for memcell in memcells],
                    metadata={"turn_id": turn_id},
                )
                events.append(refresh_event)

            await asyncio.to_thread(self.store.append_memcells, memcells)
            if episodes:
                await asyncio.to_thread(self.store.append_episodes, episodes)
            if foresights:
                await asyncio.to_thread(self.store.append_foresights, foresights)
            if agent_cases:
                await asyncio.to_thread(self.store.append_agent_cases, agent_cases)
            if events:
                await asyncio.to_thread(self.store.append_events, events)
            if len(memcells) >= max(1, int(self.config.profile_refresh_min_memcells or 1)):
                profile = await asyncio.to_thread(
                    self.profile_service.refresh_profile,
                    owner_id=actor_id or "user",
                    limit=max(80, len(memcells) * 8),
                    write_user_shadow=bool(self.config.profile_shadow_write_enabled),
                )
                if profile is not None:
                    profiles.append(profile)

            cursor_after = cursor_before + len(messages)
            await asyncio.to_thread(self.store.advance_session_cursor, session_key, cursor_after)
            await asyncio.to_thread(self.store.advance_cursor, cursor_after)
            result = NearlinePipelineResult(
                status="ok",
                session_key=session_key,
                memcells_written=len(memcells),
                episodes_written=len(episodes),
                foresights_written=len(foresights),
                agent_cases_written=len(agent_cases),
                profiles_written=len(profiles),
                events_written=len(events),
                cursor_before=cursor_before,
                cursor_after=cursor_after,
                reason="ok",
                started_at=started_at,
                finished_at=now_iso(),
                details={
                    "turn_id": turn_id,
                    "channel": channel,
                    "chat_id": chat_id,
                    "profile_consumer_last_run": dict(self.profile_service.last_consumer_result()),
                },
            )
            self._remember_report(build_task_report(
                task_name="nearline_memory",
                status="ok",
                phase="complete",
                fault_class="unknown",
                reason="ok",
                started_at=started_at,
                finished_at=result.finished_at,
                details={
                    "memcells_written": len(memcells),
                    "episodes_written": len(episodes),
                    "foresights_written": len(foresights),
                    "agent_cases_written": len(agent_cases),
                    "profiles_written": len(profiles),
                    "events_written": len(events),
                },
            ))
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("Nearline pipeline failed for session {}", session_key)
            finished_at = now_iso()
            self._remember_report(build_task_report(
                task_name="nearline_memory",
                status="error",
                phase="process_turn",
                fault_class="external",
                retryable=True,
                reason=str(exc),
                started_at=started_at,
                finished_at=finished_at,
            ))
            return NearlinePipelineResult(
                status="error",
                session_key=session_key,
                cursor_before=cursor_before,
                cursor_after=cursor_before,
                reason=str(exc),
                started_at=started_at,
                finished_at=finished_at,
            )

    def _remember_report(self, report: TaskRunReport) -> None:
        self._last_report = report
        self._consecutive_failures = remember_report(
            report=report,
            current_failures=self._consecutive_failures,
        )

    def _append_events(self, events: list[MemoryEvent]) -> int:
        if not events:
            return 0
        return self.store.append_events(events)

    def _events_locked(self) -> FileLock:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self._events_lock_path))

    @staticmethod
    def _fsync_parent(path: Path) -> None:
        with suppress(PermissionError, OSError):
            fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    @staticmethod
    def _extract_episode(memcell) -> EpisodeRecord | None:
        user_messages = [message for message in memcell.messages if message.role == "user"]
        if not user_messages:
            return None
        lead = user_messages[0].content.strip()
        if not lead:
            return None
        summary = _truncate_line(lead, 160)
        decisions = [
            _truncate_line(message.content, 180)
            for message in user_messages
            if _looks_like_decision_signal(message.content)
        ]
        constraints = [
            _truncate_line(message.content, 180)
            for message in user_messages
            if _looks_like_constraint_signal(message.content)
        ]
        open_loops = [
            _truncate_line(message.content, 180)
            for message in user_messages
            if _looks_like_open_loop_signal(message.content)
        ]
        key_events = [
            _truncate_line(f"{message.role}: {message.content}", 180)
            for message in memcell.messages
            if str(message.content or "").strip()
        ]
        digest = hashlib.sha1(
            f"{memcell.memcell_id}|episode|{user_messages[0].sender_id or 'user'}".encode("utf-8")
        ).hexdigest()[:12]
        return EpisodeRecord(
            episode_id=f"episode_{digest}",
            memcell_id=memcell.memcell_id,
            session_key=memcell.session_key,
            owner_id=user_messages[0].sender_id or "user",
            summary=summary,
            content=memcell.content,
            timestamp=memcell.ended_at,
            source_message_ids=list(memcell.message_ids),
            goal_summary=summary,
            decisions=decisions[:4],
            constraints=constraints[:4],
            open_loops=open_loops[:4],
            key_events=key_events[:6],
            source_refs=list(memcell.message_ids)[:8],
            time_range={
                "start": str(memcell.started_at or memcell.ended_at or ""),
                "end": str(memcell.ended_at or memcell.started_at or ""),
            },
            metadata={
                "kind": memcell.kind,
                "roles": list(memcell.roles),
            },
        )

    @staticmethod
    def _extract_agent_case(memcell, *, actor_id: str) -> AgentCaseRecord | None:
        assistant_messages = [message for message in memcell.messages if message.role == "assistant"]
        tool_messages = [message for message in memcell.messages if message.role == "tool"]
        if not assistant_messages or not tool_messages:
            return None
        task_intent = next(
            (
                message.content.strip()
                for message in memcell.messages
                if message.role == "user" and message.content.strip()
            ),
            "",
        )
        approach = ", ".join(
            sorted(
                {
                    message.tool_name or "tool"
                    for message in tool_messages
                    if (message.tool_name or "").strip()
                }
            )
        ) or "tool_execution"
        outcome_summary = next(
            (
                message.content.strip()
                for message in reversed(assistant_messages)
                if message.content.strip()
            ),
            next(
                (
                    message.content.strip()
                    for message in reversed(tool_messages)
                    if message.content.strip()
                ),
                "",
            ),
        )
        if not task_intent and not outcome_summary:
            return None
        digest = hashlib.sha1(
            f"{memcell.memcell_id}|agent_case|{actor_id or 'assistant'}".encode("utf-8")
        ).hexdigest()[:12]
        return AgentCaseRecord(
            case_id=f"agent_case_{digest}",
            memcell_id=memcell.memcell_id,
            session_key=memcell.session_key,
            agent_id=actor_id or "assistant",
            task_intent=_truncate_line(task_intent or "assistant_tool_execution", 240),
            approach=_truncate_line(approach, 240),
            outcome_summary=_truncate_line(outcome_summary, 400),
            quality_score=1.0 if outcome_summary else 0.6,
            timestamp=memcell.ended_at,
            source_message_ids=list(memcell.message_ids),
            metadata={
                "tool_names": [message.tool_name for message in tool_messages if message.tool_name],
                "kind": memcell.kind,
            },
        )

    def _extract_foresight(self, memcell) -> ForesightRecord | None:
        for message in memcell.messages:
            if message.role != "user":
                continue
            content = message.content.strip()
            if not content:
                continue
            if not _looks_like_future_intent(content):
                continue
            start_at = _extract_future_timestamp(content, reference=message.timestamp)
            if start_at is None:
                continue
            digest = hashlib.sha1(
                f"{memcell.memcell_id}|foresight|{message.sender_id or 'user'}|{start_at}".encode("utf-8")
            ).hexdigest()[:12]
            return ForesightRecord(
                foresight_id=f"foresight_{digest}",
                memcell_id=memcell.memcell_id,
                session_key=memcell.session_key,
                owner_id=message.sender_id or "user",
                content=_truncate_line(content, 400),
                evidence=memcell.content,
                start_at=start_at,
                end_at=None,
                timestamp=memcell.ended_at,
                source_message_ids=list(memcell.message_ids),
                metadata={
                    "detector": "rule_based",
                    "kind": memcell.kind,
                },
            )
        return None

def _truncate_line(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _looks_like_future_intent(text: str) -> bool:
    lowered = text.casefold()
    has_intent = any(pattern in lowered for pattern in _INTENT_PATTERNS)
    has_time = bool(_TIME_PATTERN.search(text))
    return has_intent and has_time


def _looks_like_decision_signal(text: str) -> bool:
    lowered = str(text or "").casefold()
    return any(token in lowered for token in ("decide", "will ", "going to", "plan to", "决定", "计划", "准备"))


def _looks_like_constraint_signal(text: str) -> bool:
    lowered = str(text or "").casefold()
    return any(token in lowered for token in ("must", "should not", "do not", "不要", "必须", "不能"))


def _looks_like_open_loop_signal(text: str) -> bool:
    lowered = str(text or "").casefold()
    return any(token in lowered for token in ("need to", "follow up", "todo", "待办", "还要", "继续"))


def _extract_future_timestamp(text: str, *, reference: str) -> str | None:
    try:
        reference_dt = datetime.fromisoformat(reference)
    except ValueError:
        reference_dt = datetime.now(timezone.utc)
    if reference_dt.tzinfo is None:
        reference_dt = reference_dt.replace(tzinfo=timezone.utc)

    match = _DATE_ISO_PATTERN.search(text)
    if match:
        return _safe_datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), reference_dt)

    match = _DATE_SLASH_PATTERN.search(text)
    if match:
        return _safe_datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), reference_dt)

    match = _CHINESE_DATE_PATTERN.search(text)
    if match:
        return _safe_datetime(reference_dt.year, int(match.group(1)), int(match.group(2)), reference_dt)

    lowered = text.casefold()
    if "tomorrow" in lowered or "明天" in text:
        return (reference_dt + timedelta(days=1)).isoformat()
    if "tonight" in lowered or "今晚" in text:
        return reference_dt.replace(hour=20, minute=0, second=0, microsecond=0).isoformat()
    if "后天" in text:
        return (reference_dt + timedelta(days=2)).isoformat()

    next_weekday = re.search(r"next\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", lowered)
    if next_weekday:
        return _next_weekday(reference_dt, _WEEKDAY_INDEX[next_weekday.group(1)], force_next_week=True)

    weekday = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lowered)
    if weekday:
        return _next_weekday(reference_dt, _WEEKDAY_INDEX[weekday.group(1)], force_next_week=False)

    zh_next_weekday = re.search(r"下周([一二三四五六日天])", text)
    if zh_next_weekday:
        return _next_weekday(reference_dt, _CHINESE_WEEKDAY_INDEX[zh_next_weekday.group(1)], force_next_week=True)

    if "下周" in text:
        target = reference_dt + timedelta(days=7)
        return target.isoformat()

    return None


def _safe_datetime(year: int, month: int, day: int, reference_dt: datetime) -> str | None:
    try:
        candidate = reference_dt.replace(year=year, month=month, day=day)
    except ValueError:
        return None
    return candidate.isoformat()


def _next_weekday(reference_dt: datetime, weekday: int, *, force_next_week: bool) -> str:
    days_ahead = weekday - reference_dt.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    if force_next_week and days_ahead < 7:
        days_ahead += 7
    return (reference_dt + timedelta(days=days_ahead)).isoformat()
