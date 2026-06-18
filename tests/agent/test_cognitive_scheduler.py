from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from OriginAgent.agent.cognitive_events import CognitiveDecision
from OriginAgent.agent.cognitive_scheduler import CognitiveScheduler, CognitiveSchedulerConfig


class _CronService:
    def __init__(self) -> None:
        self.on_job = object()
        self.registered = []

    def register_system_job(self, job):
        self.registered.append(job)
        return job


def test_cognitive_scheduler_config_defaults_match_schema() -> None:
    config = CognitiveSchedulerConfig()

    assert config.enabled is True
    assert config.interval_seconds == 15


@pytest.mark.asyncio
async def test_cognitive_scheduler_runs_passes_and_records_audit(tmp_path) -> None:
    scheduler = CognitiveScheduler(
        workspace=tmp_path,
        config=CognitiveSchedulerConfig(enabled=True, interval_seconds=30),
        cron_service=None,
        session_keys_provider=lambda: ["cli:a", "cli:b"],
        active_task_count_provider=lambda _session_key: 0,
        running_subagents_provider=lambda _session_key: 0,
        session_processor=AsyncMock(side_effect=[
            [
                CognitiveDecision(
                    decision_id="d-1",
                    event_id="e-1",
                    session_key="cli:a",
                    action="emit",
                    outcome="emitted",
                )
            ],
            [
                CognitiveDecision(
                    decision_id="d-2",
                    event_id="e-2",
                    session_key="cli:b",
                    action="suppress",
                    outcome="suppressed",
                    suppression_reason="intent_cooldown",
                )
            ],
        ]),
    )

    record = await scheduler.run_once(trigger="manual")
    summary = scheduler.ledger.summary()

    assert record.scanned_session_count == 2
    assert record.emitted_count == 1
    assert record.suppressed_count == 1
    assert summary["scheduler_run_count"] == 1
    assert summary["latest_scheduler_run"]["decision_count"] == 2


def test_cognitive_scheduler_registers_system_job_when_cron_available(tmp_path) -> None:
    cron = _CronService()
    scheduler = CognitiveScheduler(
        workspace=tmp_path,
        config=CognitiveSchedulerConfig(enabled=True, interval_seconds=45),
        cron_service=cron,
        session_keys_provider=lambda: [],
        active_task_count_provider=lambda _session_key: 0,
        running_subagents_provider=lambda _session_key: 0,
        session_processor=AsyncMock(return_value=[]),
    )

    mode = scheduler.start()

    assert mode == "cron"
    assert len(cron.registered) == 1
    job = cron.registered[0]
    assert job.id == "cognitive_scheduler"
    assert job.schedule.every_ms == 45_000
    assert job.payload.kind == "system_event"
