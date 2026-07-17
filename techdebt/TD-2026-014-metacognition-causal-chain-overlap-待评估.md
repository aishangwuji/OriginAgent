---
schema_version: 1
---

# TD-2026-014: MetaCognition 反思桥与新因果链三层架构的功能重叠

## 基本信息
| 字段 | 内容 |
|------|------|
| 发现时间 | 2026-07-18T02:30:00+08:00 |
| 发现人 | Agent 因果链集成阶段 |
| 关联Spec | 无（action-result causal chain 三层架构） |
| 关联规则 | 规则6（单一数据源）、规则33（手术式变更）、规则38.2（系统认知持久化） |
| 优先级 | P3 |
| 状态 | 待评估 |

## 详细描述

新实现的三层因果链架构（action_trace + evaluate_action + task_state）与既有的
MetaCognition 子系统在"把 tool 失败教训反馈到下一轮 LLM 上下文"这一目标上
存在**部分功能重叠**。两者关注同一类信号（tool 失败/成功）但分工不同，
目前**没有协同机制**，可能导致：

1. **重复反馈**：同一 tool 失败既被 MetaCognition 写入 working_memory
   （通过 `_bridge_to_working_memory`），又被 action_trace 自动捕获并注入
   `<task_state>` block，LLM 可能收到重复的失败信号。
2. **状态分裂**：MetaCognition 的反思结果写入 audit ledger + working_memory，
   task_state 写入 `session.metadata["_task_state"]`，两套状态源未对齐。

### 重叠点详细分析

| 维度 | MetaCognition | 新因果链三层 | 重叠程度 |
|------|---------------|-------------|----------|
| 信号源 | tool_failure/user_correction/task_completion 触发器 | action_trace 自动捕获所有 tool_call 结果 | 部分重叠 |
| 状态存储 | audit ledger (jsonl/sqlite) + working_memory | session.metadata | 无重叠（路径不同） |
| 影响上下文方式 | `_bridge_to_working_memory` 写入 attention_items/pending_questions/priority_facts | `build_task_state_block` 注入 `<task_state>` 块 | **机制不同但目标重叠** |
| 过滤策略 | dedup + cooldown + turn_limit + policy_denied 排除 | opt-in（仅 _task_state 存在时注入）+ 5 条上限 | 重叠度低 |

**关键结论**：两者**部分重叠但不替代**：
- 新因果链是"硬约束 + 状态机"路径——数据捕获自动、状态推进显式、上下文注入条件化
- MetaCognition 是"软反思 + 学习"路径——LLM 生成可复用规则跨 session 持久化

## 影响范围
- **影响文件**：
  - `OriginAgent/agent/meta_cognition_reflector.py:811-890`（`_bridge_to_working_memory`）
  - `OriginAgent/agent/context.py:630-671`（`build_task_state_block`）
  - `OriginAgent/agent/context.py:839-850`（`build_phase1_continuity_blocks` 调用点）
- **影响功能**：LLM 上下文可能收到重复的失败反馈信号（MetaCognition bridge + task_state block）
- **潜在风险**：
  - P3 级：重复信号不会造成功能损害，但浪费 token 预算
  - 长期风险：两套状态源可能产生不一致（MetaCognition 认为"已学习"，task_state 仍为 BLOCKED）

## 复现/验证路径
1. 在一个有 tool_failure 的 turn 中，检查 LLM 上下文：
   - `working_memory` 是否包含 MetaCognition bridge 写入的 `what_failed`/`caution`
   - `<task_state>` block 是否同时包含 action_trace 中的失败记录
2. 如果两者同时出现，说明存在重复反馈

## 修复方案（可选）

### 方案 A（推荐）：分层职责明确化
- MetaCognition 专注于**跨 session 学习**（生成 learned_rule_candidate，持久化到 memory_candidates）
- task_state 专注于**session 内状态机**（当前 turn 的 action→result→state 因果链）
- 在 `_bridge_to_working_memory` 中增加判断：如果 task_state 已经记录了同一 action_id
  的失败，则不再重复写入 working_memory 的 attention_items

### 方案 B：统一上下文入口
- 将 MetaCognition 的 bridge 输出和 task_state block 合并到同一个上下文块
- 但这会增加耦合，违反规则 16（领域模型与外部适配器隔离）

## 评审记录
| 日期 | 评审人 | 结论 |
|------|--------|------|
| 2026-07-18 | Agent | 初次记录，待 task_state 稳定后评估是否需要协同机制 |
