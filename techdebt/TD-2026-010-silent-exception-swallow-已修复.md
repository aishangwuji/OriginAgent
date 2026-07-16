---
schema_version: 1
---

# TD-2026-010: 三处静默吞异常(except Exception: pass)无日志,影响可观测性

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-16T19:30:00+08:00 |
| 发现人 | Agent 排查 |
| 关联Spec | 无(排查发现) |
| 关联规则 | 规则1(全链路追踪)、规则11(错误处理必须先分类) |
| 优先级 | P0 |
| 状态 | 已修复 |

## 详细描述
代码库中存在 3 处 `except Exception: pass` 静默吞异常,无任何日志输出,导致真实问题被完全隐藏。

### 位置 1:P0 — 数据持久化层
- **文件**:`OriginAgent/memory/store.py:119-120`
- **代码**:
  ```python
  try:
      return self._sqlite.read_all(kind, limit=limit)
  except Exception:
      pass
  ```
- **问题**:SQLite 读取失败时静默返回 `None`,调用方回退到 JSONL。设计意图是"降级",但 `except Exception` 过于宽泛,数据库损坏、Schema 不匹配等真实问题被完全隐藏。
- **影响**:整个 memory 模块的所有读取操作

### 位置 2:P0 — Cron-BDI 桥接回调
- **文件**:`OriginAgent/cli/commands.py:889-890`
- **代码**:
  ```python
  try:
      await cron_bridge.on_job_completed(...)
  except Exception:
      pass
  ```
- **问题**:桥接回调的任何异常被完全吞掉,主任务结果已通过 `raise` 传播,但 BDI 投递结果记录丢失且无任何可观测信号。
- **影响**:Cron 任务与 BDI 意愿系统的桥接投递结果追踪

### 位置 3:P2 — Action Summary 缓存
- **文件**:`OriginAgent/agent/action_summary.py:62-63`
- **代码**:
  ```python
  try:
      setattr(loop, "_cached_action_summary", dict(summary))
  except Exception:
      pass
  ```
- **问题**:缓存写入失败被静默吞掉,函数仍返回正确的 `summary`,功能正确性不受影响,但若频繁失败会导致重复计算且无告警。
- **影响**:单个函数的缓存路径

## 影响范围
- **影响文件**:`OriginAgent/memory/store.py`、`OriginAgent/cli/commands.py`、`OriginAgent/agent/action_summary.py`
- **影响功能**:数据持久化降级、Cron-BDI 桥接、动作摘要缓存
- **潜在风险**:真实故障被隐藏,无法通过日志定位;违反规则 1(全链路追踪)和规则 11(错误处理必须先分类)

## 复现/验证路径
1. 位置 1:`tests/agent/test_memory_store.py::test_read_sqlite_failure_logs_warning_and_falls_back` — 模拟 SQLite 读取抛 RuntimeError,断言 warning 日志包含 "SQLite read failed" 且降级返回空列表
2. 位置 2:`tests/cli/test_commands_cron_bridge.py::test_cron_bridge_callback_failure_logs_warning` — 模拟 `cron_bridge.on_job_completed` 抛 RuntimeError,断言 warning 日志包含 "Cron-BDI bridge" 且主任务结果不受影响
3. 位置 3:`tests/agent/test_action_summary.py::test_cache_failure_logs_debug` — 模拟 `setattr` 抛 RuntimeError,断言 debug 日志包含 "Failed to cache action summary" 且 summary 正常返回

## 修复方案(可选)
对每处 `except Exception: pass`:
1. 添加 `logger.warning(...)` 或 `logger.debug(...)` 记录异常(至少保留可观测性)
2. 按规则 11 对异常分类:可重试(网络抖动)→ 降级 + 记录;不可重试(Schema 错误)→ 显式失败或 error 级日志
3. 位置 1 建议:区分 `sqlite3.OperationalError`(降级)与其他异常(error 日志)
4. 位置 2 建议:回调异常应 warning 级记录,不吞掉
5. 位置 3 建议:debug 级记录即可,功能正确性不受影响

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-16 | Agent 排查 | P0 项(位置 1、2)建议优先修复;位置 3 可降级处理 |
| 2026-07-16 | Agent 修复 | 已修复:三处 except 块均添加日志(位置1/2 warning,位置3 debug),保留降级行为;按规则34先写测试后改实现,三处均有测试覆盖并通过 |
