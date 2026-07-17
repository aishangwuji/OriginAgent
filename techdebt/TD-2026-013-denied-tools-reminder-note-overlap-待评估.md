---
schema_version: 1
---

# TD-2026-013: denied_tools (runner 层) 与 reminder_note (commands.py) 功能重叠

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-17T18:10:00+08:00 |
| 发现人 | Agent 设计阶段 |
| 关联Spec | 无（本会话 action-result causal chain 设计） |
| 关联规则 | 规则6（单一数据源）、规则33（手术式变更） |
| 优先级 | P3 |
| 状态 | 待评估 |

## 详细描述

本会话 Phase 1 在 runner 层实现了 `session.metadata["_denied_tools"]` 持久化机制，
Phase 1c 在 `commands.py` 中更新了 `_scan_recent_policy_denials` 使其优先读取
`session.metadata["_denied_tools"]`（权威源），fallback 到消息扫描。

但代码库中现在存在**两个并行的 policy_denied 追踪机制**：

### 机制 1: runner 层 denied_tools（Phase 1 新增，权威源）
- **存储位置**：`session.metadata["_denied_tools"]`（list[str]）
- **写入时机**：`_run_tool_core` 三条拒绝路径（prep_error / PolicyDeniedError / string Error）
- **读取时机**：
  - `_run_tool_core` 入口短路检查
  - `commands.py` 的 `_scan_recent_policy_denials`（Phase 1c 已改为优先读取此源）
- **语义**：session 永久拒绝（仅 `capability_*` 规则）
- **优势**：O(1) 查找，不依赖消息窗口

### 机制 2: reminder_note 消息扫描（既有，fallback）
- **存储位置**：`session.messages` 中的 `[POLICY_DENIED ...]` 文本
- **写入时机**：runner 层将 `[POLICY_DENIED ...]` 前缀写入工具结果消息
- **读取时机**：`commands.py` 的 `_scan_recent_policy_denials`（fallback，当
  `session.metadata["_denied_tools"]` 不存在时）
- **语义**：包含所有 policy_denial 类型（capability_* + SSRF + protected_path + workspace）
- **劣势**：O(n) 扫描，受 20 条消息窗口限制

### 重叠点
两者都试图回答"本 session 有哪些工具被策略拒绝了？"。Phase 1c 已通过
"优先读 metadata，fallback 扫消息"的方式缓解了不一致风险，但**根本问题**
仍未解决：两套机制各自维护各自的语义，未来可能出现分歧。

### 为何不立即合并
1. **语义不同**：denied_tools 只追踪 `capability_*`（session 永久），
   reminder_note 扫描包含所有 policy_denial 类型（含 SSRF 等单次拒绝）
2. **消费者不同**：denied_tools 用于 runner 短路（硬底线），
   reminder_note 用于 cron reminder（软提示）
3. **规则33 合规**：合并需要重新设计 reminder_note 的语义，
   超出本会话"行动-结果因果链评估"的范围

## 影响范围
- **影响文件**：
  - `OriginAgent/agent/runner.py`（denied_tools 写入与短路）
  - `OriginAgent/cli/commands.py`（`_scan_recent_policy_denials` 读取）
- **影响功能**：cron reminder 的 policy_denied 提示
- **潜在风险**：
  - 未来如果 runner 层修改了 denied_tools 的语义（如新增非 capability_* 规则），
    reminder_note 的 fallback 扫描可能产生不一致
  - 两套机制并存增加维护成本

## 复现/验证路径
1. 在 cron session 中触发 `capability_*` 拒绝 → 检查 `session.metadata["_denied_tools"]`
2. 在 cron session 中触发 SSRF 拒绝 → 检查 `session.metadata["_denied_tools"]`（应为空，
   因为 SSRF 不是 capability_*）
3. 检查 `commands.py:_scan_recent_policy_denials` 的 fallback 路径是否正确触发

## 修复方案（可选）
**方案 A（推荐，渐进式）**：扩展 `denied_tools` 的 schema，区分 `session_permanent` 和
`transient` 拒绝：
```python
session.metadata["_denied_tools"] = [
    {"name": "exec", "rule": "capability_snapshot_required", "permanent": True},
    {"name": "web_fetch", "rule": "ssrf_denied", "permanent": False, "ts": "..."},
]
```
然后 `reminder_note` 完全从 denied_tools 读取，废弃消息扫描 fallback。

**方案 B（保守）**：保持现状，但在 `_scan_recent_policy_denials` 的 fallback 路径
添加日志，标记"使用了 fallback 而非权威源"，便于未来评估 fallback 使用频率。

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-17 | Agent | 标记为待评估，等 task_state 状态机稳定后评估是否需要合并 |
