---
schema_version: 1
---

# TD-2026-017: 4 个预存测试失败（cognitive pass / subagent drain / max_iterations 同步）

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-18T20:00:00+08:00 |
| 发现人 | assistant（A+C 重构 Phase 4 验证过程中） |
| 关联Spec | `.trae/specs/fix-preexisting-test-failures-td015-td017/` |
| 关联规则 | 规则15（测试策略需匹配变更风险）、规则34（验证先行）、规则33（手术式变更） |
| 优先级 | P3 |
| 状态 | 已修复 |

## 详细描述
A+C 重构 Phase 4 验证过程中发现 4 个测试失败。通过 `git stash` 验证
（无 Phase 4 改动）仍全部失败，确认为**预存失败**，与 Phase 4 无关。

### 修正后的根因分析（原描述部分不准）

1. `tests/agent/test_active_intents.py::test_agent_loop_cognitive_pass_emits_due_reminder_and_writes_working_memory`
   - **原描述**：`assert 'pending' == 'fired'`，疑点为 reminder_store 实例不共享。
   - **实际根因**（两个独立问题）：
     - **Issue 1（测试 setup）**：`session.updated_at` 为当前时间，触发
       `_check_user_active` 5 分钟门禁
       (`agent_cognitive_runtime.py:166`)，认知 pass 被完全跳过，
       `decisions` 为空，L518 `assert any(item.outcome == "emitted")` 失败。
     - **Issue 2（生产代码缺陷）**：`ReminderStore.mark_fired`/`_transition`
       仅写 JSONL，但 `upsert`/`get`/`list_due` 用 sqlite。`mark_fired` 后
       `get` 从 sqlite 读到旧状态。**非实例不共享问题**——实例是共享的，
       但 sqlite/JSONL 写读路径不一致。
   - **额外发现**：`_record_cognitive_scan` 写入 `state_holder` 但
     `cognition_summary()` 读取 `loop._last_cognitive_scan`（实例属性），
     存在写读断连。

2. `tests/agent/tools/test_subagent_tools.py::test_agent_loop_syncs_updated_max_iterations_before_run`
   - **原描述**：`assert 42 == 55`，疑点为 subagent spec 构建路径未读取最新配置。
   - **实际根因**：`RuntimeDependencies` 是 frozen dataclass，
     `deps.max_iterations` 在 `__init__` 时冻结。`loop.max_iterations = 55`
     只更新 loop 实例属性，不传播到 `deps.max_iterations`。
     `AgentRuntime._run_agent_loop` 读取 `d.max_iterations`（冻结值 42）。
   - **非 "subagent spec 构建路径" 问题**——是 deps 冻结导致的数据流断裂。

3. `tests/agent/tools/test_subagent_tools.py::test_drain_pending_blocks_while_subagents_running`
   - **原描述**：`AttributeError: 'SimpleNamespace' has no attribute 'last_sent_messages'`
   - **实际根因**：原描述正确。runner result 协议升级后增加了 `last_sent_messages`
     字段，测试 mock 未同步。修复后暴露第二个问题：L797 断言
     `results[0]["role"] == "user"` 过时——`_drain_pending` 在 L1092 明确将
     `sender_id="subagent"` 标记为内部事件（SYSTEM role），符合项目约束
     （内部事件必须用 system role，避免 LLM 误解为用户指令）。

4. `tests/agent/tools/test_subagent_tools.py::test_drain_pending_timeout`
   - **原描述**：同 #3
   - **实际根因**：同 #3（`last_sent_messages` AttributeError）。修复后通过。

## 影响范围
- **影响文件**：
  - `tests/agent/test_active_intents.py`（测试 setup + 断言更新）
  - `tests/agent/tools/test_subagent_tools.py`（mock 字段 + role 断言）
  - `OriginAgent/agent/reminders.py`（mark_fired/_transition 增加 sqlite 支持）
  - `OriginAgent/agent/reminders_sqlite.py`（新增 transition_status 方法）
  - `OriginAgent/agent/loop.py`（_sync_subagent_runtime_limits 同步 deps +
    _record_cognitive_scan 镜像到实例属性 + _run_agent_loop 入口同步）
- **影响功能**：测试套件持续 4 个失败，干扰 CI 信号
- **潜在风险**：CI 噪音掩盖真实回归

## 复现/验证路径
修复前：4 个测试全部失败。
修复后：4 个测试全部通过。test_active_intents.py 24 个全绿，
test_subagent_tools.py 相关测试全绿。

## 修复方案（已实施）
1. **#1 Issue 1**：测试 setup 中老化 `session.updated_at` 到 6 分钟前，
   绕过 `_check_user_active` 门禁。
2. **#1 Issue 2**：`ReminderStoreSqlite` 新增 `transition_status` 方法；
   `ReminderStore.mark_fired`/`_transition` 改为优先使用 sqlite，回退 JSONL。
3. **#1 额外**：`_record_cognitive_scan` 增加镜像到 `self._last_cognitive_scan`，
   修复 cognition_summary() 写读断连。同时修复 attention_items 断言
   （从精确匹配改为 containment 检查，适配前缀注入）。
4. **#2**：`loop._sync_subagent_runtime_limits` 用 `object.__setattr__`
   更新 frozen `deps.max_iterations`；`loop._run_agent_loop` 入口调用
   `self._sync_subagent_runtime_limits()` 确保委托前同步。
5. **#3**：测试 SimpleNamespace 添加 `last_sent_messages=None`；
   L797 role 断言从 `"user"` 改为 `"system"`，对齐生产代码行为。
6. **#4**：同 #3 的 `last_sent_messages=None` 修复。

额外修复：`test_disabled_agent_loop_does_not_auto_emit_due_reminder`
（同 #1 Issue 1 的 user_active 门禁问题，同文件同根因）。

提交：spec `fix-preexisting-test-failures-td015-td017` Task 2/3/4。

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-18 | assistant | 登记为待评估，原描述部分根因不准 |
| 2026-07-18 | assistant | 修复完成，4/4 + 1 额外测试通过，状态更新为已修复。根因描述已修正。 |
