# RQ-004 任务包：工作记忆与 ContextAssembler v2 最小方案

Date: 2026-06-09
Status: Proposed
Scope: Phase 1 工作记忆与上下文构造骨架

## title

`RQ-004` 工作记忆与 `ContextAssembler` v2 最小方案

## goal

建立 Phase 1 可运行的上下文构造主骨架，让 OriginAgent 能显式维护“当前正在做什么”，并把它稳定地组装到每轮 LLM 输入中。

## scope

本任务包覆盖以下内容：

1. 新增最小 `WorkingMemoryManager`：
   - 读写当前工作记忆
   - 维护过期时间或更新时间
   - 提供覆盖与清理入口
2. 定义工作记忆最小数据模型：
   - `current_goal`
   - `current_plan`
   - `pending_questions`
   - `priority_facts`
   - `attention_items`
   - `tool_residue`
3. 定义工作记忆更新来源：
   - 当前 user turn
   - 当前 assistant plan/state
   - `ActiveIntentService` 候选事项
   - `ReminderStore` 到期提醒
   - 工具执行残留摘要
4. 设计并落地 `ContextAssembler` v2 最小组装顺序：
   - system prompt
   - runtime state block
   - working memory block
   - retrieved context block
   - world state block
   - recent dialogue messages
   - current user message
5. 定义最小预算与裁剪规则：
   - 对话视图保底
   - 工作视图稳定小预算
   - 检索视图按来源裁剪
   - 世界视图保留极小预算或空块
6. 提供最小调试/审计能力：
   - 查看当前工作记忆
   - 查看本轮 assembler 选入来源
   - 查看去重、过滤和裁剪结果摘要

## non_goals

本任务包不覆盖以下内容：

1. 向量检索接入。
2. 完整检索融合学习排序。
3. 工作记忆自动晋升到长期事实。
4. 遗忘策略的完整实现。
5. 真实世界视图数据接入。
6. 跨 session 漫游预热。

## dependencies

1. [`docs/rq-002-continuity-memory-os-boundary.md`](./rq-002-continuity-memory-os-boundary.md)
2. [`docs/rq-003-identity-scope-model.md`](./rq-003-identity-scope-model.md)
3. [`docs/continuity_memory_phase1_plan.md`](./continuity_memory_phase1_plan.md)
4. 当前运行时组件：
   - [OriginAgent/agent/context.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py)
   - [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
   - [OriginAgent/session/manager.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/session/manager.py)
   - [OriginAgent/session/search_index.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/session/search_index.py)
   - [OriginAgent/agent/active_intents.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/active_intents.py)
   - [OriginAgent/agent/reminders.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/reminders.py)

## files_or_modules

本任务包后续实现预计主要触达：

1. [OriginAgent/agent/context.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py)
2. [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
3. [OriginAgent/agent/active_intents.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/active_intents.py)
4. [OriginAgent/agent/reminders.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/reminders.py)
5. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
6. 候选新模块：
   - `OriginAgent/agent/working_memory.py`
   - `OriginAgent/agent/context_assembler.py`
7. 测试目录：
   - `tests/agent/`
   - `tests/session/`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. 运行时存在独立的工作记忆对象，而不是把任务状态散落在对话历史和系统提示中。
2. 至少一个主路径可以显式写入和读取工作记忆。
3. 每轮 LLM 输入都经过 `ContextAssembler` v2 或等价的分层组装逻辑。
4. 工作记忆、检索视图、对话视图在最终上下文中能区分来源。
5. 上下文裁剪后不会打破合法消息边界。
6. 调试接口可以输出当前工作记忆和本轮组装摘要。
7. 至少一个典型任务型对话案例能证明：即使对话尾部不再重复计划，系统仍能保留当前任务状态。

## tests

后续实现至少应覆盖以下测试：

1. 工作记忆 CRUD 测试：
   - 创建、覆盖、删除、过期。
2. 工作记忆来源测试：
   - 提醒项、待确认项、工具残留结果可进入工作记忆。
3. assembler 顺序测试：
   - 四层视图及 runtime block 顺序固定。
4. token/裁剪测试：
   - 工作视图、检索视图被裁剪时不会破坏 recent dialogue 的合法边界。
5. 去重测试：
   - 同一事实不同时存在于工作记忆和检索视图中造成明显重复。
6. 调试能力测试：
   - inspect 输出包含工作记忆、来源摘要、过滤结果。
7. 回归测试：
   - 未启用新路径时，旧 `ContextBuilder` 逻辑仍可工作。

## rollback_plan

若本任务包实现导致上下文质量下降或兼容问题，回滚方式应为：

1. 保留旧 `ContextBuilder` 路径作为 feature-flag fallback。
2. 先关闭 `ContextAssembler` v2 新路径，再评估是否保留工作记忆存储。
3. 工作记忆存储可保留为旁路调试数据，不强制注入 prompt。
4. 调试与审计接口可以保留，即使主运行路径回退。

## open_questions

1. 工作记忆应优先挂在 session metadata，还是独立 JSON/JSONL 文件。
2. `tool_residue` 应保存原始结果摘要，还是只保存下一步行动提示。
3. `pending_questions` 与现有 ask-user 机制如何避免重复表达。
4. `ContextAssembler` v2 是重构 `ContextBuilder` 内部实现，还是新建独立类后逐步切换调用方。
