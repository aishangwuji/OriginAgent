# RQ-011 任务包：P2B 真实 Ingress 与感知异常桥接

Date: 2026-06-15
Status: In Progress
Scope: Phase 2 `P2B` 真实 producer ingress 合同、world-state 事件候选与 introspection 暴露

## title

`RQ-011` P2B 真实 Ingress 与感知异常桥接

## goal

把 `P2A` 的 path-first 文件快照原型推进到 `P2B`：

1. 外部 producer 可以通过稳定合同把完成态媒体与 sidecar 送入 `WorldStateManager`。
2. `WorldStateManager` 在继续产出 `WorldSummary` 的同时，增加最小 `PerceptionEventCandidate` 事件候选层。
3. 这些事件先进入 introspection / audit 可见范围，不桥接 `CognitiveLoop`、`MessageBus` 主链或动作层。

## scope

本任务包覆盖以下内容：

1. 在 [OriginAgent/agent/world_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/world_state.py) 中新增 producer batch ingest 入口。
2. 保持 `SceneSnapshot` / `InspectionResult` / `WorldSummary` 现有合同不拆不重命名，只为 `WorldStateSnapshot` 增加事件列表。
3. 新增 `PerceptionEventCandidate` 最小字段：
   - `event_id`
   - `kind`
   - `snapshot_id`
   - `inspection_id`
   - `summary`
   - `confidence`
   - `contested`
   - `created_at`
   - `source`
   - `scope`
   - `owner_id`
   - `provenance`
4. 冻结本轮事件类型：
   - `snapshot_ingested`
   - `inspection_changed_summary`
   - `contested_world_state`
   - `uncertain_world_state`
5. 在 [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py) 的 `views.world` 中新增：
   - `recent_events`
   - `event_summary`
6. 保持 continuity prompt 注入只消费 `WorldSummary`，不把事件明细塞进 prompt。

## non_goals

本任务包不覆盖以下内容：

1. watcher / poller 常驻进程。
2. 非 image 文件的 snapshot 物化扩展。
3. 感知事件接入 `CognitiveLoop` producer。
4. 事件独立 ledger 或 `cognitive` 审计 JSONL 写入。
5. 世界模型到长期事实层的晋升规则。
6. 动作 planner 或现实动作消费这些事件。

## dependencies

1. [`docs/continuity_memory_phase2_plan.md`](./continuity_memory_phase2_plan.md)
2. [`docs/attachments-ingress.md`](./attachments-ingress.md)
3. [`docs/rq-009-world-model-query-freshness.md`](./rq-009-world-model-query-freshness.md)
4. [`docs/rq-010-world-summary-continuity-bridge.md`](./rq-010-world-summary-continuity-bridge.md)

## files_or_modules

本任务包主要触达：

1. [OriginAgent/agent/world_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/world_state.py)
2. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
3. 测试：
   - `tests/agent/test_continuity_phase1.py`
   - `tests/tools/test_runtime_status_tools.py`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. `WorldStateManager` 新增 batch/producer 入口，能消费 `uploads/`、`inbox/` 的完成态 image 文件。
2. `.part` 文件不会被 ingest，相同 `media_path` 重复 ingest 不生成重复 snapshot。
3. `WorldStateSnapshot` 能保留最近事件候选，并在 snapshot pruning 后同步裁剪无源事件。
4. `inspect_context().views.world` 能输出 `recent_events` 与 `event_summary`。
5. `world_state_context` 继续只注入 `WorldSummary`，不注入事件明细。
6. `tests/agent/test_continuity_phase1.py` 与 `tests/tools/test_runtime_status_tools.py` 针对本任务包新增的证据全部通过，且无新增 failures。

## tests

本任务包新增/锁定以下自动化证据：

1. batch ingress：
   - `uploads/` / `inbox/` 完成态 image 文件可物化
   - `.part` 文件跳过
   - 相同 `media_path` 重复 ingest 去重
2. 事件候选：
   - 首次 ingest 生成 `snapshot_ingested`
   - inspection 修正生成 `inspection_changed_summary`
   - contested 从无到有生成 `contested_world_state`
   - uncertainty 从无到有生成 `uncertain_world_state`
3. introspection：
   - `views.world.recent_events`
   - `views.world.event_summary`
   - 原有 `selection_reasons` / `filtered_candidates` / `attention_write` 不回归

## rollback_plan

若本任务包引发 world-state 回归，回滚应按以下顺序进行：

1. 先移除 `recent_events` / `event_summary` 的 introspection 输出。
2. 再移除 `PerceptionEventCandidate` 生成逻辑，保留 batch ingress 入口。
3. 最后才回退 batch ingress 入口本身，避免破坏 `P2A` 已收口的 snapshot 主线。

## open_questions

1. `PerceptionEventCandidate` 是否在 `P2C / Phase 3` 升级为独立 ledger。
2. 非 image 的 `audio / video / sensor` 是否复用本事件模型，还是补新的 snapshot 子类合同。
3. `RQ-006` 延后问题中的 `InspectionResult.status` 是否在下轮收敛为正式枚举。
