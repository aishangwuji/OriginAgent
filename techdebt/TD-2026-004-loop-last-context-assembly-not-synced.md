# TD-2026-004: AgentLoop._last_context_assembly 从未被写入实际数据

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-15T22:00:00+08:00 |
| 发现人 | 观雪 |
| 关联Spec | .trae/specs/restore-and-wire-design-modules/spec.md |
| 关联规则 | 规则7(状态变化审计) |
| 优先级 | P2 |
| 状态 | 已确认 |

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

## 修复方案(可选)
方案 A(推荐): 在 `_process_message` 返回后,将 `state.last_context_assembly` 同步到 `self._last_context_assembly`
方案 B: 将测试更新为使用 `loop._state_holder.get(session_key).last_context_assembly` 而非 `loop._last_context_assembly`
方案 C: 添加 `@property` 将 `loop._last_context_assembly` 代理到当前 session 的 state

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-15 | 观雪 | 确认为技术债,需选择修复方案(推荐方案 A 或 C) |
