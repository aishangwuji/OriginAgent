# Tasks

> 遵循规则34（验证先行）：先确认 8 个测试当前失败，修复后确认全部通过。
> 遵循规则37（原子提交）：每个 Task 对应独立 commit，commit message 说明"为什么这么改"。
> 遵循规则33（手术式变更）：仅修改与本次根因直接相关的代码，不顺手重构。
> 遵循规则32-B：TD-015/SubTask C 为 L3，SubTask A/B 为 L2（可能触及生产代码）。

## Task 0: 验证当前 8 个测试全部失败（基线确认）

- [x] Task 0: 实测确认 8 个测试当前失败，作为修复前基线 ✅ 2026-07-18
  - [x] SubTask 0.1: 运行 `.\.venv\Scripts\python.exe -m pytest tests/agent/test_consolidator.py::TestConsolidatorTokenBudget tests/agent/test_active_intents.py::test_agent_loop_cognitive_pass_emits_due_reminder_and_writes_working_memory tests/agent/tools/test_subagent_tools.py::test_agent_loop_syncs_updated_max_iterations_before_run tests/agent/tools/test_subagent_tools.py::test_drain_pending_blocks_while_subagents_running tests/agent/tools/test_subagent_tools.py::test_drain_pending_timeout --basetemp=.pytest_basetmp -v` → 确认 8 个 FAILED
  - [x] SubTask 0.2: 记录每个测试的失败堆栈首行，作为修复后对比基线

## Task 1: TD-015 — test_consolidator 4 个废弃测试（L3）

- [x] Task 1: 修复 `TestConsolidatorTokenBudget` 的 4 个测试 ✅ 2026-07-18
  - [x] SubTask 1.1（根因确认）: Read `tests/agent/test_consolidator.py:232-340` 与 `OriginAgent/agent/memory.py` 中 `maybe_consolidate_by_tokens` 当前实现，确认：
    - 4 个测试的原始意图（验证 token 超预算压缩 / raw_archive 回退 / round loop 中断 / 长 tool chain 边界）
    - 新 API 下 `archive` 何时被调用（仅当 `replay_max_messages` 非空时通过 `_consolidate_replay_overflow` 触发）
    - 新架构下这些场景是否已被温区总结（warm summary）覆盖
  - [x] SubTask 1.2（方案决策——规则26）: 选定**方案 A**——改写 4 个测试，传入 `replay_max_messages` 参数触发 `archive` 调用，保留原始断言意图
    - 理由：4 个测试的原始意图（archive 被调用、cursor 推进、round loop 中断、边界遵守）在新架构下仍有价值，不应废弃
  - [x] SubTask 1.3（实现）: 移除 `consolidator._SAFETY_BUFFER = 0` 和 `estimate_session_prompt_tokens` mock；将 `maybe_consolidate_by_tokens(session)` 改为 `maybe_consolidate_by_tokens(session, replay_max_messages=20)`；更新 docstring 说明新 API 行为
  - [x] SubTask 1.4（验证）: `pytest tests/agent/test_consolidator.py -v` → 26 个 PASSED
  - [x] SubTask 1.5（回归验证）: `pytest tests/agent/test_consolidator.py tests/agent/test_memory.py -v` → 无新失败

## Task 2: TD-017 SubTask C — last_sent_messages AttributeError（L3，最简单先修）

- [x] Task 2: 修复 `test_drain_pending_blocks_while_subagents_running` 与 `test_drain_pending_timeout` 的 `SimpleNamespace` mock 缺陷 ✅ 2026-07-18
  - [x] SubTask 2.1（根因确认）: `agent_runtime.py:1140` 附近访问 `result.last_sent_messages`，但测试 `SimpleNamespace` 未提供该属性。**额外发现**：L797 断言 `results[0]["role"] == "user"` 过时——`_drain_pending`（L1092）将 `sender_id="subagent"` 标记为内部事件（SYSTEM role），符合规则18（内部事件不得伪装为 user role）
  - [x] SubTask 2.2（实现）: 两个 `SimpleNamespace` 添加 `last_sent_messages=None`（falsy 默认值，匹配 `if ... and result.last_sent_messages:` 判断）；L797 role 断言从 `"user"` 改为 `"system"`
  - [x] SubTask 2.3（验证）: `pytest tests/agent/tools/test_subagent_tools.py::test_drain_pending_blocks_while_subagents_running tests/agent/tools/test_subagent_tools.py::test_drain_pending_timeout -v` → 2 个 PASSED

## Task 3: TD-017 SubTask A — reminder_store 实例不共享（L2，可能触及生产代码）

- [x] Task 3: 修复 `test_agent_loop_cognitive_pass_emits_due_reminder_and_writes_working_memory` ✅ 2026-07-18
  - [x] SubTask 3.1（根因确认）: **修正原描述**——实例是共享的，根因有两个独立问题：
    - **Issue 1（测试 setup）**：`session.updated_at` 为当前时间，触发 `_check_user_active` 5 分钟门禁（`agent_cognitive_runtime.py:166`），认知 pass 被完全跳过，`decisions` 为空
    - **Issue 2（生产代码缺陷）**：`ReminderStore.mark_fired`/`_transition` 仅写 JSONL，但 `upsert`/`get`/`list_due` 用 sqlite——`mark_fired` 后 `get` 从 sqlite 读到旧状态
    - **额外发现**：`_record_cognitive_scan` 写入 `state_holder` 但 `cognition_summary()` 读取 `loop._last_cognitive_scan`（实例属性），写读断连
  - [x] SubTask 3.2（方案决策——规则26）: Issue 1 修测试 setup（老化 `session.updated_at`）；Issue 2 修生产代码（sqlite-first + JSONL 回退）；额外修复 `_record_cognitive_scan` 镜像到实例属性
  - [x] SubTask 3.3（影响分析——规则0红线闭集）: `ReminderStore.mark_fired` 调用方排查：cognitive_runtime 与 loop 共享同一实例，修复后所有路径访问同一 sqlite/JSONL 双写路径。无 HIGH/CRITICAL 风险
  - [x] SubTask 3.4（实现）:
    - `reminders_sqlite.py`：新增 `transition_status` 方法（L236-305）
    - `reminders.py`：`mark_fired`（L219-256）和 `_transition`（L283-319）改为 sqlite-first + JSONL 回退
    - `loop.py`：`_record_cognitive_scan` 增加 `self._last_cognitive_scan = dict(payload)` 镜像
    - `test_active_intents.py`：老化 `session.updated_at` 到 6 分钟前；`attention_items` 断言从精确匹配改为 containment 检查（适配前缀注入）
  - [x] SubTask 3.5（验证）: `pytest tests/agent/test_active_intents.py::test_agent_loop_cognitive_pass_emits_due_reminder_and_writes_working_memory -v` → PASSED
  - [x] SubTask 3.6（回归验证）: `pytest tests/agent/test_active_intents.py -v` → 24/24 PASSED

## Task 4: TD-017 SubTask B — max_iterations 未同步（L2，可能触及生产代码）

- [x] Task 4: 修复 `test_agent_loop_syncs_updated_max_iterations_before_run` ✅ 2026-07-18
  - [x] SubTask 4.1（根因确认）: **修正原描述**——非 "subagent spec 构建路径" 问题。`RuntimeDependencies` 是 frozen dataclass，`deps.max_iterations` 在 `__init__` 时冻结。`loop.max_iterations = 55` 只更新 loop 实例属性，不传播到 `deps.max_iterations`。`AgentRuntime._run_agent_loop` 读取 `d.max_iterations`（冻结值 42）
  - [x] SubTask 4.2（方案决策——规则26）: 在 `_sync_subagent_runtime_limits` 中用 `object.__setattr__` 更新 frozen deps；在 `_run_agent_loop` 入口调用 `self._sync_subagent_runtime_limits()` 确保委托前同步
  - [x] SubTask 4.3（影响分析——规则0红线闭集）: `_sync_subagent_runtime_limits` 调用方排查：`AgentLoop` 构造后、`_run_agent_loop` 入口、配置热更新路径。`object.__setattr__` 是 Python 官方机制用于 frozen dataclass，无风险
  - [x] SubTask 4.4（实现）:
    - `loop.py` `_sync_subagent_runtime_limits`（L1043-1051）：`object.__setattr__(self._runtime.deps, "max_iterations", self.max_iterations)`
    - `loop.py` `_run_agent_loop` 入口（L1568-1577）：调用 `self._sync_subagent_runtime_limits()` 确保委托前同步
  - [x] SubTask 4.5（验证）: `pytest tests/agent/tools/test_subagent_tools.py::test_agent_loop_syncs_updated_max_iterations_before_run -v` → PASSED
  - [x] SubTask 4.6（回归验证）: `pytest tests/agent/tools/test_subagent_tools.py -v` → 无新失败

## Task 5: 全量回归 + 技术债状态更新

- [x] Task 5: 全量回归测试 + 更新 TD-015/TD-017 状态为"已修复" ✅ 2026-07-18
  - [x] SubTask 5.1（全量回归）: `pytest tests/agent/test_consolidator.py tests/agent/test_active_intents.py tests/agent/tools/test_subagent_tools.py -q` → **72 passed, 0 failed**（161.84s）
  - [x] SubTask 5.2（更新 TD-015 状态）: 旧 `待评估` 文件已删除，新 `TD-2026-015-...-已修复.md` 已创建（评审记录追加 2026-07-18 修复结论）
  - [x] SubTask 5.3（更新 TD-017 状态）: 旧 `待评估` 文件已删除，新 `TD-2026-017-...-已修复.md` 已创建（根因描述已修正，评审记录追加 2026-07-18 修复结论）
  - [x] SubTask 5.4（范围外发现）: 登记新 TD-019（`test_consolidation_ratio.py` 3 个预存失败，同 TD-015 根因——旧 loop API 废弃），状态 `待评估`

# Task Dependencies

## 执行顺序
- Task 0（基线确认）必须最先执行
- Task 1（TD-015）与 Task 2（SubTask C）相互独立，可并行
- Task 3（SubTask A）与 Task 4（SubTask B）相互独立，可并行，但都需在 Task 2 后执行（避免 SimpleNamespace 改动冲突）
- Task 5（全量回归）必须在 Task 1-4 全部完成后执行

## 建议执行顺序
1. **第一批（并行）**：Task 0（基线）→ Task 1（TD-015）+ Task 2（SubTask C）
2. **第二批（并行）**：Task 3（SubTask A）+ Task 4（SubTask B）
3. **第三批**：Task 5（全量回归 + TD 状态更新）

## 提交策略（规则37）
- Task 1（TD-015）：1 个 commit，commit message 说明"为什么改写/删除这 4 个测试"
- Task 2（SubTask C）：1 个 commit，commit message 说明"补 last_sent_messages mock 字段"
- Task 3（SubTask A）：1 个 commit，commit message 说明根因（生产代码缺陷 or 测试 mock 问题）+ 修复点
- Task 4（SubTask B）：1 个 commit，commit message 说明根因 + 修复点
- Task 5：1 个 commit（TD 文件状态更新），commit message 引用前 4 个 commit hash
- 共 5 个原子 commit，每个可独立 `git revert`
