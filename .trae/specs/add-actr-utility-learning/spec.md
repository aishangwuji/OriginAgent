# ACT-R 效用学习 Spec

## Why
当前 `DeliberationEngine._prioritize()` 使用静态优先级排序（LOW/MEDIUM/HIGH/CRITICAL + overdue bonus），无法根据历史执行结果动态调整偏好。引入 ACT-R 效用学习机制，使 Agent 能够基于 Desire 状态转换与 `MetaCognitionReflector` 的反思反馈动态调整 utility 值，并通过随机软最大化（Stochastic Softmax）实现"探索/利用"平衡的意图选择。

## What Changes
- 扩展 `Desire` 模型，增加 `utility: float = 0.5` 字段（范围 [0.0, 1.0]）
- 扩展 `DesireStore.update()` 与 `DesireStoreSqlite.update()` 支持 `utility` 参数更新
- 新增 `UtilityRewardBridge` 类，从 `ReflectionRecord` 与 Desire 状态转换提取奖励信号
- 修改 `DeliberationEngine._prioritize()` 为随机软最大化选择（特性开关控制）
- 在 `DeliberationEngine.run_cycle()` 末尾增加效用更新逻辑（ACT-R 公式）
- 新增特性开关：`use_actr_utility`、`utility_learning_rate`、`selection_temperature`

## Impact
- Affected code:
  - `OriginAgent/bdi/models.py` — Desire dataclass（新增字段 + `from_json` 兼容）
  - `OriginAgent/bdi/desire_store.py` — `DesireStore.update()` / `DesireStoreSqlite.update()`
  - `OriginAgent/bdi/deliberation.py` — `DeliberationEngine.__init__` / `_prioritize()` / `run_cycle()`
  - `OriginAgent/bdi/utility_reward_bridge.py` — 新增（奖励信号桥接器）
  - 测试文件（新增 + 现有回归）

## ADDED Requirements

### Requirement: Desire 效用字段
Desire 模型 SHALL 包含 `utility: float` 字段，默认值 0.5，范围 [0.0, 1.0]。该字段记录该 Desire 历史执行的成功/失败累积评估，用于 ACT-R 效用学习。

#### Scenario: 新建 Desire 默认效用
- **WHEN** 创建新 Desire 且未指定 utility
- **THEN** utility 为 0.5（中性初始值）

#### Scenario: 反序列化旧数据兼容
- **WHEN** 从 JSONL/SQLite 加载不包含 utility 字段的旧 Desire 数据
- **THEN** utility 默认填充为 0.5，不抛出异常

#### Scenario: 效用值域约束
- **WHEN** 通过 `with_utility()` 设置 utility
- **THEN** 值被 clamp 到 [0.0, 1.0] 区间

### Requirement: DesireStore 支持 utility 更新
`DesireStore.update()` 和 `DesireStoreSqlite.update()` SHALL 支持可选 `utility` 参数，当提供时更新 Desire 的 utility 字段并持久化。

#### Scenario: 更新 utility
- **WHEN** 调用 `store.update(desire_id, utility=0.8)`
- **THEN** 返回的 Desire 的 utility 为 0.8
- **AND** 持久化层（JSONL + SQLite）中该 Desire 的 utility 被更新

#### Scenario: 不更新 utility（向后兼容）
- **WHEN** 调用 `store.update(desire_id, status=...)` 且未传 utility
- **THEN** utility 保持原值不变

### Requirement: 随机软最大化选择
当 `use_actr_utility=True` 时，`DeliberationEngine._prioritize()` SHALL 使用 softmax 概率分布基于 utility 进行无放回随机采样，而非确定性排序。

#### Scenario: 特性开关关闭（默认）
- **WHEN** `use_actr_utility=False`
- **THEN** 使用原有的确定性优先级排序（overdue bonus + priority value）

#### Scenario: 特性开关开启 — 多 desire 选择
- **WHEN** `use_actr_utility=True`
- **AND** 有多个 deliberable desires
- **THEN** 使用 softmax(utility / temperature) 概率分布无放回采样至 max_desires_per_cycle
- **AND** overdue desires 优先保留在列表顶部（确定性插入）

#### Scenario: 特性开关开启 — 单 desire
- **WHEN** `use_actr_utility=True` 且仅有一个 deliberable desire
- **THEN** 直接返回该 desire，不执行随机采样

#### Scenario: 温度参数影响随机性
- **WHEN** `selection_temperature` 趋近 0
- **THEN** 退化为贪心选择（最高 utility 优先）
- **WHEN** `selection_temperature` 趋近无穷大
- **THEN** 退化为均匀随机选择

### Requirement: 效用更新（ACT-R 公式）
`run_cycle()` 末尾 SHALL 基于本周期 Desire 状态转换与近期反思记录更新 utility，使用 ACT-R 效用更新公式：`U_new = U_old + α * (R - U_old)`，其中 α 为 `utility_learning_rate`，R 为奖励信号。

#### Scenario: Desire 被满足
- **WHEN** Desire 在本周期内状态转为 SATISFIED
- **THEN** 获得正向奖励 R = +1.0，utility 增加

#### Scenario: Desire 被取消
- **WHEN** Desire 在本周期内状态转为 CANCELLED
- **THEN** 获得负向惩罚 R = -0.5，utility 减少

#### Scenario: Desire 仍活跃（停滞惩罚）
- **WHEN** Desire evaluation_count > 3 且仍为 ACTIVE
- **THEN** 获得微负奖励 R = -0.1，utility 缓慢减少

#### Scenario: 特性开关关闭
- **WHEN** `use_actr_utility=False`
- **THEN** 跳过效用更新逻辑，utility 保持不变

### Requirement: 反思奖励信号提取
`UtilityRewardBridge` SHALL 从 `MetaCognitionReflector` 的 `ReflectionRecord` 提取奖励信号，结合 Desire 状态转换，映射回 Desire 的 utility 更新。

#### Scenario: 成功反思
- **WHEN** ReflectionRecord 的 outcome_class 为 "success"
- **THEN** 提取正向奖励 = confidence * (+1.0)

#### Scenario: 失败反思
- **WHEN** ReflectionRecord 的 outcome_class 为 "failure"
- **THEN** 提取负向奖励 = confidence * (-1.0)

#### Scenario: 中性反思
- **WHEN** ReflectionRecord 的 outcome_class 为其他值或为空
- **THEN** 不产生额外奖励信号（R = 0）

#### Scenario: 反思与 Desire 关联
- **WHEN** ReflectionRecord 的 payload.trigger_contexts 或 evidence_refs 包含 desire_id 引用
- **THEN** 奖励信号映射到对应 Desire
- **WHEN** 无法映射到具体 Desire
- **THEN** 奖励信号被丢弃（不影响任何 Desire utility）

#### Scenario: 反思时间窗口
- **WHEN** 查询奖励信号
- **THEN** 仅考虑 `last_utility_update_at` 之后创建的 ReflectionRecord
- **AND** 更新 `last_utility_update_at` 为当前时间

### Requirement: 特性开关与配置参数
DeliberationEngine SHALL 支持以下配置参数，默认关闭 ACT-R 行为以保证向后兼容。

#### Scenario: 默认配置
- **WHEN** 未指定 ACT-R 参数
- **THEN** `use_actr_utility=False`，`utility_learning_rate=0.2`，`selection_temperature=0.1`
- **AND** 行为与当前完全一致

#### Scenario: 启用 ACT-R
- **WHEN** `use_actr_utility=True`
- **THEN** 启用随机软最大化选择 + 效用更新逻辑

## MODIFIED Requirements

### Requirement: DeliberationEngine._prioritize()
原方法使用确定性排序：`(overdue_bonus - priority.value, 0)`。

修改后：当 `use_actr_utility=True` 时，改为基于 utility 的 softmax 无放回采样，overdue desires 优先保留。当 `use_actr_utility=False` 时，保持原有确定性排序逻辑不变。

### Requirement: DeliberationEngine.run_cycle()
原方法在步骤 8（Merge cached + LLM intentions, emit）后结束。

修改后：在步骤 8 之后增加步骤 9（效用更新），当 `use_actr_utility=True` 时：
1. 收集本周期状态转换的 Desire IDs 及其新状态
2. 通过 `UtilityRewardBridge` 查询近期反思奖励信号
3. 使用 ACT-R 公式更新相关 Desire 的 utility
4. 持久化更新到 DesireStore
5. 更新 `last_utility_update_at` 时间戳
