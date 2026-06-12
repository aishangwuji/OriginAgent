# RQ-009 任务包：世界模型最小查询接口与过期机制

Date: 2026-06-12
Status: Proposed
Scope: Phase 2 `P2A` 最小 `world_view` 读模型、过滤与 freshness 规则冻结

## title

`RQ-009` 世界模型最小查询接口与过期机制

## goal

为 continuity 主线冻结最小真实 `world_view` 数据源，使 OriginAgent 能从 snapshot 与 inspection 构造出短期、可过滤、可过期、可解释的 `WorldSummary`，并通过统一查询接口提供给 prompt 构造和 introspection。

本任务包完成后，系统应能稳定回答：

1. `world_view` 的最小查询入口是什么。
2. 哪些 snapshot 可以进入当前世界摘要，哪些必须因 freshness、scope 或 relevance 被排除。
3. `WorldSummary` 的 TTL、冲突和不确定性如何表达。
4. 为什么当前世界状态是一个短期读模型，而不是长期事实数据库。

## scope

本任务包覆盖以下内容：

1. 冻结最小 `WorldStateManager` 查询面：
   - `load`
   - `inspect`
   - `snapshot_prompt_payload`
   - `filtered_candidates`
   - `current_attention_items`
2. 定义 `WorldSummary` 生成规则：
   - 来自哪些 snapshot
   - 来自哪些 inspection
   - 如何聚合 `focus`
   - 如何聚合 `constraints`
   - 如何聚合 `uncertainties`
   - 如何生成 `contested_items`
3. 定义 freshness / TTL 规则：
   - snapshot freshness
   - summary freshness
   - 过期记录的过滤原则
4. 定义可见性与相关性规则：
   - `scope`
   - `owner_id`
   - `device_id`
   - 当前 message relevance
5. 定义最小 query surface 的使用者：
   - `ContextBuilder`
   - `ContextAssembler` audit
   - `RuntimeIntrospectionService`
   - working memory world attention 提取
6. 明确 `WorldSummary` 是短期读模型，不是长期事实，也不是原始快照列表直出。

## non_goals

本任务包不覆盖以下内容：

1. 完整 persistent world database。
2. 多房间 / household 全状态建模。
3. 高吞吐事件风暴处理。
4. 音频/视频/多传感器融合算法。
5. 复杂 planner 对 world model 的消费。
6. world summary 自动晋升长期事实的规则实现。

## dependencies

1. [`docs/continuity_memory_phase2_plan.md`](./continuity_memory_phase2_plan.md)
2. [`docs/rq-006-perception-layered-data-models.md`](./rq-006-perception-layered-data-models.md)
3. [`docs/rq-008-inspect-snapshot-tool-prototype.md`](./rq-008-inspect-snapshot-tool-prototype.md)
4. 当前 `P2A` 原型实现：
   - [OriginAgent/agent/world_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/world_state.py)
   - [OriginAgent/agent/context.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py)
   - [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)

## files_or_modules

本任务包后续实现预计主要触达：

1. [OriginAgent/agent/world_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/world_state.py)
2. [OriginAgent/agent/context.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py)
3. [OriginAgent/agent/context_assembler.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context_assembler.py)
4. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
5. 文档：
   - [`docs/rq-009-world-model-query-freshness.md`](./rq-009-world-model-query-freshness.md)
6. 测试目录：
   - `tests/agent/`
   - `tests/tools/`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. `world_view` 最小查询接口已冻结。
2. `WorldSummary` 的生成逻辑、TTL 规则和过滤语义已写清楚。
3. 已明确 snapshot freshness、summary freshness、scope visibility、message relevance 四类选择信号。
4. 已明确 `WorldSummary` 只输出世界摘要而非原始 snapshot 明细。
5. `ContextBuilder`、introspection 与 working memory world attention 可直接复用本任务包的 query surface。
6. 后续真实 ingress 和 `Phase 3` 扩展时，不需要推翻当前最小查询面。

## tests

本任务包为文档冻结任务，不要求立即新增自动化测试。

完成时应形成的后续测试约束包括：

1. summary 生成测试：
   - 单 snapshot
   - 多 snapshot
   - inspection 纠正后 contested 输出
2. freshness 测试：
   - 过期 snapshot 被排除
   - fresh summary 被保留
3. scope visibility 测试：
   - `device`
   - `session`
   - `user`
4. relevance 选择测试：
   - `relevant_to_message`
   - fallback
   - inspected override
5. prompt payload 不泄露原始媒体明细测试。

## rollback_plan

若本任务包定义被证明不适合后续 world model 演进，回滚方式应为：

1. 保留 `world_view` 对 prompt 的最小只读接口。
2. 允许内部聚合逻辑替换，但必须保留 freshness、scope 与 relevance 的解释出口。
3. 不允许直接回退为“把 snapshot 原始明细塞进 prompt”的实现方式。

## open_questions

1. `selection_reasons` 是否应在后续阶段标准化为有限枚举列表。
2. `current_attention_items` 是否需要单独的 TTL，而不是完全继承 `WorldSummary` freshness。
3. `WorldSummary.source_count` 是否足以覆盖未来多源融合观测，还是需要更细的 source breakdown。
