# MC-002 任务包：元认知触发采集与标准化

Date: 2026-06-12
Status: Proposed
Scope: tool failure / user correction / task completion 三类元认知触发的最小采集链路冻结

## implementation_status

`MC-002` 的最小采集链路已按保守边界落地：

1. `tool_failure`
   - 通过 `ToolRegistry.execution_observer` 采集
   - 仅接受 `error` / `policy_denied`
   - `validation_error` 只保留 tool 审计，不升级为元认知触发
2. `user_correction`
   - 通过 `AgentLoop._process_message()` turn 结束后的轻量扫描采集
   - 首版仅识别显式纠错模式
3. `task_completion`
   - 通过 `complete_goal` 成功执行 + `goal_state.status == completed` 采集
   - 不依赖返回文本自由推断
4. 已实现：
   - source-based duplicate suppression
   - session / trigger-type cooldown
   - per-turn accepted trigger limit
   - JSONL 审计与 introspection summary

## title

`MC-002` 元认知触发采集与标准化

## goal

建立 `MetaCognitionRuntime` 的最小触发采集边界，使 OriginAgent 能稳定捕获最有价值的三类元认知入口：

1. `tool_failure`
2. `user_correction`
3. `task_completion`

本任务包完成后，系统应能稳定回答：

1. 哪些 runtime 事件值得触发一次元认知记录。
2. 这些事件从哪里被采集。
3. 采集后以什么统一结构流向 `MetaCognitionRuntime`。
4. 如何避免每个失败、每次用户消息、每次结束都触发高成本反思。

## scope

本任务包覆盖以下内容：

1. 定义最小 `MetaTrigger` 结构：
   - `trigger_id`
   - `session_key`
   - `trigger_type`
   - `source_type`
   - `source_reference`
   - `severity`
   - `created_at`
   - `cooldown_key`
   - `evidence_refs`
   - `payload`
2. 冻结三类触发的采集来源：
   - `tool_failure`
   - `user_correction`
   - `task_completion`
3. 冻结最小 hook 点与来源模块：
   - 工具执行观察链
   - 用户入站消息与最近 assistant turn 对照链
   - 显式任务完成与稳定 outcome 事件链
4. 冻结标准化规则：
   - 不同来源统一映射成 `MetaTrigger`
   - 每类触发都带 evidence refs
   - 每类触发都带最小 severity 与 cooldown key
5. 冻结去重与限流规则：
   - per-session cooldown
   - per-trigger-type cooldown
   - 同一 source_reference 去重
   - 单次 turn / pass 的触发上限
6. 冻结当前阶段的保守边界：
   - 只要求捕获三类高价值触发
   - 不要求做开放式“任何不确定都算触发”的泛化采集
   - 不要求触发后立即深反思

## non_goals

本任务包不覆盖以下内容：

1. `ReflectionPlanner` 的具体决策逻辑。
2. 结构化 reflection prompt。
3. `repeated_confusion` 与 `periodic_review` 的完整采集实现。
4. 元认知对象的完整落盘与回写。
5. 多模态感知异常的元认知触发接入。
6. 任务完成的开放式自然语言推断。

## dependencies

1. [`docs/meta_cognition_runtime_outline.md`](./meta_cognition_runtime_outline.md)
2. [`docs/mc-001-meta-cognition-runtime-boundary.md`](./mc-001-meta-cognition-runtime-boundary.md)
3. [`docs/red_queen_phase1_remaining_plan.md`](./red_queen_phase1_remaining_plan.md)
4. 当前运行时锚点：
   - [OriginAgent/agent/tools/registry.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/tools/registry.py)
   - [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
   - [OriginAgent/agent/context.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py)
   - [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)
   - [OriginAgent/agent/agent_cognitive_runtime.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_cognitive_runtime.py)
   - [OriginAgent/session/goal_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/session/goal_state.py)
   - [OriginAgent/agent/tools/long_task.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/tools/long_task.py)

## files_or_modules

本任务包预期主要触达文档层：

1. [`docs/mc-001-meta-cognition-runtime-boundary.md`](./mc-001-meta-cognition-runtime-boundary.md)
2. [`docs/mc-002-meta-trigger-collection.md`](./mc-002-meta-trigger-collection.md)

后续实现预计主要影响：

1. [OriginAgent/agent/tools/registry.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/tools/registry.py)
2. [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
3. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
4. 候选新模块：
   - `OriginAgent/agent/meta_cognition_triggers.py`
   - `OriginAgent/agent/meta_cognition_runtime.py`
   - `OriginAgent/agent/meta_cognition_audit.py`
5. 测试目录：
   - `tests/agent/`
   - `tests/tools/`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. 三类核心触发 `tool_failure`、`user_correction`、`task_completion` 的采集来源已写清楚。
2. `MetaTrigger` 的最小字段集合已冻结。
3. 已明确每类触发的 evidence refs、severity、cooldown key 和去重策略。
4. 已明确当前阶段只要求显式、保守、可解释的触发采集，不做全量不确定性泛化监听。
5. 已明确触发采集与深反思执行分离，采集成功不等于立即进入高成本 reflection。
6. `MC-003` 和 `MC-004` 可直接复用本任务包定义的 trigger surface。

## tests

后续实现至少应覆盖以下测试：

1. `tool_failure` 触发测试：
   - 工具异常时产生 trigger
   - 工具返回空或失败状态时产生 trigger
   - 同一工具失败在 cooldown 内不重复触发
2. `user_correction` 触发测试：
   - 显式否定或纠错语句产生 trigger
   - 普通追问不误判为 correction
   - 同一轮重复纠错去重
3. `task_completion` 触发测试：
   - `complete_goal` 产生 trigger
   - 显式任务完成 outcome 可被标准化
   - 没有稳定 outcome 时不误触发
4. 标准化测试：
   - `MetaTrigger` 字段完整
   - `source_reference` / `cooldown_key` 可追溯
   - evidence refs 保真
5. introspection / audit 回归测试：
   - 最近触发摘要可见
   - 去重、抑制原因可见

## rollback_plan

若本任务包实现导致噪音过高、误判严重或成本异常，回滚方式应为：

1. 先关闭元认知 trigger collector feature flag。
2. 保留只读审计与最近触发摘要能力，停止进一步 reflection 触发。
3. 优先退回到只保留 `tool_failure` 的最小采集路径。
4. 不允许在未修订 `MC-002` 前继续扩大触发类型。

## open_questions

1. `user_correction` 的首版是否只识别显式否定模式，而不做语义级纠错分类。
2. `task_completion` 的首版是否只接 `complete_goal` 和明确 outcome 事件，而不尝试从普通 assistant 文本猜测“任务已完成”。
3. `tool_failure` 是否需要区分“真实失败”“策略性拒绝”“权限阻断”三种子类型，还是先统一进入同一触发类型。
