from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
