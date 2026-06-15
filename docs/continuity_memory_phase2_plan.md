# OriginAgent 连续性与记忆操作系统 Phase 2 计划书

Date: 2026-06-09
Status: P2A Completed
Last Reviewed: 2026-06-15
Scope: OriginAgent 连续性主线的 Phase 2 感知快照与世界摘要接入方案

## 1. 目标

本计划书用于把 [`continuity_memory_os_outline.md`](./continuity_memory_os_outline.md) 中的 Phase 2 收敛成可实施方案。

Phase 2 的目标不是实现完整世界模型，而是先证明一件事：

1. 感知快照可以通过 identity / scope / context assembly 主线被稳定接入。
2. `world_view` 不再只是占位块，而是能承载当前环境摘要。
3. 当前任务可以同时消费最近对话、工作记忆、检索结果和世界摘要，而不把环境状态粗暴塞进长期事实或对话历史。

## 2. 预设条件

Phase 2 以前提为：

1. Phase 1 的最小骨架已经落地到代码中：
   - [OriginAgent/agent/identity.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/identity.py)
   - [OriginAgent/agent/scope.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/scope.py)
   - [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)
   - [OriginAgent/agent/context_assembler.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context_assembler.py)
2. 当前 `ContextBuilder` 已具备四层视图插槽，且最小 `world_state` 实现已经可以输出真实世界摘要：
   - [OriginAgent/agent/context.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py)
   - [OriginAgent/agent/world_state.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/world_state.py)
3. `AgentLoop` 已具备 runtime context、working memory 写入点、world attention 写入点和 context assembly 审计输出：
   - [OriginAgent/agent/loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py)
4. 当前仓库已为多来源附件预留 provider-neutral ingress 合同：
   - [`docs/attachments-ingress.md`](./attachments-ingress.md)
5. 当前仓库已具备最小 `inspect_snapshot` 原型、world summary 过滤与 introspection 展示路径：
   - [OriginAgent/agent/snapshot_inspection.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/snapshot_inspection.py)
   - [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
6. Phase 2 仍处于“感知原型接入”阶段，不要求真实设备控制闭环，也不要求持久化 world model 达到最终形态。

## 2.1 当前推进位置

截至 2026-06-13，当前仓库对本计划的推进应更准确地描述为：

1. `Phase 2` 已经启动，但当前实际形态更接近 `P2A`，即“本地文件快照驱动的世界摘要原型”。
2. `SceneSnapshot`、`InspectionResult`、`WorldSummary`、freshness / scope / relevance 过滤、`inspect_snapshot` 最小链路均已有实现痕迹。
3. 当前实现仍不等于真实 camera / audio / sensor ingress，也不等于完整多模态感知阶段。
4. `RQ-006` 至 `RQ-010` 已完成建档，但当前更需要继续同步任务包状态、验收口径与 `P2A` 代码原型之间的差距。

## 3. 边界与非目标

### 3.1 本阶段必须完成

1. 形成最小 `SceneSnapshot` 数据模型。
2. 形成最小 `InspectionResult` 数据模型。
3. 形成最小 `WorldState` / `WorldSummary` 读模型。
4. 明确快照归属与作用域规则：
   - 属于谁
   - 来自哪个 source / device / room
   - 默认在哪个 scope 可见
5. 让世界摘要通过现有 continuity 主线进入 `ContextAssembler`。
6. 让关键世界状态可写入 working memory 的 `attention_items`，但不与长期事实混淆。
7. 提供最小 introspection / inspect 能力，解释：
   - 哪个世界摘要进入了 prompt
   - 来源于哪些快照
   - 被哪些 scope / freshness 规则过滤

### 3.2 本阶段明确不做

1. 完整向量检索和多源学习排序。
2. 视频流全量实时理解。
3. 多摄像头大规模部署与调度。
4. 完整 `household` / `room` / `global` 作用域体系的最终定稿。
5. 感知结果自动晋升长期事实的完整策略。
6. 动作层自动消费世界摘要并执行物理动作。

## 4. Phase 2 验收标准

Phase 2 完成时，必须同时满足以下条件：

1. 至少一种快照来源可以产出结构化 `SceneSnapshot`，并能回溯到原始文件路径或等价证据。
2. `world_view` 不再固定输出 placeholder，而是能输出最小真实世界摘要。
3. 当前 LLM 输入中可以区分：
   - working memory block
   - world state block
   - reference / retrieval block
4. 世界摘要不会默认写进长期事实存储，也不会直接污染最近对话历史。
5. 世界摘要进入上下文时受到 freshness、scope 和 relevance 三类约束。
6. 当感知结果存在不确定性或冲突时，世界视图能显式标注 `uncertainties` 或 `contested` 状态。
7. introspection 可解释某轮上下文使用了哪些快照、哪些摘要、哪些过滤规则。

截至 2026-06-15，上述条件在“本地文件快照原型”范围内已满足 `P2A` 收口口径：

1. `world_view` 已从 placeholder 升级为真实可解释摘要。
2. `SceneSnapshot` / `InspectionResult` / `WorldSummary` 三层对象已按当前 dataclass 合同稳定复用。
3. `inspect_snapshot` 已可稳定写回 inspection 与 world summary。
4. introspection 已可解释 `summary`、`filtered_candidates`、`freshness`、`contested`、`selection_reasons` 与 world attention 写回结果。
5. 当前阶段仍只完成 `P2A`，不等于真实 camera / audio / sensor ingress 已启动。

## 5. 核心设计判断

### 5.1 Phase 2 的重点是“接主线”，不是“造第二套感知 prompt”

世界摘要必须通过现有 `ContextAssembler` 插槽进入 prompt，而不是在别处追加一段临时系统提示。

原因是：

1. Phase 1 已经明确四层运行视图边界。
2. 如果感知结果绕过该主线，后续会再次出现上下文归属不清和预算失控。
3. 审计、调试和作用域过滤已经围绕 continuity 主线展开，Phase 2 应复用而不是旁路。

### 5.2 快照、世界摘要、长期事实必须三层分开

Phase 2 需要强行区分三类对象：

1. `SceneSnapshot`
   - 一次感知输入的结构化摘要
   - 带来源、时间、置信度和原始证据
2. `WorldState`
   - 当前短期环境状态的聚合读模型
   - 可刷新、可过期、可冲突
3. `FactStore`
   - 稳定、高价值、跨时间仍成立的长期事实

默认规则：

1. 快照先进入 `SceneSnapshot`。
2. 世界摘要由多个 `SceneSnapshot` 聚合得出。
3. 只有经后续 Phase 3/4 治理确认的信息才考虑晋升到长期事实。

### 5.3 世界摘要要保守，不要装作“环境真相”

Phase 2 的世界摘要应优先做到：

1. 带时间戳
2. 带来源
3. 带置信度
4. 带不确定项
5. 带适用范围

不要求：

1. 完整建模房屋全状态
2. 对所有设备和空间做统一 ontology
3. 对每次快照都做深推理

### 5.4 Working memory 只承载“当前相关”的环境关注点

世界摘要不应整体复制进 working memory。

Phase 2 的推荐规则：

1. `world_view` 承载完整的当前环境摘要。
2. 只有与当前任务直接相关或存在异常的项，才进入 `working_memory.attention_items`。
3. `attention_items` 只保存紧凑提示，不保存整段场景描述。

### 5.5 Phase 2 先支持“查询式世界视图”，再考虑事件风暴

本阶段优先验证：

1. 有快照
2. 有摘要
3. 能查
4. 能注入上下文

不优先追求：

1. 高频感知事件风暴
2. 自动多轮重规划
3. 大规模主动唤起

## 6. 目标架构落点

Phase 2 的最小运行链路建议收敛为：

```text
Attachment / inbox ingress / future sensor producer
         ↓
Snapshot ingestion
         ↓
SceneSnapshot builder
         ↓
WorldSummary builder
         ↓
Identity / scope attribution
         ↓
ContextAssembler v2
  ├─ runtime state block
  ├─ recovered continuity checkpoint（可选）
  ├─ continuity blocks
  ├─ reference blocks
  ├─ internal event（可选）
  └─ current user message
         ↓
LLM-facing message bundle
         ↓
Introspection / audit / future promotion hooks
```

## 7. 与现有组件的集成清单

| 现有组件 | Phase 2 角色 | 需要增强 |
|---|---|---|
| `ContextBuilder` | 世界视图注入入口 | 用真实 `WorldSummary` 替换 placeholder world block |
| `ContextAssemblerV2` | continuity 薄编排与审计层 | 在 audit 中增加 world summary 来源、freshness、过滤摘要 |
| `WorkingMemoryManager` | 环境关注点写入点 | 增加 world-derived `attention_items` 写入约定 |
| `ActorResolver` / `ScopeResolver` | 快照归属与可见性治理 | 增加 snapshot / world summary 的 owner 和 scope 语义 |
| `RuntimeIntrospectionService` | inspect / debug 入口 | 展示 snapshot 来源、world view 选入结果与过滤原因 |
| `FactStore` | 长期事实层 | Phase 2 保持隔离，只定义未来晋升边界，不直接回灌 |
| `attachments ingress` 合同 | 快照文件入口 | 从 path-first ingress 演化到 snapshot ingestion |
| `AgentLoop._update_working_memory_from_turn` | 当前工作集更新点 | 接入与当前任务相关的 world attention 写回 |

## 8. 数据模型建议

### 8.1 `SceneSnapshot`

最小结构建议：

```json
{
  "snapshot_id": "snap_001",
  "kind": "image",
  "source": "camera.front_door",
  "scope": "device",
  "owner_id": "user_123",
  "device_id": "camera_front_door",
  "captured_at": "2026-06-09T10:00:00+08:00",
  "media_path": "inbox/camera/alice-20260607T101530Z.jpg",
  "summary": "Front door area appears empty.",
  "objects": ["door", "mat"],
  "relationships": ["door closed"],
  "confidence": 0.83,
  "uncertainties": ["person presence unclear"],
  "provenance": {
    "producer": "camera",
    "ingest_method": "workspace_inbox"
  }
}
```

最小要求：

1. 必须可回溯到原始媒体路径或等价证据。
2. 必须带 `captured_at`。
3. 必须带 `source` 和至少一个 identity / scope 归属字段。
4. 允许 `summary` 很保守，但不允许无来源高断言。

### 8.2 `InspectionResult`

最小结构建议：

```json
{
  "inspection_id": "inspect_001",
  "snapshot_id": "snap_001",
  "requested_by": "user_turn",
  "requested_at": "2026-06-09T10:05:00+08:00",
  "confirmed": ["door closed"],
  "corrected": ["no visible package near the door"],
  "new_details": ["light reflection on floor"],
  "uncertain": ["person-like shape may be reflection"],
  "confidence": 0.76
}
```

最小要求：

1. 明确它是对哪个 snapshot 的补充。
2. 能区分确认、修正和新增细节。
3. 不直接覆盖原始 snapshot，而是生成可追溯补充层。

### 8.3 `WorldSummary`

最小结构建议：

```json
{
  "summary_id": "world_001",
  "scope": "session",
  "owner_id": "user_123",
  "generated_at": "2026-06-09T10:06:00+08:00",
  "fresh_until": "2026-06-09T10:11:00+08:00",
  "focus": [
    "front door currently appears closed",
    "no confirmed visitor detected"
  ],
  "constraints": [
    "snapshot confidence is moderate"
  ],
  "uncertainties": [
    "presence near the doorway is not fully confirmed"
  ],
  "source_snapshot_ids": ["snap_001"],
  "inspection_ids": ["inspect_001"]
}
```

最小要求：

1. 有 freshness / TTL 概念。
2. 有来源快照链路。
3. 有不确定性表达。
4. 是给上下文组装消费的读模型，而不是全量持久世界数据库。

## 9. 作用域与归属规则建议

### 9.1 Phase 2 推荐最小 scope 集合

为避免一次做太大，建议 Phase 2 在 continuity 主线内部新增以下语义，但不要求一次性开放所有外显枚举：

1. `device`
2. `session`
3. `user`

其中：

1. `SceneSnapshot` 默认 `device` 或 `session`
2. `WorldSummary` 默认 `session`
3. 用户显式确认后的稳定偏好或规则，仍由 `FactStore` 走 `user`

### 9.2 默认可见性规则

1. `device` 级快照不直接跨更宽 scope 自动传播。
2. `session` 级世界摘要可在当前任务和当前会话中可见。
3. 未显式授权的感知结论不自动提升到 `user` 级长期记忆。
4. 高敏感来源如 camera / presence / security 默认保守，不做宽作用域自动共享。

## 10. 上下文构造策略

### 10.1 世界视图插入规则

建议 Phase 2 中 `ContextAssembler` 的顺序保持与当前 continuity freeze 一致：

1. system prompt
2. runtime state block
3. recovered continuity checkpoint（可选）
4. continuity blocks：`continuity_context`、`working_memory`、`world_state`
5. reference blocks：`user_profile`、retrieval blocks、`recent_history`、`archived_session_summary`
6. internal event（可选）
7. current user message

但世界视图需要从占位升级为真实摘要对象。

### 10.2 预算策略

建议最小预算策略：

1. 继承 continuity freeze 的裁剪顺序：先裁历史消息，再裁 `recent_history`，再裁一般 retrieval blocks，最后裁 `user_profile` / `archived_session_summary`。
2. working memory 与 world view 继续作为 continuity core 保底。
3. world view 的预算可以高于 placeholder，但必须在保底块内部硬限制。
4. 当 world view 与 retrieval view 内容重复时，优先保留 world view 中更近、更有时间戳的版本。

### 10.3 选入策略

世界摘要只有满足以下条件才应进入当前 prompt：

1. 仍在 freshness 窗口内。
2. 与当前 scope 可见性相容。
3. 与当前消息、当前任务或当前 attention item 有关系。

建议初始判断信号：

1. 当前 turn 明确提到场景、设备、门口、房间、刚刚、现在等环境词。
2. 当前 working memory 有世界关注点。
3. 当前 internal event 或 reminder 与环境状态有关。

## 11. 审计与调试策略

Phase 2 至少要补齐以下 inspect 能力：

1. 查看当前 world summary 内容。
2. 查看 world summary 来源于哪些 snapshot / inspection。
3. 查看哪些 snapshot 因 scope / freshness 被排除。
4. 查看哪些 world attention 被写入了 working memory。

建议在现有 `inspect_context` 输出中增加：

1. `views.world.summary`
2. `views.world.source_snapshot_ids`
3. `views.world.filtered_candidates`
4. `views.world.freshness`

## 12. 工作包拆分

说明：

1. 当前代码已经对 `RQ-006` 至 `RQ-010` 的目标做了不同程度的原型性提前实现。
2. 本节后续的主要作用不再是“从零指挥实现”，而是为现有原型补齐正式边界、验收标准与缺口清单。

### 12.1 `RQ-006` 感知分层数据模型设计

目标：
冻结 `SceneSnapshot`、`InspectionResult`、`WorldSummary` 三类对象的最小字段和职责边界。

范围：

1. 定义最小字段。
2. 定义 provenance、confidence、uncertainty 表达。
3. 定义 snapshot 与 inspection 的关系。
4. 定义 world summary 作为读模型而不是长期事实的边界。

交付：

1. `RQ-006` 正式任务包。
2. 数据模型文档与最小示例。

验收：

1. 实现阶段不再反复讨论“世界摘要算不算长期记忆”。
2. 快照、深查、世界摘要的层级边界明确。

当前状态：

1. `P2A` 已完成收口，当前三层对象合同已由运行时代码与测试共同冻结。
2. `RQ-006` 的 open questions 统一顺延到 `P2B/Phase 3`：`relationships` 聚合、`InspectionResult.status` 正式枚举、`SceneSnapshot.kind` 的多模态细分语义，本轮不继续关闭。

### 12.2 `RQ-007` 视觉快照采集与本地留存策略

目标：
把现有 path-first ingress 合同发展为最小快照采集链路。

范围：

1. 明确 snapshot ingress 目录与 sidecar metadata 约定。
2. 定义从 `uploads/` 或 `inbox/` 到 `SceneSnapshot` 的最小构建过程。
3. 明确文件留存、引用和清理策略。
4. 定义最小身份归属字段如何从 producer metadata 注入。

交付：

1. `RQ-007` 正式任务包。
2. ingress 与 snapshot 构建规则文档。

验收：

1. 至少一种来源的文件可被稳定转换为 snapshot 记录。
2. snapshot 可回溯原始路径和捕获时间。

当前状态：

1. `P2A` 已完成收口，path-first ingress 已支持 `foo.json` 优先、`foo.<ext>.json` 兼容，以及 `uploads/inbox/user-turn` 三类 provenance 默认注入。

### 12.3 `RQ-008` `inspect_snapshot` 深查工具原型

目标：
建立“粗略摘要 + 按需深查”的最小链路。

范围：

1. 定义 `inspect_snapshot` 输入输出。
2. 支持对单个 snapshot 做补充核验。
3. 输出 `confirmed` / `corrected` / `new_details` / `uncertain`。
4. 不要求在本阶段支持批量或高吞吐深查。

交付：

1. `RQ-008` 正式任务包。
2. 工具原型接口设计。

验收：

1. 能对关键 snapshot 进行回溯式深查。
2. 深查结果可被世界摘要消费。

当前状态：

1. `P2A` 已完成收口，service path / fallback path 已统一回写 `InspectionResult` 与 `WorldSummary`，并保留 `inspection_path` 解释出口。

### 12.4 `RQ-009` 世界模型最小查询接口与过期机制

目标：
为 continuity 主线提供最小真实 `world_view` 数据源。

范围：

1. 定义 `WorldSummary` 生成规则。
2. 定义 freshness / TTL 规则。
3. 定义冲突与 uncertainty 标注。
4. 定义最小 query surface，供 `ContextBuilder` 和 introspection 读取。

交付：

1. `RQ-009` 正式任务包。
2. 世界摘要最小读模型设计。

验收：

1. `world_state_context` 可以输出真实摘要。
2. 过期快照不会无限期占用 prompt 预算。

当前状态：

1. `P2A` 已完成收口，`world_state` 查询面已稳定提供 `summary / filtered_candidates / freshness / contested / selection_reasons`。

### 12.5 `RQ-010` 感知结果向 continuity 主线桥接规则

目标：
把 world summary 真正接进上下文构造和 working memory。

范围：

1. 定义哪些 world summary 可以进入 prompt。
2. 定义哪些 world attention 可以进入 `working_memory.attention_items`。
3. 定义 context audit 中的世界视图来源输出。
4. 定义与 retrieval / working memory 的去重规则。

交付：

1. `RQ-010` 正式任务包。
2. continuity 主线增强规则文档。

验收：

1. 当前会话可显式消费真实世界摘要。
2. inspect 输出能解释世界视图的注入与过滤。

当前状态：

1. `P2A` 已完成收口，continuity 主线已稳定注入真实 `world_state_context`，并暴露 world-derived attention 写回审计。
2. `selection_reasons` 与 `attention_write` 已进入 introspection；更正式的 reason taxonomy 与统一预算协调顺延到后续阶段。

## 13. 实施顺序

建议按以下顺序继续推进：

1. 先对当前 `P2A` 代码原型逐项做 gap audit，明确哪些已满足、哪些仍是占位或最小保守实现。
2. 再按现有任务包口径补强 snapshot ingress、按需深查、world summary 读模型和 continuity 主线桥接中的剩余缺口。
3. 与红后 `Phase 1` 收口计划同步，避免把 `P2A` 原型误记为完整 `Phase 2` 完成。
4. 只有在 `P2A` 边界稳定后，才考虑真实 camera / audio / sensor ingress 的前置设计。
5. 继续暂缓完整晋升与遗忘策略，避免在世界摘要稳定前污染长期事实层。

建议不要在 Phase 2 同时做完整晋升与遗忘策略，原因是：

1. 世界摘要是否稳定有价值，需要先看真实运行反馈。
2. 过早把感知结果写进长期事实，会放大污染风险。
3. Phase 3 的 retrieval fusion 会反过来影响世界摘要的使用价值。

## 14. 最小实现策略

### 14.1 存储策略

1. `SceneSnapshot` 可先用轻量 JSON/JSONL 或 workspace metadata 证明链路。
2. `InspectionResult` 可先与 snapshot 保持松耦合存储。
3. `WorldSummary` 优先做读模型，不必先做复杂持久化。

### 14.2 上下文接入策略

1. 优先复用 [OriginAgent/agent/context.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py) 的 `build_world_state_block`。
2. 优先复用 [OriginAgent/agent/context_assembler.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context_assembler.py) 的 audit 输出。
3. 优先通过 [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py) 暴露调试视图。

### 14.3 Working memory 联动策略

1. world-derived attention 必须短小、明确、可过期。
2. 不将整段 `WorldSummary` 复制进 working memory。
3. 当前任务未涉及环境时，允许 world view 不进入 working memory。

## 15. 测试策略

Phase 2 新增或更新测试应覆盖：

1. snapshot 数据模型测试：
   - 必填字段、时间戳、来源、路径、scope。
2. snapshot 归属测试：
   - 相同来源在不同 session / device 下归属正确。
3. world summary 生成测试：
   - 由一个或多个 snapshot 生成摘要。
   - 过期 snapshot 被排除。
4. continuity 注入测试：
   - `world_state_context` 进入上下文。
   - placeholder 被真实摘要替换。
5. working memory 联动测试：
   - 只有高相关环境关注点进入 `attention_items`。
6. introspection 测试：
   - inspect 可展示 snapshot 来源、world summary 和过滤结果。
7. 冲突与不确定性测试：
   - 深查修正摘要时，世界视图保留可解释冲突出口。

## 16. 风险与止损点

### 16.1 设计风险

1. 把世界摘要直接做成长期事实，导致记忆污染。
2. 把所有 snapshot 都塞进 prompt，导致预算失控。
3. 把 world view 和 working memory 复制粘贴，造成冗余。

对应策略：

1. 坚持 snapshot / world summary / long-term fact 三层分离。
2. 只注入世界摘要，不注入原始 snapshot 明细。
3. 只有环境关注点进入 working memory。

### 16.2 运行风险

1. 快照质量不稳，世界摘要误导任务决策。
2. 高敏感来源如 camera / presence 引入隐私与误判风险。
3. 作用域设计过快扩展到 household / room，拖慢最小可用落地。

对应策略：

1. 强制保留 confidence / uncertainty / provenance。
2. 高敏感来源默认保守，不自动跨作用域传播。
3. Phase 2 先收敛到 `device`、`session`、`user` 的最小组合。

## 17. 建议提交边界

为保持阶段边界清晰，建议按以下 commit 粒度推进：

1. Phase 2 计划书与 `RQ-006` 文档冻结提交。
2. `RQ-007` snapshot ingress 与本地留存提交。
3. `RQ-008` `inspect_snapshot` 原型提交。
4. `RQ-009` world summary 最小读模型提交。
5. `RQ-010` continuity 主线接入与 inspect 增强提交。

## 18. Phase 2 完成定义

当以下目标都成立时，可以认为连续性主线的 Phase 2 完成：

1. OriginAgent 已具备最小快照、深查、世界摘要链路。
2. `world_view` 已从 placeholder 升级为真实可解释摘要。
3. 真实世界摘要可以进入 LLM 上下文，但不会污染长期事实层。
4. 当前任务可在工作记忆与世界视图之间形成最小协同。
5. 调试者可以解释某轮上下文为何包含这些环境摘要、排除了哪些快照。

截至 2026-06-15，以上目标已在 `P2A` 范围内成立。当前明确的验收触发器为：

1. `tests/agent/test_continuity_phase1.py`
2. `tests/tools/test_runtime_status_tools.py`
3. `tests/agent/test_loop_save_turn.py`
4. `tests/agent/test_auto_compact.py`
5. `tests/agent/test_active_intents.py`

上述回归全部通过且无新增 failures 时，方可将 `P2A` 切到完成态。

## 18.1 P2B 承接状态

`P2A` 完成后，`Phase 2` 的下一步统一收敛为 `P2B`：

1. 把当前 path-first 文件快照原型扩展为真实 producer 可复用的 ingress 主线。
2. 在 `WorldStateManager` 内增加最小 `PerceptionEventCandidate` 事件候选层。
3. 将这些事件暴露到 introspection / audit，而不是直接接入 `CognitiveLoop` producer。

`P2B` 的主包文档为：

- [`rq-011-p2b-ingress-anomaly-bridge.md`](./rq-011-p2b-ingress-anomaly-bridge.md)

本轮继续保持以下边界：

1. `RQ-006` 的 deferred open questions 仍留在 `P2B / Phase 3` 内处理。
2. `WorldSummary` 继续只做短期读模型，不晋升长期事实。
3. 世界事件不进入 prompt 明细，不进入 `MessageBus` 主链。

## 19. 当前建议的下一步

最合适的下一步不是继续把本阶段写成纯规划，而是把“已存在的原型”收敛成“有文档边界的原型”：

1. 先核对当前 `world_state` / `inspect_snapshot` / introspection 实现与本计划验收口径之间的差距。
2. 将当前能力继续明确标记为 `P2A` 文件快照原型，避免误解为真实多模态感知已开始。
3. 同步 `RQ-006` 至 `RQ-010` 的状态标记、验收口径与剩余 gap，而不是重复把它们写成“待补档”。
4. 在完成 gap audit 和红后 `Phase 1` 收口之后，再决定是否推进真实 ingress 或更广义的 `Phase 3` 前置设计。
