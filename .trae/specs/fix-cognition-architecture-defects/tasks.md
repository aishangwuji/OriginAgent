# Tasks

## P0：MetaCognitionReflector 健壮性（防止元认知瘫痪）

- [x] Task 1: 修复 `auxiliary_llm.call_llm` 空内容触发 fallback
  - [x] SubTask 1.1: 在 `auxiliary_llm.py:489-490` 修改 `call_llm`，当 `response.finish_reason != "error"` 但 `content` 为空且 `reasoning_content` 也为空时，将响应视为可降级错误（调用 `_fallback_reason` 或新增 `_is_empty_content` 检查），尝试下一个 candidate
  - [x] SubTask 1.2: 确保所有 candidate 都返回空内容时，仍返回最后一个响应（保持现有行为），由调用方降级处理
  - [x] SubTask 1.3: 新增单元测试覆盖"primary 返回空内容触发 fallback"和"所有 candidate 都返回空内容"场景

- [x] Task 2: 修复 `meta_cognition_reflector._parse_response` 优雅降级
  - [x] SubTask 2.1: 在 `meta_cognition_reflector.py:580-593` 修改 `_parse_response`，空内容或 invalid JSON 时返回 `(None, None, None)` 而非 `raise ValueError`
  - [x] SubTask 2.2: 保留 warning 日志记录，便于排查
  - [x] SubTask 2.3: 确认 `reflect_turn`（line 387-462）在 `_parse_response` 返回 `(None, None, None)` 时走 minimal journal 路径（`enriched=None`、`reflection=None`、`trace=None`，返回 `status="ok"` `reason="minimal_only"`）
  - [x] SubTask 2.4: 新增单元测试覆盖"LLM 返回空字符串"和"LLM 返回 invalid JSON"场景

- [x] Task 3: 增加 `reasoning_content` fallback
  - [x] SubTask 3.1: 在 `reflect_turn`（line 363-393）中，LLM 响应后检查 `response.content` 是否为空，若空则尝试 `response.reasoning_content`
  - [x] SubTask 3.2: 将 `response.content or response.reasoning_content or ""` 传给 `_parse_response`
  - [x] SubTask 3.3: 新增单元测试覆盖"reasoning 模型把 JSON 放在 reasoning_content"场景

## P0：内部事件角色分离（防止 LLM 误解为用户指令）

- [x] Task 4: 拆分 `_drain_pending` 的消息分类
  - [x] SubTask 4.1: 在 `agent_runtime.py:840-858` 将 `_to_user_message` 拆分为 `_to_user_message`（真实用户）和 `_to_system_event`（内部事件）
  - [x] SubTask 4.2: `_to_system_event` 返回 `{"role": "system", "content": [runtime_block, internal_event_block]}`
  - [x] SubTask 4.3: `_drain_pending`（line 860-879）根据 `pending_msg.metadata.get("injected_event")` 选择调用哪个方法：`active_intent` 和 `subagent_result` 走 `_to_system_event`，其他走 `_to_user_message`
  - [x] SubTask 4.4: 新增单元测试覆盖"active_intent nudge 注入为 system role"和"真实用户消息仍为 user role"场景
  - [x] SubTask 4.5: 运行现有回归测试，确认 subagent_result 和 active_intent 相关测试不破坏

## P1：工具循环终止 + cron 幂等（防止重复创建）

- [x] Task 5: 实现 cron 工具 turn-scoped 幂等
  - [x] SubTask 5.1: 在 `cron.py` 的 `CronTool` 类中新增 `_turn_idempotency_keys: dict[str, str]` 实例属性（turn-scoped，通过 ContextVar 重置）
  - [x] SubTask 5.2: 在 `_add_job`（line 332-350）中计算幂等键 `f"{name}|{schedule.kind}|{schedule.at_ms or schedule.cron_expr or schedule.every_seconds}|{message}"`，检查是否已存在
  - [x] SubTask 5.3: 已存在则返回首次创建的 job id 和"已创建，勿重复"提示，不创建新 job
  - [x] SubTask 5.4: 新增单元测试覆盖"同一 turn 内重复创建相同 job"和"不同参数的 job 不受影响"场景

- [x] Task 6: 实现 runner turn-scoped 工具幂等键
  - [x] SubTask 6.1: 在 `runner.py:290-306` 的 `run` 方法中新增 `_successful_idempotency_keys: set[str]` 局部变量
  - [x] SubTask 6.2: 在工具执行成功后（约 line 446），计算 `(tool_name, args_hash)` 并加入集合
  - [x] SubTask 6.3: 在工具执行前（约 line 350-400），检查 `(tool_name, args_hash)` 是否在集合中，若在则拒绝执行并返回"已执行，勿重复"提示
  - [x] SubTask 6.4: 新增单元测试覆盖"重复调用同一工具同一参数"和"重复调用同一工具不同参数"场景
  - [x] SubTask 6.5: 运行现有 runner 回归测试，确认不破坏现有工具调用流程

## P1：working_memory 防自我强化（防止虚假记忆固化）

- [x] Task 7: 实现 `_hydrate_goal` 过期检查
  - [x] SubTask 7.1: 在 `working_memory.py:186-196` 的 `_hydrate_goal` 中，检查 goal_state 的 `updated_at` 字段
  - [x] SubTask 7.2: 若 `updated_at` 超过 30 分钟（通过 `datetime.now() - parse(updated_at)` 计算），不注入 `current_goal` 和 `priority_facts`，记录 debug 日志
  - [x] SubTask 7.3: 新增单元测试覆盖"goal 未过期"和"goal 已过期"场景

- [x] Task 8: 实现 `_hydrate_due_reminders` source 标记
  - [x] SubTask 8.1: 在 `working_memory.py:198-211` 的 `_hydrate_due_reminders` 中，给 reminder 内容加前缀 `[reminder]`
  - [x] SubTask 8.2: 新增单元测试覆盖 reminder 注入带 source 标记

- [x] Task 9: 实现 `save` 冲突验证
  - [x] SubTask 9.1: 在 `working_memory.py:110-113` 的 `save` 方法中，加载现有 snapshot
  - [x] SubTask 9.2: 若现有 `current_goal` 非空且新 `current_goal` 完全不同（非子串关系），保留现有值，记录 warning
  - [x] SubTask 9.3: 同样逻辑应用于 `attention_items`（现有列表非空且新列表完全不同时保留现有值）
  - [x] SubTask 9.4: 新增单元测试覆盖"新值与现有值一致"、"新值覆盖非空现有值"、"现有值为空"场景

## P1：跨会话上下文恢复（防止"刚恢复上下文"失忆）

- [x] Task 10: 扩展 `continuity_checkpoint_v1` 包含最近轮次摘要
  - [x] SubTask 10.1: 在 `agent_runtime.py:600-615` 的 `_save_continuity_checkpoint` 中，新增 `recent_turns_summary` 字段，保存最近 2 轮对话的 user message + assistant final content（各截断到 500 字符）
  - [x] SubTask 10.2: 从 session 历史中提取最近 2 轮对话内容
  - [x] SubTask 10.3: 在 `agent_runtime.py:618-630` 的 `_load_continuity_checkpoint` 中，返回 `recent_turns_summary` 字段
  - [x] SubTask 10.4: 在 `context.py:490-503` 的 `build_recovered_continuity_context` 中，渲染 `recent_turns_summary` 内容
  - [x] SubTask 10.5: 新增单元测试覆盖"保存 checkpoint 时追加摘要"、"加载 checkpoint 时返回摘要"、"渲染 recovered_continuity 时包含摘要"场景

## P2：防虚构验证（防止 Agent 顺从用户的错误陈述）

- [x] Task 11: 在 identity.md 增加防虚构指引
  - [x] SubTask 11.1: 在 `templates/agent/identity.md` 中增加指引段落："当用户质疑你过去的行为时（如'你忘记刚刚 X 了吗？'），先检查自己最近的输出历史，再决定是否承认。若自己的输出历史中无相关记录，应自信地指出未发现相关记录，而非顺从用户陈述。"
  - [x] SubTask 11.2: 确认指引在所有支持的语言下生效（通过 `output_language` 注入）

## 验证与回归

- [x] Task 12: 运行完整测试套件
  - [x] SubTask 12.1: 运行 `.\.venv\Scripts\python.exe -m pytest tests/ -x` 确认无阻断性失败
  - [x] SubTask 12.2: 重点验证 `tests/agent/test_meta_cognition_reflector.py`、`tests/agent/test_working_memory.py`、`tests/agent/test_runner.py`、`tests/tools/test_cron.py`
  - [x] SubTask 12.3: 验证 `auxiliary_llm` 相关测试通过

# Task Dependencies
- Task 2 依赖 Task 1（`_parse_response` 降级需要 `call_llm` 先有空内容检查）
- Task 3 独立于 Task 1/2（`reasoning_content` fallback 是 `reflect_turn` 内部的修改）
- Task 4 独立于 Task 1/2/3（`_drain_pending` 拆分是 `agent_runtime` 的修改）
- Task 5 和 Task 6 可并行（cron 幂等和 runner 幂等是独立修改）
- Task 7、8、9 可并行（`working_memory` 的三个修改点独立）
- Task 10 依赖 Task 4 完成（`_drain_pending` 拆分后再扩展 checkpoint）
- Task 11 独立于其他任务
- Task 12 依赖所有其他任务完成
