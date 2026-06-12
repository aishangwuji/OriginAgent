# RQ-008 任务包：`inspect_snapshot` 深查工具原型

Date: 2026-06-12
Status: Proposed
Scope: Phase 2 `P2A` 粗略快照到按需深查的最小工具路径冻结

## title

`RQ-008` `inspect_snapshot` 深查工具原型

## goal

建立“粗略摘要 + 按需深查”的最小可用工具路径，使 OriginAgent 能对单个 snapshot 进行回溯式补充核验，并把结果写回 `InspectionResult` 与 `WorldSummary`，而不是直接覆盖原始 snapshot。

本任务包完成后，系统应能稳定回答：

1. `inspect_snapshot` 的输入、输出与运行前提是什么。
2. 深查结果如何区分确认、修正、新增、不确定。
3. 深查结果如何写回世界摘要而不污染原始快照。
4. 在 inspection provider 缺失时，系统应如何优雅降级。

## scope

本任务包覆盖以下内容：

1. 冻结 `inspect_snapshot` 的最小输入契约：
   - `snapshot_id`
   - active session context
   - runtime identity context
2. 冻结 `inspect_snapshot` 的最小输出契约：
   - `snapshot`
   - `inspection`
   - `world_summary`
   - `inspection_path`
3. 冻结 `InspectionResult` 的最小语义输出：
   - `confirmed`
   - `corrected`
   - `new_details`
   - `uncertain`
   - `confidence`
   - `status`
   - `contested`
   - `contested_reasons`
   - `evidence_excerpt`
4. 明确工具路径分层：
   - 优先走 `SnapshotInspectionService`
   - 失败时走最小 fallback
   - 必须继续写回统一 `InspectionResult` 结构
5. 明确深查结果与原始 snapshot 的关系：
   - 深查是补充层，不是原地覆盖
   - 深查可影响 `WorldSummary`
   - 深查应保留来源与 inspector 信息
6. 明确 `P2A` 阶段的降级规则与失败输出语义。

## non_goals

本任务包不覆盖以下内容：

1. 批量深查或高吞吐 inspection pipeline。
2. 专门的第二 provider 栈。
3. 复杂多轮 inspection dialog。
4. 视频片段或音频片段的深查策略。
5. inspection 结果自动晋升长期事实。
6. 复杂审查队列与抢占调度。

## dependencies

1. [`docs/continuity_memory_phase2_plan.md`](./continuity_memory_phase2_plan.md)
2. [`docs/rq-006-perception-layered-data-models.md`](./rq-006-perception-layered-data-models.md)
3. [`docs/rq-007-visual-snapshot-ingress-retention.md`](./rq-007-visual-snapshot-ingress-retention.md)
4. 当前 `P2A` 原型实现：
   - [OriginAgent/agent/snapshot_inspection.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/snapshot_inspection.py)
   - [OriginAgent/agent/world_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/world_state.py)
   - [OriginAgent/agent/tools/runtime_status.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/tools/runtime_status.py)

## files_or_modules

本任务包后续实现预计主要触达：

1. [OriginAgent/agent/snapshot_inspection.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/snapshot_inspection.py)
2. [OriginAgent/agent/world_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/world_state.py)
3. [OriginAgent/agent/tools/runtime_status.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/tools/runtime_status.py)
4. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
5. 文档：
   - [`docs/rq-008-inspect-snapshot-tool-prototype.md`](./rq-008-inspect-snapshot-tool-prototype.md)
6. 测试目录：
   - `tests/agent/`
   - `tests/tools/`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. `inspect_snapshot` 的输入输出契约已冻结。
2. 已明确深查结果的四类最小语义：确认、修正、新增、不确定。
3. 已明确深查结果写回 `InspectionResult` 与 `WorldSummary` 的关系，而不是覆盖原始 snapshot。
4. 工具的服务路径与 fallback 路径已形成统一边界。
5. `RQ-009` 与 `RQ-010` 可以直接消费本任务包定义的 inspection 语义与写回行为。
6. 当前 `P2A` 原型后续增强时，不需要重新发明第二套 snapshot inspection 契约。

## tests

本任务包为文档冻结任务，不要求立即新增自动化测试。

完成时应形成的后续测试约束包括：

1. 工具契约测试：
   - 缺 session context 报错
   - 缺 runtime identity 报错
   - snapshot 不存在时报错
2. provider 可用路径测试：
   - service path
   - fallback path
3. 写回测试：
   - `InspectionResult` 记录写回
   - `WorldSummary` 被相应更新
4. contested / uncertainty 保真测试。

## rollback_plan

若本任务包定义被证明不适用于后续 inspection 演进，回滚方式应为：

1. 保留当前 `inspect_snapshot` 工具名与最小输入输出兼容层。
2. 允许内部 service 替换，但不允许静默改变调用方看到的核心语义字段。
3. 在新版本文档中显式记录 service path 与 fallback path 的变更原因。

## open_questions

1. `inspection_path` 是否需要在后续阶段扩展为更细的 execution backend 分类。
2. `requested_by` 是否应统一映射为有限枚举，而不是保留当前 runtime source 文本。
3. 失败型 inspection 是否需要在后续阶段单独计入 cognition / world-state 审计口径。
