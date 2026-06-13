# Meta Cognition Runbook

Date: 2026-06-13
Status: Active
Audience: operator / developer

## Purpose

这份 runbook 用于收官后的日常验收、排障和操作，覆盖：

1. 如何查看元认知运行状态。
2. 如何读取 `patterns.jsonl` / `evolution_seeds.jsonl`。
3. 如何检查 working memory / `memory_candidates` / signal bridge 是否产生噪音。
4. 如何通过 `originagent_evolution_control`、`inspect_signal`、`suppress_signal`、`resume_signal` 操作 meta-origin signal。
5. 如何按既定顺序回退这条总线。

## Runtime Views

优先使用只读运行时视图确认当前状态：

1. `originagent_runtime_status`
   - 关注整体系统状态和 `meta_cognition` 摘要。
2. `originagent_inspect_context`
   - 关注 `views.meta_cognition`、retrieval 视图和 working memory 视图。
3. `RuntimeIntrospectionService.meta_cognition_summary()`
   - 关键字段：
   - `structured_reflection_enabled`
   - `pattern_consolidation_enabled`
   - `evolution_bridge_enabled`
   - `recent_journals`
   - `recent_reflections`
   - `recent_confidence_traces`
   - `recent_patterns`
   - `recent_evolution_seeds`
   - `working_memory_bridge`
   - `memory_candidate_bridge`
   - `bridge_decision_counts`
   - `last_signal_upserts`

判断标准：

1. feature flag 关闭时，以上字段应稳定存在，但列表为空、bridge 状态为 disabled。
2. feature flag 打开且 sidecar 有效运行时，`recent_*` 字段应可见最近 redacted preview。
3. 若 `last_signal_upserts` 持续为空，但 `recent_patterns` 持续增长，优先检查 `evolution_bridge_enabled`、signal 抑制状态和阈值配置。

## Artifact Files

元认知 sidecar 的审计文件位于：

`memory/meta_cognition/`

核心文件：

1. `triggers.jsonl`
2. `decisions.jsonl`
3. `journals.jsonl`
4. `reflections.jsonl`
5. `confidence_traces.jsonl`
6. `patterns.jsonl`
7. `evolution_seeds.jsonl`

重点阅读方式：

1. `patterns.jsonl`
   - 看 `pattern_id`、`pattern_key`、`owner_id`、`trigger_types`、`capability_domain`、`severity`、`frequency`、`distinct_turn_count`、`candidate_target_type`、`summary`
   - 预期只包含 redacted summary 和 ref，不包含 raw prompt / history / payload
2. `evolution_seeds.jsonl`
   - 看 `seed_id`、`pattern_id`、`change_target_type`、`target_key`、`title`、`summary`、`confidence`、`severity`、`evidence_refs`
   - 预期首行 summary 带 `Origin: meta_cognition`
3. `decisions.jsonl`
   - 看去重、cooldown、turn limit、suppression 是否生效
4. `journals.jsonl` / `reflections.jsonl`
   - 仅用于排查桥接前的结构化产物是否生成，不用于读取原始推理过程

## Signal Operations

受治理演进入口优先使用：

`originagent_evolution_control`

常用只读操作：

1. `operation=status`
2. `operation=list_signals`
3. `operation=list_recommendations`
4. `operation=inspect_signal`

常用写操作：

1. `operation=execute_action, action_kind=suppress_signal, target_id=<opportunity_id>, reason=<why>`
2. `operation=execute_action, action_kind=resume_signal, target_id=<opportunity_id>, reason=<why>`

兼容入口：

1. `my action=inspect_signal key=<opportunity_id>`
2. `my action=suppress_signal key=<opportunity_id>`
3. `my action=resume_signal key=<opportunity_id>`

操作顺序建议：

1. 先 `list_signals` 找到 target。
2. 再 `inspect_signal` 看 `summary`、`evidence_sources`、状态和 operator guidance。
3. 若确认噪音过高，再 `suppress_signal`。
4. 只有在确认压制过头或阈值已调整后，才 `resume_signal`。

## Soak Checklist

做真实样本 soak 时，按以下顺序观察：

1. 开启：
   - `structured_reflection_enabled`
   - `working_memory_bridge_enabled`
   - `memory_candidate_bridge_enabled`
   - `pattern_consolidation_enabled`
   - `evolution_bridge_enabled`
2. 回放至少三类样本：
   - `tool_failure(error)`
   - `user_correction`
   - `complete_goal` 成功完成
3. 记录：
   - `meta_cognition_summary()`
   - `memory/meta_cognition/*.jsonl`
   - working memory 追加项
   - `memory_candidates` 入队记录
   - `OpportunitySignalStore` 的 open / suppressed 数
   - `last_signal_upserts`

噪音判断：

1. working memory 只应出现轻量 caution / pending question。
2. `memory_candidates` 不应由普通失败日志直接入队。
3. 单 turn 默认最多一个 signal upsert。
4. 被 suppress 的 signal 不应在后续 turn 重复 upsert。

## Threshold Freeze

阈值定稿按固定顺序进行：

1. `memory_candidate_min_confidence`
2. `pattern_min_frequency`
3. `pattern_min_distinct_turns`
4. `max_signal_upserts_per_turn`

规则：

1. 每次只调一个阈值。
2. 只在 soak 明确出现误晋升、漏报或信号噪音时调整。
3. 不借着调阈值去改 schema、增加新 sink 或改变 consumer 契约。

## Rollback Order

如果 soak 或线上观察到噪音超标，按以下顺序回退：

1. 先关闭 `evolution_bridge_enabled`
2. 再关闭 `pattern_consolidation_enabled`
3. 再关闭 `memory_candidate_bridge_enabled`
4. 再关闭 `working_memory_bridge_enabled`
5. 保留 introspection / audit 只读视图

目标是始终保留可观测性，最后才收缩写路径。

## Acceptance Snapshot

收官前至少确认：

1. `tests/agent/test_meta_cognition_runtime.py`
2. `tests/tools/test_runtime_status_tools.py`
3. `tests/agent/test_loop_runtime_status_tools.py`
4. `tests/session/test_session_search.py`
5. `tests/agent/test_retrieval_fusion.py`
6. `tests/tools/test_session_search_tool.py`
7. `tests/agent/test_dream.py`
8. `tests/agent/test_nearline_pipeline.py`
9. `tests/agent/test_evolution_control_plane.py`
10. `tests/agent/test_curator.py`
11. `tests/agent/test_evolution_opportunity_signals.py`
12. `tests/agent/tools/test_self_tool.py`

## Notes

1. 元认知总线当前是“功能收官，工程验收中”。
2. 这份 runbook 不覆盖 `MetaProgrammingEngine`，也不允许据此开启任何自动自修改路径。
