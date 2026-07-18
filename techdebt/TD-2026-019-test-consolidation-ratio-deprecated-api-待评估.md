---
schema_version: 1
---

# TD-2026-019: test_consolidation_ratio 3 个测试为已废弃的 consolidator API 编写

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-18T21:30:00+08:00 |
| 发现人 | assistant（TD-015 修复过程中发现） |
| 关联Spec | `.trae/specs/fix-preexisting-test-failures-td015-td017/` |
| 关联规则 | 规则15（测试策略需匹配变更风险）、规则34（验证先行）、规则35（技术债持久化） |
| 优先级 | P3 |
| 状态 | 待评估 |

## 详细描述
`tests/agent/test_consolidation_ratio.py` 中
`test_consolidation_ratio_controls_target` 参数化测试有 3 个 case 失败：
- `test_consolidation_ratio_controls_target[0.5-200-estimates0-1]`
- `test_consolidation_ratio_controls_target[0.1-1000-estimates1-2]`
- `test_consolidation_ratio_controls_target[0.9-200-estimates2-1]`

**根因与 TD-015 相同**：这些测试调用
`consolidator.maybe_consolidate_by_tokens(session)` 不传
`replay_max_messages` 参数。Phase 5 Task 7 重写后，当
`replay_max_messages=None` 时 `archive` 永远不会被调用，
`consolidator.archive.await_count == 0`，断言
`assert archive.await_count == N` 失败。

错误信息：`AssertionError: assert 0 == 1` / `assert 0 == 2`
（archive 实际被 await 0 次，期望 1 或 2 次）。

## 影响范围
- **影响文件**：`tests/agent/test_consolidation_ratio.py`
- **影响功能**：测试套件持续 3 个失败，干扰 CI 信号
- **潜在风险**：CI 噪音掩盖真实回归

## 复现/验证路径
```powershell
.\.venv\Scripts\python.exe -m pytest tests/agent/test_consolidation_ratio.py --basetemp=.pytest_basetmp -q --tb=line
```
3 个失败，错误为 `assert 0 == N`（archive 未被调用）。
`git stash` 后仍失败——确认非本次修复引入。

## 修复方案（可选）
与 TD-015 方案 A 相同：改写测试传入 `replay_max_messages` 参数，
或直接调用 `_consolidate_replay_overflow` 并传入该参数。
需保留原始断言意图（验证 consolidation ratio 控制目标行为）。

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-18 | assistant | 登记为待评估，根因与 TD-015 相同，建议同一批次修复 |
