# RQ-001 任务包：后台认知编排层边界冻结

Date: 2026-06-09
Status: Implemented, Closeout Validation Pending
Last Reviewed: 2026-06-13
Scope: 红后化总主线 `Phase 1` 的后台认知编排边界冻结与文档收口

## title

`RQ-001` 后台认知编排层边界冻结

## goal

冻结后台认知编排层在 `Phase 1` 的真实边界，明确：

1. `CognitiveScheduler`、`AgentCognitiveRuntime`、`CognitiveLoop`、`AgentLoop` 各自负责什么。
2. 它们与 `ActiveIntentService`、`CronService`、`Dream`、nearline memory、`ReminderStore` 的关系。
3. 后台认知事件如何进入现有语义执行主链。
4. 哪些是 `Phase 1` 已经落地的事实，哪些只作为显式 gap 留存。

本任务包的上位收口口径见：

1. [`docs/red_queen_phase1_closeout_plan.md`](./red_queen_phase1_closeout_plan.md)

## scope

本任务包覆盖以下内容：

1. 冻结后台认知编排层的最小职责：
   - session eligibility 判定
   - candidate collection
   - cooldown / suppression / per-pass limit
   - 内部事件投递
   - 最小 working memory 写回触发
   - 审计与最近扫描摘要
2. 冻结后台认知编排层与现有模块的关系：
   - `AgentLoop` 继续作为唯一语义执行入口
   - `CognitiveScheduler` 作为首选定时驱动
   - `AgentCognitiveRuntime` 作为实际编排运行时
   - `CognitiveLoop` 作为 fallback interval driver
   - `ActiveIntentService` 继续作为独立 producer
   - `ReminderStore` 继续作为 `scheduled_reminder` 的候选来源
   - `CronService` 继续负责持久调度触发
   - `Dream` / nearline memory 继续负责长期与近线记忆整理
3. 冻结 `Phase 1` 允许支持的后台机会类型：
   - `goal_nudge`
   - `pending_confirmation_nudge`
   - `scheduled_reminder`
   - `foresight_nudge`
4. 冻结 sidecar 原则：
   - 不改写现有主状态机
   - 不引入第二条执行旁路
   - 不让后台认知层直接绕过安全与确认边界

## non_goals

本任务包不覆盖以下内容：

1. 感知事件接入。
2. 真实世界模型查询。
3. 动作规划。
4. 完整优先级调度算法。
5. 分布式调度、队列或多进程执行基础设施。
6. `Phase 2+` 的新 producer 设计。

## dependencies

1. [`docs/red_queen_master_plan.md`](./red_queen_master_plan.md)
2. [`docs/red_queen_phase1_closeout_plan.md`](./red_queen_phase1_closeout_plan.md)
3. [`docs/red_queen_phase1_remaining_plan.md`](./red_queen_phase1_remaining_plan.md)
4. 现有运行时模块：
   - [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
   - [OriginAgent/agent/agent_cognitive_runtime.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_cognitive_runtime.py)
   - [OriginAgent/agent/cognitive_scheduler.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_scheduler.py)
   - [OriginAgent/agent/cognitive_loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_loop.py)
   - [OriginAgent/agent/active_intents.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/active_intents.py)
   - [OriginAgent/agent/background_review.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/background_review.py)

## files_or_modules

本任务包当前以文档层冻结为主：

1. [`docs/red_queen_phase1_closeout_plan.md`](./red_queen_phase1_closeout_plan.md)
2. [`docs/rq-001-cognitive-orchestration-boundary.md`](./rq-001-cognitive-orchestration-boundary.md)
3. [`docs/p1-backend-cognition-integration-task-package.md`](./p1-backend-cognition-integration-task-package.md)

当前边界对应的关键实现模块：

1. [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
2. [OriginAgent/agent/agent_cognitive_runtime.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_cognitive_runtime.py)
3. [OriginAgent/agent/cognitive_scheduler.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_scheduler.py)
4. [OriginAgent/agent/cognitive_loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_loop.py)
5. [OriginAgent/agent/active_intents.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/active_intents.py)

## boundary_freeze

### 1. 统一执行入口

1. `AgentLoop` 继续是唯一语义执行入口。
2. 后台认知不会直接执行工具，也不会绕过 `_process_message()` 主路径。
3. 所有后台认知输出仍通过内部 inbound event 进入现有消息主链。

### 2. 主路径与 fallback 路径

1. 首选主路径：
   - `CronService`
   - `CognitiveScheduler.run_once()`
   - `AgentCognitiveRuntime.run_cognitive_pass_for_session()`
   - `MessageBus`
   - `AgentLoop._process_message()`
2. fallback 路径：
   - `AgentCognitiveRuntime.start_active_intent_loop()`
   - `CognitiveLoop.run_forever()`
   - `AgentCognitiveRuntime.run_cognitive_pass_for_session()`
3. `CognitiveLoop` 在当前 `Phase 1` 中是 fallback interval driver，不应被描述为唯一主路径。

### 3. 各模块最小职责

1. `CognitiveScheduler`
   - 负责周期触发与 session sweep 调度
   - 负责 `scheduler_runs.jsonl` 审计写入
   - 不负责事件语义执行
2. `AgentCognitiveRuntime`
   - 负责 eligibility 结果承接
   - 负责 candidate collection 后的 policy / cooldown / emit / audit 编排
   - 负责 `latest_scan` 摘要写回
3. `CognitiveLoop`
   - 负责 fallback 情况下的 interval 驱动
   - 直接复用 session processor
   - 不写 `scheduler_runs.jsonl`
4. `ActiveIntentService`
   - 负责构建前三类候选
   - 负责其独立 ledger 与 cooldown 规则来源
   - 不在 `Phase 1` 中被吸收到编排运行时内部
5. `ReminderStore`
   - 负责到期 reminder 候选源
   - 不通过 `ActiveIntentService._build_candidates()` 产出
6. `Dream` / nearline memory
   - 继续负责记忆整理与 foresight 数据来源
   - 不承担认知编排职责

### 4. Producer 边界

当前 `Phase 1` 的候选信号来源明确分为两类：

1. `ActiveIntentService._build_candidates()`
   - `goal_nudge`
   - `pending_confirmation_nudge`
   - `foresight_nudge`
2. `AgentLoop._collect_cognitive_candidates()` 中的 `ReminderStore.list_due()`
   - `scheduled_reminder`

补充说明：

1. `ActiveIntentService.process_session()` 是该服务自包含的独立整体运行能力。
2. 认知主链当前统一使用 `collect_candidates() + build_message() + 外部 publish` 路径。
3. `process_session()` 当前不在 `Phase 1` 的认知主链中使用。

### 5. 当前不纳入主链的相邻能力

1. `BackgroundReviewService` 当前不进入统一 candidate collection / emit / audit 主链。
2. household 级后台认知不在 `Phase 1` 实现。
3. 感知事件 producer 不在 `Phase 1` 实现。

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. 后台认知编排层的职责边界已冻结。
2. `CognitiveScheduler`、`AgentCognitiveRuntime`、`CognitiveLoop`、`AgentLoop` 的关系已写清楚。
3. 已明确：后台认知层是 sidecar，不是第二主循环。
4. 已明确：所有后台认知输出仍走现有 `_process_message()` 主路径。
5. 已明确：`Phase 1` 支持哪些后台机会类型，以及哪些类型不在主链中。
6. `RQ-005` 与 `P1 Integration` 可直接引用本任务包，不再重复定义核心边界。

## tests

本任务包为文档冻结任务，不要求本轮直接新增自动化测试。

完成时应进行以下校验：

1. 文档术语检查：`AgentCognitiveRuntime`、主路径、fallback 路径、producer 词汇在三个子包中一致。
2. 架构一致性检查：不存在“后台认知直接执行工具”或“第二主循环”残留表述。
3. 路径一致性检查：`scheduled_reminder` 的来源与 `ActiveIntentService` 的来源区分清楚。
4. 证据缺口登记：主路径与 fallback 路径在 `scheduler_runs.jsonl` 产出上的差异已被明确记录。

## rollback_plan

若本任务包结论被证明不适用于后续实现，回滚方式应为：

1. 保留本任务包历史版本。
2. 在新版本中显式记录边界变更与原因。
3. 不允许在未修订边界文档前，让实现悄悄偏离既定职责。

## open_questions

当前 `Phase 1` 收口中，不再保留阻塞性开放问题。仅保留以下后续关注项：

1. `ActiveIntentService` 是否在未来被进一步收敛为更窄的 producer 接口。
2. `BackgroundReviewService` 是否在未来作为独立 producer 接入统一认知事件模型。
3. household 级认知扫描是否需要在后续阶段引入新的作用域层能力。
