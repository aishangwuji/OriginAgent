# OriginAgent 红后化 Phase 1 收口总包计划

Date: 2026-06-13
Status: Completed
Last Reviewed: 2026-06-13
Scope: 红后化总主线 `Phase 1` 的文档收口、边界冻结、验收证据口径与差距登记

## 1. 文档定位

本文件是红后化总主线 `Phase 1` 的唯一收口总包，用于统一回答三类问题：

1. 当前代码现实到底是什么。
2. 哪些口径已经冻结，可以直接被后续实现或验收引用。
3. 哪些差距属于文档修正、证据补齐、`Phase 1` 接受的技术债，或后续实现包。

本文件优先级高于以下旧口径：

1. [`red_queen_phase1_remaining_plan.md`](./red_queen_phase1_remaining_plan.md) 中仍带“待实现”语气的描述。
2. `RQ-001`、`RQ-005`、`P1 Integration` 中与当前实现不一致的前瞻性表述。

本文件不做以下事情：

1. 不扩展 `Phase 2` 或更后续阶段的新能力。
2. 不把现有双轨审计、绑定开关等现实强行改写成理想形态。
3. 不在本轮文档收口中夹带结构性重构任务。

## 2. 当前阶段判断

截至 2026-06-13，红后化总主线 `Phase 1` 的正确状态是：

1. 后台认知闭环已完成代码、测试和 introspection 收口验证。
2. 当前工作重点已从“待验收收口”切换为维护既有合同、保留 backlog 与承接 `Phase 2+`。
3. `RQ-001`、`RQ-005`、`P1 Integration` 应统一改写为 `Completed`。
4. 显式 gap 条目继续保留为 backlog，但不再阻塞 `Phase 1` 结项。

## 3. 冻结口径

### 3.1 运行时主链

红后 `Phase 1` 的首选主路径冻结为：

```text
CronService / manual scheduler trigger
        ↓
CognitiveScheduler.run_once()
        ↓
AgentCognitiveRuntime.run_cognitive_pass_for_session()
        ↓
MessageBus inbound publish
        ↓
AgentLoop._process_message()
```

补充说明：

1. `AgentLoop` 仍是唯一语义执行入口。
2. `AgentCognitiveRuntime` 是当前实际的后台认知编排运行时，后续文档不得继续隐去它。
3. `CognitiveLoop` 不是“死代码”，但也不是首选主路径；它在 `scheduler.mode != "cron"` 时承担 fallback interval driver 角色。

### 3.2 Producer 词汇统一

本收口包中，“producer”指能向认知候选收集阶段提供机会项的来源，不等价于“直接发布内部消息的模块”。

当前 `Phase 1` 的候选来源冻结为两类：

1. `ActiveIntentService._build_candidates()` 负责：
   - `goal_nudge`
   - `pending_confirmation_nudge`
   - `foresight_nudge`
2. `AgentLoop._collect_cognitive_candidates()` 中的 `ReminderStore.list_due()` 负责：
   - `scheduled_reminder`

两类来源在 candidate collection 阶段合并后，再统一进入 `AgentCognitiveRuntime.run_cognitive_pass_for_session()` 的 policy / emit / audit 流程。

### 3.3 边界冻结

1. `ActiveIntentService` 在 `Phase 1` 继续保持独立 producer 身份，不被 `CognitiveLoop` 吸收。
2. `BackgroundReviewService` 明确列为相邻能力，排除在红后 `Phase 1` 主认知链外。
3. `idle session` 的当前定义严格以代码为准：`active_task_count == 0` 且 `running_subagents == 0`。
4. 候选排序规则冻结为 `priority(high/medium/low) + created_at` 的简单排序，不写成完整优先级调度器。
5. 当前 working memory 写回事实只包括：
   - `attention_items`
   - `pending_questions`
6. `tool_residue` 不属于当前 `Phase 1` 已落地写回真相。

## 4. 收口矩阵

说明：

1. 一行代表一个断言级别的行为主张，而不是字段级穷举。
2. `证据状态` 只允许使用：
   - `已有测试`
   - `已有代码无测试证据`
   - `待补验证`
3. `差距处置` 只允许归入：
   - `文档修正`
   - `证据补齐`
   - `Phase 1 接受的技术债`
   - `后续实现包`

| 冻结主张 | 代码现实 | 证据状态 | 差距处置 |
| --- | --- | --- | --- |
| `AgentLoop` 仍是唯一语义执行入口 | 后台认知输出统一经 `MessageBus` 回到 `_process_message()` 主路径 | 已有测试 | 文档修正：统一删除“第二执行入口”暗示 |
| `CognitiveScheduler` 是首选定时驱动 | `start_active_intent_loop()` 优先调用 `cognitive_scheduler.start()`；`cron` 模式下不创建本地任务 | 已有测试 | 文档修正：主路径统一写为 `CognitiveScheduler -> AgentCognitiveRuntime` |
| `CognitiveLoop` 是 fallback interval driver | `scheduler.mode != "cron"` 时，运行时创建任务执行 `CognitiveLoop.run_forever()` | 已有测试 | 文档修正：不再把 `CognitiveLoop` 与主路径并列渲染 |
| `scheduler_runs.jsonl` 只在 scheduler 扫描路径产出 | `CognitiveScheduler.run_once()` 末尾写入 `scheduler_runs.jsonl`；fallback `CognitiveLoop` 路径直接调用 session processor，不写该文件 | 已有测试 | 文档修正：显式写出主路径与 fallback 路径审计产物差异 |
| `AgentCognitiveRuntime` 是实际编排运行时 | eligibility、candidate collection 之后的 cooldown / emit / audit / latest_scan 都在该运行时中完成 | 已有代码无测试证据 | 文档修正：升格为正式架构名词 |
| `ActiveIntentService` 继续是独立 producer | 仍保留自身 ledger、cooldown 逻辑与候选构建职责 | 已有测试 | 文档修正：明确“不吸收进 `CognitiveLoop`” |
| `ActiveIntentService` 存在两条 API 路径 | `process_session()` 是自包含 pipeline；认知主链实际使用 `collect_candidates() + build_message()` | 已有代码无测试证据 | Phase 1 接受的技术债：文档明确主链只使用后者 |
| `scheduled_reminder` 不是从 `ActiveIntentService` 产出 | 第四类机会由 `ReminderStore.list_due()` 在 `_collect_cognitive_candidates()` 中独立注入 | 已有测试 | 文档修正：在 producer glossary 中写清两类来源 |
| `Phase 1` 支持的机会类型只限四类 | `CognitiveEventType` 固定为 `goal_nudge`、`pending_confirmation_nudge`、`scheduled_reminder`、`foresight_nudge` | 已有测试 | 文档修正：删除 `background_review` 等未接入类型的歧义 |
| session eligibility 当前只有两个忙碌条件 | `eligible_session()` 只检查 `active_task_count` 和 `running_subagents`，不含静默时长阈值 | 已有测试 | 文档修正：统一把 idle 定义精确到代码 |
| cooldown 判断逻辑来自 `ActiveIntentService` | 认知运行时通过公开 `passes_cooldown()` 包装方法复用 session / intent cooldown 规则 | 已有测试 | 文档修正：旧版私有跨类调用口径已收敛 |
| 每轮单 session 有消息上限 | `max_messages_per_session_per_pass` 在认知运行时内生效，并将超限记录为 `session_message_limit` | 已有测试 | 文档修正：不写成“完整排序器”或“完整预算系统” |
| working memory 写回已最小打通 | `attention_items` 对所有带摘要事件写回；`pending_questions` 针对 `pending_confirmation_nudge` 与 `scheduled_reminder` 写回 | 已有测试 | 文档修正：不再把 `tool_residue` 写成已落地事实 |
| 认知审计 JSONL 已冻结 | `memory/cognitive/events.jsonl` 与 `memory/cognitive/decisions.jsonl` 为 append-only 合同 | 已有测试 | 无 |
| 双轨审计在当前实现中并存 | 同一次决策会同时写认知审计 JSONL 与 `memory/active_intents/records.jsonl` | 已有测试 | Phase 1 接受的技术债：本轮冻结现状，不合并模型 |
| introspection 已暴露最小认知摘要 | `cognition_summary()` 聚合 `latest_scan`、审计摘要、scheduler summary 与 runtime status | 已有测试 | 文档修正：按当前字段口径书写 |
| `latest_scan` 不含最近抑制明细列表 | 当前只有聚合计数和最近扫描摘要，没有“最近 N 条 suppression 明细” | 已有代码无测试证据 | 后续实现包：若需要明细，再扩 introspection 合同 |
| `_cognitive_loop_enabled` 是派生值 | 该值来自 `_active_intent_config.enabled` 的派生布尔值，且被 introspection 读取 | 已有代码无测试证据 | 后续实现包：拆独立开关时同步修改派生值与 introspection consumer |
| 启用开关当前统一绑定到 `allow_agent_initiated_messages` | `CognitiveLoopConfig.enabled`、`CognitiveSchedulerConfig.enabled` 结构已存在，但运行时来源统一绑定到 active intent enablement | 已有测试 | 后续实现包：如需独立开关，修改 `build_loop_components` 中 enabled 绑定与 `_cognitive_loop_enabled` 赋值 |
| `scheduled_reminder` 自动投递依赖认知扫描 | 关闭 `allow_agent_initiated_messages` 会同时停止该自动投递 | 已有测试 | 文档修正：明确这是当前兼容现实，而非旁路行为 |
| 当前排序是简单优先级排序 | `_collect_cognitive_candidates()` 仅按 `priority + created_at` 排序，不做语义交叉排序 | 已有代码无测试证据 | 文档修正：避免夸大为完整优先级系统 |
| `BackgroundReviewService` 不在 `Phase 1` 主认知链 | 当前没有进入统一 candidate collection / emit / audit 主线 | 已有代码无测试证据 | 文档修正：写为相邻能力，不再混入 `Phase 1` 统计 |

## 5. 验收证据与通过标准

`Phase 1` 收口验证采用“现有测试优先、缺口最小补齐”的策略。本次 closeout 已完成以下四组关键证据验证：

### 5.1 主路径与 fallback 路径

通过标准：

1. `cron` 或 manual scheduler 路径下，至少产生一条新的 `scheduler_runs.jsonl` 记录，并可关联到同轮 `events.jsonl` / `decisions.jsonl`。
2. fallback 路径下，后台认知任务可被启动并产生活动事件与决策记录。
3. fallback 路径下，没有 `scheduler_runs.jsonl` 新行不视为失败，而应视为当前设计现实。

验证结果：

1. 已通过；scheduler 模式分支与 fallback 审计差异已有自动化测试覆盖。

### 5.2 suppression reason 一致性

通过标准：

1. 抽取最近 20 条 `outcome == "suppressed"` 的 `CognitiveDecision`。
2. 对比同一 session / cooldown key 对应的 `ActiveIntentRecord.suppression_reason`。
3. 100% 一致视为通过。

验证结果：

1. 已通过；双轨 `suppression_reason` 一致性已有自动化测试覆盖。

### 5.3 关闭开关兼容行为

通过标准：

1. 设置 `allow_agent_initiated_messages = False` 后，不产生新的自动认知扫描投递。
2. 同时不产生新的自动 reminder 投递。
3. 用户前台消息路径保持正常，不因认知关闭而回归失败。

验证结果：

1. 已通过；disabled 模式下 reminder 不自动投递且前台消息路径保持正常。

### 5.4 `latest_scan` / audit / scheduler summary 一致性

通过标准：

1. 抽取 3 次认知扫描样本。
2. `latest_scan` 的 `decision_count`、`emitted_count`、`suppressed_count` 与同期决策记录相符。
3. `event_types` 与同期事件类型列表相符。
4. 若样本来自 fallback 路径，不要求存在新的 `scheduler_runs.jsonl`；若来自 scheduler 路径，则要求存在。

验证结果：

1. 已通过；`latest_scan` / audit / scheduler summary 对账已有自动化测试覆盖。

## 6. 子包定位

本收口总包下的三个直接子包定位如下：

1. [`rq-001-cognitive-orchestration-boundary.md`](./rq-001-cognitive-orchestration-boundary.md)
   - 负责边界冻结与模块责任定义。
2. [`rq-005-cognitive-event-audit-model.md`](./rq-005-cognitive-event-audit-model.md)
   - 负责事件、决策、审计与 introspection 合同冻结。
3. [`p1-backend-cognition-integration-task-package.md`](./p1-backend-cognition-integration-task-package.md)
   - 负责主运行时链路、fallback 路径、producer 集合和收口验收项。

## 7. 非阻塞备注

以下项目不阻塞 `Phase 1` 收口，但保留为显式备注：

1. `_utcnow_iso()` 当前在多个模块中分别定义，属于可接受的模块级重复模式，不在本轮统一抽取。
2. `process_session()` 与 `collect_candidates() + build_message()` 的双路径 API 面需要在后续维护中持续避免误用。
3. 若未来拆分独立认知开关，必须同时更新 `build_loop_components`、`_cognitive_loop_enabled` 和 introspection consumer。

## 8. 默认解释规则

1. 当前代码现实始终优先于旧设计稿表述。
2. 若本总包与旧 `Phase 1` 剩余计划冲突，以本总包为准。
3. 若子包与本总包冲突，以本总包为准。
