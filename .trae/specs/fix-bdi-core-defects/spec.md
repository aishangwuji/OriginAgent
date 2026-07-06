# BDI 核心缺陷修复 Spec

## Why
上一轮验证发现 BDI 模块存在 7 个缺陷，其中 1 个阻断性（`DesirePriority.NORMAL` 未定义导致 cron 创建 Desire 崩溃）、2 个严重影响 ACT-R 效用学习回路（IntentionStack 不持久化、PlanLibrary 缓存命中静默失败）、4 个设计与稳定性问题。本 spec 聚焦修复这些问题，使 ACT-R Phase 1 真正可用。

## What Changes
- 修复 `cron_desire_bridge.py` 中 `DesirePriority.NORMAL` → `DesirePriority.MEDIUM`（阻断性）
- 为 `IntentionStack` 增加持久化：`intention_stack.jsonl`，Engine 启动时加载、push/pop 时持久化
- 修复 PlanLibrary 缓存命中路径：避免 `ACTIVE→ACTIVE` 非法转换，并显式递增 `evaluation_count`
- 重构 `DesireStore.update` 的"魔法默认对象"比较，改为显式 `increment_eval: bool` 参数
- 移除 `_sync_foresights` 的第二次冗余调用
- 修复 `_stochastic_prioritize` 的 Softmax 指数溢出（clip 指数参数到安全范围）
- **不在本 spec 范围**：`_gather_beliefs` 的同步 I/O 阻塞问题（需架构级 DI 改造，留待后续 spec）

## Impact
- Affected specs: `add-actr-utility-learning`（ACT-R Phase 1 依赖本修复才能对高频任务生效）
- Affected code:
  - `OriginAgent/bdi/cron_desire_bridge.py` — 一行修复
  - `OriginAgent/bdi/models.py` — `IntentionStack` 增加持久化方法（已有 `to_json`/`from_json`，无需改数据模型）
  - `OriginAgent/bdi/deliberation.py` — Engine 启动加载栈、push/pop 持久化、移除冗余 `_sync_foresights`、修复缓存命中路径、修复 Softmax 溢出
  - `OriginAgent/bdi/desire_store.py` — `update()` 重构为显式 `increment_eval` 参数
  - 测试文件（新增覆盖 + 现有回归）

## ADDED Requirements

### Requirement: IntentionStack 持久化
`IntentionStack` SHALL 在每次 `push`/`pop` 后将栈帧持久化到 `workspace/memory/bdi/intention_stack.jsonl`，并在 `DeliberationEngine.start()` 时从该文件重建栈。服务重启后 SUSPENDED 状态的 Desire 可被 `_check_resumptions` 恢复。

#### Scenario: 首次启动无栈文件
- **WHEN** Engine 启动且 `intention_stack.jsonl` 不存在
- **THEN** 初始化空栈，不抛异常

#### Scenario: 启动时重建栈
- **WHEN** Engine 启动且 `intention_stack.jsonl` 存在且包含 2 个帧
- **THEN** 栈深度为 2，`list_resumable()` 返回对应 2 个 ResumeCandidate

#### Scenario: push 后持久化
- **WHEN** 调用 `stack.push(frame)` 并传入持久化路径
- **THEN** `intention_stack.jsonl` 被原子写入，包含该帧

#### Scenario: pop 后持久化
- **WHEN** 调用 `stack.pop()` 并传入持久化路径
- **THEN** `intention_stack.jsonl` 被更新，移除栈顶帧

#### Scenario: 损坏文件降级
- **WHEN** `intention_stack.jsonl` 内容损坏（JSON 解析失败）
- **THEN** 记录 warning 日志，初始化空栈，不抛异常

### Requirement: 显式 evaluation_count 递增参数
`DesireStore.update()` SHALL 接受 `increment_eval: bool = False` 参数，当为 `True` 时显式调用 `desire.with_evaluation()`，替代当前"魔法默认对象"比较逻辑。

#### Scenario: 显式递增
- **WHEN** 调用 `store.update(desire_id, status=ACTIVE, increment_eval=True)`
- **THEN** 返回的 Desire 的 `evaluation_count` 比原值 +1

#### Scenario: 不递增（默认）
- **WHEN** 调用 `store.update(desire_id, status=ACTIVE)` 且未传 `increment_eval`
- **THEN** `evaluation_count` 保持原值

#### Scenario: 仅更新 utility 不递增
- **WHEN** 调用 `store.update(desire_id, utility=0.8)`
- **THEN** `evaluation_count` 保持原值，`utility` 为 0.8

### Requirement: Softmax 数值稳定性
`_stochastic_prioritize` SHALL 对 softmax 指数参数进行 clip，防止极端温度下 `math.exp` 溢出崩溃。

#### Scenario: 极端低温不崩溃
- **WHEN** `selection_temperature=0.001` 且两个 Desire 的 utility 差为 1.0
- **THEN** 不抛 `OverflowError`，返回有效选择结果

#### Scenario: 正常温度行为不变
- **WHEN** `selection_temperature=0.1`（默认）
- **THEN** softmax 行为与修复前一致

## MODIFIED Requirements

### Requirement: PlanLibrary 缓存命中路径
原 [deliberation.py:478-483](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/bdi/deliberation.py) 对缓存命中的 Desire 调用 `store.update(status=DesireStatus.ACTIVE)`，但 `ACTIVE→ACTIVE` 是非法转换，`ValueError` 被 `except ValueError: pass` 静默吞掉，导致整个 update（含 evaluation_count 递增）被跳过。

修改后：缓存命中时不再调用 `transition_to(ACTIVE)`，改为直接调用 `store.update(desire_id, increment_eval=True, reasoning="Plan cache execution")`。这样：
1. 不触发非法状态转换
2. 显式递增 evaluation_count，使停滞检测（`evaluation_count > 3`）能生效
3. 缓存命中的 Desire 进入 `updated_ids`，参与 `_update_utilities` 的奖励更新

### Requirement: _sync_foresights 调用次数
原 `run_cycle()` 在 line 284-285 调用 `_sync_foresights([])`，又在 line 303-304 调用 `_sync_foresights(desires)`。第一次是必要的（冷启动创建首个 Desire），第二次是冗余 I/O（去重后无操作）。

修改后：移除第二次调用（line 303-304），保留第一次。第一次后直接 `list_deliberable()` 即可。

### Requirement: cron_desire_bridge 优先级
原 `DesirePriority.NORMAL` 未定义，抛 `AttributeError`。修改为 `DesirePriority.MEDIUM`。

### Requirement: DesireStore.update 内部实现
原"魔法默认对象"比较（构造新 Desire 实例比较 evaluation_count）被移除，改为依赖显式 `increment_eval` 参数。

## REMOVED Requirements

### Requirement: 魔法默认对象 evaluation_count 比较
**Reason**: 脆弱、可读性差、依赖默认值不变，且实际效果是 evaluation_count 最多从 0→1 永远到不了 >3，使停滞检测失效。
**Migration**: 调用方需显式传入 `increment_eval=True` 来递增评估计数。现有调用方默认不递增（行为与原"已递增"分支一致）。

## Out of Scope（留待后续 spec）
- `_gather_beliefs` 与 `_sync_foresights` 的同步 SQLite I/O 阻塞问题：需引入依赖注入或 aiosqlite 迁移，属架构级改造，本 spec 不处理。
