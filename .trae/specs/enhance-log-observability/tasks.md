# Tasks

> 遵循规则34（验证先行）：先写测试，再实现。
> 遵循规则37（原子提交）：每个 Task 对应独立 commit。

## Task 1: 修改 `log_event` 让 attrs 出现在消息文本中

- [x] Task 1: 修改 `log_event` 函数，把 attrs 格式化为 `key=value` 拼入消息
  - [x] SubTask 1.1（验证先行）: 在 `tests/utils/test_tracing.py` 新增 4 个测试用例（TestLogEventMessageFormat 类）
  - [x] SubTask 1.2（实现）: 修改 `OriginAgent/utils/tracing.py` 的 `log_event`（line 82-100）
  - [x] SubTask 1.3（验证）: 17 passed（13 既有 + 4 新增）

## Task 2: 增强 LLM 请求与工具执行日志的 attrs

- [x] Task 2: 给 `llm.request`、`tools.execute`、`run.complete` 补充更多 attrs
  - [x] SubTask 2.1（验证先行）: 在 `tests/agent/test_runner_logging.py` 新增 3 个测试用例
  - [x] SubTask 2.2（实现）: 修改 `OriginAgent/agent/runner.py`：
    - `llm.request`：补充 `stream=(wants_streaming or wants_progress_streaming)` 和 `message_count=len(messages)`（迁移到流式判断之后）
    - `tools.execute`：补充 `tool_names=",".join(tc.name for tc in tool_calls)`（ToolCallRequest 是 dataclass，用属性访问）
    - `run.complete`：在 run 方法首行加 `_run_start = time.monotonic()`，补充 `elapsed_ms=round((time.monotonic() - _run_start) * 1000, 2)`
  - [x] SubTask 2.3（验证）: 3 passed

# Task Dependencies

- Task 2 依赖 Task 1（Task 1 让 attrs 可见后，Task 2 补充的 attrs 才能在日志中显示）
- 建议顺序：Task 1 → Task 2
