# RQ-005 任务包：后台认知事件与审计合同冻结

Date: 2026-06-09
Status: Implemented, Closeout Validation Pending
Last Reviewed: 2026-06-13
Scope: 红后化总主线 `Phase 1` 的后台认知事件、决策、审计与 introspection 合同冻结

## title

`RQ-005` 后台认知事件与审计合同冻结

## goal

冻结统一的后台认知事件与审计合同，使系统可以回答：

1. 为什么某个后台机会被识别出来。
2. 为什么它被投递、被抑制或被跳过。
3. 哪些记录属于认知审计主链，哪些属于并存的 `ActiveIntentRecord` 现实。
4. introspection 当前真实能暴露到什么程度，哪些细节仍是显式 gap。

本任务包的上位收口口径见：

1. [`docs/red_queen_phase1_closeout_plan.md`](./red_queen_phase1_closeout_plan.md)

## scope

本任务包覆盖以下内容：

1. 冻结 `CognitiveEvent` 合同。
2. 冻结 `CognitiveDecision` 合同。
3. 冻结 append-only JSONL 认知审计主链。
4. 冻结 `scheduler_runs.jsonl` 的适用边界。
5. 冻结 introspection 当前已暴露的最小认知摘要口径。
6. 登记双轨审计与 suppression 解释链的显式差距。

## non_goals

本任务包不覆盖以下内容：

1. 完整认知排序器。
2. 世界模型冲突审计。
3. 动作规划审计。
4. 长期保留策略与数据清理完整实现。
5. 多租户或分布式审计存储。
6. `Phase 1` 内完成双轨审计合并。

## dependencies

1. [`docs/red_queen_phase1_closeout_plan.md`](./red_queen_phase1_closeout_plan.md)
2. [`docs/rq-001-cognitive-orchestration-boundary.md`](./rq-001-cognitive-orchestration-boundary.md)
3. 现有可复用模块：
   - [OriginAgent/agent/cognitive_events.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_events.py)
   - [OriginAgent/agent/cognitive_audit.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_audit.py)
   - [OriginAgent/agent/cognitive_scheduler.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_scheduler.py)
   - [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
   - [OriginAgent/agent/active_intents.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/active_intents.py)

## files_or_modules

本任务包当前以文档冻结为主：

1. [`docs/red_queen_phase1_closeout_plan.md`](./red_queen_phase1_closeout_plan.md)
2. [`docs/rq-005-cognitive-event-audit-model.md`](./rq-005-cognitive-event-audit-model.md)
3. [`docs/p1-backend-cognition-integration-task-package.md`](./p1-backend-cognition-integration-task-package.md)

当前对应的关键实现模块：

1. [OriginAgent/agent/cognitive_events.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_events.py)
2. [OriginAgent/agent/cognitive_audit.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_audit.py)
3. [OriginAgent/agent/cognitive_scheduler.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_scheduler.py)
4. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
5. [OriginAgent/agent/active_intents.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/active_intents.py)

## contract_freeze

### 1. `CognitiveEvent`

当前 `Phase 1` 已冻结的最小事件合同包括：

1. `event_id`
2. `session_key`
3. `event_type`
4. `source_type`
5. `source_reference`
6. `summary`
7. `priority`
8. `created_at`
9. `payload`

事件类型当前只允许：

1. `goal_nudge`
2. `pending_confirmation_nudge`
3. `scheduled_reminder`
4. `foresight_nudge`

### 2. `CognitiveDecision`

当前 `Phase 1` 已冻结的最小决策合同包括：

1. `decision_id`
2. `event_id`
3. `session_key`
4. `action`
5. `outcome`
6. `suppression_reason`
7. `cooldown_key`
8. `written_to_working_memory`
9. `published_internal_event`
10. `created_at`
11. `payload`

动作当前只允许：

1. `emit`
2. `suppress`
3. `skip`
4. `error`

结果当前只允许：

1. `emitted`
2. `suppressed`
3. `skipped`
4. `errored`

### 3. 认知审计主链

当前 `Phase 1` 已冻结的 append-only 审计主链包括：

1. `memory/cognitive/events.jsonl`
2. `memory/cognitive/decisions.jsonl`

其职责是：

1. 对后台认知机会做统一事件记录。
2. 对后台认知 policy 决策做统一决策记录。
3. 为 introspection 提供最近事件、最近决策、结果统计和 suppression reason 统计。

### 4. scheduler run 审计边界

当前 `Phase 1` 已冻结的 scheduler run 审计文件为：

1. `memory/cognitive/scheduler_runs.jsonl`

但必须明确：

1. 该文件只在 `CognitiveScheduler.run_once()` 被调用时写入。
2. 它覆盖的是 scheduler sweep 审计，而不是所有后台认知活动。
3. `CognitiveLoop` fallback 路径不写该文件。
4. 因此 fallback 路径下，只保证 `events.jsonl` 与 `decisions.jsonl` 存在，不保证新 `scheduler_runs.jsonl` 记录。

### 5. 双轨审计现实

当前实现中存在两条并存但未合并的审计链：

1. 认知审计主链：
   - `events.jsonl`
   - `decisions.jsonl`
2. `ActiveIntentService` 独立 ledger：
   - `memory/active_intents/records.jsonl`

这两条链在当前实现中的关系是：

1. 同一后台机会可能同时留下两类记录。
2. `CognitiveDecision` 与 `ActiveIntentRecord` 不是同一模型。
3. 本轮收口只冻结这种现实，不在 `Phase 1` 文档收口中合并。

### 6. introspection 当前口径

`RuntimeIntrospectionService.cognition_summary()` 当前已公开：

1. `enabled`
2. `latest_scan`
3. `config`
4. `scheduler`
5. `event_count`
6. `decision_count`
7. `latest_event`
8. `latest_decision`
9. `recent_event_types`
10. `recent_outcomes`
11. `outcome_counts`
12. `suppression_reason_counts`
13. `scheduler_run_count`
14. `latest_scheduler_run`
15. `scheduler_trigger_counts`

补充说明：

1. 当前只有 suppression reason 聚合计数，没有“最近 N 条 suppression 明细列表”。
2. `_cognitive_loop_enabled` 是派生运行时值，不是独立持久配置。

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. 后台认知事件模型已按当前实现冻结。
2. 后台认知决策与抑制模型已按当前实现冻结。
3. `events.jsonl`、`decisions.jsonl` 与 `scheduler_runs.jsonl` 的边界已写清楚。
4. 双轨审计现实已显式记录，不再伪装成单链统一模型。
5. introspection 当前已公开和未公开的口径已写清楚。
6. 后续 producer 若接入 `Phase 1` 合同，不需要另造第二套认知事件与决策对象形态。

## tests

本任务包本轮不要求直接新增测试，但应引用以下已有证据：

1. 事件与决策序列化/反序列化测试。
2. JSONL 落盘与读取测试。
3. scheduler ledger 汇总测试。
4. introspection 汇总字段测试。

后续 closeout patch 的优先补证方向包括：

1. suppression reason 在双轨审计中的一致性验证。
2. fallback 路径下 scheduler ledger 不新增记录的显式验证。
3. `latest_scan` 与审计摘要的一致性验证。

## rollback_plan

若本任务包合同冻结结论被证明不适用，回滚方式应为：

1. 保留旧事件格式兼容读取能力。
2. 新版本模型必须记录字段迁移说明。
3. 不允许直接无说明替换旧日志结构，避免后续观测断裂。

## open_questions

当前 `Phase 1` 收口中，不再保留阻塞性开放问题。仅保留以下后续关注项：

1. 双轨审计是否在后续阶段合并为统一账本模型。
2. `suppression_reason` 是否需要在 introspection 中暴露明细视图而非仅聚合计数。
3. `budget_bucket` 等更细粒度字段是否在后续阶段引入。
