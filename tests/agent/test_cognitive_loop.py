from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from OriginAgent.agent.cognitive_loop import CognitiveLoop, CognitiveLoopConfig


@pytest.mark.asyncio
async def test_cognitive_loop_run_once_for_session_uses_sidecar_providers() -> None:
    processor = AsyncMock(return_value=["ok"])
    loop = CognitiveLoop(
        config=CognitiveLoopConfig(enabled=True, interval_seconds=30),
        session_keys_provider=lambda: ["cli:direct"],
        active_task_count_provider=lambda session_key: 2 if session_key == "cli:direct" else 0,
        running_subagents_provider=lambda session_key: 1 if session_key == "cli:direct" else 0,
        session_processor=processor,
    )

    result = await loop.run_once_for_session("cli:direct")

    assert result == ["ok"]
    processor.assert_awaited_once_with(
        "cli:direct",
        active_task_count=2,
        running_subagents=1,
    )


@pytest.mark.asyncio
async def test_cognitive_loop_run_forever_scans_sessions_until_stopped() -> None:
    calls: list[str] = []

    async def processor(session_key: str, **kwargs):
        calls.append(session_key)
        return []

    running = {"value": True}
    loop = CognitiveLoop(
        config=CognitiveLoopConfig(enabled=True, interval_seconds=1),
        session_keys_provider=lambda: ["cli:a", "cli:b"],
        active_task_count_provider=lambda session_key: 0,
        running_subagents_provider=lambda session_key: 0,
        session_processor=processor,
    )

    task = asyncio.create_task(loop.run_forever(lambda: running["value"]))
    await asyncio.sleep(1.1)
    running["value"] = False
    await asyncio.wait_for(task, timeout=1.0)

    assert "cli:a" in calls
    assert "cli:b" in calls
