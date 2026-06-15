# RQ-012 任务包：P3A 世界模型稳定化、长期晋升与多模态融合

Date: 2026-06-15
Status: Completed
Scope: Phase 3 `P3A` 多模态 world-state 合同稳定、长期晋升 gating 与 retrieval/prewarm/world 融合闭环

## title

`RQ-012` `P3A` 世界模型稳定化、长期晋升与多模态融合

## goal

在当前 `P2B` 运行时之上，把已经落地的 `world_state`、`memory_governance`、`retrieval_fusion`、`roaming_prewarm` 收敛成一条可验收闭环：

1. `audio / video / sensor` 复用同一套 `SceneSnapshot` / `InspectionResult` / `WorldSummary` 主线。
2. world-derived 内容不再以宽松 `user` scope 自动进入长期记忆，而是受 inspection、scope 与 contested gating 约束。
3. retrieval fusion 与 roaming prewarm 能真实消费 world summary / relationships / recent events 作为提示层，而不是维持僵尸接口。

## scope

本任务包覆盖以下内容：

1. 冻结 `SceneSnapshot.kind` 的 `image | audio | video | sensor` 多模态合同。
2. 在 `WorldSummary` 中新增并冻结 `relationships` 聚合字段。
3. 冻结 `InspectionResult.status` 的 `pending | completed | failed` 运行时口径。
4. 收紧 `MemoryGovernance.evaluate_turn()` 的 world-derived promotion gating：
   - `session` scope 不自动晋升
   - `contested` world summary 不自动晋升
   - 缺少 `completed inspection` 支撑的不自动晋升
5. 把 `world_summary_hints` 从 `ContextBuilder` 真正接入 `RetrievalFusion`。
6. 让 `RoamingPrewarmService` 复用 world `relationships` 与现有 world seed。

## non_goals

本任务包不覆盖以下内容：

1. 感知事件接入 `CognitiveLoop` 或 `MessageBus` 主链。
2. 动作 planner 或现实动作消费 world summary / world events。
3. watcher / daemon 常驻进程。
4. 完整 retention / permission 重构。
5. 多模态专用第二套对象树。

## dependencies

1. [`docs/continuity_memory_phase2_plan.md`](./continuity_memory_phase2_plan.md)
2. [`docs/rq-011-p2b-ingress-anomaly-bridge.md`](./rq-011-p2b-ingress-anomaly-bridge.md)
3. [`docs/continuity_memory_os_outline.md`](./continuity_memory_os_outline.md)

## files_or_modules

本任务包主要触达：

1. [OriginAgent/agent/world_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/world_state.py)
2. [OriginAgent/agent/context.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py)
3. [OriginAgent/agent/memory_governance.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/memory_governance.py)
4. [OriginAgent/agent/roaming_prewarm.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/roaming_prewarm.py)
5. 测试：
   - `tests/agent/test_continuity_phase1.py`
   - `tests/agent/test_retrieval_fusion.py`
   - `tests/tools/test_runtime_status_tools.py`
   - `tests/agent/test_dream.py`
   - `tests/agent/test_fact_store.py`
   - `tests/agent/test_nearline_pipeline.py`
   - `tests/session/test_session_search.py`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. `audio` 与 `sensor` 可按统一 snapshot 合同进入 world-state 主线。
2. `WorldSummary.relationships` 只聚合允许来源，不与 `focus` 形成无控制双写。
3. world-derived 内容不会在 `session` scope、`contested` 状态或缺少 `completed inspection` 支撑时自动晋升长期记忆。
4. `ContextBuilder` 已把 `world_summary_hints` 接到 `RetrievalFusion`。
5. `RoamingPrewarmService` 能复用 world `relationships` 作为预热种子。
6. raw perception events 仍不进入 prompt 明细，不进入 `MessageBus` 主链。

## tests

本任务包已通过以下收口证据：

1. `tests/agent/test_continuity_phase1.py`
   - `relationships` 聚合
   - `audio` / `sensor` 统一 ingress
   - `session` / `contested` / `pending inspection` 的 promotion gating
2. `tests/agent/test_retrieval_fusion.py`
   - `world_summary_hints` 真实进入 retrieval query
3. `tests/tools/test_runtime_status_tools.py`
   - introspection 继续暴露完整 world summary / relationships / recent events 视图
4. 关键回归集 `0 new failures`：
   - `tests/agent/test_dream.py`
   - `tests/agent/test_fact_store.py`
   - `tests/agent/test_nearline_pipeline.py`
   - `tests/session/test_session_search.py`

## rollback_plan

若本任务包引发回归，回滚顺序应为：

1. 先回退 world-derived promotion gating 的收紧逻辑与对应测试。
2. 再回退 `world_summary_hints` / prewarm world seed 的融合增强。
3. 最后才回退多模态 snapshot 合同扩展，避免破坏已完成的 `P2B` ingress 主线。

## open_questions

1. raw perception events 是否在后续阶段升级为独立 ledger。
2. `selection_reasons` / `attention_items` 是否需要共享正式 taxonomy。
3. `Phase 3+` 是否引入 retention / permission 更严格的 world-state 治理层。
