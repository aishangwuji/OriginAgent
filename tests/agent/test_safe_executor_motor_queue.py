"""Task 4 测试：SafeActionExecutor 的 motor_queue 可选参数。

覆盖场景：
1. motor_queue=None 保持同步执行行为
2. motor_queue 启用后 allow 分支返回 queued 且入队
3. motor_queue 启用后 backend.execute 不被调用
4. deny 分支不入队
5. already_executed 分支不入队
6. ask_confirmation 分支不入队
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from OriginAgent.agent.action_runtime import (
    ActionIntent,
    SafeActionExecutor,
)
from OriginAgent.agent.action_safety import ActionDecision
from OriginAgent.agent.confirmation import ConfirmationManager
from OriginAgent.agent.epic_motor import EpicActionQueue
from OriginAgent.domain_packs.smart_home.runtime.permissions import (
    HouseholdActor,
    PermissionResolver,
)

NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)


class FakeGate:
    """可预设决策的假门禁，记录请求以便断言。"""

    def __init__(self, decision_value):
        self.decision = decision_value
        self.requests = []

    def evaluate(self, request):
        self.requests.append(request)
        return self.decision


class CountingBackend:
    """计数后端，记录调用次数与看到的 intent。"""

    def __init__(self, result=None):
        self.result = result if result is not None else {"dry_run": True}
        self.calls = 0
        self.seen_intents = []

    def execute(self, intent):
        self.calls += 1
        self.seen_intents.append(intent)
        return self.result


def decision(value, **kwargs):
    """构造 ActionDecision 的便捷 helper。"""
    defaults = {
        "decision": value,
        "reason": f"{value} reason",
        "presence_status": "unknown",
    }
    defaults.update(kwargs)
    return ActionDecision(**defaults)


def intent(**kwargs):
    """构造 ActionIntent 的便捷 helper。"""
    defaults = {
        "action": "turn_on",
        "scope": "home.living_room.light",
        "trigger": "user_initiated",
        "risk": "low",
        "requested_by": "alice",
    }
    defaults.update(kwargs)
    return ActionIntent(**defaults)


def permissions(**actors):
    """构造智能家领域 PermissionResolver，alice 默认 admin。"""
    defaults = {"alice": HouseholdActor("alice", "admin")}
    defaults.update(actors)
    return PermissionResolver(defaults)


def make_executor(
    tmp_path,
    gate_decision,
    *,
    motor_queue=None,
    backend=None,
    permission_resolver=None,
):
    """构造带可选 motor_queue 的 SafeActionExecutor。

    复用 ConfirmationManager 提供 workspace，使幂等键存储可正常加载。
    """
    return SafeActionExecutor(
        gate=FakeGate(gate_decision),
        confirmation_manager=ConfirmationManager(tmp_path),
        backend=backend or CountingBackend(),
        permission_resolver=permission_resolver or permissions(),
        motor_queue=motor_queue,
    )


def test_motor_queue_none_keeps_sync_behavior(tmp_path):
    """motor_queue=None 时走同步执行路径，返回 executed 状态。"""
    # backend 返回无 dry_run 标记的结果 → status="executed"
    backend = CountingBackend({"backend": "test"})
    executor = make_executor(
        tmp_path,
        decision("allow"),
        motor_queue=None,
        backend=backend,
    )

    result = executor.submit(intent(), now=NOW)

    assert result.status == "executed"
    assert backend.calls == 1


def test_motor_queue_enabled_returns_queued(tmp_path):
    """motor_queue 启用后 allow 分支入队，返回 queued 且队列计数 +1。"""
    queue = EpicActionQueue()
    backend = CountingBackend({"backend": "test"})
    executor = make_executor(
        tmp_path,
        decision("allow"),
        motor_queue=queue,
        backend=backend,
    )

    result = executor.submit(intent(), now=NOW)

    assert result.status == "queued"
    assert result.reason == "queued for motor processor dispatch"
    assert queue.pending_count == 1


def test_motor_queue_skips_execute_allowed(tmp_path):
    """motor_queue 启用后 backend.execute 不应被调用（用 MagicMock 断言）。"""
    queue = EpicActionQueue()
    mock_backend = MagicMock()
    mock_backend.execute.return_value = {"backend": "test"}
    executor = make_executor(
        tmp_path,
        decision("allow"),
        motor_queue=queue,
        backend=mock_backend,
    )

    result = executor.submit(intent(), now=NOW)

    assert result.status == "queued"
    mock_backend.execute.assert_not_called()
    assert queue.pending_count == 1


def test_motor_queue_deny_does_not_enqueue(tmp_path):
    """deny 分支不入队，队列保持空。"""
    queue = EpicActionQueue()
    backend = CountingBackend()
    executor = make_executor(
        tmp_path,
        decision("deny"),
        motor_queue=queue,
        backend=backend,
    )

    result = executor.submit(intent(), now=NOW)

    assert result.status == "denied"
    assert queue.pending_count == 0
    assert backend.calls == 0


def test_motor_queue_already_executed_does_not_enqueue(tmp_path):
    """已执行的幂等键命中时返回 already_executed，不入队。"""
    queue = EpicActionQueue()
    backend = CountingBackend({"backend": "test"})
    executor = make_executor(
        tmp_path,
        decision("allow"),
        motor_queue=queue,
        backend=backend,
    )
    # 直接注入幂等键，模拟此前已成功执行过
    executor._successful_idempotency_keys.add("idem-already")

    result = executor.submit(intent(idempotency_key="idem-already"), now=NOW)

    assert result.status == "already_executed"
    assert queue.pending_count == 0
    assert backend.calls == 0


def test_motor_queue_ask_confirmation_does_not_enqueue(tmp_path):
    """ask_confirmation 分支走确认流程，不入队。"""
    queue = EpicActionQueue()
    backend = CountingBackend()
    executor = make_executor(
        tmp_path,
        decision("ask_confirmation"),
        motor_queue=queue,
        backend=backend,
    )

    result = executor.submit(
        intent(action="unlock", scope="home.entry.lock", risk="high"),
        now=NOW,
    )

    assert result.status == "pending_confirmation"
    assert result.confirmation_id is not None
    assert queue.pending_count == 0
    assert backend.calls == 0
