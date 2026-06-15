# RQ-006 任务包：感知分层数据模型设计

Date: 2026-06-12
Status: Completed
Scope: Phase 2 `P2A` 快照、深查与世界摘要三层数据模型冻结

## title

`RQ-006` 感知分层数据模型设计

## goal

冻结 `SceneSnapshot`、`InspectionResult`、`WorldSummary` 三类对象的最小字段、层级边界与职责语义，使当前 `P2A` 世界视图原型和后续真实感知 ingress 共用同一套基础表达。

本任务包完成后，系统应能稳定回答：

1. 一条感知记录到底是“原始快照摘要”“补充深查结果”还是“当前世界读模型”。
2. 哪些字段必须保留来源、时间、置信度与不确定性。
3. 哪些对象可以直接进入 prompt，哪些对象只能作为中间层或审计层保留。
4. 为什么世界摘要不是长期事实，也不应直接取代原始快照。

## scope

本任务包覆盖以下内容：

1. 冻结最小 `SceneSnapshot` 数据模型：
   - `snapshot_id`
   - `kind`
   - `source`
   - `scope`
   - `owner_id`
   - `device_id`
   - `captured_at`
   - `media_path`
   - `summary`
   - `objects`
   - `relationships`
   - `confidence`
   - `uncertainties`
   - `provenance`
2. 冻结最小 `InspectionResult` 数据模型：
   - `inspection_id`
   - `snapshot_id`
   - `requested_by`
   - `requested_at`
   - `confirmed`
   - `corrected`
   - `new_details`
   - `uncertain`
   - `confidence`
   - `status`
   - `contested`
   - `contested_reasons`
   - `evidence_excerpt`
   - `inspector`
3. 冻结最小 `WorldSummary` 数据模型：
   - `summary_id`
   - `scope`
   - `owner_id`
   - `generated_at`
   - `fresh_until`
   - `focus`
   - `constraints`
   - `uncertainties`
   - `source_snapshot_ids`
   - `inspection_ids`
   - `contested`
   - `contested_items`
   - `source_count`
   - `last_inspected_at`
4. 明确三层对象的职责边界：
   - `SceneSnapshot` 是一次快照摘要
   - `InspectionResult` 是对单个快照的补充核验层
   - `WorldSummary` 是当前短期世界状态读模型
5. 明确 provenance、confidence、uncertainty、contested 的最小表达要求。
6. 明确三层对象与 `FactStore` 的边界，禁止将 `WorldSummary` 直接等同为长期事实。

## non_goals

本任务包不覆盖以下内容：

1. 真实 camera / audio / sensor ingress 实现。
2. 完整 world-state 持久化方案。
3. 完整 ontology、room graph 或 household graph 设计。
4. 感知结果自动晋升长期事实的策略实现。
5. `Phase 3` 的多源融合算法。
6. 动作规划对世界模型的消费逻辑。

## dependencies

1. [`docs/red_queen_master_plan.md`](./red_queen_master_plan.md)
2. [`docs/continuity_memory_phase2_plan.md`](./continuity_memory_phase2_plan.md)
3. [`docs/continuity_memory_os_outline.md`](./continuity_memory_os_outline.md)
4. 当前 `P2A` 原型实现：
   - [OriginAgent/agent/world_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/world_state.py)
   - [OriginAgent/agent/snapshot_inspection.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/snapshot_inspection.py)
   - [OriginAgent/agent/context.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py)

## files_or_modules

本任务包预期主要触达文档层：

1. [`docs/continuity_memory_phase2_plan.md`](./continuity_memory_phase2_plan.md)
2. [`docs/rq-006-perception-layered-data-models.md`](./rq-006-perception-layered-data-models.md)

后续实现预计主要影响：

1. [OriginAgent/agent/world_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/world_state.py)
2. [OriginAgent/agent/snapshot_inspection.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/snapshot_inspection.py)
3. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
4. 测试目录：
   - `tests/agent/`
   - `tests/tools/`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. `SceneSnapshot`、`InspectionResult`、`WorldSummary` 的最小字段集合已冻结。
2. 三层对象的职责边界已写清楚，后续实现不再混用“快照”“深查”“世界摘要”三个概念。
3. provenance、confidence、uncertainty、contested 的最小要求已形成文档约束。
4. 已明确：`WorldSummary` 是读模型，不是长期事实，不直接回灌 `FactStore`。
5. 当前 `P2A` 原型和后续真实 ingress 可直接复用本任务包定义，而不需要另起一套感知对象模型。
6. `RQ-007`、`RQ-008`、`RQ-009`、`RQ-010` 可直接引用本任务包作为上位边界。

## tests

本任务包为文档冻结任务，不要求立即新增自动化测试。

完成时应形成的后续测试约束包括：

1. 数据模型序列化/反序列化测试：
   - `SceneSnapshot`
   - `InspectionResult`
   - `WorldSummary`
2. 字段完整性测试：
   - 置信度范围
   - 时间戳必填
   - provenance 结构保真
3. 边界测试：
   - 深查不直接覆盖原始快照
   - 世界摘要不直接写成长期事实
4. 不确定性与 contested 保真测试。

## rollback_plan

若本任务包定义被证明不适用于后续实现，回滚方式应为：

1. 保留当前三层对象的兼容读取能力。
2. 在新版本文档中显式记录字段变更、边界变更与迁移原因。
3. 不允许在未更新任务包与阶段计划前，让实现静默漂移为另一套对象体系。

## open_questions

1. `WorldSummary` 是否需要在后续阶段额外承载 `relationships` 聚合字段，还是继续保持最小 focus/constraints 结构。
2. `InspectionResult.status` 是否要尽早标准化为有限枚举，还是允许阶段性自由文本。
3. `SceneSnapshot.kind` 在 `P2A` 之外是否需要提前预留 `audio`、`video`、`sensor` 的更细分语义。
