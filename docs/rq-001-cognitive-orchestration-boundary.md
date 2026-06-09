# RQ-001 任务包：后台认知编排层边界与最小职责定义

Date: 2026-06-09
Status: Proposed
Scope: 红后化总主线 `Phase 1` 的后台认知编排边界冻结

## title

`RQ-001` 后台认知编排层边界与最小职责定义

## goal

冻结后台认知编排层的边界，明确：

1. `CognitiveLoop` / `CognitiveScheduler` 负责什么。
2. 它与 `AgentLoop`、`ActiveIntentService`、`CronService`、`Dream`、nearline memory 的关系。
3. 后台认知事件如何进入现有执行主链。
4. 哪些能力属于 `Phase 1` 必须落地，哪些延后到后续阶段。

## scope

本任务包覆盖以下内容：

1. 定义后台认知编排层的最小职责：
   - 周期巡检
   - session eligibility 判定
   - 候选信号收集
   - cooldown / suppression / dedupe
   - 内部事件投递
   - 审计与写回
2. 明确后台认知编排层与现有模块的关系：
   - `AgentLoop` 继续作为唯一语义执行入口
   - `ActiveIntentService` 继续作为候选主动意图来源之一
   - `CronService` 继续负责持久任务调度
   - `Dream` / nearline memory 继续负责长期与近线记忆整理
3. 定义 `Phase 1` 允许支持的后台机会类型：
   - `goal_nudge`
   - `pending_confirmation_nudge`
   - `scheduled_reminder`
   - `foresight_nudge`
4. 定义 sidecar 原则：
   - 不改写现有主状态机
   - 不引入第二条执行旁路
   - 不让后台认知层直接绕过安全与确认边界

## non_goals

本任务包不覆盖以下内容：

1. `CognitiveLoop` 的完整代码实现。
2. 感知事件接入。
3. 真实世界模型查询。
4. 动作规划。
5. 完整优先级调度算法。
6. 分布式调度、队列或多进程执行基础设施。

## dependencies

1. [`docs/red_queen_master_plan.md`](./red_queen_master_plan.md)
2. [`docs/continuity_memory_os_outline.md`](./continuity_memory_os_outline.md)
3. [`docs/red_queen_phase1_remaining_plan.md`](./red_queen_phase1_remaining_plan.md)
4. 现有运行时模块：
   - [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
   - [OriginAgent/agent/active_intents.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/active_intents.py)
   - [OriginAgent/agent/background_review.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/background_review.py)

## files_or_modules

本任务包预期主要触达文档层：

1. [`docs/red_queen_master_plan.md`](./red_queen_master_plan.md)
2. [`docs/red_queen_phase1_remaining_plan.md`](./red_queen_phase1_remaining_plan.md)
3. [`docs/rq-001-cognitive-orchestration-boundary.md`](./rq-001-cognitive-orchestration-boundary.md)

后续实现预计主要影响：

1. [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
2. [OriginAgent/agent/active_intents.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/active_intents.py)
3. 候选新模块：
   - `OriginAgent/agent/cognitive_loop.py`
   - `OriginAgent/agent/cognitive_events.py`
   - `OriginAgent/agent/cognitive_audit.py`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. 后台认知编排层的职责边界已冻结。
2. 与 `AgentLoop`、`ActiveIntentService`、`CronService`、`Dream`、nearline memory 的关系已写清楚。
3. 已明确：后台认知层是 sidecar，不是第二主循环。
4. 已明确：所有后台认知输出仍走现有 `_process_message()` 主路径。
5. 已明确：`Phase 1` 支持哪些后台机会类型，以及不支持哪些类型。
6. `RQ-005` 与后续 integration 任务包可直接引用本任务包，不再重复定义核心边界。

## tests

本任务包为文档冻结任务，不要求新增自动化测试。

完成时应进行以下校验：

1. 文档审阅检查：`RQ-005` 与 integration 任务包术语与本任务包一致。
2. 路径一致性检查：不存在“后台认知直接执行工具”的设计残留。
3. 架构一致性检查：`AgentLoop` 仍被定义为统一执行入口。

## rollback_plan

若本任务包结论被证明不适用于后续实现，回滚方式应为：

1. 保留本任务包历史版本。
2. 在新版本中显式记录边界变更与原因。
3. 不允许在未修订边界文档前，让实现悄悄偏离既定职责。

## open_questions

1. `ActiveIntentService` 后续是继续保留独立候选生成职责，还是逐步被 `CognitiveLoop` 包裹后内聚。
2. `BackgroundReviewService` 在 `Phase 1` 中是否只参与“候选摘要”，还是允许参与更显式的认知机会生成。
3. session 级后台认知扫描是否需要为未来 `household` 级认知预留接口，但不在 `Phase 1` 实现。
