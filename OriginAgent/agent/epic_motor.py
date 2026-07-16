"""EPIC 运动缓冲层 — 感知-认知-运动三处理器并行的运动队列。

本模块实现 EPIC 认知架构的运动处理器侧：
- EpicMotorCommand: 封装已通过门禁的可执行意图 + 运动调度元数据
- EpicActionQueue: 线程安全 FIFO + ready_at 时间戳调度
- EpicMotorProcessor: 50ms 节拍器（Task 2 实现）

设计要点：使用 threading.Lock 而非 asyncio.Lock，原因是生产者
SafeActionExecutor.submit() 为同步方法、消费者 EpicMotorProcessor.tick() 为
异步方法，threading.Lock 在两种上下文中都能直接使用，避免 async 桥接复杂度。
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from OriginAgent.agent.action_runtime import ActionExecutionResult, ActionIntent
from OriginAgent.agent.action_safety import ActionDecision

if TYPE_CHECKING:
    # SafeActionExecutor 仅用于类型注解；运行时构造 ActionExecutionResult 需要顶层导入，
    # 故 ActionExecutionResult 不放在 TYPE_CHECKING 下。action_runtime 不导入 epic_motor，无循环依赖。
    from OriginAgent.agent.action_runtime import SafeActionExecutor


@dataclass
class EpicMotorCommand:
    """已通过安全门/权限/确认的可执行意图 + 运动调度元数据。

    duration_ms: 动作物理执行时长（ms），0 表示瞬时
    requires_parallel: True 表示可与其它并行命令同 tick 派发；False 表示必须独占 tick
    ready_at: 命令可派发的最早时间；ready_at <= now 时才被 dequeue_ready 选中
    enqueued_at: 入队时间戳，用于 FIFO 排序与诊断
    """

    action_id: str
    intent: ActionIntent
    decision: ActionDecision
    enqueued_at: datetime
    ready_at: datetime
    duration_ms: int = 0
    requires_parallel: bool = False

    def to_json(self) -> dict[str, Any]:
        # 序列化为可 JSON 化的 dict；datetime 转 ISO 字符串以便跨进程/持久化
        # intent/decision 均为 dataclass，用 asdict 递归转 dict；payload 已在入队前 sanitize
        return {
            "action_id": self.action_id,
            "intent": asdict(self.intent),
            "decision": asdict(self.decision),
            "enqueued_at": self.enqueued_at.isoformat(),
            "ready_at": self.ready_at.isoformat(),
            "duration_ms": self.duration_ms,
            "requires_parallel": self.requires_parallel,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "EpicMotorCommand":
        # 反序列化：ISO 字符串还原为 datetime；intent/decision 用关键字重建
        intent = ActionIntent(**data["intent"])
        decision = ActionDecision(**data["decision"])
        return cls(
            action_id=data["action_id"],
            intent=intent,
            decision=decision,
            enqueued_at=datetime.fromisoformat(data["enqueued_at"]),
            ready_at=datetime.fromisoformat(data["ready_at"]),
            duration_ms=data.get("duration_ms", 0),
            requires_parallel=data.get("requires_parallel", False),
        )


class EpicActionQueue:
    """线程安全 FIFO 队列 + ready_at 时间戳调度。

    使用 threading.Lock（而非 asyncio.Lock）以支持同步生产者（SafeActionExecutor.submit）
    与异步消费者（EpicMotorProcessor.tick）双向访问：
    - 生产者 submit() 是同步方法，无法 await asyncio.Lock
    - 消费者 tick() 虽是 async，但可直接调用同步 dequeue_ready（无需 await）
    - threading.Lock 无竞争时开销极低，且在 sync/async 上下文中行为一致
    """

    def __init__(self) -> None:
        self._commands: list[EpicMotorCommand] = []
        self._lock = threading.Lock()

    def enqueue(self, command: EpicMotorCommand) -> None:
        """同步入队（线程安全）。"""
        with self._lock:
            self._commands.append(command)

    def dequeue_ready(self, now: datetime, *, max_parallel: int = 4) -> list[EpicMotorCommand]:
        """取出就绪命令（ready_at <= now），遵循 requires_parallel=False 优先串行语义。

        算法：
        1. 在锁内按 FIFO 顺序遍历队列
        2. 跳过 ready_at > now 的命令（保留在队列中等待后续 tick）
        3. 收集就绪命令：
           - 非并行命令（requires_parallel=False）每 tick 最多取 1 个；
             之后遇到的非并行命令跳过（不中断扫描），让后续并行命令仍可填充至 max_parallel
           - 并行命令（requires_parallel=True）直接填充至 max_parallel 上限
        4. 从队列中移除已返回的命令（从后往前删以避免索引错位）
        5. 返回就绪命令列表
        """
        if max_parallel <= 0:
            return []
        ready: list[EpicMotorCommand] = []
        indices_to_remove: list[int] = []
        seen_non_parallel = False  # 本 tick 是否已选取过非并行命令
        with self._lock:
            for index, command in enumerate(self._commands):
                if len(ready) >= max_parallel:
                    break  # 已达并行上限，停止扫描
                if command.ready_at > now:
                    continue  # 未就绪，保留在队列中
                if not command.requires_parallel:
                    # 非并行命令必须独占 tick：每 tick 最多派发 1 个
                    # 跳过而非中断，以便其后就绪的并行命令仍可填充 max_parallel 槽位
                    if seen_non_parallel:
                        continue
                    seen_non_parallel = True
                ready.append(command)
                indices_to_remove.append(index)
            # 从后往前删，避免索引错位
            for index in reversed(indices_to_remove):
                del self._commands[index]
        return ready

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return not self._commands

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._commands)


class EpicMotorProcessor:
    """EPIC 运动处理器 — 以可配置节拍（默认 50ms）从队列取出就绪命令派发。

    每个节拍：
    1. 调用 queue.dequeue_ready(now, max_parallel=max_parallel_per_tick) 取出就绪命令
    2. 用 asyncio.gather 并发调用 executor._execute_allowed(action_id, intent, decision, now)
    3. 单个命令异常隔离：捕获后产出 ActionExecutionResult(status="failed")，不影响其他命令
    4. 返回本 tick 的派发结果列表
    """

    def __init__(
        self,
        *,
        executor: "SafeActionExecutor",
        queue: EpicActionQueue,
        max_parallel_per_tick: int = 4,
        tick_interval_ms: int = 50,
    ) -> None:
        self._executor = executor
        self._queue = queue
        self._max_parallel_per_tick = max_parallel_per_tick
        self._tick_interval_ms = tick_interval_ms

    async def tick(self) -> list[ActionExecutionResult]:
        """执行一次节拍：dequeue_ready + 并发派发 + 异常隔离。"""
        # 同一 tick 内统一时间戳，保证 dequeue_ready 与 _execute_allowed 看到一致的 now
        now = datetime.now(timezone.utc)
        # dequeue_ready 是同步方法，直接调用（不要 await）
        ready = self._queue.dequeue_ready(now, max_parallel=self._max_parallel_per_tick)
        if not ready:
            return []

        async def dispatch(command: EpicMotorCommand) -> ActionExecutionResult:
            # _execute_allowed 是同步方法（会调用 backend.execute），
            # 用 asyncio.to_thread 包装避免阻塞事件循环
            # 单个命令异常隔离：捕获后返回 failed result，不抛出，不影响其他命令
            try:
                result = await asyncio.to_thread(
                    self._executor._execute_allowed,
                    command.action_id,
                    command.intent,
                    command.decision,
                    now,
                )
                # 规则12 红线：派发成功后必须记忆幂等键，防止重试导致重复执行
                # _remember_successful_idempotency 内部已检查 status in {"executed", "dry_run"}
                self._executor._remember_successful_idempotency(command.intent, result)
                return result
            except Exception as exc:
                return ActionExecutionResult(
                    status="failed",
                    action_id=command.action_id,
                    reason=str(exc),
                )

        # 并发派发所有就绪命令；dispatch 内部已捕获异常，gather 不会传播
        return await asyncio.gather(*(dispatch(cmd) for cmd in ready))

    async def run_forever(self, is_running: Callable[[], bool]) -> None:
        """节拍循环：tick() + sleep(tick_interval_ms/1000)，异常后 logger.exception 并继续。

        is_running() 返回 False 时退出循环。
        """
        interval = self._tick_interval_ms / 1000
        while is_running():
            try:
                await self.tick()
            except Exception:
                # tick 内部已有异常隔离；此处捕获 dequeue/gather 层面的意外异常，避免中断循环
                logger.exception("EpicMotorProcessor tick failed")
            await asyncio.sleep(interval)
