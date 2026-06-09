"""Cron-backed cognitive scheduler for bounded backend cognition scans."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from OriginAgent.agent.cognitive_events import CognitiveDecision
from OriginAgent.cron.types import CronJob, CronPayload, CronSchedule
from OriginAgent.utils.helpers import ensure_dir

_RECENT_SCAN_LIMIT = 200


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


SchedulerMode = Literal["disabled", "fallback", "cron"]
SchedulerTrigger = Literal["cron", "manual", "fallback"]


@dataclass(frozen=True)
class CognitiveSchedulerConfig:
    enabled: bool = False
    interval_seconds: int = 30
    job_id: str = "cognitive_scheduler"
    job_name: str = "cognitive_scheduler"


@dataclass(frozen=True)
class CognitiveSchedulerRun:
    run_id: str
    trigger: SchedulerTrigger
    scheduled_job_id: str | None = None
    scanned_session_count: int = 0
    decision_count: int = 0
    emitted_count: int = 0
    suppressed_count: int = 0
    skipped_count: int = 0
    errored_session_count: int = 0
    created_at: str = field(default_factory=_utcnow_iso)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JsonlCognitiveSchedulerLedger:
    """Append-only ledger for cognitive scheduler sweeps."""

    def __init__(self, workspace: Path):
        root = Path(workspace) / "memory" / "cognitive"
        self._runs_path = root / "scheduler_runs.jsonl"
        self._lock = threading.Lock()

    def append(self, record: CognitiveSchedulerRun) -> None:
        with self._lock:
            ensure_dir(self._runs_path.parent)
            with self._runs_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

    def recent(self, *, limit: int = _RECENT_SCAN_LIMIT) -> list[dict[str, Any]]:
        if not self._runs_path.exists():
            return []
        items: list[dict[str, Any]] = []
        try:
            with self._runs_path.open("r", encoding="utf-8") as handle:
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

    def summary(self, *, limit: int = 20) -> dict[str, Any]:
        runs = self.recent(limit=limit)
        trigger_counts: dict[str, int] = {}
        for record in runs:
            trigger = str(record.get("trigger") or "unknown")
            trigger_counts[trigger] = trigger_counts.get(trigger, 0) + 1
        return {
            "scheduler_run_count": len(runs),
            "latest_scheduler_run": runs[-1] if runs else None,
            "scheduler_trigger_counts": trigger_counts,
        }


class CognitiveScheduler:
    """Coordinate periodic cognitive sweeps while keeping AgentLoop as executor."""

    def __init__(
        self,
        *,
        workspace: Path,
        config: CognitiveSchedulerConfig,
        cron_service: Any | None,
        session_keys_provider: Callable[[], list[str]],
        active_task_count_provider: Callable[[str], int],
        running_subagents_provider: Callable[[str], int],
        session_processor: Callable[..., Awaitable[list[CognitiveDecision]]],
    ) -> None:
        self.workspace = Path(workspace)
        self.config = config
        self._cron_service = cron_service
        self._session_keys_provider = session_keys_provider
        self._active_task_count_provider = active_task_count_provider
        self._running_subagents_provider = running_subagents_provider
        self._session_processor = session_processor
        self.ledger = JsonlCognitiveSchedulerLedger(workspace)
        self._registered = False
        self._last_run: dict[str, Any] = {}

    @property
    def mode(self) -> SchedulerMode:
        if not self.config.enabled:
            return "disabled"
        if self._cron_service is not None and getattr(self._cron_service, "on_job", None) is not None:
            return "cron"
        return "fallback"

    def start(self) -> SchedulerMode:
        mode = self.mode
        if mode != "cron":
            self._registered = False
            return mode
        if self._registered:
            return mode
        if self._cron_service is None:
            self._registered = False
            return "fallback"
        interval_seconds = max(5, int(self.config.interval_seconds))
        self._cron_service.register_system_job(CronJob(
            id=self.config.job_id,
            name=self.config.job_name,
            schedule=CronSchedule(kind="every", every_ms=interval_seconds * 1000),
            payload=CronPayload(kind="system_event"),
        ))
        self._registered = True
        return mode

    async def run_once(self, *, trigger: SchedulerTrigger, scheduled_job_id: str | None = None) -> CognitiveSchedulerRun:
        session_keys = list(dict.fromkeys(self._session_keys_provider()))
        decision_count = 0
        emitted_count = 0
        suppressed_count = 0
        skipped_count = 0
        errored_session_count = 0
        session_stats: dict[str, Any] = {}

        for session_key in session_keys:
            try:
                decisions = await self._session_processor(
                    session_key,
                    active_task_count=self._active_task_count_provider(session_key),
                    running_subagents=self._running_subagents_provider(session_key),
                )
            except Exception as exc:
                errored_session_count += 1
                session_stats[session_key] = {"error": exc.__class__.__name__}
                continue

            decision_count += len(decisions)
            emitted = sum(1 for item in decisions if item.outcome == "emitted")
            suppressed = sum(1 for item in decisions if item.outcome == "suppressed")
            skipped = sum(1 for item in decisions if item.outcome == "skipped")
            emitted_count += emitted
            suppressed_count += suppressed
            skipped_count += skipped
            session_stats[session_key] = {
                "decision_count": len(decisions),
                "emitted_count": emitted,
                "suppressed_count": suppressed,
                "skipped_count": skipped,
            }

        record = CognitiveSchedulerRun(
            run_id=f"{trigger}:{scheduled_job_id or self.config.job_id}:{_utcnow_iso()}",
            trigger=trigger,
            scheduled_job_id=scheduled_job_id,
            scanned_session_count=len(session_keys),
            decision_count=decision_count,
            emitted_count=emitted_count,
            suppressed_count=suppressed_count,
            skipped_count=skipped_count,
            errored_session_count=errored_session_count,
            payload={"session_stats": session_stats, "session_keys": session_keys},
        )
        self.ledger.append(record)
        self._last_run = record.to_dict()
        return record

    def runtime_status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.config.enabled),
            "mode": self.mode,
            "registered": self._registered,
            "interval_seconds": int(self.config.interval_seconds),
            "job_id": self.config.job_id,
            "job_name": self.config.job_name,
            "last_run": dict(self._last_run),
        }
