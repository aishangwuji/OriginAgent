# Checklist

> 验证清单遵循规则31（清单可信度审计机制）：
> - L2 变更（SubTask A/B 若触及生产代码）须 100% 独立核验
> - L3 变更（TD-015/SubTask C）按 ≥10% 抽样核验
> - 纯文档/测试代码走简化声明

## Task 0: 基线确认
- [x] 8 个测试实测全部 FAILED（修复前基线）✅ 2026-07-18
- [x] 每个测试的失败堆栈首行已记录（用于修复后对比）✅ 2026-07-18

## Task 1: TD-015 — test_consolidator 4 个废弃测试（L3）

- [x] **根因确认**：4 个测试的原始意图已文档化（验证 token 超预算压缩 / raw_archive 回退 / round loop 中断 / 长 tool chain 边界）✅
- [x] **方案决策（规则26）**：选定**方案 A**——改写而非删除，保留原始断言意图 ✅
- [x] **方案 A 路径**：
  - [x] 4 个测试改写后传入 `replay_max_messages=20` 参数
  - [x] 原始断言意图保留（archive 被调用、cursor 推进、round loop 中断、边界遵守）
- [x] **场景验证**：`pytest tests/agent/test_consolidator.py -v` → 26 个 PASSED ✅
- [x] **回归验证**：`pytest tests/agent/test_consolidator.py tests/agent/test_memory.py -v` → 无新失败 ✅
- [x] [✅] 本次变更仅涉及测试代码，不触发业务规则检查（L3 简化声明）

## Task 2: TD-017 SubTask C — last_sent_messages AttributeError（L3）

- [x] **根因确认**：`agent_runtime.py:1140` 附近的访问模式 `result.last_sent_messages` 已确认 ✅
- [x] **额外发现**：L797 断言 `results[0]["role"] == "user"` 过时——`_drain_pending`（L1092）将 `sender_id="subagent"` 标记为内部事件（SYSTEM role），对齐规则18
- [x] **mock 修复**：两个测试的 `SimpleNamespace` 添加 `last_sent_messages=None`（falsy 默认值）
- [x] **role 断言修复**：L797 从 `"user"` 改为 `"system"`
- [x] **场景验证**：2 个 PASSED ✅
- [x] [✅] 本次变更仅涉及测试 mock + 断言对齐，不触发业务规则检查（L3 简化声明）

## Task 3: TD-017 SubTask A — reminder_store 实例不共享（L2，可能触及生产代码）

- [x] **根因确认**：
  - [x] `reminder_store` 注入点已定位——实例**是共享的**，原描述"实例不共享"有误
  - [x] **实际根因 1（测试 setup）**：`session.updated_at` 为当前时间触发 `_check_user_active` 5 分钟门禁，认知 pass 被跳过
  - [x] **实际根因 2（生产代码缺陷）**：`mark_fired`/`_transition` 仅写 JSONL，`upsert`/`get`/`list_due` 用 sqlite——写读路径不一致
  - [x] **额外发现**：`_record_cognitive_scan` 写 `state_holder` 但 `cognition_summary()` 读 `loop._last_cognitive_scan`，写读断连
- [x] **触及生产代码**：
  - [x] 影响分析：`mark_fired` 调用方排查（cognitive_runtime + loop 共享同一实例），无 HIGH/CRITICAL 风险
  - [x] 修复点 1：`reminders_sqlite.py` 新增 `transition_status` 方法
  - [x] 修复点 2：`reminders.py` `mark_fired`/`_transition` 改为 sqlite-first + JSONL 回退
  - [x] 修复点 3：`loop.py` `_record_cognitive_scan` 增加镜像到 `self._last_cognitive_scan`
  - [x] **规则7（状态变化审计）独立核验**：修复后 `mark_fired` 写入 sqlite（主）+ JSONL（冷备），`get` 从 sqlite 读——写读路径一致 ✅
  - [x] **规则27清单**：L2 完整清单 + 抽样核验
- [x] **测试 setup 修复**：老化 `session.updated_at` 到 6 分钟前；`attention_items` 断言改为 containment 检查
- [x] **场景验证**：`pytest tests/agent/test_active_intents.py::test_agent_loop_cognitive_pass_emits_due_reminder_and_writes_working_memory -v` → PASSED ✅
- [x] **回归验证**：`pytest tests/agent/test_active_intents.py -v` → 24/24 PASSED ✅

## Task 4: TD-017 SubTask B — max_iterations 未同步（L2，可能触及生产代码）

- [x] **根因确认**：
  - [x] `RuntimeDependencies` 是 frozen dataclass，`deps.max_iterations` 在 `__init__` 时冻结
  - [x] `loop.max_iterations = 55` 只更新 loop 实例属性，不传播到 `deps.max_iterations`
  - [x] `AgentRuntime._run_agent_loop` 读取 `d.max_iterations`（冻结值 42）——非 spec 构建路径问题
- [x] **触及生产代码**：
  - [x] 影响分析：`_sync_subagent_runtime_limits` 调用方排查，`object.__setattr__` 是 Python 官方 frozen dataclass 机制，无风险
  - [x] 修复点 1：`_sync_subagent_runtime_limits` 用 `object.__setattr__` 更新 frozen deps
  - [x] 修复点 2：`_run_agent_loop` 入口调用 `self._sync_subagent_runtime_limits()` 确保委托前同步
  - [x] **规则17（禁止硬编码）独立核验**：`max_iterations` 从 `self.max_iterations`（配置驱动）读取，非硬编码 ✅
  - [x] **规则27清单**：L2 完整清单 + 抽样核验
- [x] **场景验证**：`pytest tests/agent/tools/test_subagent_tools.py::test_agent_loop_syncs_updated_max_iterations_before_run -v` → PASSED ✅
- [x] **回归验证**：`pytest tests/agent/tools/test_subagent_tools.py -v` → 无新失败 ✅

## Task 5: 全量回归 + TD 状态更新

- [x] **全量回归**：`pytest tests/agent/test_consolidator.py tests/agent/test_active_intents.py tests/agent/tools/test_subagent_tools.py -q` → **72 passed, 0 failed**（161.84s）✅
- [x] **TD-015 状态更新**：旧 `待评估` 文件已删除，新 `TD-2026-015-...-已修复.md` 已创建 ✅
- [x] **TD-017 状态更新**：旧 `待评估` 文件已删除，新 `TD-2026-017-...-已修复.md` 已创建（根因描述已修正）✅
- [x] **范围外发现**：新 TD-019 已登记（`test_consolidation_ratio.py` 3 个预存失败，同 TD-015 根因）✅

## 跨任务验证项（系统认知同步，规则38.4）

- [x] **systemmap 同步**：本次变更未触及已记录的业务规则/状态机，无需更新 systemmap ✅
- [x] **理由**：测试修复 + reminder_store sqlite/JSONL 写读一致性修复 + frozen deps 同步均属于运行时基础设施层，不属于业务域规则

## 提交前最终检查（规则37）

- [ ] 每个 Task 对应独立 commit（共 5 个：Task 1-4 + Task 5 TD 文件更新）
- [ ] commit message 首行为简明祈使句
- [ ] commit message 正文说明"为什么这么改"（非复述 diff）
- [ ] Task 3/4 的 commit message 说明根因（生产代码缺陷 or 测试 mock 问题）
- [x] 敏感信息扫描：本次变更不涉及密钥/凭证/access token（规则18）✅
- [x] 生成文件/构建产物未入库 ✅

## 测试套件总回归

- [x] `pytest tests/agent/test_consolidator.py tests/agent/test_active_intents.py tests/agent/tools/test_subagent_tools.py -q` → **72 passed, 0 failed** ✅
- [x] 本次变更未引入新失败（TD-015/TD-017 的 8 个测试全部修复，TD-019 的 3 个预存失败在本次范围外）✅

## 规则27合规性自检清单（L2 完整清单 + L3 简化声明）

### 红线规则确认（规则0闭集）
- [✅] 规则3（边界数据校验）：本次未引入新的外部输入路径，N/A
- [✅] 规则9（session token 隔离）：本次未涉及异步实例隔离，N/A
- [✅] 规则12（幂等）：本次未涉及可重试写操作，N/A
- [✅] 规则14（关键假设断言化）：`transition_status` 的"reminder_id 存在则更新"假设由 sqlite `SELECT` 返回 `None` 时的 `return None` 守护；`object.__setattr__` 用于 frozen dataclass 是 Python 官方机制
- [✅] 规则18（安全边界）：本次未涉及身份操作或外部输入，N/A

### 高风险规则推演记录
- [✅] 规则7（状态变化审计）：`mark_fired` 修复后写入路径 = sqlite（主）+ JSONL（冷备），读取路径 = sqlite（主）。写读一致，无僵尸引用
- [✅] 规则5（缓存值生命周期）：`deps.max_iterations` 修复后在 `_run_agent_loop` 入口实时同步，不缓存过期值
- [✅] 规则33（手术式变更）：每行 diff 可追溯到具体需求点（见 tasks.md SubTask 实现说明）

### 改动范围合规性（规则33）
- `tests/agent/test_consolidator.py` → TD-015（4 个测试改写）
- `tests/agent/tools/test_subagent_tools.py` → TD-017 SubTask B/C（mock 字段 + role 断言）
- `tests/agent/test_active_intents.py` → TD-017 SubTask A（setup 老化 + 断言适配）
- `OriginAgent/agent/reminders_sqlite.py` → TD-017 SubTask A（新增 `transition_status`）
- `OriginAgent/agent/reminders.py` → TD-017 SubTask A（mark_fired/_transition sqlite-first）
- `OriginAgent/agent/loop.py` → TD-017 SubTask A（`_record_cognitive_scan` 镜像）+ SubTask B（`_sync_subagent_runtime_limits` + `_run_agent_loop` 入口同步）
- **无需求范围外的改动**

### 系统认知同步（规则38.4）
- [✅] 本次变更未更新 `systemmap/`——改动均属于运行时基础设施层（reminder 持久化、frozen dataclass 同步），不属于已记录的业务规则/状态机

### 技术债持久化（规则35）
- [✅] TD-015 已标记 `已修复`：`techdebt/TD-2026-015-test-consolidator-loop-api-deprecated-已修复.md`
- [✅] TD-017 已标记 `已修复`：`techdebt/TD-2026-017-cognitive-subagent-preexisting-test-failures-已修复.md`
- [✅] TD-019 新登记 `待评估`：`techdebt/TD-2026-019-test-consolidation-ratio-deprecated-api-待评估.md`
