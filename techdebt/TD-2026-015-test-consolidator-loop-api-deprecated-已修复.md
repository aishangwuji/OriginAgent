---
schema_version: 1
---

# TD-2026-015: test_consolidator 4 个测试为已废弃的 loop API 编写

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-17T23:10:00+08:00 |
| 发现人 | assistant（P1-B 修复过程中） |
| 关联Spec | `.trae/specs/fix-preexisting-test-failures-td015-td017/` |
| 关联规则 | 规则15（测试策略需匹配变更风险）、规则34（验证先行） |
| 优先级 | P3 |
| 状态 | 已修复 |

## 详细描述
`tests/agent/test_consolidator.py` 中 `TestConsolidatorTokenBudget` 类的以下 4 个测试：
- `test_large_chunk_archived_without_cap`
- `test_raw_archive_fallback_advances_last_consolidated`
- `test_raw_archive_fallback_breaks_round_loop`
- `test_boundary_respected_when_no_intermediate_user_turn`

均为 Phase 5 Task 7 之前的旧 loop API 编写：它们调用
`consolidator.maybe_consolidate_by_tokens(session)` **不传**
`replay_max_messages` 参数，并断言 `consolidator.archive` 被 await。

但 Phase 5 Task 7 重写了 `maybe_consolidate_by_tokens`，废弃了旧的
token 估算循环压缩逻辑——新实现只在 `_consolidate_replay_overflow`
中调用 `archive`（且仅当 `replay_max_messages` 非空时）。当
`replay_max_messages=None` 时，`_replay_overflow_boundary` 立即返回
`None`，`archive` 永远不会被调用，导致 4 个测试在
`archive.assert_awaited_once()` 处失败。

## 影响范围
- **影响文件**：`tests/agent/test_consolidator.py`
- **影响功能**：测试套件持续 4 个失败，干扰 CI 信号
- **潜在风险**：CI 噪音掩盖真实回归（"狼来了"效应）

## 复现/验证路径
修复前：4 个测试在 `archive.assert_awaited_once()` 处失败。
修复后：4 个测试全部通过（test_consolidator.py 26 个测试全绿）。

## 修复方案（已实施）
采用方案 A：改写 4 个测试以适配新 API。具体改动：
- 移除 `consolidator._SAFETY_BUFFER = 0` 和 `estimate_session_prompt_tokens` mock
- 将 `maybe_consolidate_by_tokens(session)` 改为
  `maybe_consolidate_by_tokens(session, replay_max_messages=20)`
- 更新 docstring 说明新 API 行为
- 保留原始断言意图（验证 archive 被调用、session 修剪、cursor 重置）

提交：spec `fix-preexisting-test-failures-td015-td017` Task 1。

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-17 | assistant | 登记为待评估 |
| 2026-07-18 | assistant | 修复完成，4/4 测试通过，状态更新为已修复 |
