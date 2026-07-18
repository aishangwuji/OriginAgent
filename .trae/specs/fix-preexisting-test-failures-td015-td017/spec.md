# 修复 TD-015 + TD-017 预存测试失败 Spec

## Why

项目当前有 8 个预存测试失败持续污染 CI 信号（已实测确认）：
- **TD-015**：`tests/agent/test_consolidator.py::TestConsolidatorTokenBudget` 的 4 个测试为已废弃的 loop API 编写，断言 `archive.assert_awaited_once()` 处全部失败
- **TD-017**：`tests/agent/test_active_intents.py` 1 个 + `tests/agent/tools/test_subagent_tools.py` 3 个，根因涉及 reminder_store 实例不共享、max_iterations 未同步、SimpleNamespace 缺 `last_sent_messages` 字段

本次 spec `fix-cron-runtime-and-context-gaps` 验证期间反复需要 `git stash` 才能区分新旧失败，严重降低效率。8 个失败中有 2 类（TD-017 #1/#2）可能揭示真实代码缺陷而非纯测试问题，值得深入排查。

## What Changes

### TD-015（4 个废弃测试）
- **决策方向（执行阶段确认）**：三选一
  - 方案 A：改写 4 个测试，传入 `replay_max_messages` 参数触发 `archive` 调用，保留原始测试意图（验证 token 超预算压缩、raw_archive 回退、round loop 中断、长 tool chain 边界）
  - 方案 B：通过 `state_save` 端到端路径触发，更接近真实使用场景
  - 方案 C：若确认新架构下这些场景已被温区总结（warm summary）覆盖，标记废弃并删除
- **推荐**：方案 A（保留原始测试意图，最小改动）→ 若执行阶段发现新架构已无对应代码路径，降级为方案 C

### TD-017（4 个预存失败）

#### SubTask A：#1 reminder 未 fired（可能涉及真实代码缺陷）
- **现象**：`test_agent_loop_cognitive_pass_emits_due_reminder_and_writes_working_memory` 中 `loop._reminder_store.get("r-1").status` 仍是 `"pending"`，但 cognitive pass 已发射 scheduled_reminder 消息
- **疑点**：`loop._reminder_store` 与 `cognitive_runtime._deps.reminder_store` 可能不是同一实例，导致 `mark_fired` 写入的对象与测试读取的对象不同
- **修复方向**：
  1. 排查 AgentLoop 构造时 reminder_store 的注入路径，核对是否同一实例
  2. 若不共享，在 AgentLoop 构造时绑定同一对象，或在 cognitive_runtime 中通过 `loop._reminder_store` 访问
  3. 若排查发现是测试侧 mock 问题（而非生产代码缺陷），则修测试

#### SubTask B：#2 max_iterations 未同步（可能涉及真实代码缺陷）
- **现象**：`test_agent_loop_syncs_updated_max_iterations_before_run` 中 `AgentRunSpec.max_iterations` 仍是旧值 42，未同步配置更新后的 55
- **疑点**：subagent run spec 构建路径未读取最新的 `config.agents.*.max_iterations`
- **修复方向**：
  1. 排查 subagent `AgentRunSpec` 构建路径是否在 spawn 时一次性读取配置
  2. 若是，需在 loop 检测到配置更新后重置 spec 或改用惰性读取
  3. 若排查发现是测试侧 mock 问题，则修测试

#### SubTask C：#3/#4 last_sent_messages AttributeError（纯测试 mock 缺陷）
- **现象**：`test_drain_pending_blocks_while_subagents_running` 与 `test_drain_pending_timeout` 用 `SimpleNamespace` 模拟 runner result，但 `agent_runtime.py:1173` 访问 `result.last_sent_messages` 未在 SimpleNamespace 上提供
- **修复方向**：更新测试 mock，在 `SimpleNamespace` 上添加 `last_sent_messages` 字段（或改用 `MagicMock` 自动生成属性）
- **对照**：`agent_runtime.py:1173` 的访问模式为 `if session is not None and result.last_sent_messages:`，默认值应为 `None` 或 `[]`

## Impact

- **Affected specs**: 无（techdebt 推进，非 spec 驱动）
- **Affected code**:
  - TD-015: `tests/agent/test_consolidator.py`（仅测试代码）
  - TD-017 SubTask A: `tests/agent/test_active_intents.py` 或 `OriginAgent/agent/loop.py`/`agent_loop_components.py`/`cognitive_runtime.py`（取决于根因）
  - TD-017 SubTask B: `tests/agent/tools/test_subagent_tools.py` 或 `OriginAgent/agent/loop.py`/`subagent.py`（取决于根因）
  - TD-017 SubTask C: `tests/agent/tools/test_subagent_tools.py`（仅测试代码）

## 风险分级（规则32-B）

| SubTask | 风险等级 | 理由 |
|---------|---------|------|
| TD-015 | L3 | 纯测试代码，无逻辑分支/外部IO；若降级为方案 C（删除）需谨慎确认场景已被覆盖 |
| TD-017 SubTask A | L2 | 可能涉及 reminder_store 实例共享（状态一致性，规则7）；若触及生产代码需完整清单 |
| TD-017 SubTask B | L2 | 可能涉及 subagent spec 构建路径（配置读取，规则17）；若触及生产代码需完整清单 |
| TD-017 SubTask C | L3 | 纯测试 mock 适配，无逻辑分支 |

## 规则合规要点

- **规则34（验证先行）**：先确认 8 个测试当前失败，修复后确认全部通过
- **规则33（手术式变更）**：若根因在生产代码，仅修复具体缺陷，不顺手重构相邻代码
- **规则32（最小化实现）**：若根因在测试 mock，仅补必要字段，不重写整个 mock 结构
- **规则27（清单）**：每个 SubTask 完成后输出合规性自检清单
- **规则37（原子提交）**：TD-015 一个 commit，TD-017 每个 SubTask 一个 commit（共 4 个 commit）
- **规则31（清单可信度审计）**：SubTask A/B 若触及生产代码（L2），主 agent 需独立核验根因分析与修复点

## 不在本次范围

- TD-016（工具鲁棒性缺陷）、TD-018（MCP 启动重试）等其余 9 个待评估技术债——属后续批次
- TD-017 中"max_iterations 未同步"若揭示更广泛的 subagent spec 构建问题——本次仅修复让测试通过的最小范围， broader 重构登记为新 TD
