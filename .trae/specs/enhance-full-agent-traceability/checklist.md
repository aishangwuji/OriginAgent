# Checklist

## P0: 核心盲区

### Task 1: llm.response 事件
- [x] `_request_model` 在 LLM 返回后输出 `llm.response` 事件
- [x] 事件包含 `finish_reason`、`has_tool_calls`、`tool_call_count`、`content_chars`、`reasoning_chars`、`usage_completion`
- [x] 测试 `test_llm_response_logs_finish_reason_and_tool_count` 通过

### Task 2: tool.complete 事件
- [x] `_run_tool` 在工具执行后输出 `tool.complete` 事件
- [x] 事件包含 `name`、`status`、`duration_ms`、`result_size`，失败时含 `error_kind`
- [x] 测试 `test_tool_complete_logs_status_and_duration` 通过

### Task 3: context.assembled 事件
- [x] `ContextAssemblerV2.assemble` 在组装后输出 `context.assembled` 事件
- [x] 事件包含 `block_count`、`block_kinds`、`retrieval_sources`、`retrieved_total`、`trimmed_count`、`recovered_continuity_included`
- [x] 测试 `test_context_assembled_logs_block_count_and_retrieval` 通过

### Task 4: continuity.checkpoint.saved/loaded 事件
- [x] `_save_continuity_checkpoint` 输出 `continuity.checkpoint.saved` 事件
- [x] `_load_continuity_checkpoint` 输出 `continuity.checkpoint.loaded` 事件
- [x] 保存事件含 `field_count`、`recent_turns_count`、`cold_indices_count`、`current_goal_present`
- [x] 加载事件含 `source`、`field_count`、`recent_turns_count`、`cold_indices_count`
- [x] 测试通过（7/7 PASSED，6 个既有失败属于其他在制品）

### Task 5: loop.continue/finalize/max_iterations 事件
- [x] 有 tool_calls 时输出 `loop.continue`（含 `reason=tool_calls`、`iteration`、`tool_count`）
- [x] 无 tool_calls 时输出 `loop.finalize`（含 `reason=no_tool_calls`、`iteration`、`finish_reason`）
- [x] 达到 max_iterations 时输出 `loop.max_iterations` + WARNING 级别日志
- [x] 3 个测试通过

### Task 6: exec 工具分层日志
- [x] exec 工具日志从 `<redacted>` 改为命令形状摘要（如 `git:2:`）
- [x] message 工具日志包含 `channel=` 和 `content_chars=`
- [x] web_fetch 工具日志包含 netloc + path（不含 query string）
- [x] 非敏感工具日志格式不变
- [x] 测试通过（4/4 PASSED）

## P1: 效率提升盲区

### Task 7: working_memory.loaded/saved 事件
- [x] `load` 输出 `working_memory.loaded` 事件（含 `has_goal`、`open_loops_count`、`attention_items_count`）
- [x] `save` 输出 `working_memory.saved` 事件
- [x] 测试通过（7/7 PASSED）

### Task 8: retrieval.fused 事件
- [x] `RetrievalFusion.retrieve` 输出 `retrieval.fused` 事件
- [x] 事件含 `sources`、`per_source_counts`、`deduped_count`、`trimmed_count`、`final_block_count`、`top_score`
- [x] 测试通过（8/8 PASSED）

### Task 9: bdi.cycle.complete 事件
- [x] `DeliberationEngine.run_cycle` 输出 `bdi.cycle.complete` 事件
- [x] 事件含 `desires_evaluated`、`cached_intentions`、`llm_desires`、`intentions_emitted`、`elapsed_ms`
- [x] 测试通过（11/11 PASSED）

### Task 10: heartbeat.decided 事件
- [x] `HeartbeatService._decide` 输出 `heartbeat.decided` 事件
- [x] 事件含 `action`、`tasks_preview`、`tasks_len`
- [x] 测试通过（20/20 PASSED）

### Task 11: cognitive.pass.skipped 补充 attrs
- [x] 事件补充 `active_task_count` 和 `running_subagents`
- [x] 测试通过（6/6 PASSED）

### Task 12: 工具策略拒绝 WARNING
- [x] 工具被策略拒绝时输出 WARNING 日志（含工具名、policy_rule、session_key）
- [x] 测试通过（9/9 PASSED）

### Task 13: 子 Agent 脱敏策略统一
- [x] `_SubagentHook` 接受 `sensitive_tool_log_names` 参数
- [x] 子 Agent 调用 exec 时使用命令形状摘要（与主 Agent 一致）
- [x] 测试通过（2/2 PASSED）

## 通用

- [ ] 每个 Task 对应独立原子 commit（规则37）— 待提交时验证
- [x] 无投机性设计/过度抽象（规则32）
- [x] 无任务范围外的越界改动（规则33）
- [x] 测试先于实现编写（规则34）— 所有 Sub-Agent 均确认 TDD 红→绿
- [x] 无硬编码敏感信息（规则18）— exec 命令形状摘要不暴露参数，web_fetch 不含 query string，子 Agent 脱敏策略统一
- [x] 所有新事件通过 `log_event` 输出（复用 `enhance-log-observability` 的 attrs 可见化机制）
