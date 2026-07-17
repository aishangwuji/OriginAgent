# Tasks

> 遵循规则34（验证先行）：先写测试，再实现。
> 遵循规则37（原子提交）：每个 Task 对应独立 commit。
> 依赖 `enhance-log-observability` spec（log_event attrs 可见化已实现）。

## P0 级（5 个核心盲区）

### Task 1: LLM 响应事件 `llm.response`

- [x] Task 1: 在 `_request_model` 中新增 `llm.response` 事件
  - [x] SubTask 1.1（验证先行）: 在 `tests/agent/test_runner_logging.py` 新增 `test_llm_response_logs_finish_reason_and_tool_count`，mock provider 返回带 tool_calls 的响应，断言 `llm.response` 事件包含 `finish_reason`、`has_tool_calls`、`tool_call_count`、`content_chars`、`reasoning_chars`
  - [x] SubTask 1.2（实现）: 修改 `OriginAgent/agent/runner.py` 的 `_request_model`（约 line 694-805），在 `return response` 前新增 `log_event("llm.response", ...)` 调用
  - [x] SubTask 1.3（验证）: 8/8 PASSED

### Task 2: 工具执行完成事件 `tool.complete`

- [x] Task 2: 在 `_run_tool` 中新增 `tool.complete` 事件
  - [x] SubTask 2.1（验证先行）: 在 `tests/agent/test_runner_logging.py` 新增 `test_tool_complete_logs_status_and_duration`，mock 工具执行成功和失败两种场景，断言 `tool.complete` 事件包含 `name`、`status`、`duration_ms`、`result_size`，失败时还包含 `error_kind`
  - [x] SubTask 2.2（实现）: 修改 `OriginAgent/agent/runner.py` 的 `_run_tool`，在 return 前新增 `log_event("tool.complete", ...)` 调用
  - [x] SubTask 2.3（验证）: 8/8 PASSED

### Task 3: 上下文组装事件 `context.assembled`

- [x] Task 3: 在 `ContextAssemblerV2.assemble` 中新增 `context.assembled` 事件
  - [x] SubTask 3.1（验证先行）: 在 `tests/agent/test_context_assembler_logging.py`（新建）新增 `test_context_assembled_logs_block_count_and_retrieval` 和 `test_context_assembled_logs_recovered_continuity_false_when_absent`
  - [x] SubTask 3.2（实现）: 修改 `OriginAgent/agent/context_assembler.py` 的 `assemble` 方法，在 return 前新增 `log_event("context.assembled", ...)` 调用
  - [x] SubTask 3.3（验证）: 2/2 PASSED

### Task 4: Continuity Checkpoint 保存/加载事件

- [x] Task 4: 在 `_save_continuity_checkpoint` 和 `_load_continuity_checkpoint` 中新增事件
  - [x] SubTask 4.1（验证先行）: 在 `tests/agent/test_continuity_checkpoint.py` 新增 `test_checkpoint_saved_logs_field_counts` 和 `test_checkpoint_loaded_logs_source_and_counts`
  - [x] SubTask 4.2（实现）: 修改 `OriginAgent/agent/agent_runtime.py`：`_save_continuity_checkpoint` 新增 `continuity.checkpoint.saved`；`_load_continuity_checkpoint` 新增 `continuity.checkpoint.loaded`
  - [x] SubTask 4.3（验证）: 7/7 PASSED（注：6 个既有失败属于其他在制品，非本次引入）

### Task 5: 工具循环决策事件 `loop.continue` / `loop.finalize` / `loop.max_iterations`

- [x] Task 5: 在 `AgentRunner.run` 主循环中新增循环决策事件
  - [x] SubTask 5.1（验证先行）: 在 `tests/agent/test_runner_logging.py` 新增 3 个测试：
    - `test_loop_continue_logs_on_tool_calls`：LLM 返回 tool_calls 时断言 `loop.continue` 事件
    - `test_loop_finalize_logs_on_no_tool_calls`：LLM 无 tool_calls 时断言 `loop.finalize` 事件
    - `test_loop_max_iterations_logs_warning`：达到 max_iterations 时断言 `loop.max_iterations` 事件且为 WARNING 级别
  - [x] SubTask 5.2（实现）: 修改 `OriginAgent/agent/runner.py` 的 `run` 方法（约 line 323-673）：
    - 在 `response.should_execute_tools == True` 分支前新增 `log_event("loop.continue", reason="tool_calls", iteration=iteration, tool_count=len(tool_calls), session_key=spec.session_key)`
    - 在 finalize 分支（无 tool_calls）前新增 `log_event("loop.finalize", reason="no_tool_calls", iteration=iteration, finish_reason=response.finish_reason, session_key=spec.session_key)`
    - 在 for-else 分支（max_iterations）新增 `log_event("loop.max_iterations", iteration=spec.max_iterations, tools_used_count=len(tools_used), session_key=spec.session_key)` + `logger.warning(...)`
  - [x] SubTask 5.3（验证）: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_runner_logging.py -v --basetemp=.pytest_basetemp`（8/8 PASSED）

### Task 6: exec 工具分层日志（替换 `<redacted>`）

- [x] Task 6: 修改 `progress_hook.py` 的敏感工具日志策略，从完全 `<redacted>` 改为命令形状摘要
  - [x] SubTask 6.1（验证先行）: 在 `tests/agent/test_progress_hook.py` 新增 4 个测试（exec/message/web_fetch/非敏感工具）
  - [x] SubTask 6.2（实现）: 修改 `OriginAgent/agent/progress_hook.py`：新增 `_sensitive_tool_summary` 方法，exec 返回命令形状摘要（如 `git:2:`），message 返回 channel+content_chars，web_fetch 返回 netloc+path
  - [x] SubTask 6.3（验证）: 4/4 PASSED

## P1 级（7 个效率提升盲区）

### Task 7: 工作记忆加载/保存事件

- [x] Task 7: 在 `WorkingMemoryManager.load` 和 `save` 中新增事件
  - [x] SubTask 7.1（验证先行）: 在 `tests/agent/test_working_memory.py` 新增 `test_load_logs_field_counts` 和 `test_save_logs_change_summary`
  - [x] SubTask 7.2（实现）: 修改 `OriginAgent/agent/working_memory.py`：`load` 末尾新增 `log_event("working_memory.loaded", ...)`，`save` 末尾新增 `log_event("working_memory.saved", ...)`
  - [x] SubTask 7.3（验证）: 7/7 PASSED

### Task 8: 长期记忆检索事件 `retrieval.fused`

- [x] Task 8: 在 `RetrievalFusion.retrieve` 中新增事件
  - [x] SubTask 8.1（验证先行）: 在 `tests/agent/test_retrieval_fusion_logging.py`（新建）新增 `test_retrieval_fused_logs_source_counts`
  - [x] SubTask 8.2（实现）: 修改 `OriginAgent/agent/retrieval_fusion.py` 的 `retrieve` 方法，在 return 前新增 `log_event("retrieval.fused", ...)`
  - [x] SubTask 8.3（验证）: 8/8 PASSED

### Task 9: BDI 单轮决策摘要事件 `bdi.cycle.complete`

- [x] Task 9: 在 `DeliberationEngine.run_cycle` 中新增事件
  - [x] SubTask 9.1（验证先行）: 在 `tests/bdi/test_deliberation_logging.py`（新建）新增 `test_cycle_complete_logs_summary`
  - [x] SubTask 9.2（实现）: 修改 `OriginAgent/bdi/deliberation.py` 的 `run_cycle` 方法，在 return 前新增 `log_event("bdi.cycle.complete", ...)`
  - [x] SubTask 9.3（验证）: 11/11 PASSED

### Task 10: 心跳决策事件 `heartbeat.decided`

- [x] Task 10: 在 `HeartbeatService._decide` 中新增事件
  - [x] SubTask 10.1（验证先行）: 在 `tests/heartbeat/test_service_logging.py`（新建）新增 `test_decide_logs_action_and_tasks`
  - [x] SubTask 10.2（实现）: 修改 `OriginAgent/heartbeat/service.py` 的 `_decide` 方法，在 return 前新增 `log_event("heartbeat.decided", ...)`
  - [x] SubTask 10.3（验证）: 20/20 PASSED

### Task 11: 认知 pass 跳过补充 attrs

- [x] Task 11: 修改 `cognitive.pass.skipped` 事件，补充 `active_task_count` 和 `running_subagents`
  - [x] SubTask 11.1（验证先行）: 在 `tests/agent/test_cognitive_runtime_logging.py`（新建）新增 `test_pass_skipped_includes_task_counts`
  - [x] SubTask 11.2（实现）: 修改 `OriginAgent/agent/agent_cognitive_runtime.py`，在 `log_event("cognitive.pass.skipped", ...)` 调用中补充 `active_task_count` 和 `running_subagents` attrs
  - [x] SubTask 11.3（验证）: 6/6 PASSED

### Task 12: 工具策略拒绝 WARNING

- [x] Task 12: 在 `_run_tool` 中工具被策略拒绝时新增 WARNING 日志
  - [x] SubTask 12.1（验证先行）: 在 `tests/agent/test_runner_logging.py` 新增 `test_tool_denied_logs_warning`
  - [x] SubTask 12.2（实现）: 修改 `OriginAgent/agent/runner.py` 的 `_run_tool`，在 3 处 policy_rule 分支新增 `logger.warning(...)`
  - [x] SubTask 12.3（验证）: 9/9 PASSED

### Task 13: 子 Agent 脱敏策略统一

- [x] Task 13: 让 `_SubagentHook` 接受并应用 `sensitive_tool_log_names`
  - [x] SubTask 13.1（验证先行）: 在 `tests/agent/test_subagent_logging.py`（新建）新增 `test_subagent_exec_redacted` 和 `test_subagent_non_sensitive_unchanged`
  - [x] SubTask 13.2（实现）: 修改 `OriginAgent/agent/subagent.py` 的 `_SubagentHook`，新增 `sensitive_tool_log_names` 参数 + `_summarize_sensitive` 方法 + `_is_sensitive` 方法，日志输出对敏感工具应用命令形状摘要
  - [x] SubTask 13.3（验证）: 2/2 PASSED

# Task Dependencies

## P0 内部依赖
- Task 1-5 之间无依赖，可并行执行
- Task 6（exec 分层日志）独立，可并行

## P1 内部依赖
- Task 7-13 之间无依赖，可并行执行
- Task 13 依赖 Task 6（复用 `_sensitive_tool_summary` 逻辑）

## P0 → P1 依赖
- P1 任务不依赖 P0（都是独立新增日志事件）
- 但建议先完成 P0（核心盲区优先）

## 建议执行顺序
1. **第一批（P0 并行）**：Task 1、2、3、4、5、6（6 个 Sub-Agent 并行）
2. **第二批（P1 并行）**：Task 7、8、9、10、11、12（6 个 Sub-Agent 并行）
3. **第三批**：Task 13（依赖 Task 6 的 `_sensitive_tool_summary`）
