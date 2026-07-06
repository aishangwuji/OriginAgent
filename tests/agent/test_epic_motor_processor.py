"""EpicMotorProcessor 单元测试 — 覆盖节拍派发、串并行调度、异常隔离、循环退出。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

from OriginAgent.agent.action_runtime import ActionExecutionResult, ActionIntent
from OriginAgent.agent.action_safety import ActionDecision
from OriginAgent.agent.epic_motor import EpicActionQueue, EpicMotorCommand, EpicMotorProcessor


# ---------------------------------------------------------------------------
# 测试工厂：复用 test_epic_action_queue 的构造模式
# ---------------------------------------------------------------------------


def _make_intent(**kwargs) -> ActionIntent:
    """构造测试用 ActionIntent，提供合理默认值。"""
    defaults = {
        "action": "turn_on",
        "scope": "home.living_room.light",
        "trigger": "user_initiated",
        "risk": "low",
        "requested_by": "alice",
    }
    defaults.update(kwargs)
    return ActionIntent(**defaults)


def _make_decision(**kwargs) -> ActionDecision:
    """构造测试用 ActionDecision，默认 allow。"""
    defaults = {
        "decision": "allow",
        "reason": "safety checks passed",
        "presence_status": "unknown",
    }
    defaults.update(kwargs)
    return ActionDecision(**defaults)


def _make_command(
    *,
    action_id: str = "action_test",
    ready_at: datetime | None = None,
    enqueued_at: datetime | None = None,
    duration_ms: int = 0,
    requires_parallel: bool = False,
    intent_kwargs: dict | None = None,
    decision_kwargs: dict | None = None,
) -> EpicMotorCommand:
    """构造测试用 EpicMotorCommand，ready_at/enqueued_at 默认为当前 UTC 时间。"""
    now = datetime.now(timezone.utc)
    return EpicMotorCommand(
        action_id=action_id,
        intent=_make_intent(**(intent_kwargs or {})),
        decision=_make_decision(**(decision_kwargs or {})),
        enqueued_at=enqueued_at or now,
        ready_at=ready_at or now,
        duration_ms=duration_ms,
        requires_parallel=requires_parallel,
    )


def _make_executor(
    *,
    failing_ids: set[str] | None = None,
) -> MagicMock:
    """构造 mock SafeActionExecutor。

    _execute_allowed 按 action_id 返回 executed 结果；
    failing_ids 中的 action_id 会抛 RuntimeError，用于验证异常隔离。
    """
    failing_ids = failing_ids or set()
    executor = MagicMock()

    def _execute_allowed(action_id, intent, decision, now):
        if action_id in failing_ids:
            raise RuntimeError(f"boom-{action_id}")
        return ActionExecutionResult(status="executed", action_id=action_id, reason="ok")

    executor._execute_allowed.side_effect = _execute_allowed
    return executor


# ---------------------------------------------------------------------------
# 测试 1：单个就绪命令被派发
# ---------------------------------------------------------------------------


def test_single_tick_dispatches_one_command():
    queue = EpicActionQueue()
    queue.enqueue(_make_command(action_id="c1"))
    executor = _make_executor()

    processor = EpicMotorProcessor(executor=executor, queue=queue)
    results = asyncio.run(processor.tick())

    assert len(results) == 1
    assert results[0].status == "executed"
    assert results[0].action_id == "c1"
    assert queue.is_empty
    executor._execute_allowed.assert_called_once()


# ---------------------------------------------------------------------------
# 测试 2：串行命令（requires_parallel=False）每 tick 只派发 1 个
# ---------------------------------------------------------------------------


def test_serial_commands_dispatched_one_per_tick():
    queue = EpicActionQueue()
    queue.enqueue(_make_command(action_id="c1", requires_parallel=False))
    queue.enqueue(_make_command(action_id="c2", requires_parallel=False))
    executor = _make_executor()

    processor = EpicMotorProcessor(executor=executor, queue=queue, max_parallel_per_tick=4)
    results = asyncio.run(processor.tick())

    assert len(results) == 1
    assert results[0].action_id == "c1"
    # 第二个串行命令仍在队列中，等待下一 tick
    assert queue.pending_count == 1
    assert executor._execute_allowed.call_count == 1


# ---------------------------------------------------------------------------
# 测试 3：并行命令（requires_parallel=True）同 tick 全部派发
# ---------------------------------------------------------------------------


def test_parallel_commands_dispatched_same_tick():
    queue = EpicActionQueue()
    queue.enqueue(_make_command(action_id="c1", requires_parallel=True))
    queue.enqueue(_make_command(action_id="c2", requires_parallel=True))
    executor = _make_executor()

    processor = EpicMotorProcessor(executor=executor, queue=queue, max_parallel_per_tick=4)
    results = asyncio.run(processor.tick())

    assert len(results) == 2
    assert {r.action_id for r in results} == {"c1", "c2"}
    assert queue.is_empty
    assert executor._execute_allowed.call_count == 2


# ---------------------------------------------------------------------------
# 测试 4：空队列 tick 返回空列表，不调用 executor
# ---------------------------------------------------------------------------


def test_empty_queue_tick_returns_empty():
    queue = EpicActionQueue()
    executor = _make_executor()

    processor = EpicMotorProcessor(executor=executor, queue=queue)
    results = asyncio.run(processor.tick())

    assert results == []
    executor._execute_allowed.assert_not_called()


# ---------------------------------------------------------------------------
# 测试 5：run_forever 在 is_running() 返回 False 时退出
# ---------------------------------------------------------------------------


def test_run_forever_stops_when_not_running():
    queue = EpicActionQueue()
    executor = _make_executor()
    processor = EpicMotorProcessor(
        executor=executor, queue=queue, tick_interval_ms=1
    )

    call_count = {"n": 0}

    def is_running() -> bool:
        call_count["n"] += 1
        return call_count["n"] == 1  # 仅第一次返回 True

    # 用 wait_for 加超时，防止死循环
    asyncio.run(asyncio.wait_for(processor.run_forever(is_running), timeout=2.0))
    # 至少检查了两次 is_running（第一次 True 进入循环，第二次 False 退出）
    assert call_count["n"] >= 2


# ---------------------------------------------------------------------------
# 测试 6：单个命令异常隔离，不影响其他命令派发
# ---------------------------------------------------------------------------


def test_dispatch_exception_isolated():
    queue = EpicActionQueue()
    queue.enqueue(_make_command(action_id="ok1", requires_parallel=True))
    queue.enqueue(_make_command(action_id="boom", requires_parallel=True))
    queue.enqueue(_make_command(action_id="ok2", requires_parallel=True))
    executor = _make_executor(failing_ids={"boom"})

    processor = EpicMotorProcessor(executor=executor, queue=queue, max_parallel_per_tick=4)
    results = asyncio.run(processor.tick())

    assert len(results) == 3
    by_id = {r.action_id: r for r in results}
    assert by_id["ok1"].status == "executed"
    assert by_id["ok2"].status == "executed"
    # 异常命令被转换为 failed result，不抛出
    assert by_id["boom"].status == "failed"
    assert "boom" in by_id["boom"].reason
