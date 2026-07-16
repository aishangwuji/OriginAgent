"""Auto compact: proactive compression of idle sessions to reduce token cost and latency."""

from __future__ import annotations

import json
from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from loguru import logger

from OriginAgent.agent.memory import record_recent_summary, session_summary_text
from OriginAgent.agent.runtime_models import TaskRunReport, now_iso
from OriginAgent.agent.task_runtime import build_task_report, remember_report, report_to_status_payload
from OriginAgent.session.manager import Session, SessionManager
from OriginAgent.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from OriginAgent.agent.memory import Consolidator
    from OriginAgent.session.cold_archive import SessionColdArchiveStore


class AutoCompact:
    _RECENT_SUFFIX_MESSAGES = 8

    def __init__(
        self,
        sessions: SessionManager,
        consolidator: Consolidator,
        session_ttl_minutes: int = 0,
        cold_archive: SessionColdArchiveStore | None = None,
    ):
        self.sessions = sessions
        self.consolidator = consolidator
        self.cold_archive = cold_archive
        self._ttl = session_ttl_minutes
        self._archiving: set[str] = set()
        self._summaries: dict[str, str] = {}
        self._last_report: TaskRunReport | None = None
        self._consecutive_failures = 0

    def _is_expired(self, ts: datetime | str | None,
                    now: datetime | None = None) -> bool:
        if self._ttl <= 0 or not ts:
            return False
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return ((now or datetime.now()) - ts).total_seconds() >= self._ttl * 60

    def _split_unconsolidated(
        self, session: Session,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split live session tail into archiveable prefix and retained recent suffix.

        Phase 5: episode-aware splitting. Instead of arbitrarily keeping
        8 recent messages, preserves at least one complete episode boundary.
        Falls back to the legacy 8-message suffix when no episodes exist.
        """
        tail = list(session.messages[session.last_consolidated:])
        if not tail:
            return [], []

        # Episode-aware: find the last closed episode boundary in the tail.
        kept_start = len(tail)
        if session.episodes:
            # Iterate episodes in reverse, looking for the last closed episode
            # that starts within the tail region.
            for ep in reversed(session.episodes):
                ep_start_in_tail = max(0, ep.msg_start - session.last_consolidated)
                if ep_start_in_tail < len(tail) and ep.status == "closed":
                    # Keep from this episode's start.
                    kept_start = ep_start_in_tail
                    break
            else:
                # No closed episode found in tail; keep the active episode or fall back.
                active = session.active_episode
                if active is not None:
                    kept_start = max(0, active.msg_start - session.last_consolidated)

        # Ensure we keep at least RECENT_SUFFIX_MESSAGES.
        min_keep = min(self._RECENT_SUFFIX_MESSAGES, len(tail))
        kept_start = min(kept_start, len(tail) - min_keep)

        kept = tail[kept_start:]
        archive = tail[:kept_start]
        return archive, kept

    def _write_warm_archive(
        self,
        session_key: str,
        messages: list[dict[str, Any]],
        *,
        start_index: int = 0,
    ) -> Path:
        """将归档消息追加写入 workspace/warm_archive/{session_key}.jsonl。

        Phase 5：warm_archive 是主要归档目标，供热区快速回放；
        cold_archive 仍作为持久化兜底。每行一个 JSON 消息，追加写入。
        """
        workspace = self.sessions.workspace
        archive_dir = ensure_dir(workspace / "warm_archive")
        safe_key = SessionManager.safe_key(session_key)
        path = archive_dir / f"{safe_key}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            for offset, msg in enumerate(messages):
                # 浅拷贝，避免污染内存中的 session.messages 与后续 consolidator/cold_archive
                record = dict(msg)
                record.setdefault("session_key", session_key)
                record.setdefault("turn_index", start_index + offset)
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    def check_expired(self, schedule_background: Callable[[Coroutine], None],
                      active_session_keys: Collection[str] = ()) -> None:
        """Schedule archival for idle sessions, skipping those with in-flight agent tasks."""
        now = datetime.now()
        for info in self.sessions.list_sessions():
            key = info.get("key", "")
            if not key or key in self._archiving:
                continue
            if key in active_session_keys:
                continue
            if self._is_expired(info.get("updated_at"), now):
                self._archiving.add(key)
                schedule_background(self._archive(key))

    async def _archive(self, key: str) -> None:
        started_at = now_iso()
        try:
            self.sessions.invalidate(key)
            session = self.sessions.get_or_create(key)
            archive_msgs, kept_msgs = self._split_unconsolidated(session)
            if not archive_msgs and not kept_msgs:
                session.updated_at = datetime.now()
                self.sessions.save(session)
                self._remember_report(build_task_report(
                    task_name="auto_compact",
                    status="ok",
                    phase="archive",
                    fault_class="unknown",
                    reason="nothing_to_archive",
                    started_at=started_at,
                    finished_at=now_iso(),
                    details={"session_key": key},
                ))
                return

            last_active = session.updated_at
            summary = None
            if archive_msgs:
                # Phase 5：warm_archive 作为主要归档目标先行写入；
                # 若写入失败则异常上抛，外层 except 会跳过裁剪，保证数据安全。
                self._write_warm_archive(
                    key, archive_msgs, start_index=session.last_consolidated
                )
                if self.cold_archive is not None:
                    self.cold_archive.archive(
                        key,
                        archive_msgs,
                        reason="auto_compact",
                    )
                result = await self.consolidator.archive(archive_msgs)
                if record_recent_summary(session, result, last_active=last_active):
                    summary = session_summary_text(session)
                    if summary:
                        self._summaries[key] = summary
            session.messages = kept_msgs
            session.last_consolidated = 0
            session._rebuild_episode_indices()
            session.updated_at = datetime.now()
            self.sessions.save(session)
            if archive_msgs:
                logger.info(
                    "Auto-compact: archived {} (archived={}, kept={}, summary={})",
                    key,
                    len(archive_msgs),
                    len(kept_msgs),
                    bool(summary),
                )
            self._remember_report(build_task_report(
                task_name="auto_compact",
                status="ok",
                phase="archive",
                fault_class="unknown",
                reason="ok",
                started_at=started_at,
                finished_at=now_iso(),
                details={
                    "session_key": key,
                    "archived_count": len(archive_msgs),
                    "kept_count": len(kept_msgs),
                },
            ))
        except Exception:
            logger.exception("Auto-compact: failed for {}", key)
            self._remember_report(build_task_report(
                task_name="auto_compact",
                status="degraded",
                phase="archive",
                fault_class="unknown",
                degraded=True,
                reason=f"archive_failed:{key}",
                started_at=started_at,
                finished_at=now_iso(),
                details={"session_key": key},
            ))
        finally:
            self._archiving.discard(key)

    def prepare_session(self, session: Session, key: str) -> tuple[Session, str | None]:
        if key in self._archiving or self._is_expired(session.updated_at):
            logger.info("Auto-compact: reloading session {} (archiving={})", key, key in self._archiving)
            session = self.sessions.get_or_create(key)
        # Hot path: summary from in-memory dict (process hasn't restarted).
        summary = self._summaries.pop(key, None)
        if summary:
            return session, summary
        # Cold path: summary persisted in session metadata (process restarted).
        return session, session_summary_text(session)

    def runtime_status(self) -> dict[str, Any]:
        return {
            "auto_compact_enabled": self._ttl > 0,
            "auto_compact_ttl_minutes": self._ttl,
            "auto_compact_running_count": len(self._archiving),
            **report_to_status_payload(
                self._last_report,
                consecutive_failures=self._consecutive_failures,
            ),
        }

    def _remember_report(self, report: TaskRunReport) -> None:
        self._last_report = report
        self._consecutive_failures = remember_report(
            report=report,
            current_failures=self._consecutive_failures,
        )
