# 日志可观测性增强 Spec

## Why

Agent 运行时日志中，结构化事件（`event.llm.request`、`event.tools.execute`、`event.run.complete` 等）只输出事件名，**绑定的 attrs（model、session_key、tool_count、tokens_used 等）完全不显示**。根因是 `log_event` 通过 `logger.bind(**ctx)` 绑定 attrs，但 sink 的 format 字符串（`commands.py:30-34`）只输出 `{message}`，导致 extra 字段全部丢失。

用户反馈："想让更多的信息暴露在日志里面，这样能够让我们更好的判断问题所在"。当前日志无法回答"这次 LLM 请求用了什么模型"、"这次工具执行调了哪些工具"、"这次 run 用了多少 token"等基本排查问题。

## What Changes

- **修改** `log_event` 函数（`utils/tracing.py:82-87`）：把 attrs 格式化为 `key=value` 对拼入消息文本，使人工阅读日志时可直接看到关键上下文，无需配置 JSON sink。
- **增强** 关键 `log_event` 调用点的 attrs 覆盖：
  - `llm.request`（runner.py:703）：补充 `stream`（是否流式）、`message_count`（消息数）
  - `tools.execute`（runner.py:852）：补充 `tool_names`（工具名列表，逗号分隔）
  - `run.complete`（runner.py:654）：补充 `elapsed_ms`（run 总耗时）

## Impact

- **Affected code**:
  - `OriginAgent/utils/tracing.py` — `log_event` 函数（无外部调用者变更，向后兼容）
  - `OriginAgent/agent/runner.py` — 3 个 `log_event` 调用点（line 654/703/852）
- **Affected specs**: 无
- **Risk level**: LOW（仅日志输出格式变化，无业务逻辑改动）
- **Breaking changes**: 无（日志文本变长，但不影响任何解析逻辑；attrs 仍通过 `logger.bind` 绑定，JSON sink 不受影响）

## ADDED Requirements

### Requirement: log_event 输出包含 attrs 文本

`log_event` 函数 SHALL 在日志消息文本中包含所有 attrs（除 `event` 本身），格式为 `event.{name} | key1=value1 key2=value2`。

#### Scenario: 带 attrs 的事件

- **WHEN** 调用 `log_event("llm.request", model="gpt-4", session_key="abc123")`
- **THEN** 日志消息文本为 `event.llm.request | model=gpt-4 session_key=abc123`
- **AND** `logger.bind` 仍绑定所有 attrs（JSON sink 不受影响）

#### Scenario: 无 attrs 的事件

- **WHEN** 调用 `log_event("heartbeat.tick")`（无额外 attrs）
- **THEN** 日志消息文本为 `event.heartbeat.tick`（不附加 `|`）

#### Scenario: attrs 值为 None 或空

- **WHEN** 某个 attr 值为 `None` 或空字符串
- **THEN** 该 attr 不出现在消息文本中（避免 `model=None` 噪音）

### Requirement: LLM 请求日志包含流式标志与消息数

`llm.request` 事件 SHALL 包含 `stream`（bool，是否流式调用）和 `message_count`（int，发送给 LLM 的消息数）attrs。

#### Scenario: 流式请求

- **WHEN** AgentRunner 发起流式 LLM 请求
- **THEN** 日志输出类似 `event.llm.request | model=gpt-4 session_key=abc123 stream=True message_count=12`

### Requirement: 工具执行日志包含工具名列表

`tools.execute` 事件 SHALL 包含 `tool_names`（str，逗号分隔的工具名列表）attr。

#### Scenario: 批量工具执行

- **WHEN** AgentRunner 执行 3 个工具调用（如 `read_file`、`write_file`、`edit_file`）
- **THEN** 日志输出类似 `event.tools.execute | tool_count=3 session_key=abc123 tool_names=read_file,write_file,edit_file`

### Requirement: Run 完成日志包含总耗时

`run.complete` 事件 SHALL 包含 `elapsed_ms`（float，run 总耗时毫秒）attr。

#### Scenario: 正常完成

- **WHEN** AgentRunner 的一次 run 完成
- **THEN** 日志输出类似 `event.run.complete | stop_reason=stop tool_count=2 tokens_used=150 session_key=abc123 elapsed_ms=3420.5`

## MODIFIED Requirements

无既有 requirement 需要修改。

## REMOVED Requirements

无。
