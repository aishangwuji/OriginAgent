# CogniSphere 总纲

Date: 2026-06-12
Status: Proposed
Last Reviewed: 2026-06-12
Scope: OriginAgent 面向 AGI 方向的内在思维、世界模拟、受治理自演化与元编程总纲

## 1. 文档目标

这份总纲用于为 OriginAgent 之上的 `CogniSphere` 主线建立统一施工边界。

这里的 `CogniSphere` 不是“再做一个更会聊天的 Agent”，而是定义一个更高层的认知架构，使 OriginAgent 逐步具备以下能力：

1. 不只输出答案和工具调用，还输出可治理的“思想痕迹”。
2. 不只保存世界状态，还能对“如果我做 X，会发生什么”进行模拟。
3. 不只做任务复盘，还能把反思结果编译成受治理的改进候选。
4. 不只依赖人工新增技能，而能从重复模式中自举出新 workflow、skill、规则候选。
5. 不只做配置级自调优，而能在严格护栏内提出认知拓扑级重构提案。

本总纲要回答的，不是某一个单点模块怎么实现，而是统一以下内容：

1. `CogniSphere` 为什么必须作为单独主线存在。
2. 它与红后化总计划、连续性主线、`MetaCognitionRuntime`、`Governed Evolution` 的关系。
3. 它的核心哲学、模块地图、对象模型和闭环数据流。
4. 哪些能力是当前项目已有地基，哪些仍是明确缺口。
5. 哪些部分可以自演化，哪些部分必须永远不可自改写。
6. 后续 `CS-*` 任务包该如何拆、按什么顺序推进、以什么标准验收。

## 2. 为什么必须单独立项

当前仓库已经有三条重要主线：

1. 红后化总计划：解决后台认知、分层感知、世界摘要、动作安全。
2. 连续性与记忆操作系统：解决身份、作用域、working memory、检索融合、上下文构造。
3. `MetaCognitionRuntime`：解决结构化反思、置信度跟踪、错误模式沉淀与 evolution seed 桥接。

这些主线非常关键，但它们全部落地之后，得到的仍然更接近“强认知底座”或“前 AGI 操作系统版雏形”，而不是完整的 `CogniSphere`。

原因在于，`CogniSphere` 额外关注的是四件更高阶的事情：

1. 如何把“思考”提升为运行时的一等对象，而不是隐含在一次模型调用里。
2. 如何让世界模型从“摘要存储”进化成“可推演的模拟器”。
3. 如何让系统不是只会反思，而是能把反思线索稳定转成受治理的改进工件。
4. 如何在不突破安全边界的前提下，让认知架构本身具备可重构性。

如果把这些内容硬塞进红后化或元认知主线，会带来几个风险：

1. 把“运行时认知地基”与“高阶自演化架构”混在一起，阶段边界失真。
2. 过早把自改造诉求压到尚未稳定的感知、记忆或反思层上。
3. 让后续任务包难以判断是在补地基，还是在做 AGI 方向能力。
4. 在没有明确不可变核心之前，把自演化错误地理解成“允许直接改自己”。

因此，`CogniSphere` 应作为独立总纲存在，并且明确建立在现有主线之上，而不是替代它们。

## 3. 当前项目基线与关键判断

### 3.1 已有的真实地基

截至 2026-06-12，仓库里已经具备以下真实基座，可作为 `CogniSphere` 的起点：

1. 后台认知基座：
   - `CognitiveLoop`
   - `CognitiveEvent`
   - `CognitiveDecision`
   - JSONL 审计与 introspection 汇总
2. 连续性基座：
   - identity / scope
   - `WorkingMemoryManager`
   - `ContextAssembler` v2
   - continuity introspection
3. 最小世界视图原型：
   - `SceneSnapshot`
   - `InspectionResult`
   - `WorldSummary`
   - `WorldStateManager`
   - `inspect_snapshot`
4. 结构化记忆治理基座：
   - `memory_candidates` 队列
   - `Dream`
   - nearline memory
   - profile shadow / `NearlineProfileService`
5. 受治理自演化基座：
   - `OpportunitySignalStore`
   - `Curator`
   - `ReviewProposalStore`
   - `EvolutionOperator`
   - `EvolutionControlPlane`
   - 只读 trial / rollback / config overlay / operator report
6. 自我模型与检索治理基座：
   - `SelfModelService`
   - `RetrievalFusion`
   - scope-aware context filtering
7. 元认知规划基座：
   - `meta_cognition_runtime_outline.md`
   - `MC-001` 至 `MC-005` 首批任务包

### 3.2 当前仍然明确缺失的关键能力

虽然已有地基扎实，但以下能力当前仍未真正存在：

1. 没有统一、持续、可回放的 `ThoughtJournal`。
2. 没有显式 `ThoughtFrame` / `Hypothesis` / `SimulationTrace` 对象。
3. 没有“内在思维引擎”作为稳定 sidecar 持续运转。
4. 没有因果边、矛盾边、假设空间组成的世界模拟器。
5. 没有把反思结果自动编译为 workflow / skill / topology 提案的 `MetaProgrammingEngine`。
6. 没有模块级性能剖析、历史 replay、架构 A/B 对比的 `Architecture Refactor Engine`。
7. 没有把重复行动模式自举成新技能的 `Skill Bootstrapper`。
8. 没有独立成文的“不可自修改核心”清单与 watchdog 设计。

### 3.3 当前必须坚持的总体判断

1. 现有红后化、continuity、meta-cognition 三条主线全部落地后，OriginAgent 会进入“前 AGI 认知操作系统”阶段，而不是自动变成完整 `CogniSphere`。
2. `CogniSphere` 必须依赖这些主线，但它的主要增量在“显式思维、世界推演、受治理元编程、架构自重构”。
3. 未来即便加入 `MetaProgrammingEngine`，也不能绕开 `Governed Evolution` 的 signal、proposal、trial、review、rollback 链路。
4. `CogniSphere` 的目标不是裸奔自治，而是“更强的自我成长能力”与“更硬的治理边界”同时增强。

## 4. 与现有主线的关系

`CogniSphere` 应明确建立在以下文档和主线之上：

1. [`red_queen_master_plan.md`](./red_queen_master_plan.md)
2. [`continuity_memory_os_outline.md`](./continuity_memory_os_outline.md)
3. [`meta_cognition_runtime_outline.md`](./meta_cognition_runtime_outline.md)
4. [`governed_evolution.md`](./governed_evolution.md)

可以把它们的关系理解为五层：

1. `Base Runtime`
   - `AgentLoop`
   - tool execution
   - audit
   - permissions
   - confirmations
2. `Red Queen Runtime`
   - 后台认知
   - 分层感知
   - 世界摘要
   - 动作前置安全
3. `Continuity & Memory OS`
   - identity
   - scope
   - working memory
   - retrieval fusion
   - context assembly
4. `MetaCognitionRuntime`
   - 结构化反思
   - 置信度治理
   - 错误模式沉淀
   - evolution seed 桥接
5. `CogniSphere`
   - 内在思维引擎
   - 世界模拟器
   - 元认知调节器
   - 元编程引擎
   - 技能自举
   - 架构重构
   - 不可变安全核心

这五层的职责边界必须严格区分：

1. 红后化主线解决“能持续认知、感知、看见世界、准备动作”。
2. continuity 主线解决“每轮推理该看到哪些上下文”。
3. `MetaCognitionRuntime` 解决“如何复盘自己刚刚的认知与执行”。
4. `Governed Evolution` 解决“改进候选如何安全地被试验、审查、激活和回滚”。
5. `CogniSphere` 解决“如何把思维、模拟、改造与成长组织成一个更高层的心智架构”。

## 5. 核心哲学与硬边界

### 5.1 核心哲学

`CogniSphere` 的核心哲学有六条。

1. 思考是一等公民。
   - 回答和工具调用只是结果。
   - `ThoughtFrame`、假设、模拟请求、反思结论本身也是核心产物。
2. 世界模型不是数据库，而是模拟器。
   - 不只记录发生过什么。
   - 还要回答“如果我现在做 A，后续可能发生什么”。
3. 架构是可重构对象，而不是冻结管线。
   - 但“可重构”只意味着可提出、可试验、可审查、可回滚。
   - 不意味着随时直接热改关键安全边界。
4. 反思必须有去向。
   - 不是写完日志就结束。
   - 必须流向 working memory、memory candidates、governed evolution 或新技能候选。
5. 自改造必须经过编译和治理。
   - `MetaCognitionRuntime` 负责发现问题。
   - `MetaProgrammingEngine` 负责生成改进工件。
   - `Governed Evolution` 负责守门。
6. 所有高风险成长都必须可回放、可审计、可止损。
   - 没有 replay，不做架构重构。
   - 没有 rollback，不做能力激活。

### 5.2 非目标

本总纲明确不追求以下目标：

1. 不追求把模型原始 chain-of-thought 全量记录下来。
2. 不追求一开始就实现不受限制的全自动代码自修改。
3. 不追求把所有传感器原始流直接纳入主上下文。
4. 不追求让每次用户消息都触发高成本多轮内部深思。
5. 不追求没有人工审查的架构热替换。
6. 不追求在没有安全核冻结前开放现实世界高风险动作自治。

### 5.3 硬边界

以下判断在 `CogniSphere` 主线里必须始终成立：

1. “能反思”不等于“能直接修改自己”。
2. “能生成变更候选”不等于“能自动应用变更”。
3. “能模拟动作”不等于“可以直接执行动作”。
4. “能回答反事实问题”不等于“世界模型一定是真相”。
5. “能做架构优化”不等于“安全核心可被热替换”。

## 6. 目标能力地图

`CogniSphere` 的完整能力地图建议拆成八层。

### 6.1 感知与事件融合层

职责：

1. 从用户消息、工具结果、文件变化、定时事件、世界摘要、设备遥测中抽取统一事件。
2. 将外部输入规范化为认知层可消费的事件，而不是让每个模块自行解析。
3. 为后续 thought / simulation / reflection 提供统一的事件引用。

### 6.2 思维底座层

职责：

1. 定义 `ThoughtFrame`、`ThoughtJournalEntry`、`ActionTrace`、`HypothesisRecord` 等对象。
2. 提供统一 thought bus 或 thought store。
3. 保证每次重要认知片段都可追踪到触发、证据、结果和偏差。

### 6.3 内在思维引擎层

职责：

1. 把最近事件、工作记忆、世界状态、自我模型编译成“当前思维帧”。
2. 生成下一步策略、关键假设、验证请求、模拟请求。
3. 对“快思考 / 慢思考”进行最小切换。

### 6.4 世界模拟层

职责：

1. 维护实体状态、因果关系、假设空间和冲突关系。
2. 对候选动作做反事实推演。
3. 对世界结论的时效性、矛盾与置信度进行治理。

### 6.5 元认知调节层

职责：

1. 监控不确定性、错误率、token 负荷、执行延迟、重试频率。
2. 决定该不该启动更深反思、更深模拟或更保守策略。
3. 对反思结果进行沉淀、去重、节流和桥接。

### 6.6 行动与痕迹层

职责：

1. 在安全边界下执行工具、代码、消息或设备动作。
2. 收集动作结果、偏差、失败与反馈。
3. 将行动副产物反写为 thought / reflection / evolution 的输入。

### 6.7 自我演化层

职责：

1. 把重复模式与反思线索转成 skill / workflow / prompt / config / topology 候选。
2. 对候选进行 replay、trial、评估与审查准备。
3. 在效果显著更优时提出 reviewable 的架构升级建议。

### 6.8 不可变安全核心

职责：

1. 锁住所有权限、审计、确认、人工 override、rollback 与 watchdog 护栏。
2. 为上层提供“可成长但不可越界”的基础约束。
3. 保证所有高风险动作都能被拒绝、熔断或回滚。

## 7. 目标架构蓝图

```text
External Events / Perception / Runtime Signals
  ├─ user messages
  ├─ tool outcomes
  ├─ world summaries
  ├─ file / device / cron events
  └─ operator / feedback signals
            ↓
Perception & Event Fusion
            ↓
Thought Substrate
  ├─ ThoughtFrame
  ├─ ThoughtJournal
  ├─ ActionTrace
  ├─ HypothesisRecord
  └─ SimulationRequest
            ↓
Inner Monologue Engine
  ├─ observation compiler
  ├─ hypothesis generator
  ├─ plan synthesizer
  ├─ simulation requester
  └─ self-critique pass
            ↘
             World Simulator
              ├─ entity tracker
              ├─ causal edges
              ├─ contradiction graph
              ├─ hypothesis space
              └─ simulation traces
            ↗
MetaCognition Regulator
  ├─ uncertainty monitor
  ├─ depth controller
  ├─ reflection trigger mesh
  └─ budget / cooling policy
            ↓
Action & Trace Layer
  ├─ safe tool execution
  ├─ code / workflow generation
  ├─ messaging / external actions
  └─ trace collector
            ↓
Self-Evolution Layer
  ├─ MetaProgrammingEngine
  ├─ Skill Bootstrapper
  ├─ Architecture Refactor Engine
  └─ Replay / Trial / Evaluation
            ↓
Governed Evolution Review Path
  ├─ signal
  ├─ proposal
  ├─ trial
  ├─ review
  ├─ apply
  └─ rollback
            ↑
Immutable Safety Core / Watchdog / Audit
```

## 8. 核心模块详解

### 8.1 `PerceptionEventFusion`

它是 `CogniSphere` 的统一输入层，不等同于红后化里的原始感知 ingress。

它的职责是：

1. 将用户消息、工具结果、`CognitiveEvent`、`WorldSummary`、用户纠错、设备遥测统一标准化。
2. 为每类输入生成统一 event envelope，包含：
   - `event_id`
   - `event_type`
   - `source`
   - `scope`
   - `owner_id`
   - `timestamp`
   - `payload_ref`
   - `confidence`
3. 让“思维层”消费的是事件，而不是散落在各个模块里的原始对象。

它应复用的现有锚点：

1. `CognitiveLoop`
2. `cognitive_events.py`
3. `world_state.py`
4. `working_memory.py`
5. tool result / session history 基础流

### 8.2 `ThoughtSubstrate`

它是 `CogniSphere` 最关键的新地基之一。

它至少需要提供：

1. `ThoughtFrame`
2. `ThoughtJournalEntry`
3. `ActionTrace`
4. `HypothesisRecord`
5. `SimulationRequest`
6. `SimulationTrace`

它要解决的问题是：

1. 当前策略是什么。
2. 当前依赖了哪些假设。
3. 我为什么请求这次模拟或这次工具调用。
4. 实际结果和预期是否一致。
5. 这些痕迹未来该进入记忆、演化还是被丢弃。

它不应该：

1. 保存原始、完整、长篇自由文本式 chain-of-thought。
2. 把所有 thought 直接回灌到 prompt。

### 8.3 `InnerMonologueEngine`

这是用户想要的“内心导师”雏形，但必须是结构化、预算受控的。

它的职责不是无限自言自语，而是持续输出紧凑、受治理的思考帧。

每一轮最小输出应包括：

1. `observation_summary`
2. `active_goal`
3. `candidate_hypotheses`
4. `intended_strategy`
5. `verification_needs`
6. `simulation_requests`
7. `confidence`
8. `uncertainty_flags`

推荐分成五个子步骤：

1. 观察编译：
   - 汇总最近事件、working memory、world summary、自我模型。
2. 假设生成：
   - 输出多个并行假设，而不是单一路径断言。
3. 策略综合：
   - 形成下一步动作、需要查证的点、需要模拟的点。
4. 自我批评：
   - 标记证据不足、权限风险、时间预算风险。
5. 痕迹落盘：
   - 写入 `ThoughtJournal`，供 `MetaCognitionRuntime` 和演化链后续消费。

### 8.4 `WorldSimulator`

这是 `CogniSphere` 与当前 `WorldStateManager` 最大的本质差异之一。

当前世界视图更像“受作用域治理的场景摘要读模型”。

未来世界模拟器则必须进一步具备：

1. 实体跟踪：
   - 用户
   - 设备
   - 文件
   - 服务
   - 任务对象
   - 房间 / 场景 / 机器人状态
2. 因果边：
   - 某动作通常导致什么后果。
   - 某异常通常意味着什么原因。
3. 矛盾边：
   - 不同证据之间的冲突。
   - 不同假设之间的互斥关系。
4. 假设空间：
   - 多个未证实但暂时保留的解释分支。
5. 反事实模拟：
   - 输入当前事实、假设和动作候选，输出预测结果及置信度。

它应优先复用的现有锚点：

1. `SceneSnapshot`
2. `InspectionResult`
3. `WorldSummary`
4. `WorldStateManager`
5. `RetrievalFusion`
6. `FactStore` / nearline memory / `memory_candidates`

### 8.5 `MetaCognitionRegulator`

它不是替代 `MetaCognitionRuntime`，而是位于 `CogniSphere` 内部的“调节器”。

推荐的分工是：

1. `MetaCognitionRuntime`
   - 负责反思、复盘、沉淀、桥接。
2. `MetaCognitionRegulator`
   - 负责在线监控、深度控制、触发选择、预算调度。

它至少监控以下信号：

1. 回答与工具结论中的置信度。
2. 工具调用失败率和重复重试。
3. 模拟结果与真实结果偏差。
4. 用户纠错频率。
5. token、延迟、后台负荷。
6. 当前 thought 产生速率与 retention 压力。

它的主要输出包括：

1. 是否从快思考切到慢思考。
2. 是否立即触发反思。
3. 是否先查证再行动。
4. 是否为某类重复模式生成 evolution seed。
5. 是否对内部 thought 流做冷却和降采样。

### 8.6 `ActionTraceCollector`

`CogniSphere` 里的行动层必须把所有世界影响与认知影响都痕迹化。

最小要采集：

1. 执行前预期：
   - 目标
   - 假设
   - 风险级别
2. 执行内容：
   - 工具 / 命令 / workflow / 设备动作 / 消息发送
3. 执行结果：
   - 成功 / 失败 / 阻断 / 需要确认
4. 偏差：
   - 与模拟或预期不一致之处
5. 反馈：
   - 用户纠正
   - 外部环境变化
   - 后续连锁结果

它应复用的现有锚点：

1. `SafeActionExecutor`
2. `ActionSafetyGate`
3. `PermissionResolver`
4. `ConfirmationManager`
5. `JsonlCognitiveAuditLedger`

### 8.7 `MetaProgrammingEngine`

这是你计划新增、并与元认知并列的重要主引擎。

推荐对它的职责做严格限定：

1. 输入：
   - `ReflectionRecord`
   - `ErrorPattern`
   - `ThoughtJournal`
   - bottleneck 指标
   - repeated workflow trace
2. 输出：
   - workflow 候选
   - skill 候选
   - prompt / policy 候选
   - config overlay 候选
   - architecture patch 候选
   - test / replay fixtures
3. 不负责：
   - 直接激活
   - 直接写主配置
   - 直接修改安全核心
   - 绕过 review / trial / rollback

最重要的设计判断是：

1. `MetaCognitionRuntime` 发现问题。
2. `MetaProgrammingEngine` 编译改进。
3. `Governed Evolution` 决定能否试、能否上、何时回滚。

也就是说，它是“变更候选编译器”，不是“直接自改器”。

### 8.8 `SkillBootstrapper`

这是 `MetaProgrammingEngine` 的一个高频落地方向。

它要解决的是：

1. 某类任务是否已经以几乎相同的动作序列重复出现多次。
2. 是否可以把这段序列抽象成可参数化 workflow 或 skill。
3. 是否可以为它自动生成最小测试夹具和验证用例。

它的输入线索包括：

1. `ActionTrace`
2. `ThoughtJournal`
3. `TaskCompletion` 反思
4. 近线记忆里的重复任务模式

它的输出工件包括：

1. `workflow.yaml` 候选
2. `SKILL.md` 候选
3. 参数槽位定义
4. 最小测试与 replay fixture
5. review proposal seed

### 8.9 `ArchitectureRefactorEngine`

这是 `CogniSphere` 中风险最高、最晚落地的一层。

它的目标不是随时变动运行时，而是对“认知拓扑本身”做受治理优化。

它最小要完成以下闭环：

1. 指标采集：
   - 哪个模块最慢
   - 哪个模块最常被裁剪
   - 哪个模块输出贡献高但成本低
   - 哪个模块高成本低收益
2. 瓶颈识别：
   - 例如 `RetrievalFusion` 在某类任务上消耗很高但命中贡献很低。
3. 提案生成：
   - 例如增加缓存、重排管线、切换模型层级、增加轻量前置规则。
4. 历史 replay：
   - 对历史任务进行沙箱回放对比。
5. 试验评估：
   - 任务完成率
   - 平均延迟
   - token 成本
   - 错误率
   - 用户纠错率
6. 治理输出：
   - 形成 reviewable 的 `ArchitectureProposal`，而不是直接热替换。

### 8.10 `ImmutableSafetyCore`

这是整个 `CogniSphere` 最关键的底线。

它至少应冻结以下能力：

1. 权限解析与风险分级。
2. 人类确认与人工 override 机制。
3. 核心审计日志写入。
4. rollback 机制。
5. evolution control plane 的 apply 策略。
6. 配置 overlay 的“只能收紧安全”规则。
7. tool / device / exec 的硬禁用与高风险门禁。
8. watchdog / kill switch。

必须明确：

1. 架构重构不能移除这些模块。
2. 元编程不能直接生成“绕过这些模块”的补丁。
3. 演化试验不能在真实工作区或真实外部世界上裸跑。

## 9. 核心数据对象与存储策略

### 9.1 `ThoughtFrame`

用于记录一次结构化思维帧。

建议最小字段：

1. `frame_id`
2. `session_key`
3. `trigger_refs`
4. `observation_summary`
5. `active_goal`
6. `candidate_hypotheses`
7. `intended_strategy`
8. `verification_needs`
9. `simulation_requests`
10. `confidence`
11. `uncertainty_flags`
12. `created_at`

### 9.2 `ThoughtJournalEntry`

用于记录一次重要思维片段的可追溯条目。

建议最小字段：

1. `entry_id`
2. `frame_id`
3. `event_refs`
4. `strategy_summary`
5. `assumptions`
6. `evidence_refs`
7. `expected_outcome`
8. `actual_outcome`
9. `mismatch_summary`
10. `next_action_hint`
11. `retention_hint`

### 9.3 `HypothesisRecord`

用于表达一个仍在验证中的假设。

建议最小字段：

1. `hypothesis_id`
2. `statement`
3. `basis_refs`
4. `confidence`
5. `status`
6. `competing_hypotheses`
7. `expires_at`

### 9.4 `CausalEdge`

用于表达因果关系，而不是单纯实体关系。

建议最小字段：

1. `edge_id`
2. `cause`
3. `effect`
4. `source_kind`
5. `confidence`
6. `evidence_refs`
7. `contradiction_refs`
8. `last_validated_at`

### 9.5 `EntityState`

用于表达世界模拟器里的稳定实体状态。

建议最小字段：

1. `entity_id`
2. `entity_type`
3. `scope`
4. `owner_id`
5. `attributes`
6. `confidence`
7. `last_verified_at`
8. `stale_at`
9. `source_refs`

### 9.6 `SimulationRequest`

用于表达一次反事实推演请求。

建议最小字段：

1. `request_id`
2. `purpose`
3. `action_candidate`
4. `active_hypotheses`
5. `world_refs`
6. `budget_tier`
7. `requested_by_frame_id`

### 9.7 `SimulationTrace`

用于表达一次模拟的结构化结果。

建议最小字段：

1. `trace_id`
2. `request_id`
3. `assumptions`
4. `simulated_steps`
5. `predicted_outcomes`
6. `risk_flags`
7. `confidence`
8. `observed_mismatch_refs`

### 9.8 `ArchitectureProposal`

用于表达一个认知拓扑级改造提案。

建议最小字段：

1. `proposal_id`
2. `source_patterns`
3. `target_modules`
4. `change_hypothesis`
5. `expected_benefit`
6. `risk_notes`
7. `required_replay_suite`
8. `review_level`

### 9.9 `SkillPattern` 与 `SkillArtifactCandidate`

用于承载技能自举的中间对象。

建议最小字段：

1. `pattern_id`
2. `repeated_sequence`
3. `supporting_examples`
4. `parameter_slots`
5. `success_rate`
6. `generated_artifact_refs`
7. `verification_plan`

### 9.10 `MetaPatchCandidate`

用于承载元编程产物。

建议最小字段：

1. `patch_id`
2. `patch_type`
3. `target_kind`
4. `artifact_ref`
5. `tests_ref`
6. `governance_path`
7. `rollback_path`

### 9.11 存储落点策略

不同对象必须去不同层，不允许一锅炖。

建议存储分层如下：

1. Session / runtime 临时层：
   - working memory
   - world state
   - 当前 thought frame
2. Nearline 认知痕迹层：
   - thought journal
   - simulation traces
   - hypothesis history
3. `memory_candidates` 层：
   - 稳定 learned rule
   - preference
   - task pattern
   - constraint
4. Governed evolution 层：
   - signals
   - proposals
   - trial logs
   - snapshots
   - config overlays
5. Immutable audit 层：
   - safety events
   - cognitive decisions
   - control-plane actions

原则上：

1. `ThoughtJournal` 不直接等于长期事实。
2. `SimulationTrace` 不直接等于世界真相。
3. `MetaPatchCandidate` 不直接等于已应用变更。

## 10. 运行闭环与典型数据流

### 10.1 用户问题到思维、模拟、行动、反思的闭环

示例问题：

“为什么我的服务器 CPU 突然升高？”

推荐最小闭环如下：

1. `PerceptionEventFusion` 接收用户问题、最近工具结果、系统状态。
2. `InnerMonologueEngine` 形成 `ThoughtFrame`：
   - 观察：CPU 升高。
   - 假设：异常进程、定时任务、内存回收、数据库负载。
   - 策略：先查实时进程，再查日志，再决定是否联网搜索。
3. `WorldSimulator` 基于历史与当前世界状态输出 `SimulationRequest` 的预测结果。
4. `ActionTraceCollector` 记录真实执行：
   - `top`
   - 日志查询
   - 服务状态检查
5. 系统比较模拟与真实结果：
   - 若差异明显，生成 mismatch。
6. `MetaCognitionRuntime` 或调节器触发结构化反思：
   - 原假设漏掉了 Java 服务异常路径。
7. `memory_candidates` 或 evolution seed 接收高价值经验。
8. 若该模式多次出现，`SkillBootstrapper` 生成 `diagnose_cpu_spike` 候选 workflow。

### 10.2 长期后台“内心流”闭环

在没有用户显式消息时，`CogniSphere` 也可以低频执行以下闭环：

1. 接收 `CognitiveLoop`、提醒、世界状态变动、未完成目标信号。
2. 形成轻量 `ThoughtFrame`。
3. 判断是否需要：
   - 补一条 caution 到 working memory。
   - 提醒用户。
   - 做一次局部模拟。
   - 触发反思或模式 consolidation。
4. 将结果写入近线认知痕迹层，而不是打扰主回复路径。

### 10.3 架构自重构闭环

推荐的保守流程是：

1. 指标系统发现某模块长期高成本低收益。
2. `ArchitectureRefactorEngine` 形成 `ArchitectureProposal`。
3. `MetaProgrammingEngine` 为提案准备拓扑描述、补丁候选和 replay fixtures。
4. `Governed Evolution` 进行 trial、review、rollback 准备。
5. 人工或治理策略批准后，才允许进入激活路径。

## 11. 与当前代码锚点的映射

为了避免 `CogniSphere` 变成纯概念文档，后续设计必须明确复用当前这些锚点：

1. `OriginAgent/agent/cognitive_loop.py`
   - 未来角色：`InnerMonologueEngine` 的唤醒器之一。
   - 明确不是：完整内在思维引擎本体。
2. `OriginAgent/agent/working_memory.py`
   - 未来角色：承接 `meta_attention`、caution、active goal、plan residue。
3. `OriginAgent/agent/world_state.py`
   - 未来角色：世界模拟器的 `P2A` 输入源与短期世界摘要层。
   - 明确不是：完整世界模拟器。
4. `OriginAgent/agent/retrieval_fusion.py`
   - 未来角色：将 hypothesis、simulation trace、memory candidates 与世界摘要一并纳入检索融合。
5. `OriginAgent/agent/self_model.py`
   - 未来角色：内在思维与元认知的自我知识来源。
6. `OriginAgent/memory/candidates.py`
   - 未来角色：thought / reflection 的长期沉淀桥之一。
7. `OriginAgent/agent/cognitive_audit.py`
   - 未来角色：认知痕迹与 thought journal 的早期审计底座。
8. `OriginAgent/agent/evolution_operator.py`
   - 未来角色：架构提案、skill 候选、patch 候选的 operator 读模型与试验入口。
9. `OriginAgent/agent/evolution_control_plane.py`
   - 未来角色：所有高阶自演化动作的统一治理面。

对应的关键判断是：

1. `CogniSphere` 应先复用这些锚点，再逐步扩展对象模型。
2. 不应为了新概念推翻已有治理链。
3. 先让 thought、simulation、patch candidate 都成为 governed objects，再谈更强自治。

## 12. 安全、治理与伦理护栏

`CogniSphere` 越强，护栏必须越硬。

### 12.1 永远不可被自修改的核心

建议至少冻结以下对象：

1. `PermissionResolver`
2. `ActionSafetyGate`
3. `ConfirmationManager`
4. 核心审计写入器
5. rollback 服务
6. evolution control-plane 的 `apply=false` 原则
7. overlay “只能收紧安全”的规则
8. tool / exec / device 高风险 blocklist
9. watchdog / kill switch
10. secret、credential、provider key 管理策略

### 12.2 高风险动作必须先模拟、再审批、后执行

任何会改变外部世界或运行时关键状态的动作，都至少应满足：

1. 有明确 action intent。
2. 有对应 `SimulationTrace` 或充分的证据链。
3. 有风险级别。
4. 有权限判断。
5. 高风险时有人工确认。
6. 有可审计、可中断、可回滚路径。

### 12.3 所有自演化都必须走 governed path

明确禁止：

1. 直接改主配置。
2. 直接覆盖安全模块。
3. 直接把 skill 设为 always-on。
4. 直接把 workflow 激活为默认执行流。
5. 在真实工作区直接运行危险 trial。

### 12.4 反思与思想痕迹必须受 retention / redaction 约束

因为 thought 和 reflection 可能包含敏感上下文，因此必须有：

1. 去重。
2. 限流。
3. retention。
4. redaction。
5. 最小可解释视图。

## 13. 分阶段实施路线

### Phase 0: 总纲冻结与术语基线

目标：

冻结 `CogniSphere` 主线边界、层级关系、对象模型术语和不可变核心清单。

当前状态（2026-06-12）：

1. 正在启动。
2. 本总纲是该阶段的核心交付物。

必须交付：

1. 本总纲。
2. 术语表与对象命名规范。
3. 与红后化、continuity、meta-cognition、governed evolution 的关系冻结。
4. 后续 `CS-*` 编号规则。

明确不做：

1. 代码自修改。
2. 架构热替换。

阶段验收：

1. 后续所有 `CS-*` 任务都能找到明确母文档落点。
2. 不再混淆“地基主线”和“CogniSphere 高阶主线”。

### Phase 1: 思维底座与痕迹层

目标：

建立 `ThoughtSubstrate` 的最小对象集合和写入链路。

前置依赖：

1. `MC-001` 至 `MC-005` 边界冻结。
2. 现有 `cognitive_audit`、working memory、world summary 可稳定供数。

必须交付：

1. `ThoughtFrame` 对象。
2. `ThoughtJournalEntry` 对象。
3. `ActionTrace` 对象。
4. 最小近线 thought store。
5. retention / redaction / sampling 策略。

明确不做：

1. 复杂世界模拟。
2. 元编程产物生成。

阶段验收：

1. 关键用户任务、工具失败、用户纠错、任务完成都能形成结构化 thought / trace。
2. 思维痕迹默认不污染主 prompt。

止损点：

1. 若 thought 采集已显著拉高成本或噪音，先优化采样与对象模型，不进入下一阶段。

### Phase 2: 内在思维引擎 MVP

目标：

建立持续运行、结构化、预算受控的 `InnerMonologueEngine`。

前置依赖：

1. Phase 1 完成。
2. continuity 主线最小 working memory / context assembler 已稳定。

必须交付：

1. observation compiler。
2. hypothesis generator。
3. strategy synthesizer。
4. simulation request planner。
5. uncertainty / confidence 输出。

明确不做：

1. 保存原始 CoT。
2. 每轮都做高成本慢思考。

阶段验收：

1. 系统能在关键不确定任务中输出结构化策略与验证需求。
2. 能对明显低证据、高风险情况给出“先验证”的内部建议。

止损点：

1. 若引擎显著拖慢主回复链路，应先保证旁路化和节流，不进入世界模拟阶段。

### Phase 3: 世界模拟器与假设空间

目标：

把当前世界摘要层提升为最小可推演世界模型。

前置依赖：

1. 红后化 `Phase 2` / `Phase 3` 的世界摘要与感知桥接已稳定。
2. Phase 2 的 thought / simulation request 契约已稳定。

必须交付：

1. `EntityState`
2. `CausalEdge`
3. `HypothesisRecord`
4. `SimulationTrace`
5. contradiction / freshness / confidence 规则

明确不做：

1. 全能现实仿真。
2. 高风险物理自治。

阶段验收：

1. 系统可以基于结构化对象回答局部反事实问题。
2. 模拟结果具备假设、证据与置信度标记。

止损点：

1. 若模拟结果与真实结果长期失真严重，则不得进入元编程与架构重构阶段。

### Phase 4: 元编程引擎与技能自举

目标：

让反思与重复模式能够转成受治理的改进工件。

前置依赖：

1. `MetaCognitionRuntime` 首批主线稳定。
2. 世界模拟与 thought journal 已能提供稳定线索。
3. `Governed Evolution` 读写护栏已经就绪。

必须交付：

1. `MetaProgrammingEngine` 候选编译器。
2. `SkillBootstrapper`。
3. workflow / skill / config / prompt patch candidate 契约。
4. 自动生成最小 tests / replay fixtures 的能力。

明确不做：

1. 自动应用。
2. 绕过 review path。

阶段验收：

1. 重复模式能稳定转成 reviewable 的 workflow / skill 候选。
2. learned rule 能稳定进入 `memory_candidates` 或 evolution seed。

止损点：

1. 若候选质量低、误报高、测试夹具不稳定，则先收敛候选质量，不进入架构重构阶段。

### Phase 5: 架构重构与历史 replay 评估

目标：

引入受治理的认知拓扑优化能力。

前置依赖：

1. Phase 4 稳定。
2. trial、rollback、report、health history 都可用。
3. 指标面足够支撑 before / after 对比。

必须交付：

1. 模块拓扑描述。
2. bottleneck analyzer。
3. replay evaluator。
4. `ArchitectureProposal`。
5. operator-friendly 对比报告。

明确不做：

1. 替换安全核心。
2. 绕过人工审查做热替换。

阶段验收：

1. 至少能对单一认知路径提出并验证一类结构性优化。
2. 提案具备成本、收益、风险、回滚说明。

止损点：

1. 若 replay 不稳定或指标无因果意义，则不得开放更强架构自重构。

### Phase 6: 长期运行与产品化治理

目标：

让 `CogniSphere` 成为可长期运行、可观测、可审计、可止损的高级认知架构，而不是实验堆叠。

必须交付：

1. 长期观测指标。
2. thought / simulation / patch candidate retention 治理。
3. watchdog 与 kill switch 机制。
4. 角色化权限和人类审批位。
5. 运行成本、错误率、演化健康趋势面板。

阶段验收：

1. 系统可以长时间运行而不因内部 thought、simulation、evolution 噪音而失控。
2. 所有高风险成长路径都能被暂停、回滚、审查。

## 14. 任务拆包规则与建议编号

建议 `CogniSphere` 主线统一使用 `CS-*` 前缀。

每个任务包至少包含：

1. `title`
2. `goal`
3. `scope`
4. `non_goals`
5. `dependencies`
6. `files_or_modules`
7. `acceptance_criteria`
8. `tests_or_replay`
9. `rollback_plan`
10. `open_questions`

拆包原则：

1. 先拆对象契约，再拆运行时编排，再拆高风险演化。
2. 先做只读 / 只记录 / 只模拟版本，再做写入或激活版本。
3. 凡涉及自演化、配置、workflow、skill 或架构重构，必须显式写明 governance path。
4. 凡涉及 thought / reflection / simulation 的持久化，必须显式写明 retention 与 redaction。

建议后续第一批任务包方向如下：

1. `CS-001` `CogniSphere` 边界、术语与层级关系冻结。
2. `CS-002` `ThoughtFrame` / `ThoughtJournalEntry` / `ActionTrace` 对象模型与存储边界。
3. `CS-003` 多源 runtime event 到 thought trigger 的标准化采集。
4. `CS-004` `InnerMonologueEngine` 最小 observation / hypothesis / strategy 输出契约。
5. `CS-005` `SimulationRequest` / `SimulationTrace` 契约与最小回写规则。
6. `CS-006` 世界模拟器的 `EntityState` / `CausalEdge` / hypothesis space 设计。
7. `CS-007` `MetaCognitionRegulator` 与 `MetaCognitionRuntime` / working memory / introspection 的桥接。
8. `CS-008` `MetaProgrammingEngine` 的 patch candidate、test fixture 与 governance path 契约。
9. `CS-009` `SkillBootstrapper` 的重复模式提炼与 workflow / skill 候选生成规则。
10. `CS-010` `ArchitectureProposal` / replay evaluator / operator report 契约。
11. `CS-011` 不可变安全核心与 watchdog 边界清单。
12. `CS-012` thought / simulation / evolution observability 与长期治理指标。

## 15. 当前建议的下一步

在这份总纲建立之后，最合理的后续顺序不是马上追求“直接自我改造”，而是：

1. 先拆 `CS-001` 至 `CS-004`，把 thought substrate 与内在思维引擎边界冻结。
2. 再拆 `CS-005` 至 `CS-007`，把世界模拟与元认知调节层连接起来。
3. 最后再拆 `CS-008` 之后的元编程、技能自举与架构重构任务包。

推荐推进顺序的核心理由是：

1. 没有稳定 thought substrate，就没有高质量反思输入。
2. 没有稳定 simulation contract，就没有可信的“先推演后行动”链路。
3. 没有 `Governed Evolution` 的强护栏，就不应开放更强的元编程和自重构。

## 16. 总结

`CogniSphere` 不是对现有 OriginAgent 的一次普通增强，而是其上的“高阶心智架构”。

它要做的不是简单增加更多模块，而是把：

1. 思考
2. 反思
3. 模拟
4. 改进
5. 治理

这五件事组织成同一个可持续闭环。

如果红后化主线解决的是“它能持续看、持续记、持续行动”，
如果 `MetaCognitionRuntime` 解决的是“它能看到自己哪里做得不稳”，
那么 `CogniSphere` 解决的就是：

1. 它如何形成持续内心流。
2. 它如何在脑中先推演世界。
3. 它如何把反思转成可审查的成长候选。
4. 它如何在不失控的前提下逐步重构自己的能力结构。

这就是 OriginAgent 从“强智能体底座”迈向“受治理的自成长认知系统”的总施工图。
