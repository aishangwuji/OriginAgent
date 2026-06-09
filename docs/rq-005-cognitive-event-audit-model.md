# RQ-005 任务包：后台认知事件模型与审计模型设计

Date: 2026-06-09
Status: Proposed
Scope: 红后化总主线 `Phase 1` 的后台认知事件建模、决策建模与审计落盘

## title

`RQ-005` 后台认知事件模型与审计模型设计

## goal

建立统一的后台认知事件与审计模型，使系统可以回答：

1. 为什么某个后台机会被识别出来。
2. 为什么它被投递、被抑制或被跳过。
3. 它影响了哪个 session、哪个 working memory、哪个内部事件通道。
4. 后续进入 `Phase 2` 时，感知异常与世界状态变化如何接入同一解释链。

## scope

本任务包覆盖以下内容：

1. 定义最小 `CognitiveEvent` 模型，建议字段包括：
   - `event_id`
   - `session_key`
   - `event_type`
   - `source_type`
   - `source_reference`
   - `summary`
   - `priority`
   - `created_at`
2. 定义最小 `CognitiveDecision` 模型，建议字段包括：
   - `decision_id`
   - `event_id`
   - `action`
   - `outcome`
   - `suppression_reason`
   - `cooldown_key`
   - `written_to_working_memory`
   - `published_internal_event`
   - `created_at`
3. 定义最小审计存储形式：
   - append-only JSONL
   - 事件与决策分离记录
4. 定义最小 suppression / dedupe 解释字段。
5. 定义最小 introspection 汇总口径：
   - 最近一次扫描
   - 最近命中的事件
   - 最近被抑制的原因

## non_goals

本任务包不覆盖以下内容：

1. 完整认知排序器。
2. 世界模型冲突审计。
3. 动作规划审计。
4. 长期保留策略与数据清理完整实现。
5. 多租户或分布式审计存储。

## dependencies

1. [`docs/red_queen_master_plan.md`](./red_queen_master_plan.md)
2. [`docs/red_queen_phase1_remaining_plan.md`](./red_queen_phase1_remaining_plan.md)
3. [`docs/rq-001-cognitive-orchestration-boundary.md`](./rq-001-cognitive-orchestration-boundary.md)
4. 现有可复用模块：
   - [OriginAgent/agent/active_intents.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/active_intents.py)
   - [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
   - [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)

## files_or_modules

本任务包后续实现预计主要触达：

1. 候选新模块：
   - `OriginAgent/agent/cognitive_events.py`
   - `OriginAgent/agent/cognitive_audit.py`
2. 现有模块：
   - [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
   - [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
3. 文档：
   - [`docs/rq-005-cognitive-event-audit-model.md`](./rq-005-cognitive-event-audit-model.md)

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. 后台认知事件模型已冻结，后续 producer 可复用同一对象形态。
2. 后台认知决策与抑制模型已冻结。
3. 至少可区分以下结果：
   - emitted
   - suppressed
   - skipped
   - errored
4. 事件与决策日志字段足够支撑事后解释。
5. introspection 汇总口径已定义，后续实现可直接落盘和汇总。
6. 感知阶段后续新增 producer 时，不需要另造第二套审计模型。

## tests

本任务包为设计任务，不要求立即新增自动化测试。

完成时应形成的后续测试约束包括：

1. 事件序列化/反序列化测试。
2. 审计落盘追加测试。
3. suppression reason 保真测试。
4. introspection 汇总字段完整性测试。

## rollback_plan

若本任务包模型设计被证明不适用，回滚方式应为：

1. 保留旧事件格式兼容读取能力。
2. 新版本模型必须记录字段迁移说明。
3. 不允许直接无说明替换旧日志结构，避免后续观测断裂。

## open_questions

1. `CognitiveDecision` 是否需要一开始就带 `budget_bucket` 字段，还是在实现阶段再补。
2. 后台认知事件与现有 `ActiveIntentRecord` 是否保留双轨，还是逐步迁移到统一事件模型。
3. 审计日志是否需要在 `Phase 1` 就按 session 分片，还是先统一单文件 JSONL。
