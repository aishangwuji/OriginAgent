# RQ-002 任务包：连续性与记忆操作系统边界定义

Date: 2026-06-09
Status: Proposed
Scope: Phase 1 文档冻结与边界收敛

## title

`RQ-002` 连续性与记忆操作系统边界定义

## goal

冻结 Phase 1 的连续性主线边界，明确：

1. 一轮推理到底由哪些上下文层组成。
2. 各层负责什么，不负责什么。
3. 工作记忆、对话历史、检索召回、世界摘要之间如何隔离。
4. 哪些规则属于 Phase 1 必须落地，哪些延后到后续阶段。

## scope

本任务包覆盖以下内容：

1. 将四层运行视图正式定义为 Phase 1 的上下文治理基线：
   - `dialogue_view`
   - `retrieval_view`
   - `working_view`
   - `world_view`
2. 明确对话视图与工作视图的职责边界：
   - 对话视图只包含原始消息流。
   - 工作视图只包含结构化工作状态。
3. 明确检索视图的最小责任：
   - 只放与当前问题相关的召回片段。
   - 不回灌整段历史。
4. 明确世界视图在 Phase 1 的地位：
   - 保留接口与预算插槽。
   - 不接入真实感知数据。
5. 明确作用域传播安全规则：
   - 默认禁止 `session -> user -> household -> global` 自动向上传播。
   - 仅允许显式 `allow_propagation` 的对象跨作用域晋升。
6. 明确最小可观测性要求：
   - 能解释某轮上下文包含了哪些块、来源为何、过滤为何发生。

## non_goals

本任务包不覆盖以下内容：

1. `IdentityResolver` 的具体实现。
2. `ScopeResolver` 的具体实现。
3. `WorkingMemoryManager` 的具体实现。
4. `ContextAssembler` v2 的代码实现。
5. 向量检索、完整排序器、晋升与遗忘算法。
6. 感知总线、世界模型存储和动作层接入。

## dependencies

1. [`docs/continuity_memory_os_outline.md`](./continuity_memory_os_outline.md)
2. [`docs/continuity_memory_phase1_plan.md`](./continuity_memory_phase1_plan.md)
3. [`docs/red_queen_master_plan.md`](./red_queen_master_plan.md)

## files_or_modules

本任务包预期主要触达文档层：

1. [`docs/continuity_memory_os_outline.md`](./continuity_memory_os_outline.md)
2. [`docs/continuity_memory_phase1_plan.md`](./continuity_memory_phase1_plan.md)
3. [`docs/rq-002-continuity-memory-os-boundary.md`](./rq-002-continuity-memory-os-boundary.md)

如后续实现需要同步接口命名，主要影响面预计为：

1. [OriginAgent/agent/context.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py)
2. [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
3. 候选新模块：`OriginAgent/agent/context_assembler.py`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. Phase 1 的四层运行视图定义已冻结，后续实现不再重新命名或重新切分。
2. 对话视图、工作视图、检索视图、世界视图的输入输出边界已写清楚。
3. 工作记忆“不是对话摘要，不是长期记忆，不是检索结果缓存”的边界已写清楚。
4. Phase 1 的作用域传播安全规则已形成明确默认值。
5. 可观测性最小要求已写成实施约束，而不是可选增强项。
6. `RQ-003`、`RQ-004` 可以直接引用本任务包作为上位边界，不再重复定义核心术语。

## tests

本任务包为文档冻结任务，不要求新增自动化测试。

完成时应进行以下校验：

1. 文档审阅检查：`RQ-003` 与 `RQ-004` 中的术语与本任务包一致。
2. 边界一致性检查：Phase 1 计划书、连续性提纲、总计划书中不存在互相冲突的 Phase 1 定义。
3. 实施前检查：后续代码任务引用的运行视图命名和本任务包一致。

## rollback_plan

若本任务包结论被证明不适合后续实现，回滚方式应为：

1. 保留本任务包历史版本。
2. 在新版本中显式记录被推翻的边界与原因。
3. 不允许在未更新文档的情况下，直接让实现偏离本任务包定义。

## open_questions

1. `world_view` 在 Phase 1 是否应永远输出空块，还是允许输出静态占位摘要。
2. `runtime state block` 是否视为四层视图之外的外围元信息，而不是第五层视图。
3. `ContextBuilder` 是逐步被 `ContextAssembler` v2 包裹，还是最终只保留系统提示职责。
