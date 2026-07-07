# Checklist

## P0：MetaCognitionReflector 健壮性

- [x] `auxiliary_llm.call_llm` 在 `finish_reason != "error"` 但 `content` 为空且 `reasoning_content` 为空时触发 fallback
- [x] `auxiliary_llm.call_llm` 所有 candidate 都返回空内容时仍返回最后一个响应（保持现有行为）
- [x] `meta_cognition_reflector._parse_response` 空内容或 invalid JSON 时返回 `(None, None, None)` 而非 `raise ValueError`
- [x] `meta_cognition_reflector._parse_response` 保留 warning 日志记录
- [x] `meta_cognition_reflector.reflect_turn` 在 `_parse_response` 返回 `(None, None, None)` 时走 minimal journal 路径，返回 `status="ok"` `reason="minimal_only"`
- [x] `meta_cognition_reflector.reflect_turn` 在 `response.content` 为空时优先尝试 `response.reasoning_content`
- [x] 单元测试覆盖：LLM 返回空字符串 → minimal journal 路径
- [x] 单元测试覆盖：LLM 返回 invalid JSON → minimal journal 路径
- [x] 单元测试覆盖：reasoning 模型把 JSON 放在 reasoning_content → 正常解析
- [x] 单元测试覆盖：primary 返回空内容触发 fallback
- [x] 单元测试覆盖：所有 candidate 都返回空内容

## P0：内部事件角色分离

- [x] `_drain_pending` 拆分为 `_to_user_message`（真实用户）和 `_to_system_event`（内部事件）
- [x] `active_intent` nudge 包装为 `{"role": "system", ...}`
- [x] `subagent_result` 包装为 `{"role": "system", ...}`
- [x] 真实用户消息仍为 `{"role": "user", ...}`
- [x] 单元测试覆盖：active_intent nudge 注入为 system role
- [x] 单元测试覆盖：真实用户消息仍为 user role
- [x] 现有 subagent_result 和 active_intent 相关回归测试通过

## P1：工具循环终止 + cron 幂等

- [x] `CronTool._add_job` 同一 turn 内相同 `(name, schedule, message)` 只创建一次
- [x] `CronTool._add_job` 重复调用返回首次 job id 和"已创建，勿重复"提示
- [x] `CronTool._add_job` 不同参数的 job 不受影响
- [x] `AgentRunner.run` 维护 turn-scoped 幂等键集合
- [x] `AgentRunner.run` 重复调用同一工具同一参数时拒绝执行
- [x] `AgentRunner.run` 重复调用同一工具不同参数时正常执行
- [x] 单元测试覆盖：同一 turn 内重复创建相同 job
- [x] 单元测试覆盖：不同参数的 job 不受影响
- [x] 单元测试覆盖：重复调用同一工具同一参数
- [x] 单元测试覆盖：重复调用同一工具不同参数
- [x] 现有 runner 回归测试通过

## P1：working_memory 防自我强化

- [x] `_hydrate_goal` 检查 goal_state 的 `updated_at` 是否超过 30 分钟
- [x] `_hydrate_goal` 超过 30 分钟不注入 `current_goal` 和 `priority_facts`
- [x] `_hydrate_due_reminders` 给 reminder 内容加 `[reminder]` 前缀
- [x] `save` 在写入前检查 `current_goal` 和 `attention_items` 是否与现有值冲突
- [x] `save` 现有值非空且新值完全不同时保留现有值并记录 warning
- [x] `save` 现有值为空时正常保存新值
- [x] 单元测试覆盖：goal 未过期
- [x] 单元测试覆盖：goal 已过期
- [x] 单元测试覆盖：reminder 注入带 source 标记
- [x] 单元测试覆盖：新值与现有值一致
- [x] 单元测试覆盖：新值覆盖非空现有值
- [x] 单元测试覆盖：现有值为空

## P1：跨会话上下文恢复

- [x] `continuity_checkpoint_v1` 包含 `recent_turns_summary` 字段
- [x] `_save_continuity_checkpoint` 保存最近 2 轮对话摘要（各截断到 500 字符）
- [x] `_load_continuity_checkpoint` 返回 `recent_turns_summary` 字段
- [x] `build_recovered_continuity_context` 渲染 `recent_turns_summary` 内容
- [x] 单元测试覆盖：保存 checkpoint 时追加摘要
- [x] 单元测试覆盖：加载 checkpoint 时返回摘要
- [x] 单元测试覆盖：渲染 recovered_continuity 时包含摘要

## P2：防虚构验证

- [x] `identity.md` 包含防虚构指引段落
- [x] 指引在所有支持的语言下生效（通过 `output_language` 注入）

## 验证与回归

- [x] `.\.venv\Scripts\python.exe -m pytest tests/ -x` 无阻断性失败
- [x] `tests/agent/test_meta_cognition_reflector.py` 通过
- [x] `tests/agent/test_working_memory.py` 通过
- [x] `tests/agent/test_runner.py` 通过
- [x] `tests/tools/test_cron.py` 通过
- [x] `auxiliary_llm` 相关测试通过
