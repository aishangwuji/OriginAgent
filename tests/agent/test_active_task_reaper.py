"""Tests for the stale active-task reaper (spec: fix-cron-runtime-and-context-gaps, Task 4).

Rule 34 (验证先行): these tests are written BEFORE the implementation.
They lock in the behavior required by the "Stale Active Task Reaper" requirement:
- Hung tasks older than ``stale_task_timeout_seconds`` are cancelled and removed.
- Fresh tasks are left untouched.
- ``cognitive_scheduler`` calls the reaper BEFORE evaluating eligibility (so
  stale tasks no longer block cognitive passes with ``reason=active_tasks``).
- The timeout is configurable via ``RuntimeControls`` (here: AgentDefaults).
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from OriginAgent.agent.cognitive_scheduler import (
    CognitiveScheduler,
    CognitiveSchedulerConfig,
)
from OriginAgent.agent.loop import AgentLoop


def _make_minimal_loop(stale_timeout: int = 600) -> AgentLoop:
    """Build a bare AgentLoop with only the fields the reaper touches.

    Mirrors the ``AgentLoop.__new__`` pattern used in test_loop_save_turn.py
    so we avoid constructing the full dependency graph just to exercise
    ``_reap_stale_tasks``.
    """
    loop = AgentLoop.__new__(AgentLoop)
    loop._active_tasks = {}
    loop._stale_task_timeout_seconds = stale_timeout
    return loop


def _make_mock_task(done: bool = False) -> MagicMock:
    """A mock asyncio.Task stand-in. ``cancel`` returns True (cancelled)."""
    task = MagicMock()
    task.done.return_value = done
    task.cancel.return_value = True
    return task


def test_reap_stale_tasks_cancels_old_tasks(monkeypatch) -> None:
    """A task older than the threshold (11 min > 600 s default) is reaped.

    Asserts:
    - ``task.cancel`` is called.
    - The entry is removed from ``_active_tasks``.
    - ``event.active_task.reaped`` is emitted with ``task_count=1`` and
      ``oldest_age_seconds`` close to 660.
    """
    loop = _make_minimal_loop(stale_timeout=600)
    session_key = "unified:default"
    task = _make_mock_task(done=False)
    created_at = time.time() - 660  # 11 minutes ago
    loop._active_tasks[session_key] = [(task, created_at)]

    captured: list = []
    monkeypatch.setattr(
        "OriginAgent.agent.loop.log_event",
        lambda event, **kw: captured.append((event, kw)),
    )

    reaped = loop._reap_stale_tasks(session_key)

    assert reaped == 1
    task.cancel.assert_called_once()
    # Stale entry removed; list is empty (fresh tasks would remain).
    remaining = loop._active_tasks.get(session_key, [])
    assert all(t is not task for t, _ in remaining)
    assert remaining == []

    reaped_events = [(e, kw) for e, kw in captured if e == "active_task.reaped"]
    assert len(reaped_events) == 1
    _, kw = reaped_events[0]
    assert kw.get("task_count") == 1
    # oldest_age_seconds should be ~660 (allow slack for test runtime).
    age = kw.get("oldest_age_seconds")
    assert age is not None and 650 <= age <= 700


def test_reap_stale_tasks_preserves_fresh_tasks(monkeypatch) -> None:
    """A task younger than the threshold (1 min < 600 s) is NOT reaped."""
    loop = _make_minimal_loop(stale_timeout=600)
    session_key = "cli:user-1"
    task = _make_mock_task(done=False)
    created_at = time.time() - 60  # 1 minute ago
    loop._active_tasks[session_key] = [(task, created_at)]

    monkeypatch.setattr("OriginAgent.agent.loop.log_event", lambda *a, **kw: None)

    reaped = loop._reap_stale_tasks(session_key)

    assert reaped == 0
    task.cancel.assert_not_called()
    # Task remains in the list.
    remaining = loop._active_tasks.get(session_key, [])
    assert len(remaining) == 1
    assert remaining[0][0] is task


def test_cognitive_scheduler_calls_reap_before_eligibility_check(tmp_path) -> None:
    """``cognitive_scheduler`` MUST call the reaper before the active-task-count
    provider, because the count feeds ``eligibility_for_cognition``. If the count
    is read first, stale tasks would still block the cognitive pass.

    This verifies the ordering at the scheduler boundary (reap → count →
    eligibility), which is the integration point the spec requires.
    """
    call_order: list[tuple[str, str]] = []

    def reap_provider(session_key: str) -> int:
        call_order.append(("reap", session_key))
        return 0

    def count_provider(session_key: str) -> int:
        call_order.append(("count", session_key))
        return 0

    scheduler = CognitiveScheduler(
        workspace=tmp_path,
        config=CognitiveSchedulerConfig(enabled=True, interval_seconds=30),
        cron_service=None,
        session_keys_provider=lambda: ["cli:a"],
        active_task_count_provider=count_provider,
        running_subagents_provider=lambda _sk: 0,
        session_processor=AsyncMock(return_value=[]),
        reap_stale_tasks_provider=reap_provider,
    )

    import asyncio

    asyncio.run(scheduler.run_once(trigger="manual"))

    # reap must precede count for every session.
    reap_idx = next(i for i, (tag, _) in enumerate(call_order) if tag == "reap")
    count_idx = next(i for i, (tag, _) in enumerate(call_order) if tag == "count")
    assert reap_idx < count_idx, (
        f"reap must be called before count; got order={call_order}"
    )


def test_stale_task_timeout_seconds_configurable(monkeypatch) -> None:
    """With ``stale_task_timeout_seconds=300``, a 360 s-old task is reaped
    (over the configured 300 s threshold), confirming the timeout is
    configurable (rule 14: assumption "older than threshold is safe to cancel"
    is backed by this test)."""
    loop = _make_minimal_loop(stale_timeout=300)
    session_key = "cron:job-A"
    task = _make_mock_task(done=False)
    created_at = time.time() - 360  # 6 minutes ago — over the 300 s threshold
    loop._active_tasks[session_key] = [(task, created_at)]

    monkeypatch.setattr("OriginAgent.agent.loop.log_event", lambda *a, **kw: None)

    reaped = loop._reap_stale_tasks(session_key)

    assert reaped == 1
    task.cancel.assert_called_once()
    assert loop._active_tasks.get(session_key, []) == []
