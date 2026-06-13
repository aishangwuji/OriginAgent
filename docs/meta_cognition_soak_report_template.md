# Meta Cognition Soak Report Template

Date:
Owner:
Workspace:
Runtime:

## Config Snapshot

记录本轮 soak 使用的关键配置：

1. `structured_reflection_enabled`
2. `working_memory_bridge_enabled`
3. `memory_candidate_bridge_enabled`
4. `memory_candidate_min_confidence`
5. `pattern_consolidation_enabled`
6. `evolution_bridge_enabled`
7. `pattern_window_days`
8. `pattern_min_frequency`
9. `pattern_min_distinct_turns`
10. `max_signal_upserts_per_turn`

## Input Scenarios

至少覆盖以下样本：

1. `tool_failure(error)`
2. `user_correction`
3. `complete_goal` 成功完成
4. 可选真实会话样本：
   - session key:
   - turn ids:

## Runtime Observations

记录：

1. `meta_cognition_summary()`
2. `recent_journals`
3. `recent_reflections`
4. `recent_patterns`
5. `recent_evolution_seeds`
6. `last_signal_upserts`
7. `working_memory_bridge.decision_counts`
8. `memory_candidate_bridge.decision_counts`

## Artifact Review

检查以下文件：

1. `memory/meta_cognition/journals.jsonl`
2. `memory/meta_cognition/reflections.jsonl`
3. `memory/meta_cognition/patterns.jsonl`
4. `memory/meta_cognition/evolution_seeds.jsonl`

记录：

1. 是否存在 raw prompt/history/payload/source excerpt 泄漏
2. summary / preview 是否保持 redacted
3. evidence refs 是否在上限内

## Noise Review

1. working memory 是否只出现轻量 caution / pending question
2. `memory_candidates` 是否只接收高价值 learned rule candidate
3. 单 turn signal upsert 数是否符合 `max_signal_upserts_per_turn`
4. suppressed signal 是否被重复 upsert

## Threshold Decision

按固定顺序记录是否调整：

1. `memory_candidate_min_confidence`
   - keep / change
   - reason
2. `pattern_min_frequency`
   - keep / change
   - reason
3. `pattern_min_distinct_turns`
   - keep / change
   - reason
4. `max_signal_upserts_per_turn`
   - keep / change
   - reason

## Final Decision

1. 收官结论：
   - pass
   - pass with follow-up
   - fail
2. 是否需要回退：
   - no
   - close `evolution_bridge_enabled`
   - close `pattern_consolidation_enabled`
   - close write bridges and keep introspection only
3. 后续动作：
