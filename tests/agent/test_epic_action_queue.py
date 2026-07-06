"""EpicActionQueue 单元测试 — 覆盖入队/出队/串并行调度/线程安全/序列化。"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from OriginAgent.agent.action_runtime import ActionIntent
from OriginAgent.agent.action_safety import ActionDecision
from OriginAgent.agent.epic_motor import EpicActionQueue, EpicMotorCommand


# ---------------------------------------------------------------------------
# 测试工厂：构造最小可用的 ActionIntent / ActionDecision / EpicMotorCommand
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


# ---------------------------------------------------------------------------
# 测试 1：enqueue 递增 pending_count
# ---------------------------------------------------------------------------


def test_enqueue_increments_pending_count():
    queue = EpicActionQueue()

    assert queue.pending_count == 0
    queue.enqueue(_make_command(action_id="c1"))
    assert queue.pending_count == 1
    queue.enqueue(_make_command(action_id="c2"))
    assert queue.pending_count == 2


# ---------------------------------------------------------------------------
# 测试 2：dequeue_ready 按 ready_at 过滤
# ---------------------------------------------------------------------------


def test_dequeue_ready_filters_by_ready_at():
    queue = EpicActionQueue()
    now = datetime.now(timezone.utc)
    past = now - timedelta(seconds=1)
    future = now + timedelta(seconds=10)

    ready_cmd = _make_command(action_id="ready", ready_at=past)
    future_cmd = _make_command(action_id="future", ready_at=future)
    queue.enqueue(ready_cmd)
    queue.enqueue(future_cmd)

    result = queue.dequeue_ready(now, max_parallel=4)

    assert len(result) == 1
    assert result[0].action_id == "ready"
    # 未就绪命令仍保留在队列中
    assert queue.pending_count == 1


# ---------------------------------------------------------------------------
# 测试 3：max_parallel=1 时只取 1 个
# ---------------------------------------------------------------------------


def test_dequeue_ready_serial_mode_max_parallel_1():
    queue = EpicActionQueue()
    now = datetime.now(timezone.utc)
    past = now - timedelta(seconds=1)

    for i in range(5):
        queue.enqueue(_make_command(action_id=f"cmd_{i}", ready_at=past))

    result = queue.dequeue_ready(now, max_parallel=1)

    assert len(result) == 1
    assert result[0].action_id == "cmd_0"  # FIFO 取第一个
    assert queue.pending_count == 4


# ---------------------------------------------------------------------------
# 测试 4：max_parallel=N 且就绪命令 <= N 时全部取出
# ---------------------------------------------------------------------------


def test_dequeue_ready_parallel_mode_fills_max():
    queue = EpicActionQueue()
    now = datetime.now(timezone.utc)
    past = now - timedelta(seconds=1)

    # 3 个并行命令，max_parallel=4（>3），应全部取出
    for i in range(3):
        queue.enqueue(
            _make_command(action_id=f"cmd_{i}", ready_at=past, requires_parallel=True)
        )

    result = queue.dequeue_ready(now, max_parallel=4)

    assert len(result) == 3
    assert [c.action_id for c in result] == ["cmd_0", "cmd_1", "cmd_2"]
    assert queue.is_empty


# ---------------------------------------------------------------------------
# 测试 5：前 K 个非并行命令时最多返回 1 个非并行，其后并行命令填充至 max_parallel
# ---------------------------------------------------------------------------


def test_dequeue_ready_non_parallel_first_serializes():
    queue = EpicActionQueue()
    now = datetime.now(timezone.utc)
    past = now - timedelta(seconds=1)

    # 前 3 个非并行，其后 3 个并行，max_parallel=4
    for i in range(3):
        queue.enqueue(
            _make_command(action_id=f"np_{i}", ready_at=past, requires_parallel=False)
        )
    for i in range(3):
        queue.enqueue(
            _make_command(action_id=f"p_{i}", ready_at=past, requires_parallel=True)
        )

    result = queue.dequeue_ready(now, max_parallel=4)

    # 1 个非并行 + 3 个并行 = 4
    assert len(result) == 4
    assert result[0].action_id == "np_0"  # 第一个非并行命令
    assert result[1].action_id == "p_0"
    assert result[2].action_id == "p_1"
    assert result[3].action_id == "p_2"
    # 剩余 2 个非并行命令仍在队列中，等待后续 tick
    assert queue.pending_count == 2


# ---------------------------------------------------------------------------
# 测试 6：空队列 is_empty=True，dequeue_ready 返回 []
# ---------------------------------------------------------------------------


def test_empty_queue_is_empty():
    queue = EpicActionQueue()
    now = datetime.now(timezone.utc)

    assert queue.is_empty
    assert queue.pending_count == 0
    assert queue.dequeue_ready(now, max_parallel=4) == []


# ---------------------------------------------------------------------------
# 测试 7：多线程并发 enqueue + dequeue_ready 无异常，且总数守恒
# ---------------------------------------------------------------------------


def test_concurrent_enqueue_dequeue_thread_safe():
    """4 个生产者 + 2 个消费者并发操作各 100 次，验证无异常且总数守恒。"""
    queue = EpicActionQueue()
    now = datetime.now(timezone.utc)
    # ready_at 设为过去，确保命令一经入队即就绪，可被消费者取走
    past = now - timedelta(seconds=1)
    total_per_producer = 100
    num_producers = 4
    total_enqueued = num_producers * total_per_producer

    all_dequeued: list[EpicMotorCommand] = []
    dequeued_lock = threading.Lock()
    # 用 list 做 Producer 间共享计数器，记录尚未完成的生产者数
    producers_remaining = [num_producers]
    pr_lock = threading.Lock()

    def producer():
        try:
            for _ in range(total_per_producer):
                queue.enqueue(_make_command(ready_at=past))
        finally:
            with pr_lock:
                producers_remaining[0] -= 1

    def consumer():
        local: list[EpicMotorCommand] = []
        # 持续消费直到所有生产者完成且队列排空
        while True:
            ready = queue.dequeue_ready(now, max_parallel=4)
            if ready:
                local.extend(ready)
            with pr_lock:
                # 生产者全部完成后队列若也空，则可安全退出
                # （生产者递减在 enqueue 之后，故 producers_remaining==0 时无新命令入队）
                if producers_remaining[0] == 0 and queue.is_empty:
                    break
        with dequeued_lock:
            all_dequeued.extend(local)

    producers = [threading.Thread(target=producer) for _ in range(num_producers)]
    consumers = [threading.Thread(target=consumer) for _ in range(2)]

    for t in producers + consumers:
        t.start()
    for t in producers + consumers:
        t.join()

    # 总数守恒：所有入队命令要么被消费，要么仍在队列中
    assert len(all_dequeued) + queue.pending_count == total_enqueued


# ---------------------------------------------------------------------------
# 测试 8：to_json / from_json 往返一致
# ---------------------------------------------------------------------------


def test_to_json_from_json_round_trip():
    now = datetime.now(timezone.utc)
    cmd = _make_command(
        action_id="action_abc",
        ready_at=now,
        enqueued_at=now,
        duration_ms=500,
        requires_parallel=True,
        intent_kwargs={"action": "unlock", "scope": "home.entry.lock", "risk": "high"},
        decision_kwargs={"decision": "allow", "reason": "confirmed"},
    )

    restored = EpicMotorCommand.from_json(cmd.to_json())

    assert restored.action_id == cmd.action_id
    assert restored.duration_ms == cmd.duration_ms
    assert restored.requires_parallel == cmd.requires_parallel
    assert restored.ready_at == cmd.ready_at
    assert restored.enqueued_at == cmd.enqueued_at
    assert restored.intent.action == "unlock"
    assert restored.intent.scope == "home.entry.lock"
    assert restored.intent.risk == "high"
    assert restored.decision.decision == "allow"
    assert restored.decision.reason == "confirmed"
