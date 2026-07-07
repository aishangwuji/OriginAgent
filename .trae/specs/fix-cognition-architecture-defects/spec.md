# 认知架构缺陷修复 Spec

## Why
Agent 出现"急性谵妄"症状：跨会话失忆（"刚恢复上下文"）、虚构记忆（"玩电脑"事件）、重复创建 cron、元认知功能完全瘫痪（`MetaCognitionReflector` 返回 0 chars 后崩溃）。排查确认这是 **6 个架构级缺陷连锁导致**，不是模型能力问题。本 spec 一次性修复所有根因，恢复 Agent 的元认知、记忆一致性和行为可控性。

## What Changes

### P0：MetaCognitionReflector 健壮性（防止元认知瘫痪）
- `auxiliary_llm.call_llm` 增加"空内容视为可降级错误"逻辑：`finish_reason != "error"` 但 `content` 为空时，触发 fallback 而非直接返回
- `meta_cognition_reflector._parse_response` 优雅降级：空内容或 invalid JSON 时返回 `(None, None, None)` 而非 `raise ValueError`，让 `reflect_turn` 走 minimal journal 路径
- `reflect_turn` 在 LLM 响应空内容时，优先尝试从 `response.reasoning_content` 提取 JSON（reasoning 模型把内容放在此字段的情况）

### P0：内部事件角色分离（防止 LLM 误解为用户指令）
- **BREAKING**：`active_intent` nudge 不再包装成 `{"role": "user", ...}`，改为 `{"role": "system", "content": [...]}` 或独立 `internal_events` 字段
- `_drain_pending` 中的 `_to_user_message` 拆分为 `_to_user_message`（真实用户）和 `_to_system_event`（内部事件）
- `subagent_result` 和 `active_intent` 都走 system event 路径

### P1：工具循环终止 + cron 幂等（防止重复创建）
- `runner.py` 工具执行成功后，若 LLM 下一轮再次调用**同一工具同一参数**，应检测并终止（turn-scoped 幂等键）
- `cron._add_job` 增加 turn-scoped 幂等检查：同一 turn 内相同 `(name, schedule, message)` 组合只创建一次，第二次返回首次创建的 job id
- 工具结果中明确返回"已创建，勿重复"提示

### P1：working_memory 防自我强化（防止虚假记忆固化）
- `_hydrate_goal` 增加 goal_state 过期检查：`updated_at` 超过 30 分钟的 goal 不再注入
- `_hydrate_due_reminders` 增加 source 标记：reminder 内容必须标记 `source: "reminder_store"`，与 LLM confabulated 的 attention_items 区分
- working_memory 的 `save` 在写入前验证 `current_goal` 和 `attention_items` 是否与现有内容冲突，冲突时保留原值

### P1：跨会话上下文恢复（防止"刚恢复上下文"失忆）
- `continuity_checkpoint_v1` 新增 `recent_turns_summary` 字段：保存最近 2 轮对话的摘要（user message + assistant final content，各截断到 500 字符）
- `_save_continuity_checkpoint` 在保存时追加最近轮次摘要
- `_load_continuity_checkpoint` 在加载时返回 `recent_turns_summary`
- `build_recovered_continuity_context` 在渲染时包含 `recent_turns_summary`

### P2：防虚构验证（防止 Agent 顺从用户的错误陈述）
- Agent 在收到"你忘记刚刚 X 了吗？"类质疑时，应优先反查自己最近 3 轮的输出，而非直接搜索关键词
- 在 system prompt 中增加指引："当用户质疑你过去的行为时，先检查自己最近的输出历史，再决定是否承认"

## Impact
- Affected specs: 无（本 spec 是独立的架构修复）
- Affected code:
  - `OriginAgent/agent/auxiliary_llm.py` — `call_llm` 空内容检查（约 line 489-490）
  - `OriginAgent/agent/meta_cognition_reflector.py` — `_parse_response` 降级、`reflect_turn` reasoning_content fallback（约 line 340-483, 580-593）
  - `OriginAgent/agent/agent_runtime.py` — `_drain_pending` 拆分 user/system event（约 line 836-879）、`_save_continuity_checkpoint` 增加 recent_turns_summary（约 line 600-615）、`_load_continuity_checkpoint` 返回新字段（约 line 618-630）
  - `OriginAgent/agent/context.py` — `build_recovered_continuity_context` 渲染新字段（约 line 490-503）
  - `OriginAgent/agent/runner.py` — 工具循环 turn-scoped 幂等键（约 line 290-457）
  - `OriginAgent/agent/tools/cron.py` — `_add_job` 幂等检查（约 line 332-350）
  - `OriginAgent/agent/working_memory.py` — `_hydrate_goal` 过期检查、`_hydrate_due_reminders` source 标记、`save` 冲突验证（约 line 99-211）
  - `OriginAgent/templates/agent/identity.md` — 增加防虚构指引
  - 测试文件（新增覆盖 + 现有回归）

## ADDED Requirements

### Requirement: MetaCognitionReflector 空内容降级
`MetaCognitionReflector._parse_response` SHALL 在 LLM 返回空内容或 invalid JSON 时返回 `(None, None, None)`，而非抛出异常。`reflect_turn` SHALL 在此情况下走 minimal journal 路径，确保元认知功能不因 LLM 空响应而完全瘫痪。

#### Scenario: LLM 返回空字符串
- **WHEN** LLM 响应 `content=""` 且 `finish_reason="stop"`
- **THEN** `_parse_response` 返回 `(None, None, None)`，`reflect_turn` 返回 `status="ok"` `reason="minimal_only"`，写入 minimal journal

#### Scenario: LLM 返回 invalid JSON
- **WHEN** LLM 响应 `content="not a json"` 且 `finish_reason="stop"`
- **THEN** `_parse_response` 返回 `(None, None, None)`，记录 warning 日志，`reflect_turn` 返回 `status="ok"` `reason="minimal_only"`

#### Scenario: reasoning 模型把 JSON 放在 reasoning_content
- **WHEN** LLM 响应 `content=""` 但 `reasoning_content='{"journal_enrichment": {...}}'`
- **THEN** `reflect_turn` 优先从 `reasoning_content` 提取 JSON，正常解析

### Requirement: auxiliary_llm 空内容触发 fallback
`AuxiliaryLLMRouter.call_llm` SHALL 在 `response.finish_reason != "error"` 但 `content` 为空且 `reasoning_content` 也为空时，将响应视为可降级错误，尝试下一个 candidate。

#### Scenario: primary 返回空内容但有 fallback
- **WHEN** primary candidate 返回 `content=""` `finish_reason="stop"`，且配置了 fallback candidate
- **THEN** 触发 fallback，尝试下一个 candidate，不直接返回空响应

#### Scenario: 所有 candidate 都返回空内容
- **WHEN** 所有 candidate 都返回空内容
- **THEN** 返回最后一个响应（保持现有行为），由调用方降级处理

### Requirement: 内部事件使用 system role
`_drain_pending` SHALL 将 `active_intent` 和 `subagent_result` 等 internal event 包装为 `{"role": "system", ...}` 而非 `{"role": "user", ...}`。真实用户消息仍使用 `role: user`。

#### Scenario: active_intent nudge 注入
- **WHEN** `active_intent` nudge 被注入到 messages
- **THEN** 该消息的 `role` 为 `"system"`，content 包含 `<internal_event>` 标签

#### Scenario: 真实用户消息不受影响
- **WHEN** 真实用户消息通过 `_drain_pending` 注入
- **THEN** 该消息的 `role` 为 `"user"`

### Requirement: cron 工具 turn-scoped 幂等
`CronTool._add_job` SHALL 在同一 turn 内对相同 `(name, schedule, message)` 组合只创建一次 job，第二次调用返回首次创建的 job id 并附提示"已创建，勿重复"。

#### Scenario: 同一 turn 内重复创建相同 job
- **WHEN** 同一 turn 内 LLM 两次调用 `cron add` 且参数相同
- **THEN** 第一次创建 job 并返回 id，第二次返回首次的 job id 和"已创建，勿重复"提示，不创建新 job

#### Scenario: 不同参数的 job 不受影响
- **WHEN** 同一 turn 内 LLM 调用 `cron add` 两次但参数不同（不同 message 或不同 schedule）
- **THEN** 两次都创建新 job

### Requirement: runner turn-scoped 工具幂等键
`AgentRunner.run` SHALL 维护 turn-scoped 幂等键集合，在工具执行成功后记录 `(tool_name, args_hash)`。若下一轮 LLM 再次调用相同 `(tool_name, args_hash)`，runner SHALL 拒绝执行并返回"已执行，勿重复"提示。

#### Scenario: 重复调用同一工具同一参数
- **WHEN** LLM 在第 N 轮调用 `cron add {"message": "背题"}` 成功，第 N+1 轮再次调用 `cron add {"message": "背题"}`
- **THEN** 第 N+1 轮拒绝执行，返回"已执行，勿重复"

#### Scenario: 重复调用同一工具不同参数
- **WHEN** LLM 在第 N 轮调用 `cron add {"message": "背题"}` 成功，第 N+1 轮调用 `cron add {"message": "喝水"}`
- **THEN** 第 N+1 轮正常执行

### Requirement: working_memory goal 过期检查
`WorkingMemoryManager._hydrate_goal` SHALL 检查 goal_state 的 `updated_at` 是否超过 30 分钟，超过则不再注入 `current_goal` 和 `priority_facts`。

#### Scenario: goal 未过期
- **WHEN** goal_state 的 `updated_at` 在 30 分钟内
- **THEN** 正常注入 `current_goal` 和 `priority_facts`

#### Scenario: goal 已过期
- **WHEN** goal_state 的 `updated_at` 超过 30 分钟
- **THEN** 不注入 `current_goal` 和 `priority_facts`，记录 debug 日志

### Requirement: working_memory save 冲突验证
`WorkingMemoryManager.save` SHALL 在写入前检查 `current_goal` 和 `attention_items` 是否与现有值冲突。若新值与现有值语义冲突（现有值非空且新值完全不同），保留现有值并记录 warning。

#### Scenario: 新值与现有值一致
- **WHEN** 新 `current_goal` 与现有 `current_goal` 相同
- **THEN** 正常保存

#### Scenario: 新值覆盖非空现有值
- **WHEN** 现有 `current_goal="背期末题"` 非空，新 `current_goal="玩电脑"` 完全不同
- **THEN** 保留现有 `current_goal="背期末题"`，记录 warning，不保存新值

#### Scenario: 现有值为空
- **WHEN** 现有 `current_goal=""` 为空，新 `current_goal="背期末题"`
- **THEN** 正常保存新值

### Requirement: continuity_checkpoint 包含最近轮次摘要
`continuity_checkpoint_v1` SHALL 包含 `recent_turns_summary` 字段，保存最近 2 轮对话的摘要（user message + assistant final content，各截断到 500 字符）。websocket 重连后 Agent 可通过此字段恢复最近对话上下文。

#### Scenario: 保存 checkpoint 时追加摘要
- **WHEN** `_save_continuity_checkpoint` 被调用
- **THEN** checkpoint 包含 `recent_turns_summary`，内容为最近 2 轮对话摘要

#### Scenario: 加载 checkpoint 时返回摘要
- **WHEN** `_load_continuity_checkpoint` 被调用且 checkpoint 包含 `recent_turns_summary`
- **THEN** 返回的 dict 包含 `recent_turns_summary` 字段

#### Scenario: 渲染 recovered_continuity 时包含摘要
- **WHEN** `build_recovered_continuity_context` 渲染 checkpoint
- **THEN** 输出的 `<recovered_continuity>` 块包含 `recent_turns_summary` 内容

### Requirement: Agent 防虚构指引
`identity.md` 模板 SHALL 包含指引："当用户质疑你过去的行为时（如'你忘记刚刚 X 了吗？'），先检查自己最近的输出历史，再决定是否承认。若自己的输出历史中无相关记录，应自信地指出未发现相关记录，而非顺从用户陈述。"

#### Scenario: 用户质疑 Agent 未发生的行为
- **WHEN** 用户说"你忘记刚刚提醒我玩电脑了吗？"，但 Agent 最近输出中无"玩电脑"相关内容
- **THEN** Agent 回复"我检查了最近的输出，没有发现关于'玩电脑'的提醒记录。我刚才提醒的是背期末题。"

## MODIFIED Requirements

### Requirement: _drain_pending 消息分类
`_drain_pending` 的 `_to_user_message` 方法 SHALL 拆分为两个方法：
- `_to_user_message(pending_msg)`：处理真实用户消息，返回 `{"role": "user", ...}`
- `_to_system_event(pending_msg)`：处理 `active_intent`、`subagent_result` 等 internal event，返回 `{"role": "system", ...}`

`_drain_pending` SHALL 根据 `pending_msg.metadata.get("injected_event")` 选择调用哪个方法。

## REMOVED Requirements

### Requirement: _parse_response 抛出 ValueError
**Reason**: 空内容或 invalid JSON 时抛出异常会导致元认知功能完全瘫痪，应改为优雅降级
**Migration**: `_parse_response` 改为返回 `(None, None, None)`，调用方根据返回值走 minimal journal 路径
