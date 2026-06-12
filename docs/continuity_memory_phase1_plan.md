# OriginAgent 连续性与记忆操作系统 Phase 1 计划书

Date: 2026-06-08
Status: Proposed
Scope: OriginAgent 连续性与记忆操作系统 Phase 1 最小骨架

## 1. 目标

本计划书用于把 [`continuity_memory_os_outline.md`](./continuity_memory_os_outline.md) 收敛成可实施的 Phase 1 施工方案。

Phase 1 的目标不是完成完整记忆系统，而是先证明一件事：

1. OriginAgent 的上下文构造已经不再只是“最近对话 + 静态记忆拼接”。
2. 当前推理可以显式消费身份、作用域和工作记忆。
3. 后续感知、世界模型和动作层有稳定的连续性接入骨架。

## 2. 预设条件

Phase 1 以当前仓库现状为前提：

1. 已存在 `ActorResolver`、`SessionManager`、`ContextBuilder`、`FactStore`、`Dream`、`NearlineMemoryPipeline`、`SessionSearchIndexService` 等基础组件。
2. 当前运行时已经具备基础会话历史、长期事实、nearline memory、主动意图和 reminder 能力。
3. Phase 1 不引入真实感知设备，不引入世界模型持久化，不引入物理动作执行。
4. Phase 1 允许先用轻量内存结构或 session metadata 证明链路，不要求一次性固化最终存储形态。

## 3. 边界与非目标

### 3.1 本阶段必须完成

1. Identity Layer 最小模型：至少打通 `user_id`、`session_id`、`device_id` 的运行时归属。
2. Scope Model 最小版：至少支持 `device`、`user`、`session`、`task` 四类作用域判定与过滤规则。
3. Working Memory 最小版：提供独立于原始对话历史的结构化工作集。
4. `ContextAssembler` v2 最小版：按四层运行视图组装最终 LLM 输入。
5. 最小可观测性：可以解释某一轮上下文为何被组装成当前样子。

### 3.2 本阶段明确不做

1. 向量数据库选型与接入。
2. 完整检索融合排序器。
3. 世界视图的真实感知接入。
4. 晋升、遗忘、漫游预热的完整实现。
5. 大规模跨端漫游体验优化。

## 4. Phase 1 验收标准

Phase 1 完成时，必须同时满足以下条件：

1. 一轮推理的上下文输入可区分为对话视图、工作视图、检索视图、世界视图四个逻辑块。
2. 工作记忆不再隐含地混在对话历史或系统提示中，而是有独立数据模型和更新入口。
3. 作用域规则可阻止 session 级短期状态默认泄漏到 user 级或更高作用域。
4. `ContextBuilder` 不再是唯一上下文拼装入口，或其内部已形成明确的 `ContextAssembler` v2 分层逻辑。
5. 在不接入真实检索增强的情况下，任务型对话的连续性已可通过工作记忆显式改善。
6. 至少具备审计或调试输出，能查看某轮上下文包含了哪些工作记忆、召回片段和作用域过滤结果。

## 5. Phase 1 核心设计判断

### 5.1 对话视图与工作视图必须严格分层

Phase 1 需要把这两个概念明确切开：

1. 对话视图只保存最近 N 轮原始消息流，用于指代、省略和局部连贯性。
2. 工作视图只保存结构化运行状态，如当前目标、当前计划、待确认项、当前关注点、残留工具结果。
3. 对话视图不承载系统推导、摘要或任务状态。
4. 工作视图由系统显式维护，不要求每轮都从对话历史反推。

建议最小工作记忆结构：

```json
{
  "session_key": "channel:chat",
  "owner_id": "user_123",
  "scope": "session",
  "current_goal": "当前要完成什么",
  "current_plan": [
    "步骤1",
    "步骤2"
  ],
  "pending_questions": [
    "仍待用户确认的问题"
  ],
  "priority_facts": [
    "本轮高优先级事实"
  ],
  "attention_items": [
    "当前世界关注点或异常"
  ],
  "tool_residue": [
    "尚未消费完的工具结果摘要"
  ],
  "updated_at": "2026-06-08T23:00:00+08:00"
}
```

### 5.2 世界视图在 Phase 1 先保留占位接口

Phase 1 不接入真实感知，但要预留最小接口契约：

1. 世界视图由独立摘要服务异步更新，而不是每轮实时查询设备。
2. 世界视图对象必须具备时间戳或版本号。
3. 感知异常未来应既能写入世界视图，也能写入工作记忆的 `attention_items`。
4. Phase 1 可先以空对象或静态摘要占位，重点验证 `ContextAssembler` v2 的插槽和预算规则。

### 5.3 冲突处理先走保守默认

Phase 1 不追求复杂冲突治理，但要有统一出口：

1. 长期事实冲突检测优先复用 `FactStore` 现有语义和冲突机制。
2. 工作记忆与长期事实冲突时，默认本轮工作记忆优先，但必须留下冲突日志。
3. 无法自动决策的冲突统一进入“待确认”状态，由后续 `CognitiveLoop` 或当前回合决定是否追问用户。

### 5.4 检索融合先轻量起步

Phase 1 不引入向量库，检索视图先以现有能力为主：

1. `SessionSearchIndexService` 作为全文/轻语义召回主入口。
2. `FactStore` 作为长期事实来源。
3. `NearlineMemoryRetriever` 作为近线事件补充来源。
4. 检索融合先做“多源并列 + 简单排序 + 预算裁剪”，不做复杂学习排序。

## 6. 目标架构落点

Phase 1 的最小运行链路建议收敛为：

```text
Inbound message / active trigger
         ↓
ActorResolver / identity mapping
         ↓
ScopeResolver
         ↓
WorkingMemoryManager
         ↓
Retrieval providers
  ├─ session history
  ├─ FactStore
  └─ SessionSearchIndex / Nearline retriever
         ↓
ContextAssembler v2
         ↓
LLM-facing message bundle
         ↓
Working memory update + audit trail
```

## 7. 与现有组件的集成清单

| 现有组件 | Phase 1 角色 | 需要扩展 |
|---|---|---|
| `ActorResolver` | Identity Layer 起点 | 从 `actor_id` 扩展到 `user_id` / `device_id` / `session_id` 运行时映射 |
| `SessionManager` | 对话视图来源 | 增加工作记忆快照挂载点或关联入口 |
| `ContextBuilder` | 现有上下文拼装器 | 逐步下沉为 `ContextAssembler` v2 外壳或兼容入口 |
| `FactStore` | 长期事实来源与冲突能力来源 | 增加 scope 过滤约定与传播安全规则对接 |
| `SessionSearchIndexService` | 检索视图主来源 | 增加基于 identity/scope 的过滤输入 |
| `NearlineMemoryRetriever` | 近线记忆补充来源 | 保持只读补充，不抢占 Phase 1 主链路 |
| `ActiveIntentService` | 工作记忆待确认项和主动跟进信号来源 | 可逐步把候选事项写入工作记忆 |
| `ReminderStore` | 到期提醒来源 | 到期提醒以 attention/pending 形式进入工作记忆 |
| `Dream` | 长期记忆整理器 | Phase 1 仅保持兼容，不扩展晋升规则 |

## 8. 工作包拆分

### 8.1 `RQ-002` 连续性与记忆操作系统边界定义

目标：
冻结 Phase 1 的概念边界、术语和职责切分，避免实现时继续把对话、记忆、检索、状态混在一起。

范围：

1. 定义四层运行视图的输入输出边界。
2. 明确对话视图与工作视图的职责隔离。
3. 明确 Phase 1 检索视图与世界视图的最小责任。
4. 明确作用域传播安全规则和默认禁令。

交付：

1. 本 Phase 1 计划书。
2. `RQ-002` 正式任务包。
3. 最小术语表与默认规则清单。

验收：

1. 实现阶段无需再讨论“工作记忆到底算不算对话历史的一部分”。
2. 作用域传播默认规则可直接转为实现约束。

### 8.2 `RQ-003` 身份层与作用域模型设计

目标：
在当前运行时基础上补出最小身份与作用域治理层。

范围：

1. 定义 `IdentityResolver` 或在 `ActorResolver` 之上增加 identity mapping。
2. 形成 `user_id`、`session_id`、`device_id` 的最小运行时表示。
3. 定义 `device`、`user`、`session`、`task` 四层作用域。
4. 定义默认传播规则和高敏感信息的禁止向上传播规则。

建议默认规则：

1. 短期工作记忆默认 `session` 或 `task`。
2. 用户偏好默认 `user`。
3. `session -> user -> household -> global` 默认禁止自动向上传播。
4. 只有显式声明 `allow_propagation` 的事实或摘要才允许跨作用域提升。

交付：

1. identity 数据模型。
2. scope 枚举和过滤接口。
3. 作用域安全规则文档。

验收：

1. 任一上下文项都能回答“它属于谁、在哪个作用域可见”。
2. 上下文组装时可按作用域过滤。

### 8.3 `RQ-004` 工作记忆与 `ContextAssembler` v2 最小方案

目标：
建立 Phase 1 真正可运行的上下文构造骨架。

范围：

1. 新增 `WorkingMemoryManager`。
2. 定义工作记忆最小数据模型、更新入口、过期规则。
3. 设计 `ContextAssembler` v2 的四层拼装顺序。
4. 设计最小审计输出。

按当前运行时代码冻结的拼装顺序：

1. system prompt
2. runtime state block
3. recovered continuity checkpoint（可选）
4. continuity blocks：`continuity_context`、`working_memory`、`world_state`
5. reference blocks：`user_profile`、retrieval blocks、`recent_history`、`archived_session_summary`
6. internal event（可选）
7. current user message

按当前运行时代码冻结的预算策略：

1. 历史消息先按合法边界收缩。
2. 用户消息内容块内，`recent_history` 最先移除。
3. 然后移除一般 retrieval blocks。
4. 最后才移除 `user_profile` / `archived_session_summary`。
5. current user message、working memory、world state 为保底块。

交付：

1. `WorkingMemoryManager` 设计与实现任务包。
2. `ContextAssembler` v2 最小拼装方案。
3. `inspect_context` 风格的调试/审计接口设计。

验收：

1. 至少一个典型任务型对话可显式复用工作记忆，而不是从历史反推。
2. 可查看某轮上下文四层视图的内容和来源。

## 9. 实施顺序

建议按以下顺序推进，避免多模块同时漂移：

1. 先冻结 `RQ-002`，确认术语和边界。
2. 再做 `RQ-003`，补齐 identity 和 scope 最小骨架。
3. 最后做 `RQ-004`，让工作记忆和上下文组装真正跑起来。

建议不要在 Phase 1 同时实现 `RQ-013`、`RQ-014`，原因是：

1. 检索融合复杂度会提前放大。
2. 晋升与遗忘策略依赖工作记忆真实运行反馈。
3. 先把骨架跑通更利于验证连续性收益。

## 10. 最小实现策略

### 10.1 存储策略

Phase 1 建议从最轻实现起步：

1. 工作记忆优先挂载在 session 关联存储中，必要时先用内存字典加持久化快照。
2. identity 映射先以运行时解析和最小持久化为主。
3. world view 先保留空实现或静态摘要对象。

### 10.2 检索策略

1. 最近对话继续来自 `SessionManager`。
2. 长期事实来自 `FactStore`。
3. 检索视图优先复用 `SessionSearchIndexService` 和 `NearlineMemoryRetriever`。
4. 不引入外部向量存储依赖。

### 10.3 审计与调试策略

Phase 1 至少要提供一种 inspect 能力：

1. 查看工作记忆当前内容。
2. 查看本轮 context assembler 选入了哪些来源。
3. 查看作用域过滤前后差异。
4. 查看冲突或待确认项。

## 11. 测试策略

Phase 1 新增或更新测试应覆盖：

1. 身份解析：同一用户跨 session 时 identity 归属稳定。
2. 作用域过滤：session 级状态不会默认出现在 user 级召回中。
3. 工作记忆更新：目标、计划、待确认项可显式写入、覆盖、过期。
4. 上下文组装：四层视图顺序、预算与裁剪规则符合预期。
5. 冲突出口：工作记忆与长期事实冲突时会留下待确认或冲突标记。
6. 调试能力：可读出某轮 assembler 的四层视图摘要。

## 12. 风险与止损点

### 12.1 设计风险

1. 工作记忆和对话摘要重新混在一起，导致边界再次失真。
2. Phase 1 过早引入复杂检索或世界模型抽象，施工面失控。
3. 作用域模型设计得过大，拖慢最小骨架落地。

对应策略：

1. 只做最小数据模型和最小过滤规则。
2. 检索起步坚持复用现有全文/nearline/fact 能力。
3. 作用域先收敛到 `user`、`session`、`task`。

### 12.2 运行风险

1. 上下文块变多后 token 预算失控。
2. 工作记忆更新不稳，反而制造噪声。
3. 检索视图与工作视图重复，导致上下文冗余。

对应策略：

1. 对工作视图和检索视图设硬预算。
2. 先只保留高价值字段，避免“工作记忆万能字典”。
3. 在 assembler 中做去重和来源标识。

## 13. 建议提交边界

为保持阶段边界清晰，建议按以下 commit 粒度推进：

1. `RQ-002` 文档冻结与术语边界提交。
2. `RQ-003` identity + scope 数据模型与测试提交。
3. `RQ-004` working memory + assembler v2 最小实现与测试提交。
4. inspect/audit 能力补充提交。

## 14. Phase 1 完成定义

当以下目标都成立时，可以认为连续性主线的 Phase 1 完成：

1. OriginAgent 已具备最小 identity、scope、working memory、context assembler 骨架。
2. 任务型连续对话可通过工作记忆显式保持，而不只是依赖最近消息尾部。
3. 调试者可以解释某轮上下文为何包含这些块、为何遗漏那些块。
4. 后续 Phase 2 接入感知快照和世界摘要时，不需要重新改造上下文主骨架。

## 15. 当前建议的下一步

最合适的下一步是继续把本计划书拆成正式任务包，而不是直接铺开实现：

1. 先输出 `RQ-002` 任务包。
2. 再输出 `RQ-003` 任务包。
3. 最后输出 `RQ-004` 任务包。

等这三个任务包冻结后，再进入代码实现，能最大限度降低 Phase 1 返工风险。
