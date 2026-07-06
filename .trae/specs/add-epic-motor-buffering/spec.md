# EPIC 运动缓冲 Spec

## Why
当前 `SafeActionExecutor.submit()` 是同步阻塞路径：安全门 → 权限 → 确认 → `backend.execute()` 一气呵成，调用方必须等待物理执行完成才能拿到结果。这与 EPIC 认知架构的"感知-认知-运动三处理器并行"模型相悖：认知处理器发出动作意图后，应立即返回继续推理，由独立的运动处理器在 ~50ms 节拍上序列化派发。当前架构下，认知循环被 I/O 阻塞，无法在动作执行期间并行处理新感知；多个不相关动作也无法并行（即使语义安全）。

引入 EPIC 运动缓冲层：`EpicActionQueue` 缓冲已通过安全门/权限/确认的"可执行意图"，`EpicMotorProcessor` 以 50ms 节拍器（metronome）从队列取出就绪项派发到 `backend.execute()`，使认知循环与运动执行解耦，并支持 `requires_parallel=True` 的动作并发派发。

## What Changes
- 新增 `EpicActionQueue` 类（线程安全 FIFO + `ready_at` 时间戳调度，in-memory，不持久化）
- 新增 `EpicMotorCommand` 数据模型（封装 `ActionIntent` + `duration_ms` + `requires_parallel` + `ready_at` + `action_id`）
- 新增 `EpicMotorProcessor` 类（50ms tick 循环，drain ready 命令，按 `requires_parallel` 决定串行/并行派发）
- 修改 `SafeActionExecutor`：增加可选 `motor_queue: EpicActionQueue | None = None` 参数；当队列启用且 intent 通过所有门禁后，**enqueue 而非直接 `_execute_allowed`**；返回 `status="queued"` 立即释放调用方
- 修改 `CognitiveLoop`：增加可选 `motor_processor: EpicMotorProcessor | None = None`；`run_forever` 用 `asyncio.gather` 并发运行 15s 审议循环 + 50ms 运动节拍器
- 修改 `CognitiveLoopConfig`：增加 `motor_tick_interval_ms: int = 50` 字段
- 修改 `TypedDeviceAction`：增加 `duration_ms: int = 0` 与 `requires_parallel: bool = False` 字段（默认保持现有串行语义）
- 修改 `TypedActionPlanner.to_intent()`：透传 `duration_ms` / `requires_parallel` 到 `ActionIntent`
- 修改 `ActionIntent`：增加 `duration_ms: int = 0` 与 `requires_parallel: bool = False` 字段
- **不在本 spec 范围**：队列持久化（崩溃恢复）、动作取消、优先级队列、运动完成事件回调到 BDI 审议回路、`SafeActionExecutor` 全量默认切换到队列模式（保持 opt-in 向后兼容）

## Impact
- Affected specs: `add-actr-utility-learning`（认知循环并行化后，效用学习的 `_update_utilities` 在认知循环中仍串行，不受影响）、`add-soar-online-chunking`（块化触发仍在子代理回调路径，与运动队列正交）
- Affected code:
  - `OriginAgent/agent/epic_motor.py` — **新增**（`EpicActionQueue`、`EpicMotorCommand`、`EpicMotorProcessor`）
  - `OriginAgent/agent/action_runtime.py` — `SafeActionExecutor` 增加 `motor_queue` 参数 + `submit()` 分支 + `_enqueue_motor_command()` 私有方法；`ActionIntent` 增加 `duration_ms` / `requires_parallel` 字段
  - `OriginAgent/agent/cognitive_loop.py` — `CognitiveLoopConfig` 增加 `motor_tick_interval_ms`；`CognitiveLoop.__init__` 增加 `motor_processor` 可选参数；`run_forever` 用 `asyncio.gather` 并发两循环
  - `OriginAgent/domain_packs/smart_home/runtime/device_actions.py` — `TypedDeviceAction` 增加两个字段；`TypedActionPlanner.to_intent()` 透传
  - 测试文件（新增）

## ADDED Requirements

### Requirement: EpicMotorCommand 数据模型
系统 SHALL 提供 `EpicMotorCommand` 数据模型，封装已通过门禁的可执行意图及其运动调度元数据。

#### Scenario: 默认立即就绪
- **WHEN** 创建 `EpicMotorCommand(action_id, intent, decision, now)` 不指定 `ready_at`
- **THEN** `ready_at` 默认等于 `now`，下一 tick 即可派发

#### Scenario: 延迟就绪
- **WHEN** 创建时指定 `ready_at = now + timedelta(milliseconds=300)`
- **THEN** 在 `ready_at` 之前的 tick 不会派发该命令

#### Scenario: 序列化往返
- **WHEN** 调用 `to_json()` 后再 `from_json()`
- **THEN** 字段 `action_id`、`intent`、`duration_ms`、`requires_parallel`、`ready_at`、`enqueued_at` 全部保留

### Requirement: EpicActionQueue 线程安全 FIFO
`EpicActionQueue` SHALL 提供线程安全的 FIFO 队列语义，支持 `ready_at` 时间戳调度。

#### Scenario: enqueue 后立即查询
- **WHEN** 调用 `queue.enqueue(command)` 后调用 `queue.pending_count`
- **THEN** 计数 +1

#### Scenario: dequeue_ready 仅返回已就绪项
- **WHEN** 队列含 `ready_at <= now` 的命令 A 与 `ready_at > now` 的命令 B
- **THEN** `dequeue_ready(now)` 返回 `[A]`，B 仍保留在队列中

#### Scenario: dequeue_ready 串行模式
- **WHEN** `dequeue_ready(now, max_parallel=1)` 且队列中有 3 个就绪命令
- **THEN** 仅返回 `[首个]`，其余保留

#### Scenario: dequeue_ready 并行模式
- **WHEN** `dequeue_ready(now, max_parallel=N)` 且队列中有 ≤N 个就绪命令
- **THEN** 全部返回；若 >N 则返回前 N 个

#### Scenario: requires_parallel=False 优先串行
- **WHEN** 队列中前 K 个命令的 `requires_parallel=False`
- **THEN** `dequeue_ready` 最多返回 1 个非并行命令（即使 max_parallel>1），其后的 `requires_parallel=True` 命令一并返回至 max_parallel 上限

#### Scenario: 空队列
- **WHEN** 队列为空
- **THEN** `is_empty` 为 True，`dequeue_ready` 返回 `[]`

#### Scenario: 线程安全
- **WHEN** 多个协程并发 `enqueue` 与 `dequeue_ready`
- **THEN** 通过 `asyncio.Lock` 序列化，无数据竞争

### Requirement: EpicMotorProcessor 50ms 节拍器
`EpicMotorProcessor` SHALL 以可配置（默认 50ms）的节拍循环：每个 tick 调用 `queue.dequeue_ready(now)` 派发就绪命令。

#### Scenario: 单 tick 派发
- **WHEN** 队列中有 1 个就绪命令
- **AND** 调用 `await processor.tick()`
- **THEN** 命令通过 `executor._execute_allowed(action_id, intent, decision, now)` 派发
- **AND** 返回 `[ActionExecutionResult(status="executed")]`

#### Scenario: 串行命令逐 tick 派发
- **WHEN** 队列中有 2 个 `requires_parallel=False` 命令
- **AND** 调用 `await processor.tick()` 一次
- **THEN** 仅派发首个命令，第二个保留在队列

#### Scenario: 并行命令同 tick 派发
- **WHEN** 队列中有 2 个 `requires_parallel=True` 命令
- **AND** `max_parallel_per_tick=4`
- **AND** 调用 `await processor.tick()`
- **THEN** 两个命令均被派发（`asyncio.gather`）

#### Scenario: 空队列 tick
- **WHEN** 队列为空
- **AND** 调用 `await processor.tick()`
- **THEN** 返回 `[]`，不抛异常

#### Scenario: run_forever 可停止
- **WHEN** `is_running()` 返回 False
- **THEN** `run_forever` 退出循环，不阻塞

#### Scenario: 派发异常隔离
- **WHEN** 某个命令的 `backend.execute()` 抛异常
- **THEN** 该命令产出 `ActionExecutionResult(status="failed")`，不影响其他命令或下一 tick

### Requirement: SafeActionExecutor 队列模式
`SafeActionExecutor` SHALL 支持可选的 `motor_queue` 参数，启用后 submit 路径在通过门禁后 enqueue 而非直接执行。

#### Scenario: motor_queue=None 保持同步行为
- **WHEN** `SafeActionExecutor(motor_queue=None)` 且 `submit(intent)` 走 allow 分支
- **THEN** 同步调用 `_execute_allowed` 并返回 `status="executed"`（保持向后兼容）

#### Scenario: motor_queue 启用后返回 queued
- **WHEN** `SafeActionExecutor(motor_queue=queue)` 且 `submit(intent)` 走 allow 分支
- **THEN** 调用 `_enqueue_motor_command()` 将 `(action_id, intent, decision, now, duration_ms, requires_parallel)` 入队
- **AND** 返回 `ActionExecutionResult(status="queued", action_id=action_id)`

#### Scenario: 队列模式跳过 _execute_allowed
- **WHEN** motor_queue 启用
- **THEN** `submit` 不直接调用 `backend.execute()`，由 `EpicMotorProcessor` 在 tick 中调用

#### Scenario: 队列模式保留门禁
- **WHEN** motor_queue 启用且 intent 被 gate.deny
- **THEN** 返回 `status="denied"`，不入队

#### Scenario: 队列模式保留幂等性检查
- **WHEN** motor_queue 启用且 `idempotency_key` 已执行
- **THEN** 返回 `status="already_executed"`，不入队

#### Scenario: 队列模式保留 confirmation 流程
- **WHEN** motor_queue 启用且 `decision.decision == "ask_confirmation"`
- **THEN** 走原 confirmation 创建路径，返回 `status="pending_confirmation"`，不入队

### Requirement: TypedDeviceAction 运动元数据
`TypedDeviceAction` SHALL 增加 `duration_ms: int = 0` 与 `requires_parallel: bool = False` 字段，由 `TypedActionPlanner.to_intent()` 透传到 `ActionIntent`。

#### Scenario: 默认值保持向后兼容
- **WHEN** 创建 `TypedDeviceAction(action_type, device_id, domain)` 不传新字段
- **THEN** `duration_ms == 0` 且 `requires_parallel == False`

#### Scenario: 显式指定并行
- **WHEN** 创建 `TypedDeviceAction(..., duration_ms=300, requires_parallel=True)`
- **AND** 调用 `planner.to_intent(action)`
- **THEN** `ActionIntent.duration_ms == 300` 且 `ActionIntent.requires_parallel == True`

#### Scenario: schema 校验不破坏新字段
- **WHEN** `DeviceActionSchemaRegistry.validate(action)` 处理带新字段的 action
- **THEN** `duration_ms` 与 `requires_parallel` 保留，不触发 `DeviceActionSchemaError`

#### Scenario: ActionIntent 默认值
- **WHEN** 创建 `ActionIntent(action, scope, trigger, risk)` 不传新字段
- **THEN** `duration_ms == 0` 且 `requires_parallel == False`

## MODIFIED Requirements

### Requirement: CognitiveLoop 双循环并发
原 `CognitiveLoop.run_forever` 仅运行 15s 审议循环。

修改后：当 `motor_processor` 不为 None 时，`run_forever` 用 `asyncio.gather` 并发运行：
1. 原审议循环（`interval_seconds` 节拍）
2. 运动节拍器循环（`motor_tick_interval_ms` 节拍，调用 `motor_processor.tick()`）

任一循环异常不传染另一个（独立 try/except + logger.exception）。

#### Scenario: motor_processor=None 保持原行为
- **WHEN** `CognitiveLoop(motor_processor=None)`
- **THEN** `run_forever` 仅运行审议循环，行为与修改前一致

#### Scenario: 双循环并发
- **WHEN** `CognitiveLoop(motor_processor=processor)` 且 `is_running()` 持续返回 True
- **THEN** 审议循环按 `interval_seconds` 节拍运行
- **AND** 运动循环按 `motor_tick_interval_ms` 节拍运行
- **AND** 两者互不阻塞

#### Scenario: 运动循环异常隔离
- **WHEN** `motor_processor.tick()` 抛异常
- **THEN** 记录 `logger.exception`，运动循环继续下一 tick，审议循环不受影响

### Requirement: CognitiveLoopConfig 运动节拍配置
原 `CognitiveLoopConfig` 含 `enabled` 与 `interval_seconds`。

修改后增加：
- `motor_tick_interval_ms: int = 50`（EPIC 认知周期默认 50ms）

## Out of Scope（留待后续 spec）
- 队列持久化（崩溃恢复）— 复用 `IntentionStack.persist_to/load_from` 模式
- 动作取消（`cancel(action_id)`）
- 优先级队列（当前 FIFO，未来按 `risk` + `priority` 排序）
- 运动完成事件回调到 BDI 审议回路（让 `_update_utilities` 收到动作完成信号）
- `SafeActionExecutor` 默认切换到队列模式（保持 opt-in，避免破坏 30+ 现有测试）
- 队列深度监控 / 背压
- `CognitiveSchedulerConfig` 与 `CognitiveLoopConfig` 的开关统一（独立 spec 处理）
- 与 `WorldSimulator` 的 `precheck` 异步化集成（当前 precheck 仍同步）
