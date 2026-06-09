# OriginAgent 红后化 Phase 1 剩余部分落地计划书

Date: 2026-06-09
Status: Proposed
Scope: 红后化总主线 `Phase 1` 剩余施工，即后台认知闭环与统一认知编排层落地

## 1. 文档目标

本计划书用于把 [`red_queen_master_plan.md`](./red_queen_master_plan.md) 中尚未完成的 `Phase 1` 剩余部分收敛成可执行方案。

这里的重点不是继续扩展连续性骨架，而是补齐总计划 `Phase 1` 的前半截：

1. 统一后台认知编排层。
2. 后台认知事件模型与审计模型。
3. 认知层与 `ActiveIntentService`、`CronService`、`Dream`、nearline memory、working memory 的关系。
4. 进入 `Phase 2` 之前必须完成的止损与验证项。

## 2. 当前状态判断

### 2.1 已完成部分

截至当前仓库状态，连续性与记忆主线的 `Phase 1` 最小骨架已基本落地：

1. 最小 identity / scope 骨架已接入：
   - [OriginAgent/agent/identity.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/identity.py)
   - [OriginAgent/agent/scope.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/scope.py)
2. 独立工作记忆已接入：
   - [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)
3. `ContextAssembler` v2 最小路径已接入：
   - [OriginAgent/agent/context_assembler.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context_assembler.py)
   - [OriginAgent/agent/context.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py)
4. continuity introspection 已具备基础可观测性：
   - [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)

### 2.2 尚未完成部分

从红后化总主线看，`Phase 1` 仍未闭环，主要缺口在后台认知编排层：

1. 缺少统一的 `CognitiveLoop / CognitiveScheduler`。
2. 缺少统一的后台认知事件模型。
3. 缺少统一的后台认知审计模型。
4. 缺少将 `goal / pending confirmation / reminder / foresight / background review` 汇总到同一编排层的机制。
5. 缺少 P1 级别“触发、抑制、写回、解释”的完整闭环。

### 2.3 关键结论

因此当前阶段的正确判断是：

1. 连续性子主线 `Phase 1` 已基本完成。
2. 红后化总主线 `Phase 1` 尚未完成。
3. 在总主线层面，不应直接全面进入 `Phase 2`。
4. 最稳妥的顺序是：先完成后台认知闭环，再进入世界视图和视觉快照原型。

## 3. 本阶段目标

本阶段剩余工作的目标是建立一个：

1. 可低频巡检 idle session。
2. 可统一筛选高价值后台机会。
3. 可限流、可去重、可关闭。
4. 可解释、可审计、可回滚。
5. 不改写现有 `AgentLoop` 主执行模型的最小后台认知编排层。

阶段完成后，系统应证明：

1. 在无用户显式输入时，系统可以有边界地思考。
2. 后台认知行为不是“模型空转”，而是基于明确触发条件。
3. 所有后台输出仍通过现有语义执行入口处理。
4. 后续 `Phase 2` 感知事件可以自然接到这条认知主线上，而不需要再造第二套调度链。

## 4. 预设条件

本计划以以下现状为前提：

1. 已存在 `ActiveIntentService`，且具备内部注入消息能力：
   - [OriginAgent/agent/active_intents.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/active_intents.py)
2. 已存在 reminder、goal、pending confirmation、nearline memory 等候选信号源。
3. 已存在 background review 与长期记忆整理能力，但它们尚未被统一编排。
4. 已存在 continuity 最小骨架，可承接后台认知结果写回工作记忆。

## 5. 边界与非目标

### 5.1 本阶段必须完成

1. `CognitiveLoop / CognitiveScheduler` 最小职责定义与实现。
2. 后台认知事件与决策审计模型。
3. 至少四类高价值后台机会的统一编排：
   - sustained goal
   - pending confirmation
   - scheduled reminder
   - nearline foresight
4. 与 working memory 的最小联动。
5. 最小 introspection / runtime status 输出。

### 5.2 本阶段明确不做

1. 真实视觉、音频、传感器接入。
2. `SceneSnapshot`、`InspectionResult`、真实 `WorldState` 存储。
3. 动作规划与物理动作闭环。
4. 完整检索融合、晋升、遗忘、漫游预热。
5. 重写 `AgentLoop` 主状态机。

## 6. 核心设计判断

### 6.1 后台认知层应是 sidecar，不是第二主循环

后台认知层应作为独立编排器存在，负责：

1. 巡检。
2. 候选筛选。
3. 限流与抑制。
4. 投递内部事件。
5. 审计与写回。

它不应取代：

1. `AgentLoop` 的统一执行职责。
2. `ActiveIntentService` 的候选生成能力。
3. `CronService` 的持久调度职责。
4. `Dream` / nearline memory 的记忆沉淀职责。

### 6.2 先统一事件模型，再扩 producer

如果先分散接入 goal、reminder、foresight、background review，而没有统一事件模型，后续接入感知异常时会产生返工。

因此应先定义：

1. 后台认知机会的统一表示。
2. 后台认知决策的统一审计格式。
3. 统一的 cooldown / suppression / escalation 出口。

### 6.3 先跑通“可解释的低频空跑”，再允许真实投递

实现顺序建议是：

1. 先实现只读巡检。
2. 再实现审计记录。
3. 再实现内部事件投递。
4. 最后再实现写回 working memory 和 runtime inspect。

这样能先验证噪音与预算，再逐步放权。

### 6.4 后台认知结果必须能回写工作记忆

P1 不要求复杂世界模型，但至少要让认知结果能进入：

1. `attention_items`
2. `pending_questions`
3. 必要时 `tool_residue`

否则后台认知层只会成为孤立的事件发射器，无法真正改善连续性质量。

## 7. 目标架构落点

P1 剩余部分的最小链路建议收敛为：

```text
Idle runtime tick / bounded scheduler
         ↓
CognitiveLoop
  ├─ session eligibility check
  ├─ signal collection
  ├─ suppression / cooldown
  ├─ audit decision
  └─ internal event publish
         ↓
MessageBus / internal inbound event
         ↓
AgentLoop / existing turn path
         ↓
WorkingMemory update + continuity context
         ↓
Introspection / audit logs
```

## 8. 模块划分

### 8.1 `RQ-001` 后台认知编排层边界与最小职责

职责：

1. 定义 `CognitiveLoop` 与 `CognitiveScheduler` 的职责边界。
2. 定义与现有 runtime 能力的关系。
3. 定义 P1 可支持的后台机会类型与统一处理流。

### 8.2 `RQ-005` 后台认知事件与审计模型

职责：

1. 定义认知事件模型。
2. 定义认知决策与抑制模型。
3. 定义最小 JSONL 审计落盘格式。

### 8.3 P1 Integration

职责：

1. 将现有 `ActiveIntentService`、goal、reminder、pending confirmation、foresight 接到统一认知层。
2. 将认知结果接入 `AgentLoop` 内部事件通道。
3. 将认知结果最小写回 working memory 与 introspection。

## 9. 详细实施顺序

### 9.1 Step A：冻结文档边界

先完成：

1. 本计划书。
2. `RQ-001`。
3. `RQ-005`。
4. `P1 integration` 任务包。

这一步的目标是把“剩余 P1 到底是什么”冻结下来。

### 9.2 Step B：认知事件与审计骨架

优先新增：

1. `cognitive_events.py`
2. `cognitive_audit.py`
3. config / feature flag / runtime status 最小接口

先支持：

1. 事件对象建模。
2. 决策对象建模。
3. append-only 审计落盘。

暂不投递真实消息。

### 9.3 Step C：空跑式 CognitiveLoop

新增最小 `CognitiveLoop`：

1. 周期扫描 session。
2. 收集候选信号。
3. 进行 eligibility / cooldown / dedupe 判定。
4. 记录“将触发什么”和“为什么没触发”。

这一步只验证噪音、预算和解释性。

### 9.4 Step D：真实内部事件投递

在空跑稳定后，再放开：

1. 通过 `MessageBus` 投递内部 inbound event。
2. 继续走现有 `_process_message()` 主路径。
3. 保留现有 active-intent 语义模型，避免旁路执行。

### 9.5 Step E：写回与 introspection

最后补齐：

1. 后台认知结果写回 `WorkingMemoryManager`。
2. introspection 汇总最近一次认知扫描结果。
3. runtime status 暴露开关、预算、最近命中与抑制原因。

## 10. 交付清单

本阶段剩余部分完成时，必须交付：

1. 一份冻结的 P1 剩余部分计划书。
2. 三份正式任务包：
   - `RQ-001`
   - `RQ-005`
   - `P1 integration`
3. 最小 `CognitiveLoop` / `CognitiveScheduler`。
4. 后台认知事件与审计模型。
5. 内部事件投递与 working memory 写回闭环。
6. introspection / runtime status 最小可观测性。

## 11. 测试策略

至少应覆盖：

1. idle session 与 busy session 的 eligibility 测试。
2. per-session cooldown 与 per-intent cooldown 测试。
3. `goal / pending confirmation / reminder / foresight` 四类候选命中测试。
4. 审计日志写入与 suppression reason 测试。
5. 内部事件投递不产生递归自触发死循环。
6. 写回 working memory 的字段正确性测试。
7. feature flag 关闭时零副作用回归测试。

## 12. 风险与止损点

### 12.1 架构风险

1. 把认知层直接塞进 `AgentLoop` 主状态机。
2. 让 `ActiveIntentService` 与新认知层职责重叠不清。
3. 先接 producer，后补事件模型，导致返工。

对应策略：

1. sidecar 化。
2. 先定义边界，再接 producer。
3. 统一 internal event 通道，不造第二执行入口。

### 12.2 运行风险

1. 后台噪音过高。
2. 同一 session 被重复打扰。
3. 背景 review、goal nudge、reminder 互相抢占。

对应策略：

1. 强制 cooldown。
2. 单轮单 session 投递上限。
3. 先串行和保守优先级，再考虑更复杂排序。

### 12.3 止损点

如果出现以下情况，不应进入 `Phase 2`：

1. 后台认知输出不可解释。
2. 后台认知噪音明显高于收益。
3. session 冷却和抑制策略无法稳定阻止重复触发。
4. 认知结果无法稳定进入现有执行主链。

## 13. 建议提交边界

建议按以下边界分别提交：

1. P1 剩余计划书与任务包文档冻结。
2. 认知事件模型与审计模型。
3. `CognitiveLoop` 空跑骨架。
4. 内部事件投递与 producer 接入。
5. working memory / introspection 收口。

## 14. Phase 1 剩余部分完成定义

当以下目标同时满足时，可认为红后化总主线 `Phase 1` 已完成：

1. 后台认知层已可运行。
2. 后台认知层已可解释、可关闭、可限流。
3. continuity 主线与后台认知层已形成最小闭环。
4. 后续接入世界视图和感知事件时，不需要再重构后台调度主链。

## 15. 当前建议的下一步

最合适的下一步不是直接实现 `Phase 2` 感知能力，而是：

1. 先冻结本计划书。
2. 先完成 `RQ-001`、`RQ-005`、`P1 integration` 三份任务包。
3. 再进入后台认知闭环的实现。
4. 在其稳定后，再推进 `P2a` 世界视图接入。
