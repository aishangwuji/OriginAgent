from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from OriginAgent.agent.agent_cognitive_runtime import AgentCognitiveRuntime, CognitiveRuntimeDeps
from OriginAgent.agent.loop import AgentLoop


def test_start_active_intent_loop_delegates_to_cognitive_runtime() -> None:
    loop = AgentLoop.__new__(AgentLoop)
    task = object()
    delegate = SimpleNamespace(
        start_active_intent_loop=lambda current: task,
    )
    loop._active_intent_task = None
    loop._cognitive_runtime = delegate

    loop._start_active_intent_loop()

    assert loop._active_intent_task is task


@pytest.mark.asyncio
async def test_run_cognitive_pass_delegates_to_cognitive_runtime() -> None:
    loop = AgentLoop.__new__(AgentLoop)
    delegate = AsyncMock(return_value=["ok"])
    loop._cognitive_runtime = SimpleNamespace(
        run_cognitive_pass_for_session=delegate,
    )

    result = await loop._run_cognitive_pass_for_session(
        "cli:test",
        active_task_count=1,
        running_subagents=2,
    )

    assert result == ["ok"]
    delegate.assert_awaited_once_with(
        "cli:test",
        active_task_count=1,
        running_subagents=2,
    )


def _make_runtime(*, enabled: bool, scheduler_mode: str) -> AgentCognitiveRuntime:
    cognitive_loop = SimpleNamespace(
        config=SimpleNamespace(enabled=enabled, interval_seconds=1),
        run_forever=AsyncMock(return_value=None),
    )
    cognitive_scheduler = SimpleNamespace(start=lambda: scheduler_mode)
    deps = CognitiveRuntimeDeps(
        cognitive_loop=cognitive_loop,
        cognitive_scheduler=cognitive_scheduler,
        bus=SimpleNamespace(),
        sessions=SimpleNamespace(),
        active_intents=SimpleNamespace(),
        reminder_store=SimpleNamespace(),
        working_memory=SimpleNamespace(),
        cognitive_audit=SimpleNamespace(),
        running_flag=lambda: False,
        build_runtime_context=lambda _session_key: None,
        collect_candidates=lambda _session_key: [],
        write_cognitive_event_to_working_memory=lambda *args, **kwargs: False,
        record_last_scan=lambda _payload: None,
        utcnow_iso=lambda: "2026-06-13T00:00:00+00:00",
    )
    return AgentCognitiveRuntime(deps)


def test_start_active_intent_loop_returns_none_when_disabled() -> None:
    runtime = _make_runtime(enabled=False, scheduler_mode="disabled")

    task = runtime.start_active_intent_loop(None)

    assert task is None


def test_start_active_intent_loop_returns_none_when_scheduler_uses_cron() -> None:
    runtime = _make_runtime(enabled=True, scheduler_mode="cron")

    task = runtime.start_active_intent_loop(None)

    assert task is None


@pytest.mark.asyncio
async def test_start_active_intent_loop_creates_task_when_scheduler_falls_back() -> None:
    runtime = _make_runtime(enabled=True, scheduler_mode="fallback")

    task = runtime.start_active_intent_loop(None)

    assert isinstance(task, asyncio.Task)
    await asyncio.sleep(0)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
