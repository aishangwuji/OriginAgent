# Tasks

- [x] Task 1: 修复 DesirePriority.NORMAL 阻断性 bug
  - [x] SubTask 1.1: 在 `cron_desire_bridge.py` 第 79 行将 `DesirePriority.NORMAL` 改为 `DesirePriority.MEDIUM`
  - [x] SubTask 1.2: 新增测试 `test_cron_desire_bridge_creates_desire` 验证 `on_cron_job_created` 不抛异常且 Desire priority 为 MEDIUM

- [x] Task 2: 为 IntentionStack 增加持久化
  - [x] SubTask 2.1: 在 `IntentionStack` 增加 `persist_to(path: Path)` 方法，原子写入 `to_json()` 结果到 JSONL
  - [x] SubTask 2.2: 在 `IntentionStack` 增加 `load_from(path: Path)` 类方法，从 JSONL 重建栈；文件不存在或损坏时返回空栈并记 warning
  - [x] SubTask 2.3: 修改 `DeliberationEngine.__init__`，增加 `self._intention_stack_path = self._audit_dir / "intention_stack.jsonl"`
  - [x] SubTask 2.4: 修改 `DeliberationEngine.start()`，在启动时调用 `IntentionStack.load_from(self._intention_stack_path)` 重建栈
  - [x] SubTask 2.5: 修改 `run_cycle()` 中所有 `_intention_stack.push()` / `_intention_stack.pop()` 调用点（4 处），在操作后调用 `persist_to(self._intention_stack_path)`
  - [x] SubTask 2.6: 新增测试覆盖：首次启动无文件、启动重建、push 后持久化、pop 后持久化、损坏文件降级

- [x] Task 3: 重构 DesireStore.update 为显式 increment_eval 参数
  - [x] SubTask 3.1: 在 `DesireStore.update()` 签名增加 `increment_eval: bool = False` 参数
  - [x] SubTask 3.2: 移除 line 166-180 的"魔法默认对象"比较逻辑，改为 `if increment_eval: current = current.with_evaluation(reasoning=reasoning)`，否则只更新 `updated_at` 和 `last_reasoning`
  - [x] SubTask 3.3: 在 `DesireStoreSqlite.update()` 同步增加 `increment_eval` 参数
  - [x] SubTask 3.4: 审计所有 `store.update(...)` 调用点，确定哪些需要 `increment_eval=True`（目前只有缓存命中路径需要，其他保持 False）
  - [x] SubTask 3.5: 新增测试：`increment_eval=True` 递增、默认不递增、`increment_eval=True` + utility 同时更新

- [x] Task 4: 修复 PlanLibrary 缓存命中路径
  - [x] SubTask 4.1: 修改 `deliberation.py` 缓存命中块，将 `status=ACTIVE` 改为 `increment_eval=True`
  - [x] SubTask 4.2: 将缓存命中的 desire_id 加入 `updated_ids` 列表（使其参与 `_update_utilities`）
  - [x] SubTask 4.3: 验证缓存命中 Desire 的 evaluation_count 递增、进入 updated_ids、_update_utilities 被调用（通过回归测试确认）

- [x] Task 5: 移除 _sync_foresights 冗余调用
  - [x] SubTask 5.1: 删除 `deliberation.py` 第二次 `_sync_foresights(desires)` 调用及随后的 `list_deliberable()`
  - [x] SubTask 5.2: 保留第一次调用，确认其后 `desires = self._store.list_deliberable()` 已存在
  - [x] SubTask 5.3: 通过现有 `test_foresight_sync.py` 回归测试确认冷启动 foresight 创建正常

- [x] Task 6: 修复 Softmax 指数溢出
  - [x] SubTask 6.1: 在 `_stochastic_prioritize` 的 `math.exp((u - max_u) / temp)` 处增加 clip：`max(-50.0, min((u - max_u) / temp, 50.0))`
  - [x] SubTask 6.2: 通过回归测试确认极端温度不崩溃（现有 test_stochastic_prioritize 覆盖）
  - [x] SubTask 6.3: 验证默认温度 0.1 下行为不变（现有 `test_stochastic_prioritize` 通过）

- [x] Task 7: 全量回归测试
  - [x] SubTask 7.1: 运行 `.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/ -v`（125 passed）
  - [x] SubTask 7.2: 运行 `.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/test_actr_utility.py -v`（44 passed，无回归）
  - [x] SubTask 7.3: 运行新增测试（12 passed：cron 2 + increment_eval 5 + intention_stack 5）

# Task Dependencies
- Task 2 独立（仅改 IntentionStack + Engine 启动）
- Task 3 独立（仅改 DesireStore.update）
- Task 4 depends on Task 3（需要 `increment_eval` 参数）
- Task 1, 5, 6 相互独立
- Task 7 depends on 所有前序任务
- 可并行：Task 1、Task 2、Task 3、Task 5、Task 6（Task 4 需等 Task 3）
