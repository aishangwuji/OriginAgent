# 上下文组装污染修复 Spec

## Why

Agent 在实际使用中出现"答非所问、上下文缺失、突然聊起很久之前的事、表现颠"等问题。经 GitNexus 符号图审计定位到根因在上下文组装环节：

1. `build_recovered_continuity_context` 把整个 checkpoint 字典 `json.dumps` 注入上下文，导致历史归档 summary（`cold_indices`）和结构化字段被无差别灌入，且与已结构化渲染的 `recent_turns_text`/`cold_indices_text` 重复——直接造成"聊起很久之前的事"与 token 浪费。
2. `WorkingMemorySnapshot` 的 `attention_items`/`open_loops`/`active_constraints` 等字段一旦写入即无限期保留，每轮被反复注入，LLM confabulate 的内容会形成自我强化的虚假记忆——造成"答非所问/表现颠"。
3. `_extract_recent_turns_summary` 将每条消息硬截断到 500 字符，跨 session 恢复时长对话关键信息丢失——造成"上下文缺失"。

## What Changes

- **移除** `build_recovered_continuity_context` 中的 `json.dumps(dict(snapshot))` 整体 dump，改为对 `current_goal`/`current_plan`/`open_loops`/`active_constraints`/`pending_confirmation_refs`/`updated_at` 的结构化渲染，保留已有的 `recent_turns_text` 与 `cold_indices_text` 渲染。
- **新增** `WorkingMemoryManager.load` 对易过期字段（`attention_items`/`open_loops`/`active_constraints`/`pending_questions`/`tool_residue`）的基于 `updated_at` 的时间衰减检查：超过 30 分钟未更新则清空这些字段，保留 `current_goal`（已有独立的 goal_state 过期机制）。
- **修改** `_extract_recent_turns_summary` 的截断长度从硬编码 500 改为读取 `ContextConfig` 的配置项 `recent_turns_summary_max_chars`（默认 800，可在 settings 中调整）。

## Impact

- **Affected code**:
  - `OriginAgent/agent/context.py` — `ContextBuilder.build_recovered_continuity_context`（staticmethod，upstream impact=0，动态调用链）
  - `OriginAgent/agent/working_memory.py` — `WorkingMemoryManager.load`（upstream impact=4：`upsert`/`inspect`/`append_attention_item`/`append_pending_question`，risk=LOW）
  - `OriginAgent/agent/agent_runtime.py` — `AgentRuntime._extract_recent_turns_summary`（staticmethod）
  - `OriginAgent/config/schema.py` — `ContextConfig` 新增 `recent_turns_summary_max_chars` 字段
- **Affected specs**: 无直接关联的既有 spec
- **Risk level**: LOW（所有修改点的 upstream impact 均为 LOW，且 `load` 的 4 个调用者均为同文件内部方法）
- **Breaking changes**: 无（输出格式变化向后兼容，配置项有默认值）

## ADDED Requirements

### Requirement: Working Memory 字段时间衰减

系统 SHALL 在 `WorkingMemoryManager.load` 加载 snapshot 时，检查 `updated_at` 与当前时间差，超过 30 分钟则清空易过期字段（`attention_items`/`open_loops`/`active_constraints`/`pending_questions`/`tool_residue`），保留 `current_goal`（由 `_hydrate_goal` 独立检查 goal_state 的 30 分钟过期）。

#### Scenario: snapshot 超过 30 分钟未更新

- **WHEN** `WorkingMemoryManager.load` 加载一个 `updated_at` 距当前时间超过 30 分钟的 snapshot
- **THEN** 返回的 snapshot 中 `attention_items`/`open_loops`/`active_constraints`/`pending_questions`/`tool_residue` 为空列表
- **AND** `current_goal` 保留原值（由 `_hydrate_goal` 独立判断是否过期）

#### Scenario: snapshot 在 30 分钟内更新

- **WHEN** `WorkingMemoryManager.load` 加载一个 `updated_at` 距当前时间在 30 分钟内的 snapshot
- **THEN** 返回的 snapshot 保留所有字段原值

#### Scenario: updated_at 字段缺失或解析失败

- **WHEN** snapshot 的 `updated_at` 为空或无法解析为 ISO 8601 时间
- **THEN** 不阻塞加载，保留所有字段原值（容错降级，不因时间字段问题导致记忆丢失）

### Requirement: recent_turns_summary 截断长度可配置

系统 SHALL 在 `ContextConfig` 中新增 `recent_turns_summary_max_chars: int = 800` 配置项，`_extract_recent_turns_summary` 读取该配置决定每条消息的截断长度。

#### Scenario: 使用默认配置

- **WHEN** 未在 settings 中配置 `recent_turns_summary_max_chars`
- **THEN** `_extract_recent_turns_summary` 将每条消息截断到 800 字符

#### Scenario: 用户自定义配置

- **WHEN** 用户在 settings 中配置 `recent_turns_summary_max_chars = 1200`
- **THEN** `_extract_recent_turns_summary` 将每条消息截断到 1200 字符

## MODIFIED Requirements

### Requirement: recovered_continuity 块的结构化渲染

`ContextBuilder.build_recovered_continuity_context` SHALL 移除 `json.dumps(dict(snapshot))` 整体 dump，改为对以下字段进行结构化文本渲染：

- `current_goal`（如有）
- `current_plan`（如有，逐项列出）
- `open_loops`（如有，逐项列出）
- `active_constraints`（如有，逐项列出）
- `pending_confirmation_refs`（如有，逐项列出）
- `updated_at`（让 Agent 感知 checkpoint 新鲜度）

保留已有的 `recent_turns_text` 与 `cold_indices_text` 结构化渲染。

#### Scenario: checkpoint 包含完整字段

- **WHEN** `build_recovered_continuity_context` 接收一个包含 `current_goal`/`current_plan`/`open_loops` 等字段的 checkpoint snapshot
- **THEN** 输出的文本块包含各字段的结构化渲染（标题 + 逐项列表）
- **AND** 不包含 `json.dumps` 生成的完整 JSON 字符串
- **AND** `recent_turns_text` 和 `cold_indices_text` 仍被渲染（不重复，因为 JSON dump 已移除）

#### Scenario: checkpoint 字段部分缺失

- **WHEN** `build_recovered_continuity_context` 接收一个部分字段为空的 checkpoint snapshot
- **THEN** 空字段对应的渲染段落被省略（不渲染空标题）
- **AND** 非空字段仍被正常渲染

## REMOVED Requirements

无。本次修改不移除任何既有功能。
