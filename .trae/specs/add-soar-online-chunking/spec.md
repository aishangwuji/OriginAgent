# Soar 在线块化 Spec

## Why
当前子代理失败仅以字符串 `failure_summary` 记录（截断 240 字符），无结构化"障碍"概念；`SkillBootstrapper` 是纯离线批处理（`scan()` 无状态），无法在子代理解决问题后即时形成技能；`MetaCognitionReflector` 显式排除子代理 turn（`agent_runtime.py:997-1000` `sender_id == "subagent"` 直接 return），导致子代理的成功解决路径永远不进入反思/学习回路。引入 Soar 的"子目标堆栈 + 在线块化"机制，使 Agent 能在子代理绕过障碍后立即将解决路径压缩为技能候选（`SkillCandidate`），从"离线批处理学习"进化为"在线即时技能形成"。

## What Changes
- 新增 `SoarObstacle` 与 `SoarSubgoal` 数据模型（结构化障碍 + 子目标堆栈帧）
- 新增 `SoarSubgoalStack` 类（LIFO 堆栈 + 原子持久化，复用 `IntentionStack.persist_to/load_from` 模式）
- 在 `SubagentManager._run_subagent` 的失败分支（`tool_error`/`error`/`Exception`）增加障碍检测，产出 `SoarObstacle`
- 在 `_announce_result` 的 `metadata` 中增加 `obstacle_type` 字段，使下游可见
- 在 `SubagentManager` 增加 `_on_subagent_success` 回调点：当子代理在障碍后成功完成时，触发块化
- 新增 `SkillBootstrapper.ingest_chunk()` 方法（有状态在线 ingest，区别于无状态 `scan()`）
- 新增 `SoarChunker` 类：将"障碍 + 解决路径"压缩为 `SkillCandidate`，调用 `SkillCandidateCompiler`
- **不在本 spec 范围**：修改 `MetaTriggerType` 或 `_schedule_meta_cognition_reflection` 的子代理排除逻辑（属 MetaCognition 模块改动，留待后续 spec）；TurnOrchestrator 的 subgoal push/pop 集成（依赖本 spec 的数据模型稳定后再接入）

## Impact
- Affected specs: `add-actr-utility-learning`（ACT-R 效用学习可消费 Soar chunk 产出的新技能）、`fix-bdi-core-defects`（IntentionStack 持久化模式可复用）
- Affected code:
  - `OriginAgent/agent/soar_models.py` — **新增**（`SoarObstacle`、`SoarSubgoal`、`SoarSubgoalStack`）
  - `OriginAgent/agent/soar_chunker.py` — **新增**（`SoarChunker` 类）
  - `OriginAgent/agent/subagent.py` — `_run_subagent` 失败分支增加障碍检测、`_announce_result` metadata 扩展、增加 `_on_subagent_success` 回调
  - `OriginAgent/agent/skill_bootstrapper.py` — 新增 `SkillBootstrapper` 类（封装 scanner + 在线 ingest）+ `ingest_chunk()` 方法
  - 测试文件（新增）

## ADDED Requirements

### Requirement: SoarObstacle 数据模型
系统 SHALL 提供 `SoarObstacle` 数据模型，结构化表示子代理遇到的障碍。

#### Scenario: 障碍分类
- **WHEN** 子代理因 `tool_error` 失败
- **THEN** 产出 `SoarObstacle(obstacle_type="tool_failure", root_cause=failure_summary, attempted_tools=tool_events)`

#### Scenario: 障碍分类 — 通用异常
- **WHEN** 子代理因未捕获异常失败
- **THEN** 产出 `SoarObstacle(obstacle_type="internal_error", root_cause=str(exc))`

#### Scenario: 障碍可恢复性提示
- **WHEN** 障碍被检测
- **THEN** `recoverable_hint` 字段基于 `ErrorKind` 分类填充（如 `NETWORK_TIMEOUT → "retry"`、`AUTHENTICATION → "reauth"`、其他 → "unknown"`）

### Requirement: SoarSubgoal 数据模型
系统 SHALL 提供 `SoarSubgoal` 数据模型，表示子目标堆栈帧。

#### Scenario: 子目标创建
- **WHEN** 主代理为解决障碍创建子代理
- **THEN** 产出 `SoarSubgoal(parent_goal_id, obstacle, working_state_snapshot, subagent_task_id, created_at)`

#### Scenario: 子目标解决
- **WHEN** 子代理成功完成且该子目标关联障碍已被绕过
- **THEN** `SoarSubgoal.solution_path` 被填充（含子代理执行的工具有序序列 + 最终结果摘要）

### Requirement: SoarSubgoalStack 持久化
`SoarSubgoalStack` SHALL 提供 LIFO 堆栈语义 + 原子持久化，复用 `IntentionStack.persist_to/load_from` 模式。

#### Scenario: push 后持久化
- **WHEN** 调用 `stack.push(subgoal)` 并传入持久化路径
- **THEN** `soar_subgoal_stack.jsonl` 被原子写入

#### Scenario: pop 后持久化
- **WHEN** 调用 `stack.pop()` 并传入持久化路径
- **THEN** 持久化文件被更新，移除栈顶帧

#### Scenario: 启动时重建
- **WHEN** 文件存在且包含 N 个帧
- **THEN** 栈深度为 N

#### Scenario: 损坏文件降级
- **WHEN** 文件损坏
- **THEN** 记 warning，返回空栈，不抛异常

### Requirement: SubagentManager 障碍检测
`SubagentManager._run_subagent` SHALL 在失败分支检测并产出 `SoarObstacle`，存入 `SubagentTaskRecord.metadata`。

#### Scenario: tool_error 失败检测
- **WHEN** `result.stop_reason == "tool_error"`
- **THEN** 产出 `SoarObstacle(obstacle_type="tool_failure")` 并存入 task record metadata

#### Scenario: 通用异常失败检测
- **WHEN** `_run_subagent` 捕获未处理异常
- **THEN** 产出 `SoarObstacle(obstacle_type="internal_error")` 并存入 task record metadata

#### Scenario: 成功路径不产出障碍
- **WHEN** 子代理成功完成
- **THEN** 不产出 `SoarObstacle`

### Requirement: _announce_result 元数据扩展
`_announce_result` SHALL 在 `metadata` 中增加 `obstacle_type` 字段（当存在障碍时），使下游 `SystemTurnHandler` 可见。

#### Scenario: 障碍结果携带 obstacle_type
- **WHEN** 子代理失败并产出 `SoarObstacle`
- **THEN** `InboundMessage.metadata["obstacle_type"]` 被设置为障碍类型

#### Scenario: 成功结果不携带 obstacle_type
- **WHEN** 子代理成功
- **THEN** `InboundMessage.metadata` 不包含 `obstacle_type` 键

### Requirement: SkillBootstrapper 在线 ingest_chunk
系统 SHALL 新增 `SkillBootstrapper` 类（有状态，封装 `SkillBootstrapperScanner`），提供 `ingest_chunk()` 方法支持在线技能形成。

#### Scenario: 在线 ingest 单个 digest
- **WHEN** 调用 `bootstrapper.ingest_chunk(digest)`
- **THEN** digest 被加入滚动窗口（按 `fingerprint` 分组）

#### Scenario: 累计达到阈值触发 compile
- **WHEN** 同一 `fingerprint` 的 digest 累计达到 `min_repeats`（默认 3）且至少一个含 `correction_flag=True`
- **THEN** 自动调用 `SkillCandidateCompiler.compile()` 产出 `SkillCandidate`
- **AND** 从滚动窗口移除该 fingerprint 的所有 digest（避免重复 compile）

#### Scenario: 未达阈值不 compile
- **WHEN** 同一 `fingerprint` 的 digest 累计 < `min_repeats`
- **THEN** 不触发 compile，digest 保留在窗口中

#### Scenario: 窗口容量限制
- **WHEN** 滚动窗口 digest 总数超过 `max_window_size`（默认 200）
- **THEN** 按 FIFO 移除最旧 digest

### Requirement: SoarChunker 块化触发
`SoarChunker` SHALL 在子代理成功解决障碍后，将"障碍 + 解决路径"压缩为 `ActionTraceDigest`（含 `correction_flag=True`）并调用 `SkillBootstrapper.ingest_chunk()`。

#### Scenario: 成功解决障碍触发块化
- **WHEN** 子代理成功完成且其 `SoarSubgoal` 关联了 `SoarObstacle`
- **THEN** `SoarChunker.chunk(subgoal)` 被调用
- **AND** 产出 `ActionTraceDigest(correction_flag=True, task_reference=obstacle.root_cause)`
- **AND** 调用 `bootstrapper.ingest_chunk(digest)`

#### Scenario: 无障碍的子代理成功不触发块化
- **WHEN** 子代理成功完成但无关联 `SoarObstacle`
- **THEN** 不触发 `SoarChunker.chunk()`

#### Scenario: 子代理的工具有序序列提取
- **WHEN** `SoarChunker.chunk(subgoal)` 执行
- **THEN** 从 `SubagentToolRecord` 列表提取 `tool_sequence`（按时间排序的工具名列表）
- **AND** 从最终结果提取 `param_preview`（截断到 200 字符）

## MODIFIED Requirements

### Requirement: SubagentManager._run_subagent 失败处理
原失败分支仅产出字符串 `failure_summary` 并调用 `_announce_result(status="error")`。

修改后：在调用 `_record_terminal_task` 前，增加障碍检测步骤：
1. 基于 `stop_reason` / 异常类型构造 `SoarObstacle`
2. 将 `SoarObstacle` 存入 `SubagentTaskRecord.metadata["soar_obstacle"]`
3. 在 `_announce_result` 调用时传入 `obstacle_type` 参数

### Requirement: SubagentManager._announce_result 签名
原签名：
```python
async def _announce_result(self, task_id, label, task, result, origin, status, origin_message_id=None)
```

修改后增加可选参数：
```python
async def _announce_result(self, task_id, label, task, result, origin, status, origin_message_id=None, obstacle_type: str | None = None)
```
当 `obstacle_type` 不为 None 时，写入 `metadata["obstacle_type"]`。

## Out of Scope（留待后续 spec）
- 修改 `MetaTriggerType` 增加 `subagent_result`/`subagent_failure` 类型
- 修改 `_schedule_meta_cognition_reflection` 移除子代理 turn 排除
- TurnOrchestrator / SystemTurnHandler 的 subgoal push/pop 集成
- `EvolutionSeed` 与 `SkillBootstrapper` 的直接对接
- `SkillCandidate.governance_path` 新增 `fast_promote` 取值
