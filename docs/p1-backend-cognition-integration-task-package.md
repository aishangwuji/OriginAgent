# P1 任务包：后台认知闭环集成收口

Date: 2026-06-09
Status: Implemented, Closeout Validation Pending
Last Reviewed: 2026-06-13
Scope: 红后化总主线 `Phase 1` 的后台认知闭环运行时、producer 集合与验收收口

## title

`P1 Integration` 后台认知闭环集成收口

## goal

在不引入真实感知设备的前提下，确认当前 runtime 已形成一个最小可运行的后台认知闭环，并将它改写成一致、可验收、可接力的正式口径：

1. 当前哪些 session 会被巡检。
2. 当前哪些后台机会会被识别、抑制、投递与审计。
3. 当前结果如何回到 `AgentLoop` 主路径。
4. 当前结果如何最小写回 working memory 与 introspection。
5. 当前主路径、fallback 路径和关闭开关现实分别是什么。

本任务包的上位收口口径见：

1. [`docs/red_queen_phase1_closeout_plan.md`](./red_queen_phase1_closeout_plan.md)

## scope

本任务包覆盖以下内容：

1. 冻结当前主运行时链路与 fallback 链路。
2. 冻结四类后台机会与其候选来源。
3. 冻结 eligibility / cooldown / per-pass message limit 的已落地行为。
4. 冻结 working memory 最小写回与 introspection 当前输出口径。
5. 冻结启用开关、scheduler 审计和 reminder 耦合的真实兼容现实。
6. 登记当前 `Phase 1` 的显式证据缺口与可接受技术债。

## non_goals

本任务包不覆盖以下内容：

1. 真实视觉或音频感知接入。
2. 世界模型查询与冲突治理。
3. 动作规划。
4. 完整优先级调度器。
5. 多进程、分布式或外部队列化运行。
6. 在本轮收口中重写 `ActiveIntentService` 或拆独立认知开关。

## dependencies

1. [`docs/red_queen_phase1_closeout_plan.md`](./red_queen_phase1_closeout_plan.md)
2. [`docs/rq-001-cognitive-orchestration-boundary.md`](./rq-001-cognitive-orchestration-boundary.md)
3. [`docs/rq-005-cognitive-event-audit-model.md`](./rq-005-cognitive-event-audit-model.md)
4. 连续性主线已完成能力：
   - [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)
   - [OriginAgent/agent/context_assembler.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context_assembler.py)
5. 现有 producer / runtime：
   - [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
   - [OriginAgent/agent/agent_cognitive_runtime.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_cognitive_runtime.py)
   - [OriginAgent/agent/active_intents.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/active_intents.py)
   - [OriginAgent/agent/cognitive_scheduler.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_scheduler.py)
   - [OriginAgent/agent/cognitive_loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_loop.py)
   - [OriginAgent/agent/reminders.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/reminders.py)
   - [OriginAgent/agent/background_review.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/background_review.py)

## files_or_modules

本任务包当前以文档收口为主：

1. [`docs/red_queen_phase1_closeout_plan.md`](./red_queen_phase1_closeout_plan.md)
2. [`docs/p1-backend-cognition-integration-task-package.md`](./p1-backend-cognition-integration-task-package.md)

当前关键实现模块：

1. [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
2. [OriginAgent/agent/agent_cognitive_runtime.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_cognitive_runtime.py)
3. [OriginAgent/agent/active_intents.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/active_intents.py)
4. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
5. [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)

## integration_freeze

### 1. 主运行时链路

当前首选主链为：

```text
CronService / manual trigger
        ↓
CognitiveScheduler.run_once()
        ↓
AgentCognitiveRuntime.run_cognitive_pass_for_session()
        ↓
MessageBus.publish_inbound()
        ↓
AgentLoop._process_message()
```

补充说明：

1. `AgentCognitiveRuntime` 是实际的编排层，不应继续被文档隐去。
2. `CognitiveScheduler` 负责 session sweep 和 scheduler run 审计。
3. `AgentLoop` 继续是执行入口，不因 `Phase 1` 引入第二主循环。

### 2. fallback 路径

当前 fallback 路径为：

```text
AgentCognitiveRuntime.start_active_intent_loop()
        ↓
CognitiveLoop.run_forever()
        ↓
AgentCognitiveRuntime.run_cognitive_pass_for_session()
        ↓
MessageBus.publish_inbound()
        ↓
AgentLoop._process_message()
```

补充说明：

1. fallback 路径可运行，但不写 `scheduler_runs.jsonl`。
2. fallback 路径不等于“旧实现残留”，而是当前支持 `scheduler.mode != "cron"` 的兼容路径。

### 3. 当前 producer 集合

当前 `Phase 1` 的后台机会来源冻结为：

1. `ActiveIntentService._build_candidates()`
   - `goal_nudge`
   - `pending_confirmation_nudge`
   - `foresight_nudge`
2. `AgentLoop._collect_cognitive_candidates()` 中的 `ReminderStore.list_due()`
   - `scheduled_reminder`

补充说明：

1. `BackgroundReviewService` 当前不在主认知链中。
2. 未来新增 producer 时，应优先复用统一 `CognitiveEvent` / `CognitiveDecision` 合同。

### 4. `ActiveIntentService` API 面的当前现实

`ActiveIntentService` 当前存在两条有效路径：

1. `process_session()`
   - 自带 eligibility、cooldown、publish、ledger 逻辑
2. `collect_candidates() + build_message()`
   - 候选构建与消息构造

认知运行时当前统一使用第二条路径：

1. `_collect_cognitive_candidates()`
2. `AgentCognitiveRuntime.run_cognitive_pass_for_session()`

因此：

1. `process_session()` 当前不在 `Phase 1` 认知主链中使用。
2. 这属于 `Phase 1` 接受的技术债，不在本轮收口中改 API。

### 5. Eligibility、cooldown 与上限

当前已落地的行为冻结为：

1. 只有 `active_task_count == 0` 且 `running_subagents == 0` 的 session 会被认知扫描。
2. cooldown 规则复用 `ActiveIntentService` 的 session cooldown 与 intent cooldown。
3. 单轮单 session 的消息上限由 `max_messages_per_session_per_pass` 控制。
4. 超限 suppression 当前会记录为 `session_message_limit`。

### 6. working memory 与 introspection

当前最小写回现实为：

1. 带摘要事件会写入 `attention_items`
2. `pending_confirmation_nudge` 与 `scheduled_reminder` 会写入 `pending_questions`

当前 introspection 现实为：

1. 有 `latest_scan`
2. 有认知审计摘要
3. 有 scheduler runtime status
4. 没有“最近 N 条 suppression 明细列表”

### 7. 开关与兼容现实

当前启用口径冻结为：

1. `CognitiveLoopConfig.enabled` 与 `CognitiveSchedulerConfig.enabled` 结构已存在。
2. 运行时上它们统一绑定到 `allow_agent_initiated_messages`。
3. `_cognitive_loop_enabled` 是派生值，不是独立配置字段。
4. `RuntimeIntrospectionService.cognition_summary()` 会消费该派生值。

因此：

1. 关闭 `allow_agent_initiated_messages` 会同时关闭后台认知自动扫描。
2. 也会同时停止 `scheduled_reminder` 的自动投递。

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. 主路径与 fallback 路径的真实区别已冻结并写清楚。
2. `AgentCognitiveRuntime` 已被定义为正式编排运行时。
3. 至少四类高价值后台机会已明确接入统一编排层。
4. 后台投递结果仍走现有 `AgentLoop` 主路径。
5. `events.jsonl`、`decisions.jsonl` 与 `scheduler_runs.jsonl` 的产出边界已写清楚。
6. working memory 最小写回与 introspection 当前现实已写清楚。
7. feature flag / enablement 的当前绑定现实已写清楚，不再伪称存在独立运行时开关。

## tests

当前已有的主要证据包括：

1. 禁用时不启动后台认知循环。
2. `cron` 模式优先注册 scheduler job。
3. `fallback` 模式可启动本地认知任务。
4. due reminder 可被投递并写回 working memory。
5. cognitive event 仍走 `_process_message()` 主路径。
6. scheduler ledger 与 introspection 聚合可被读取。

当前应优先补足或显式核对的证据缺口包括：

1. `scheduler_runs.jsonl` 主路径与 fallback 路径差异的显式验证。
2. suppression reason 在双轨审计中的一致性验证。
3. 关闭 `allow_agent_initiated_messages` 后 reminder 自动投递停止的显式验证。
4. `_last_cognitive_scan` 与认知审计 / scheduler summary 的一致性验证。

## rollback_plan

若本任务包结论被证明不适用于后续实现，回滚方式应为：

1. 先关闭 `allow_agent_initiated_messages`。
2. 保留事件与审计落盘能力，停止真实投递。
3. 保留 working memory 结构，不强制消费后台认知结果。
4. 必要时退回到仅保留 `ActiveIntentService` 独立能力的运行模式。

## open_questions

当前 `Phase 1` 收口中，不再保留阻塞性开放问题。仅保留以下后续关注项：

1. 是否在后续阶段将双轨审计统一为单链模型。
2. 是否在后续阶段拆分独立 `enable_cognitive_loop` 配置。
3. 是否在后续阶段收敛 `ActiveIntentService` 的双路径 API 面。
