# Tasks

- [x] Task 1: 创建 EPIC 运动缓冲数据模型与队列（epic_motor.py 第一部分）
  - [x] SubTask 1.1: 新建 `OriginAgent/agent/epic_motor.py`，定义 `EpicMotorCommand` dataclass：`action_id, intent: ActionIntent, decision: ActionDecision, enqueued_at: datetime, ready_at: datetime, duration_ms: int = 0, requires_parallel: bool = False` + `to_json()/from_json()`
  - [x] SubTask 1.2: 实现 `EpicActionQueue` 类：`__init__()` 内部 `list[EpicMotorCommand]` + `asyncio.Lock`；`async enqueue(command)` / `async dequeue_ready(now, max_parallel=4) -> list[EpicMotorCommand]` / `is_empty` / `pending_count` 属性
  - [x] SubTask 1.3: `dequeue_ready` 实现 `requires_parallel=False` 优先串行逻辑：遇到非并行命令最多返回 1 个，其后并行命令可填充至 `max_parallel`
  - [x] SubTask 1.4: 编写测试 `tests/agent/test_epic_action_queue.py`（≥6 测试）：enqueue 计数、dequeue_ready 就绪过滤、串行模式、并行模式、requires_parallel=False 优先串行、空队列、并发安全

- [x] Task 2: 实现 EpicMotorProcessor（epic_motor.py 第二部分）
  - [x] SubTask 2.1: 在 `epic_motor.py` 实现 `EpicMotorProcessor` 类：`__init__(*, executor: SafeActionExecutor, queue: EpicActionQueue, max_parallel_per_tick: int = 4, tick_interval_ms: int = 50)`
  - [x] SubTask 2.2: 实现 `async def tick(self) -> list[ActionExecutionResult]`：dequeue_ready → `asyncio.gather` 调用 `executor._execute_allowed(action_id, intent, decision, now)` → 异常隔离（单个失败不影响其他）
  - [x] SubTask 2.3: 实现 `async def run_forever(self, is_running: Callable[[], bool]) -> None`：循环 `await asyncio.sleep(tick_interval_ms / 1000)` + `await self.tick()`，异常 `logger.exception` 后继续
  - [x] SubTask 2.4: 编写测试 `tests/agent/test_epic_motor_processor.py`（≥6 测试）：单 tick 派发、串行逐 tick、并行同 tick、空队列 tick、run_forever 可停止、派发异常隔离

- [x] Task 3: ActionIntent 与 TypedDeviceAction 增加运动元数据字段
  - [x] SubTask 3.1: 在 `OriginAgent/agent/action_runtime.py` 的 `ActionIntent` dataclass 末尾增加 `duration_ms: int = 0` 与 `requires_parallel: bool = False` 字段
  - [x] SubTask 3.2: 在 `OriginAgent/domain_packs/smart_home/runtime/device_actions.py` 的 `TypedDeviceAction` dataclass 末尾增加 `duration_ms: int = 0` 与 `requires_parallel: bool = False` 字段
  - [x] SubTask 3.3: 修改 `TypedActionPlanner.to_intent()`：将 `validated.duration_ms` 与 `validated.requires_parallel` 透传到构造的 `ActionIntent`
  - [x] SubTask 3.4: 验证 `DeviceActionSchemaRegistry._normalize_action` 的 `replace(...)` 调用保留新字段（dataclass `replace` 默认保留未指定字段，无需额外修改；如有覆盖需补全）
  - [x] SubTask 3.5: 编写测试 `tests/agent/test_typed_device_action_epic_fields.py`（≥4 测试）：TypedDeviceAction 默认值、显式指定、planner.to_intent 透传、schema.validate 保留字段

- [x] Task 4: SafeActionExecutor 队列模式集成
  - [x] SubTask 4.1: 在 `SafeActionExecutor.__init__` 增加可选参数 `motor_queue: EpicActionQueue | None = None`，存为 `self._motor_queue`
  - [x] SubTask 4.2: 新增私有方法 `_enqueue_motor_command(self, action_id, intent, decision, now) -> ActionExecutionResult`：构造 `EpicMotorCommand`（`ready_at=now`，从 `intent.duration_ms` / `intent.requires_parallel` 取值）→ `await self._motor_queue.enqueue(cmd)` → 返回 `ActionExecutionResult(status="queued", action_id=action_id, decision=decision)`
  - [x] SubTask 4.3: 修改 `submit()` 的 allow 分支：当 `self._motor_queue is not None` 时调用 `_enqueue_motor_command` 而非 `_execute_allowed`；其余分支（deny / ask_confirmation / already_executed / blocked_by_simulation）保持不变
  - [x] SubTask 4.4: 由于 `submit` 当前是同步方法而 `enqueue` 是 async，需要将 `submit` 的队列分支改为同步调用 `enqueue`（内部用 `asyncio.Lock` 但 enqueue 本身可同步包装：`asyncio.get_event_loop().run_until_complete` 不适用于已运行循环；改为 `EpicActionQueue.enqueue` 提供 sync 变体 `enqueue_sync` 或在 `submit` 中使用 `asyncio.create_task` 后立即返回 queued 状态）
  - [x] SubTask 4.5: 编写测试 `tests/agent/test_safe_executor_motor_queue.py`（≥6 测试）：motor_queue=None 同步行为、motor_queue 启用返回 queued、队列模式跳过 _execute_allowed、deny 不入队、already_executed 不入队、ask_confirmation 不入队

- [x] Task 5: CognitiveLoop 双循环并发集成
  - [x] SubTask 5.1: 修改 `CognitiveLoopConfig`：增加 `motor_tick_interval_ms: int = 50` 字段
  - [x] SubTask 5.2: 修改 `CognitiveLoop.__init__`：增加可选参数 `motor_processor: EpicMotorProcessor | None = None`
  - [x] SubTask 5.3: 重构 `run_forever`：当 `motor_processor is not None` 时用 `asyncio.gather` 并发运行 `_run_deliberation_loop(is_running)` 与 `_run_motor_loop(is_running)`；当为 None 时仅运行审议循环（保持向后兼容）
  - [x] SubTask 5.4: 新增 `_run_motor_loop(self, is_running)`：循环 `await asyncio.sleep(self.config.motor_tick_interval_ms / 1000)` + `await self._motor_processor.tick()`，异常 `logger.exception` 后继续
  - [x] SubTask 5.5: 提取原 `run_forever` 主体为 `_run_deliberation_loop(self, is_running)`
  - [x] SubTask 5.6: 编写测试 `tests/agent/test_cognitive_loop_motor.py`（≥4 测试）：motor_processor=None 仅审议循环、双循环并发、运动循环异常隔离、motor_tick_interval_ms 默认值 50

- [x] Task 6: 全量回归测试
  - [x] SubTask 6.1: 运行 `.\.venv\Scripts\python.exe -m pytest tests/agent/test_epic_action_queue.py tests/agent/test_epic_motor_processor.py tests/agent/test_typed_device_action_epic_fields.py tests/agent/test_safe_executor_motor_queue.py tests/agent/test_cognitive_loop_motor.py -v`（新增全部通过）
  - [x] SubTask 6.2: 运行 `.\.venv\Scripts\python.exe -m pytest tests/agent/test_action_runtime.py tests/agent/test_action_runtime_resume.py -v`（无回归）
  - [x] SubTask 6.3: 运行 `.\.venv\Scripts\python.exe -m pytest tests/agent/test_device_actions.py tests/agent/test_device_backends.py tests/tools/test_device_tools.py -v`（无回归）
  - [x] SubTask 6.4: 运行 `.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/ -v`（125 passed，无回归）
  - [x] SubTask 6.5: 运行 `.\.venv\Scripts\python.exe -m pytest tests/agent/test_soar_models.py tests/agent/test_soar_chunker.py tests/agent/test_subagent_obstacle_detection.py tests/agent/test_subagent_soar_callback.py tests/agent/test_skill_bootstrapper_online.py -v`（43 passed，无回归）

# Task Dependencies
- Task 2 depends on Task 1（需要 EpicActionQueue）
- Task 4 depends on Task 1, Task 3（需要 EpicActionQueue + ActionIntent 新字段）
- Task 5 depends on Task 2（需要 EpicMotorProcessor）
- Task 6 depends on 所有前序任务
- 可并行：Task 1、Task 3 相互独立；Task 2 可在 Task 1 完成后并行于 Task 3
