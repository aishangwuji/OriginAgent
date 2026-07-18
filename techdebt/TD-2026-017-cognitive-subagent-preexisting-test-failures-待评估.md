---
schema_version: 1
---

# TD-2026-017: 4 个预存测试失败（cognitive pass / subagent drain / max_iterations 同步）

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-18T20:00:00+08:00 |
| 发现人 | assistant（A+C 重构 Phase 4 验证过程中） |
| 关联Spec | 无 |
| 关联规则 | 规则15（测试策略需匹配变更风险）、规则34（验证先行） |
| 优先级 | P3 |
| 状态 | 待评估 |

## 详细描述
A+C 重构 Phase 4（cognitive nudge 标记 `is_internal=True`）验证过程中
发现 4 个测试失败。通过 `git stash` 验证（无 Phase 4 改动）仍全部失败，
确认为**预存失败**，与 Phase 4 无关：

1. `tests/agent/test_active_intents.py::test_agent_loop_cognitive_pass_emits_due_reminder_and_writes_working_memory`
   - 失败：`assert 'pending' == 'fired'`
   - 现象：cognitive pass 发射了 scheduled_reminder 消息（断言 1-3 通过：
     `decisions` 含 `outcome == "emitted"`、`bus.consume_inbound` 取到
     消息、metadata 正确），但 `_reminder_store.get("r-1").status` 仍是
     `"pending"`——`mark_fired` 未生效。
   - 疑点：`loop._reminder_store` 与 `cognitive_runtime._deps.reminder_store`
     可能不是同一实例，导致 `mark_fired` 写入的对象与测试读取的对象不同。

2. `tests/agent/tools/test_subagent_tools.py::test_agent_loop_syncs_updated_max_iterations_before_run`
   - 失败：`assert 42 == 55`
   - 现象：`AgentRunSpec.max_iterations` 仍是旧值 42，未同步配置更新后的 55。
   - 疑点：subagent run spec 构建路径未读取最新的 `config.agents.*.max_iterations`。

3. `tests/agent/tools/test_subagent_tools.py::test_drain_pending_blocks_while_subagents_running`
   - 失败：`AttributeError: 'types.SimpleNamespace' object has no attribute 'last_sent_messages'`
   - 现象：测试用 `SimpleNamespace` 模拟 runner result，但
     `agent_runtime.py:1140` 的 `result.last_sent_messages` 访问未在
     SimpleNamespace 上提供该属性。
   - 疑点：runner result 协议升级后增加了 `last_sent_messages` 字段，但
     测试 mock 未同步更新。

4. `tests/agent/tools/test_subagent_tools.py::test_drain_pending_timeout`
   - 失败：同 #3，`'SimpleNamespace' has no attribute 'last_sent_messages'`
   - 根因与 #3 相同。

## 影响范围
- **影响文件**：
  - `tests/agent/test_active_intents.py`
  - `tests/agent/tools/test_subagent_tools.py`
- **影响功能**：测试套件持续 4 个失败，干扰 CI 信号
- **潜在风险**：
  - CI 噪音掩盖真实回归（"狼来了"效应）
  - 后续 cognitive / subagent 改动难以判断哪些失败是预存的、哪些是新引入的
  - 与 TD-015 累计已有 8 个预存测试失败

## 复现/验证路径
```powershell
$env:TMPDIR = "$PWD\.pytest_basetmp"
.\.venv\Scripts\python.exe -m pytest `
  tests/agent/test_active_intents.py::test_agent_loop_cognitive_pass_emits_due_reminder_and_writes_working_memory `
  tests/agent/tools/test_subagent_tools.py::test_agent_loop_syncs_updated_max_iterations_before_run `
  tests/agent/tools/test_subagent_tools.py::test_drain_pending_blocks_while_subagents_running `
  tests/agent/tools/test_subagent_tools.py::test_drain_pending_timeout
```
4 个全部失败。`git stash` 后（无 Phase 4 改动）仍全部失败——确认非 A+C
重构引入。

## 修复方案（可选）
1. **#1（reminder 未 fired）**：核对 `loop._reminder_store` 与
   `cognitive_runtime._deps.reminder_store` 是否共享同一实例；若不共享，
   需在 AgentLoop 构造时把它们绑定为同一对象，或在 cognitive_runtime
   中通过 `loop._reminder_store` 访问。
2. **#2（max_iterations 未同步）**：检查 subagent `AgentRunSpec` 构建
   路径是否在 spawn 时一次性读取配置；若是，需在 loop 检测到配置更新后
   重置 spec 或改用惰性读取。
3. **#3、#4（last_sent_messages AttributeError）**：更新测试 mock，在
   `SimpleNamespace` 上添加 `last_sent_messages` 字段（或改用
   `MagicMock` 自动生成属性）。需对照 `agent_runtime.py:1140` 附近的
   访问模式，确认字段类型与默认值。

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-18 | assistant | 登记为待评估，需人工确认根因分析与修复优先级 |
