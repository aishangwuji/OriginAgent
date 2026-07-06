"""CognitiveLoop 双循环并发测试 — 审议循环（15s）+ 运动节拍器（50ms）。

覆盖场景：
1. motor_processor=None 时仅运行审议循环（向后兼容）
2. 双循环并发：tick 与 session_processor 都被调用
3. motor tick 异常隔离：不影响审议循环
4. CognitiveLoopConfig 默认 motor_tick_interval_ms == 50
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.cognitive_loop import CognitiveLoop, CognitiveLoopConfig


@pytest.mark.asyncio
async def test_motor_processor_none_only_deliberation_loop():
    """motor_processor=None 时仅运行审议循环。"""
    processor = AsyncMock()
    loop = CognitiveLoop(
        config=CognitiveLoopConfig(enabled=True, interval_seconds=0.01),
        session_keys_provider=lambda: ["s1"],
        active_task_count_provider=lambda k: 0,
        running_subagents_provider=lambda k: 0,
        session_processor=processor,
        motor_processor=None,
    )
    # 运行 50ms 后停止
    running = [True]

    async def stop_after_50ms():
        await asyncio.sleep(0.05)
        running[0] = False

    await asyncio.wait_for(
        asyncio.gather(
            loop.run_forever(lambda: running[0]),
            stop_after_50ms(),
        ),
        timeout=2.0,
    )
    assert processor.call_count > 0


@pytest.mark.asyncio
async def test_dual_loops_run_concurrently():
    """双循环并发：motor_processor.tick 被调用，session_processor 也被调用。"""
    session_proc = AsyncMock()
    motor_proc = MagicMock()
    motor_proc.tick = AsyncMock()
    loop = CognitiveLoop(
        config=CognitiveLoopConfig(
            enabled=True, interval_seconds=0.01, motor_tick_interval_ms=10
        ),
        session_keys_provider=lambda: ["s1"],
        active_task_count_provider=lambda k: 0,
        running_subagents_provider=lambda k: 0,
        session_processor=session_proc,
        motor_processor=motor_proc,
    )
    running = [True]

    async def stop_after_80ms():
        await asyncio.sleep(0.08)
        running[0] = False

    await asyncio.wait_for(
        asyncio.gather(
            loop.run_forever(lambda: running[0]),
            stop_after_80ms(),
        ),
        timeout=2.0,
    )
    assert motor_proc.tick.call_count >= 2
    assert session_proc.call_count >= 1


@pytest.mark.asyncio
async def test_motor_loop_exception_isolation():
    """motor_processor.tick 抛异常时不影响审议循环（异常隔离）。"""
    session_proc = AsyncMock()
    motor_proc = MagicMock()
    motor_proc.tick = AsyncMock(side_effect=RuntimeError("tick boom"))
    loop = CognitiveLoop(
        config=CognitiveLoopConfig(
            enabled=True, interval_seconds=0.01, motor_tick_interval_ms=5
        ),
        session_keys_provider=lambda: ["s1"],
        active_task_count_provider=lambda k: 0,
        running_subagents_provider=lambda k: 0,
        session_processor=session_proc,
        motor_processor=motor_proc,
    )
    running = [True]

    async def stop_after_50ms():
        await asyncio.sleep(0.05)
        running[0] = False

    await asyncio.wait_for(
        asyncio.gather(
            loop.run_forever(lambda: running[0]),
            stop_after_50ms(),
        ),
        timeout=2.0,
    )
    # 审议循环仍被调用（异常被隔离）
    assert session_proc.call_count >= 1
    # motor_tick 多次抛异常但循环未退出
    assert motor_proc.tick.call_count >= 2


def test_motor_tick_interval_ms_default_50():
    """CognitiveLoopConfig 默认 motor_tick_interval_ms == 50。"""
    config = CognitiveLoopConfig()
    assert config.motor_tick_interval_ms == 50
