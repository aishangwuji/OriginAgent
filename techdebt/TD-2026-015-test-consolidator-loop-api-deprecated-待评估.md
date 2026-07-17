---
schema_version: 1
---

# TD-2026-015: test_consolidator 4 个测试为已废弃的 loop API 编写

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-17T23:10:00+08:00 |
| 发现人 | assistant（P1-B 修复过程中） |
| 关联Spec | 无 |
| 关联规则 | 规则15（测试策略需匹配变更风险）、规则34（验证先行） |
| 优先级 | P3 |
| 状态 | 待评估 |

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

这些测试的**原始意图**（验证 token 超预算时的循环压缩、raw_archive
回退、round loop 中断、长 tool chain 边界）仍有价值，但需要改写为
适应新 API 的形式——可能需要：
1. 直接调用 `_consolidate_replay_overflow` 并传入 `replay_max_messages`，或
2. 通过 `state_save` 端到端路径触发，或
3. 若新架构下这些场景已由温区总结（warm summary）覆盖，则标记为
   已废弃并删除。

## 影响范围
- **影响文件**：`tests/agent/test_consolidator.py`
- **影响功能**：测试套件持续 4 个失败，干扰 CI 信号
- **潜在风险**：
  - CI 噪音掩盖真实回归（"狼来了"效应）
  - 新贡献者难以判断哪些失败是预存的、哪些是自己引入的
  - P1-B 修复时需额外 `git stash` 验证才能确认非本次引入

## 复现/验证路径
```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/agent/test_consolidator.py::TestConsolidatorTokenBudget::test_large_chunk_archived_without_cap `
  tests/agent/test_consolidator.py::TestConsolidatorTokenBudget::test_raw_archive_fallback_advances_last_consolidated `
  tests/agent/test_consolidator.py::TestConsolidatorTokenBudget::test_raw_archive_fallback_breaks_round_loop `
  tests/agent/test_consolidator.py::TestConsolidatorTokenBudget::test_boundary_respected_when_no_intermediate_user_turn
```
4 个全部失败，错误信息为 `archive.assert_awaited_once()` 处
`Awaited 0 times` 或 `'NoneType' object has no attribute 'args'`。

`git stash` 后（无任何工作区改动）仍然失败——确认非 P1-B 引入。

## 修复方案（可选）
需先评估 Phase 5 Task 7 的温区总结架构是否已覆盖这些场景：
1. **若已覆盖**：删除这 4 个测试，在 `TestConsolidatorTokenBudget` 中
   留下注释指向温区总结的对应测试。
2. **若未覆盖**：改写为直接调用 `_consolidate_replay_overflow` 并传入
   `replay_max_messages`，或通过 `state_save` 端到端路径触发。
3. **部分覆盖**：保留有价值的场景，删除冗余的。

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-17 | assistant | 登记为待评估，需人工确认 Phase 5 Task 7 架构覆盖范围 |
