# P1 任务包：后台认知闭环集成落地

Date: 2026-06-09
Status: Proposed
Scope: 红后化总主线 `Phase 1` 剩余部分的实现型集成任务包

## title

`P1 Integration` 后台认知闭环集成落地

## goal

在不引入真实感知设备的前提下，把现有 runtime 能力编排成一个最小可运行的后台认知闭环，并证明：

1. idle session 可以被受限巡检。
2. 后台高价值机会可以被识别、抑制、投递与审计。
3. 投递结果继续走现有 `AgentLoop` 主执行路径。
4. 认知结果可以最小写回 working memory 与 introspection。

## scope

本任务包覆盖以下内容：

1. 新增最小 `CognitiveLoop / CognitiveScheduler` 实现。
2. 对接统一的 `CognitiveEvent` / `CognitiveDecision` 模型。
3. 接入四类后台机会：
   - sustained goal
   - pending confirmation
   - scheduled reminder
   - nearline foresight
4. 接入统一 eligibility / cooldown / dedupe 策略。
5. 通过 `MessageBus` 投递内部 inbound event。
6. 将认知结果最小写回 working memory：
   - `attention_items`
   - `pending_questions`
   - 必要时 `tool_residue`
7. 为 introspection / runtime status 提供最近一次认知扫描摘要。

## non_goals

本任务包不覆盖以下内容：

1. 真实视觉或音频感知接入。
2. 世界模型查询与冲突治理。
3. 动作规划。
4. 完整优先级调度器。
5. 多进程、分布式或外部队列化运行。

## dependencies

1. [`docs/red_queen_phase1_remaining_plan.md`](./red_queen_phase1_remaining_plan.md)
2. [`docs/rq-001-cognitive-orchestration-boundary.md`](./rq-001-cognitive-orchestration-boundary.md)
3. [`docs/rq-005-cognitive-event-audit-model.md`](./rq-005-cognitive-event-audit-model.md)
4. 连续性主线已完成能力：
   - [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)
   - [OriginAgent/agent/context_assembler.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context_assembler.py)
5. 现有 producer / runtime：
   - [OriginAgent/agent/active_intents.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/active_intents.py)
   - [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
   - [OriginAgent/agent/background_review.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/background_review.py)
   - [OriginAgent/agent/reminders.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/reminders.py)

## files_or_modules

本任务包后续实现预计主要触达：

1. [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
2. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
3. [OriginAgent/agent/active_intents.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/active_intents.py)
4. [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)
5. 候选新模块：
   - `OriginAgent/agent/cognitive_loop.py`
   - `OriginAgent/agent/cognitive_events.py`
   - `OriginAgent/agent/cognitive_audit.py`
6. 测试目录：
   - `tests/agent/`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. 运行时存在独立、可关闭的后台认知循环。
2. 只有 idle session 会被后台认知巡检，busy session 不会被打扰。
3. 至少四类高价值后台机会可以进入统一编排层。
4. 至少一个后台机会会被投递为内部 inbound event 并走现有 `AgentLoop` 主路径。
5. 所有命中、抑制、跳过结果均有统一审计记录。
6. 后台认知结果可最小写回 working memory。
7. introspection 可展示最近一次认知扫描摘要、最近命中事件与抑制原因。
8. 在 feature flag 关闭时，系统行为与当前版本保持兼容。

## tests

后续实现至少应覆盖以下测试：

1. eligibility 测试：
   - active foreground turn 时不触发
   - running subagent 时不触发
2. cooldown / dedupe 测试：
   - per-session cooldown
   - per-intent cooldown
   - 单次扫描消息上限
3. producer 命中测试：
   - goal nudge
   - pending confirmation nudge
   - scheduled reminder
   - foresight nudge
4. internal event 路径测试：
   - 进入 `MessageBus`
   - 继续走 `_process_message()`
5. working memory 写回测试：
   - `attention_items`
   - `pending_questions`
6. introspection / audit 回归测试。

## rollback_plan

若本任务包实现导致后台噪音、解释性或兼容性问题，回滚方式应为：

1. 先关闭 `enable_cognitive_loop` feature flag。
2. 保留事件与审计落盘能力，停止真实投递。
3. 保留 working memory 结构，不强制消费后台认知结果。
4. 必要时退回到仅保留现有 `ActiveIntentService` 的运行模式。

## open_questions

1. `ActiveIntentService` 是继续直接被 `AgentLoop` 启动，还是在 integration 实现中改由 `CognitiveLoop` 编排启动。
2. `BackgroundReviewService` 在本阶段是否需要输出“认知候选摘要”，还是保持完全旁路。
3. 未来 `Phase 2` 的感知异常事件是否直接复用本任务包的 `CognitiveEvent` 模型。
