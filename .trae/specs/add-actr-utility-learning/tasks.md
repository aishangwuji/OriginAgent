# Tasks

- [x] Task 1: 扩展 Desire 模型增加 utility 字段
  - [x] SubTask 1.1: 在 `Desire` dataclass 增加 `utility: float = 0.5` 字段（位于 `evaluation_count` 之后）
  - [x] SubTask 1.2: 更新 `to_json()` 确保 utility 被序列化（`asdict` 自动包含，验证即可）
  - [x] SubTask 1.3: 更新 `from_json()` 增加 `data.setdefault("utility", 0.5)` 兼容旧数据
  - [x] SubTask 1.4: 添加 `with_utility(utility: float) -> Desire` 方法，clamp 到 [0.0, 1.0]

- [x] Task 2: 扩展 DesireStore 支持 utility 更新
  - [x] SubTask 2.1: `DesireStore.update()` 增加 `utility: float | None = None` 参数
  - [x] SubTask 2.2: 在 `DesireStore.update()` 中当 utility 不为 None 时调用 `current.with_utility(utility)`
  - [x] SubTask 2.3: `DesireStoreSqlite.update()` 增加 `utility: float | None = None` 参数并更新 payload_json
  - [x] SubTask 2.4: 验证 `utility=None` 时现有调用方行为不变

- [x] Task 3: 创建 UtilityRewardBridge 类
  - [x] SubTask 3.1: 新建 `OriginAgent/bdi/utility_reward_bridge.py`
  - [x] SubTask 3.2: 实现 `__init__(audit_ledger, desire_store)` 注入依赖
  - [x] SubTask 3.3: 实现 `extract_status_rewards(desire_ids: list[str]) -> dict[str, float]`：从 Desire 状态转换提取奖励（SATISFIED=+1.0, CANCELLED=-0.5, ACTIVE_STAGNANT=-0.1）
  - [x] SubTask 3.4: 实现 `extract_reflection_rewards(since: str, session_keys: list[str]) -> dict[str, float]`：从 ReflectionRecord 的 outcome_class + confidence 提取奖励
  - [x] SubTask 3.5: 实现 `merge_rewards(status_rewards, reflection_rewards) -> dict[str, float]`：加权合并（α=0.7 状态 + β=0.3 反思）

- [x] Task 4: 修改 DeliberationEngine 支持随机软最大化
  - [x] SubTask 4.1: `__init__` 增加 `use_actr_utility: bool = False`、`utility_learning_rate: float = 0.2`、`selection_temperature: float = 0.1` 参数
  - [x] SubTask 4.2: 增加 `reward_bridge: UtilityRewardBridge | None = None` 参数注入
  - [x] SubTask 4.3: 增加 `_last_utility_update_at: str` 实例变量跟踪上次更新时间
  - [x] SubTask 4.4: 实现 `_stochastic_prioritize(desires) -> list[Desire]`：overdue 优先 + softmax 无放回采样
  - [x] SubTask 4.5: 修改 `_prioritize()` 根据 `use_actr_utility` 路由到 `_stochastic_prioritize` 或原有逻辑

- [ ] Task 5: 在 run_cycle() 末尾增加效用更新
  - [ ] SubTask 5.1: 在 `run_cycle()` 步骤 8 之后增加步骤 9 调用 `_update_utilities()`
  - [ ] SubTask 5.2: 实现 `_update_utilities(cycle_desires, updated_ids)`：
    - 当 `use_actr_utility=False` 或 `reward_bridge=None` 时直接返回
    - 收集本周期状态转换奖励（status_rewards）
    - 查询近期反思奖励（reflection_rewards，since=`_last_utility_update_at`）
    - 合并奖励信号
    - 使用 ACT-R 公式 `U_new = U_old + α*(R-U_old)` 更新 utility
    - 调用 `self._store.update(did, utility=new_utility)` 持久化
    - 更新 `_last_utility_update_at = now_iso()`
  - [ ] SubTask 5.3: 在审计日志 `BDICycleRecord` 的 token_usage 或新字段中记录 utility 更新数量（可选，用于可观测性）

- [ ] Task 6: 编写测试
  - [ ] SubTask 6.1: `test_desire_utility_field` — 验证默认值 0.5、`with_utility()` clamp、`from_json` 旧数据兼容
  - [ ] SubTask 6.2: `test_desire_store_update_utility` — 验证 `update(utility=...)` 持久化、`utility=None` 不变更
  - [ ] SubTask 6.3: `test_utility_reward_bridge` — 验证状态奖励提取、反思奖励提取、合并逻辑
  - [ ] SubTask 6.4: `test_stochastic_prioritize` — 验证 softmax 选择概率分布、overdue 优先、单 desire 直接返回、temperature 极端值
  - [ ] SubTask 6.5: `test_run_cycle_utility_update` — 集成测试：SATISFIED 增 utility、CANCELLED 减 utility、`use_actr_utility=False` 不更新
  - [ ] SubTask 6.6: `test_backward_compatibility` — `use_actr_utility=False` 时 `run_cycle()` 行为与改动前一致
  - [ ] SubTask 6.7: 运行全量回归 `.\.venv\Scripts\python.exe -m pytest OriginAgent/bdi/ OriginAgent/agent/test_meta_cognition_reflector.py -v`

# Task Dependencies
- Task 2 depends on Task 1（需要 `with_utility()` 方法）
- Task 3 depends on Task 1（需要 Desire 模型）
- Task 4 depends on Task 1（需要 utility 字段）
- Task 5 depends on Task 2, Task 3, Task 4（需要 store 更新 + bridge + 选择逻辑）
- Task 6 depends on Task 5（需要完整实现后集成测试）
- Task 1 可独立先行
