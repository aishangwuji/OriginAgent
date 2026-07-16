# 三级对话记忆架构 Spec

## Why
当前 Agent 的对话记忆依赖 token 估算触发的"一次性压缩"（`maybe_consolidate_by_tokens`）和空闲归档（`AutoCompact`），存在三个根本缺陷：(1) 压缩后原始消息不可见，Agent 忘记自己刚说过什么；(2) 压缩时机依赖 token 估算，不可预测；(3) 压缩时孤立总结，丢失跨段连贯性。本 spec 引入"热-温-冷"三级存储，让最近 50 轮对话永远可见，温区 50 轮缓冲后结合热区上下文总结，冷区作为索引按需暴露，彻底解决"痴呆"问题。

## What Changes

### 三级状态机
- **热区（Hot）**：最近 50 轮对话（50 user + 50 assistant，含 tool_calls），永远作为独立 message 注入 LLM，不压缩、不截断（除 token 硬限制外）
- **温区（Warm）**：第 51-100 轮对话，逐条填补，满 50 轮触发一次"结构化总结"，总结时结合热区上下文
- **冷区（Cold）**：已总结的旧对话，原始消息归档到独立文件，总结结果作为"索引条目"被动注入（最近 5 条），更早的通过 `session_search` 主动检索

### 温区总结的结构化模板
- 温区满 50 轮触发总结时，使用结构化 JSON 模板（commitments/decisions/open_questions/key_entities），而非自由文本
- 结构化字段同步写入 `working_memory` 的 `open_loops` / `priority_facts`，成为被动注入的一部分

### 冷区归档文件
- **新增** `warm_archive/{session_key}.jsonl`：温区总结后，原始 50 轮消息移到此文件（方案 C）
- **新增** `warm_summaries.jsonl`：温区总结的结构化索引条目
- `session.messages` 只保留热区 50 轮，保持精简

### 渐进式暴露
- 冷区索引条目（最近 5 条）通过 `recovered_continuity` 块被动注入
- LLM 看到索引后，可通过 `session_search(sources=["warm_archive"])` 主动拉取原文
- 更早的冷区索引通过 `session_search(sources=["warm_summaries"])` 主动检索

### 替换现有机制
- **BREAKING**：废弃 `maybe_consolidate_by_tokens` 的 token 估算触发，改为温区轮次阈值触发
- **BREAKING**：废弃 `AutoCompact._archive` 的 `session.messages = kept_msgs` 删除行为，改为移到 `warm_archive` 文件
- 保留 `recent_turns_summary`（已有）作为热区未加载时的保底视图

## Impact
- Affected specs: `fix-cognition-architecture-defects`（三级存储替代其 P1 跨会话恢复的简易方案）
- Affected code:
  - `OriginAgent/agent/memory.py` — `maybe_consolidate_by_tokens` 改为温区触发，`Consolidator.archive` 改用结构化模板
  - `OriginAgent/agent/autocompact.py` — `_archive` 不再删除 `session.messages`，改为移到 `warm_archive`
  - `OriginAgent/session/manager.py` — `get_history` 增加 `max_turns` 参数，按 user turn 计数截断
  - `OriginAgent/agent/context.py` — `build_recovered_continuity_context` 渲染冷区索引条目
  - `OriginAgent/agent/context_assembler.py` — `assemble` 编排热区/冷区索引注入
  - `OriginAgent/agent/agent_runtime.py` — `_save_continuity_checkpoint` 保存温区状态，`_load_continuity_checkpoint` 恢复三级状态
  - `OriginAgent/session/search.py` — `SessionSearchService` 新增 `warm_archive` / `warm_summaries` source
  - `OriginAgent/agent/tools/session_search.py` — 工具 schema 暴露新 source
  - `OriginAgent/agent/working_memory.py` — 温区总结的结构化字段写入 `open_loops` / `priority_facts`
  - 新增 `OriginAgent/agent/warm_store.py` — 温区缓冲区管理器
  - 新增 `OriginAgent/agent/warm_summarizer.py` — 温区总结器（结构化模板 + 结合热区上下文）
  - 测试文件（新增覆盖 + 现有回归）

## ADDED Requirements

### Requirement: 热区 50 轮永远可见
系统 SHALL 维护"热区"，包含最近 50 轮对话（50 user + 50 assistant，含 tool_calls/reasoning_content）。热区消息 SHALL 作为独立 message 对象注入 LLM messages，不进入压缩流程。

#### Scenario: 热区边界对齐到完整轮次
- **WHEN** session 有 53 轮对话（106 条消息），调用 `get_hot_history(max_turns=50)`
- **THEN** 返回第 4-53 轮（100 条消息），边界对齐到第 4 轮的 user message，不从 assistant tool_call 中间截断

#### Scenario: 热区不压缩
- **WHEN** session 有 53 轮对话，触发温区总结
- **THEN** 热区 50 轮（第 4-53 轮）完整保留在 `session.messages` 中，不进入温区总结

#### Scenario: tool_call 完整性
- **WHEN** 第 4 轮的 assistant 响应包含 3 个 tool_call 和对应 tool_result
- **THEN** 热区边界向前扩展到包含所有 tool_call 和 tool_result，确保 LLM 看到"调用 + 结果"完整配对

### Requirement: 温区渐进式填补
系统 SHALL 维护"温区"缓冲区，存储第 51-100 轮对话。温区 SHALL 逐条填补（每轮对话结束后追加），而非一次性加载 50 条。

#### Scenario: 逐条填补
- **WHEN** 第 51 轮对话结束（assistant 响应完成）
- **THEN** 第 51 轮的 user + assistant 消息追加到温区缓冲区，温区此时有 1 轮

#### Scenario: 温区未满不总结
- **WHEN** 温区有 49 轮对话，第 100 轮对话结束
- **THEN** 第 100 轮追加到温区，温区满 50 轮，触发总结

#### Scenario: 温区满触发总结
- **WHEN** 温区缓冲区达到 50 轮（第 51-100 轮）
- **THEN** 触发温区总结，总结完成后温区清空，第 101 轮开始新的温区填补

### Requirement: 温区结构化总结结合热区上下文
温区总结 SHALL 使用结构化 JSON 模板，且总结时 SHALL 结合当前热区 50 轮的上下文，避免孤立总结丢失跨段连贯性。

#### Scenario: 结构化模板
- **WHEN** 温区满 50 轮触发总结
- **THEN** 生成结构化 JSON，包含字段：`turn_range`、`summary`、`commitments`、`decisions`、`open_questions`、`key_entities`、`timestamp_range`

#### Scenario: 结合热区上下文
- **WHEN** 温区（第 51-100 轮）触发总结，热区当前是第 101-150 轮
- **THEN** 总结 prompt 包含热区 50 轮的完整内容，LLM 能看到"温区讨论的 X 在热区有了后续 Y"，生成连贯的总结

#### Scenario: 结构化字段写入 working_memory
- **WHEN** 温区总结生成 `commitments=["提醒用户背题"]`、`open_questions=["cron幂等性测试是否通过"]`
- **THEN** 这些字段同步写入 `working_memory.open_loops` 和 `working_memory.priority_facts`，成为被动注入的一部分

### Requirement: 冷区归档文件
温区总结后，原始 50 轮消息 SHALL 移到 `warm_archive/{session_key}.jsonl` 文件，`session.messages` 只保留热区。

#### Scenario: 原始消息归档
- **WHEN** 温区总结完成
- **THEN** 温区 50 轮的原始消息追加到 `workspace/warm_archive/{session_key}.jsonl`，从 `session.messages` 中移除

#### Scenario: session.messages 保持精简
- **WHEN** session 有 153 轮对话（306 条消息），热区 50 轮 + 温区 3 轮
- **THEN** `session.messages` 只有 106 条消息（热区 100 + 温区 6），其余 200 条在 `warm_archive` 文件中

#### Scenario: 原始消息可检索
- **WHEN** LLM 调用 `session_search(sources=["warm_archive"], query="背题")`
- **THEN** 从 `warm_archive/{session_key}.jsonl` 检索命中条目，返回 snippet + locator

### Requirement: 冷区索引渐进式暴露
冷区总结条目 SHALL 作为"索引"被动注入，最近 5 条始终可见，更早的通过 `session_search(sources=["warm_summaries"])` 主动检索。

#### Scenario: 最近 5 条索引被动注入
- **WHEN** 冷区有 7 条总结索引（覆盖第 1-350 轮）
- **THEN** `recovered_continuity` 块包含最近 5 条索引的 `turn_range` + `summary` + `key_entities`，不包含完整的 `commitments`/`decisions`

#### Scenario: 主动检索更早索引
- **WHEN** LLM 调用 `session_search(sources=["warm_summaries"], query="系统架构")`
- **THEN** 从 `workspace/warm_summaries.jsonl` 检索所有索引条目（含第 1-2 条），返回完整结构化字段

#### Scenario: 主动拉取原文
- **WHEN** LLM 看到索引"第 101-150 轮：讨论了 cron 幂等性"，调用 `session_search(sources=["warm_archive"], query="cron幂等")`
- **THEN** 返回第 101-150 轮中命中"cron幂等"的原始消息片段

### Requirement: 温区总结异步执行
温区满 50 轮触发总结时 SHALL 异步执行，不阻塞用户等待。

#### Scenario: 异步触发
- **WHEN** 第 100 轮对话的 assistant 响应返回给用户
- **THEN** 同时在后台调度温区总结任务，用户无需等待总结完成

#### Scenario: 第 101 轮总结未完成
- **WHEN** 第 101 轮对话开始，但第 51-100 轮的温区总结仍在后台执行
- **THEN** 第 51-100 轮的原始消息暂时保留在温区缓冲区，直到总结完成后才移到 `warm_archive`

### Requirement: 跨会话三级重建
websocket 重连/进程重启后，系统 SHALL 从持久化存储重建三级状态。

#### Scenario: 重建热区
- **WHEN** session 恢复，`session.messages` 有 106 条消息
- **THEN** 热区取最后 100 条（50 轮），温区取前 6 条（3 轮）

#### Scenario: 重建冷区索引
- **WHEN** session 恢复，`warm_summaries.jsonl` 有 5 条索引
- **THEN** `recovered_continuity` 块包含最近 5 条索引，LLM 可见

#### Scenario: 温区状态持久化
- **WHEN** 第 75 轮对话结束（温区有 25 轮未总结），进程重启
- **THEN** 从 `session.metadata["warm_buffer"]` 恢复温区 25 轮，继续填补直到 50 轮触发总结

## MODIFIED Requirements

### Requirement: session 历史注入
`Session.get_history` SHALL 新增 `max_turns` 参数，按 user turn 计数截断（而非仅按 message 条数）。`max_turns=50` 时返回最近 50 轮完整对话，边界对齐到 user message。

### Requirement: Consolidator.maybe_consolidate_by_tokens
`maybe_consolidate_by_tokens` SHALL 废弃 token 估算触发逻辑，改为检查温区轮次。当温区满 50 轮时触发温区总结，而非循环压缩直到 token 低于 target。

### Requirement: AutoCompact._archive
`AutoCompact._archive` SHALL 不再执行 `session.messages = kept_msgs` 删除操作。归档消息 SHALL 移到 `warm_archive/{session_key}.jsonl` 文件，而非直接删除。

### Requirement: build_recovered_continuity_context
`build_recovered_continuity_context` SHALL 渲染冷区索引条目（最近 5 条），格式为 `[turn_range] summary (key_entities)`，作为渐进式暴露的索引视图。

### Requirement: session_search 工具
`session_search` 工具 SHALL 新增 `warm_archive` 和 `warm_summaries` 两个 source，分别检索温区归档的原始消息和冷区总结索引。

## REMOVED Requirements

### Requirement: maybe_consolidate_by_tokens 的 token 估算循环压缩
**Reason**: token 估算不可预测，一次性压缩大量消息导致摘要质量差，且压缩后原始消息不可见
**Migration**: 改为温区 50 轮阈值触发，结合热区上下文总结，原始消息归档到 `warm_archive` 可检索

### Requirement: AutoCompact._archive 的 session.messages 删除
**Reason**: 直接删除原始消息是不可逆的破坏性操作，summary 质量差时找不回
**Migration**: 原始消息移到 `warm_archive/{session_key}.jsonl`，`session_search` 可检索
