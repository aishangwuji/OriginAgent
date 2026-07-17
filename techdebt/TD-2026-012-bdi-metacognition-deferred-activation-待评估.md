---
schema_version: 1
---

# TD-2026-012: BDI WorldStateWatcher 部分空转 + MetaCognition policy_denied 过滤器未集成 task_state

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-17T18:10:00+08:00（2026-07-18 修正过时描述） |
| 发现人 | Agent 设计阶段 |
| 关联Spec | 无（本会话 action-result causal chain 设计） |
| 关联规则 | 规则7（状态变化审计）、规则32-B（按风险分级裁剪）、规则38.2（陈旧度门槛） |
| 优先级 | P2 |
| 状态 | 待评估 |

## 详细描述

本会话实现了"行动-结果因果链评估"和"状态机化"两个核心能力，包含三层：

1. **数据层 action_trace**（已实现）：runner 自动捕获 `(tool_call, result, event)` 到
   `session.metadata["_action_trace"]`，Agent 无需主动调用。
2. **评估层 evaluate_action**（已实现）：Agent 主动评估 action_id 的 outcome/goal_alignment，
   持久化到 `session.metadata["_action_evaluations"]`。
3. **状态机层 task_state**（已实现）：EXPLORE→BLOCKED→WAITING→{RECOVERED|ABANDONED}→SATISFIED
   状态机，持久化到 `session.metadata["_task_state"]`，通过 `build_phase1_continuity_blocks`
   条件注入到 LLM 上下文。

但有两个更深层的 BDI/元认知集成点**被有意推迟**（用户决策："不激活"、"不修改"）：

### 推迟点 1: WorldStateWatcher 部分空转（severity 过滤导致 2/3 事件被忽略）

**[2026-07-18 修正]**：原描述称"没有 belief.changed 事件的发布者，Watcher 完全空转"——
**此描述已过时**。代码现状是已有 3 处发布点，但 Watcher 的 severity 过滤逻辑导致
只有 1/3 的事件源能真正触发 BDI 重规划：

| 发布点 | 文件:行号 | severity | 是否触发 BDI |
|--------|-----------|----------|-------------|
| device_discovery | `world_state.py:811-817` | 显式 HIGH | 是 |
| media_ingest | `world_state.py:962-966` | 默认 MEDIUM | **否（被静默忽略）** |
| inspection | `world_state.py:1161-1166` | 默认 MEDIUM | **否（被静默忽略）** |

`world_state_watcher.py:88-91` 的过滤逻辑仅放行 `is_critical`（CRITICAL/HIGH）事件，
MEDIUM 级事件被 debug 日志后丢弃。这不是"完全空转"，而是"部分空转"——
发布者发布了事件，但订阅者因 severity 门槛将其视为"非关键"而忽略。

### 推迟点 2: MetaCognitionReflector policy_denied 过滤器未识别 session_permanent 语义

`MetaCognitionReflector`（`meta_cognition_reflector.py:283-290`）在检测到
`status == "policy_denied"` 时会跳过反思。本会话新增的
`[POLICY_DENIED session_permanent=true]` 前缀是结构化信号，但过滤器**没有更新**
来识别这个新前缀的语义（即"这是 session 永久拒绝，不应重试"）。

**为何推迟**：runner 层短路已经确保了 session 永久拒绝的工具不会被重试
（无论 MetaCognition 是否反思）。MetaCognition 过滤器的修改是"锦上添花"
（让反思更精确），不是"必需"（短路已经兜底）。

## 影响范围
- **影响文件**：
  - `OriginAgent/agent/world_state.py:962, 1161`（MEDIUM severity 发布点）
  - `OriginAgent/bdi/world_state_watcher.py:88-91`（severity 过滤逻辑）
  - `OriginAgent/agent/meta_cognition_reflector.py:283-290`（policy_denied 过滤器）
- **影响功能**：BDI 闭环对 media_ingest/inspection 事件无响应；MetaCognition 反思日志无法区分 session_permanent 与单次拒绝
- **潜在风险**：
  - WorldStateWatcher 部分空转不造成功能损害（只是 2/3 事件无效），但可能误导未来开发者
  - MetaCognition 过滤器未更新不造成功能损害（runner 短路已兜底），但反思日志
    可能不精确（无法区分"session 永久拒绝"和"单次拒绝"）

## 复现/验证路径
1. WorldStateWatcher 部分空转: 在 `world_state.py` 搜索 `_publish_belief_changed` 的 3 个调用点，
   确认 `media_ingest` 和 `inspection` 使用默认 MEDIUM severity；在 `world_state_watcher.py:88-91`
   确认 `is_critical` 仅放行 CRITICAL/HIGH
2. MetaCognition: 在 `meta_cognition_reflector.py:283-290` 搜索 `policy_denied` 过滤逻辑，
   确认它不识别 `session_permanent=true` 语义

## 修复方案（可选）

### WorldStateWatcher 部分空转修复
方案 A（推荐）：将 `media_ingest` 和 `inspection` 的 severity 提升为 HIGH
（如果它们确实应该触发 BDI 重规划）
方案 B：降低 `is_critical` 的门槛到 MEDIUM（但这会让所有 MEDIUM 事件都触发重规划，可能过频）

### MetaCognition 过滤器更新
1. 在 `MetaCognitionReflector` 中识别 `[POLICY_DENIED session_permanent=true]` 前缀
2. 对 session_permanent=true 的拒绝，跳过"重试策略"反思，改为"状态机推进"反思
   （即引导 LLM 调用 `task_state` 工具转移到 BLOCKED→RECOVERED 或 ABANDONED）

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-17 | 用户 | 确认推迟（"不激活"、"不修改"），待 task_state 稳定后评估 |
| 2026-07-18 | Agent | 修正过时描述：原"完全空转"改为"部分空转"（severity 过滤问题），补充 3 处发布点现状 |
