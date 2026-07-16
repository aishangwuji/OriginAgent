---
schema_version: 1
---

# TD-2026-004: AgentLoop._last_context_assembly 从未被写入实际数据

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-15T22:00:00+08:00 |
| 发现人 | 观雪 |
| 关联Spec | .trae/specs/restore-and-wire-design-modules/spec.md |
| 关联规则 | 规则7(状态变化审计) |
| 优先级 | P2 |
| 状态 | 已修复 |

## 详细描述
`AgentLoop._last_context_assembly` 属性在 `loop.py` L348 初始化为 `{}`,在 L2355 重置为 `{}`,但**没有任何代码将实际数据写入它**。审计数据实际写入的是 `state.last_context_assembly`(SessionState 属性),而非 `loop._last_context_assembly`(AgentLoop 属性)。

这导致 `tests/agent/test_ask_user.py` 中 2 个测试失败:
- `test_ask_user_text_fallback_resumes_with_next_message`(L183: `assert loop._last_context_assembly["enabled"] is True`)
- `test_ask_user_resume_uses_legacy_context_when_phase1_disabled`(L233: `assert loop._last_context_assembly["enabled"] is False`)

两者均抛出 `KeyError: 'enabled'`,因为 `loop._last_context_assembly` 始终为空 dict。

## 影响范围
- **影响文件**: `OriginAgent/agent/loop.py`, `tests/agent/test_ask_user.py`
- **影响功能**: 上下文组装审计的状态同步
- **潜在风险**: 测试覆盖缺口;如果外部消费者依赖 `loop._last_context_assembly`,会读到空数据

## 复现/验证路径
1. 运行 `pytest tests/agent/test_ask_user.py::test_ask_user_resume_uses_legacy_context_when_phase1_disabled --tb=short`
2. 观察 `KeyError: 'enabled'`
3. 搜索 `self._last_context_assembly\s*=\s*[^{]` — 确认无写入点

## 修复方案
采用方案 A: 在 `_process_message` 返回后,将 `state.last_context_assembly` 同步到 `self._last_context_assembly`。

修复过程中发现两个独立根因,需三处同步点才能完整修复:

1. **主同步路径**(`_clear_pending_user_turn`,L2372-2391):SAVE 阶段会调用 `_clear_pending_user_turn` → `_state_holder.drop(session.key)` 清空 state。在 drop 之前将 `state.last_context_assembly` 同步到 `self._last_context_assembly`,并移除原来的 `self._last_context_assembly = {}` 重置。这是测试通过的关键路径。

2. **次要同步路径**(`_process_message`,L1862-1875):在 turn orchestrator 返回后,若 state 仍持有数据(如 turn 未清除 pending user turn),将 `state.last_context_assembly` 同步到 `self._last_context_assembly`。兜底场景覆盖。

3. **episode msg_end 同步**(`_save_turn`,L2346-2358):发现 `TurnPersistManager.save_turn` 直接 `session.messages.append(entry)` 不调用 `Session.add_message`,导致 `episode.msg_end` 过期。第二次(ask_user resume)调用时 `get_episode_history` 返回不完整的历史(缺失 assistant 的 ask_user tool_call),使 `pending_ask_id=None`,走了常规路径而非 ask_user resume 路径。在 `_save_turn` 中 save_turn 完成后同步 `active_ep.msg_end = len(session.messages)`。

## 验证结果
- `tests/agent/test_ask_user.py` 全部 6 个测试通过(含原先失败的 2 个)
- `tests/session/test_session_episodes.py` 全部 45 个测试通过(确认 episode msg_end 修改无回归)
- `tests/agent/test_loop_save_turn.py`、`test_turn_pipeline_delegation.py`、`test_continuity_phase1.py`、`test_stop_preserves_context.py` 中 7 个失败均为**预存失败**(stash 验证),与本次修改无关——根因是测试 mock 策略与 turn orchestrator 架构不匹配(如 `loop._run_agent_loop` mock 不再被直接调用)。

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-15 | 观雪 | 确认为技术债,需选择修复方案(推荐方案 A 或 C) |
| 2026-07-16 | Agent(GLM-5.2) | 采用方案 A 修复完成,3 处同步点,测试全通过,无回归 |
