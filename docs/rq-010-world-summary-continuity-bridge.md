# RQ-010 任务包：感知结果向 continuity 主线桥接规则

Date: 2026-06-12
Status: Proposed
Scope: Phase 2 `P2A` world summary 注入 prompt、working memory 与 introspection 的桥接边界冻结

## title

`RQ-010` 感知结果向 continuity 主线桥接规则

## goal

把 `WorldSummary` 真正收敛进 continuity 主线，使 OriginAgent 能在不污染长期事实、不复制原始快照明细的前提下，让当前任务稳定消费世界摘要，并通过 working memory 与 introspection 暴露最小环境关注点。

本任务包完成后，系统应能稳定回答：

1. 哪些 world summary 可以进入当前 prompt。
2. 哪些 world attention 可以写入 working memory。
3. introspection 和 context audit 如何解释世界视图的注入与过滤。
4. 世界摘要如何与 retrieval、working memory、recent dialogue 保持去重与角色分工。

## scope

本任务包覆盖以下内容：

1. 冻结 `WorldSummary` 注入 continuity 主线的最小位置：
   - runtime state block 之后
   - continuity blocks 内，与 `continuity_context`、`working_memory` 同组
   - reference blocks 之前
   - current user message 之前
2. 定义 world summary 进入 prompt 的最小条件：
   - freshness
   - scope visibility
   - relevance to current task / message / attention
3. 定义 world-derived attention 写入 working memory 的最小规则：
   - contested 优先
   - uncertainty 次之
   - focus 作为补充
   - 去重与数量上限
4. 定义 introspection / audit 输出口径：
   - `views.world.summary`
   - `views.world.filtered_candidates`
   - `views.world.freshness`
   - `selection_reasons`
   - world-derived attention 的写回效果
5. 定义与 retrieval / working memory 的去重与角色边界：
   - retrieval 保持参考上下文职责
   - world view 保持短期环境摘要职责
   - working memory 只保留当前相关关注点
6. 明确 `P2A` 阶段的 bridge 边界：
   - 允许 prompt 注入与工作记忆联动
   - 不允许直接自动晋升长期事实
   - 不允许自动触发现实动作

## non_goals

本任务包不覆盖以下内容：

1. 感知异常到 `CognitiveLoop` producer 的完整接入。
2. 动作 planner 对 world summary 的消费。
3. 长期记忆晋升与遗忘策略。
4. 多源融合后的复杂冲突仲裁。
5. 完整的 prompt learning ranker。
6. household / room 级跨域共享策略。

## dependencies

1. [`docs/continuity_memory_phase2_plan.md`](./continuity_memory_phase2_plan.md)
2. [`docs/rq-004-working-memory-context-assembler-v2.md`](./rq-004-working-memory-context-assembler-v2.md)
3. [`docs/rq-009-world-model-query-freshness.md`](./rq-009-world-model-query-freshness.md)
4. 当前 `P2A` 原型实现：
   - [OriginAgent/agent/context.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py)
   - [OriginAgent/agent/context_assembler.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context_assembler.py)
   - [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)
   - [OriginAgent/agent/world_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/world_state.py)
   - [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)

## files_or_modules

本任务包后续实现预计主要触达：

1. [OriginAgent/agent/context.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py)
2. [OriginAgent/agent/context_assembler.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context_assembler.py)
3. [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)
4. [OriginAgent/agent/world_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/world_state.py)
5. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
6. 文档：
   - [`docs/rq-010-world-summary-continuity-bridge.md`](./rq-010-world-summary-continuity-bridge.md)
7. 测试目录：
   - `tests/agent/`
   - `tests/tools/`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. 已明确 `WorldSummary` 进入 continuity 主线的最小注入位置与选择条件。
2. 已明确 world-derived attention 进入 working memory 的最小规则、优先级与上限。
3. 已明确 introspection / audit 应输出哪些世界视图注入与过滤信息。
4. 已明确 world view、retrieval、working memory 三者的职责边界与去重原则。
5. 已明确当前阶段只允许 prompt 注入和 working memory 联动，不允许直接晋升长期事实或触发现实动作。
6. 后续感知异常接入认知层或动作层时，可直接复用本任务包的主线桥接边界。

## tests

本任务包为文档冻结任务，不要求立即新增自动化测试。

完成时应形成的后续测试约束包括：

1. prompt 注入测试：
   - `world_state_context` 位置正确
   - placeholder 被真实摘要替换
2. attention 写回测试：
   - contested 优先
   - uncertainty 保留
   - 去重与数量上限
3. introspection 测试：
   - `views.world.summary`
   - `views.world.filtered_candidates`
   - `views.world.freshness`
4. 去重测试：
   - retrieval 与 world view 重复内容不明显双写
   - working memory 不复制整段 `WorldSummary`

## rollback_plan

若本任务包定义被证明不适合后续 continuity 演进，回滚方式应为：

1. 保留 world summary 只读注入能力。
2. 允许先关闭 world-derived attention 写回，而不回退整个 world view 查询面。
3. 保留 introspection 输出，避免失去世界视图可解释性。
4. 不允许回退为“把环境摘要散落到系统提示或 recent dialogue 中”的隐式做法。

## open_questions

1. world-derived attention 的优先级是否需要在后续阶段与 pending confirmation / reminder 做统一预算协调。
2. `selection_reasons` 与 `attention_items` 是否需要共享一套更正式的 reason taxonomy。
3. `Phase 3` 感知异常是否直接复用本任务包的 world-to-working-memory bridge，还是单独走认知 producer。
