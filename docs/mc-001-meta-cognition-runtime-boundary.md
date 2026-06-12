# MC-001 任务包：MetaCognitionRuntime 边界、触发面与对象模型冻结

Date: 2026-06-12
Status: Proposed
Scope: MetaCognitionRuntime 主线的边界、对象模型与治理约束冻结

## implementation_status

`MC-001` 的最小实现边界已落地到代码：

1. `MetaCognitionRuntime` 以 sidecar 形式接入，未成为第二个 `AgentLoop`。
2. 已新增最小对象模型骨架：
   - `MetaTrigger`
   - `RecordTriggerResult`
   - `ThoughtJournalEntry`
   - `ReflectionRecord`
   - `ConfidenceTrace`
   - `ErrorPattern`
   - `EvolutionSeed`
3. 当前真正进入运行链的对象只有：
   - `MetaTrigger`
   - `RecordTriggerResult`
4. 已明确并保持：
   - 不直接自修改
   - 不直接写主配置
   - 不直接激活 evolution 产物

## title

`MC-001` MetaCognitionRuntime 边界、触发面与对象模型冻结

## goal

冻结 `MetaCognitionRuntime` 的最小边界，明确：

1. 它在 OriginAgent 架构中负责什么，不负责什么。
2. 它与 `AgentLoop`、`CognitiveLoop`、continuity 主线、`SelfModelService`、`Dream`、governed evolution 的关系。
3. 元认知最小输入面、输出对象和受治理落点是什么。
4. 哪些内容属于当前阶段必须冻结，哪些推迟到后续 `MC-*` 任务包。

本任务包完成后，系统应能稳定回答：

1. 元认知 runtime 是旁路反思系统，还是主回复执行器。
2. 一条元认知记录是 journal、reflection、confidence trace、error pattern 还是 evolution seed。
3. 反思结果如何进入 working memory、introspection、memory candidates 与 governed evolution。
4. 为什么“能反思”不等于“能直接改自己”。

## scope

本任务包覆盖以下内容：

1. 冻结 `MetaCognitionRuntime` 的最小职责：
   - 消费结构化 runtime 副产品
   - 组织元认知触发
   - 生成结构化反思对象
   - 将反思结果写入受治理落点
2. 冻结与现有模块的关系：
   - `AgentLoop` 继续作为唯一主回复执行入口
   - `CognitiveLoop` 继续负责后台巡检和有限主动跟进
   - continuity 主线继续负责上下文承载与回写位置
   - `SelfModelService` 继续作为静态自我知识输入
   - `Dream` / nearline memory 继续负责长期沉淀
   - governed evolution 继续负责 review、trial、激活与回滚
3. 冻结最小输入面：
   - 最近对话
   - 工具结果与失败信息
   - 任务 outcome
   - `CognitiveEvent` / `CognitiveDecision`
   - working memory
   - `WorldSummary`
   - `SelfModelService` / runtime snapshot
   - 用户纠正与显式反馈
4. 冻结最小输出对象边界：
   - `ThoughtJournalEntry`
   - `ReflectionRecord`
   - `ConfidenceTrace`
   - `ErrorPattern`
   - `EvolutionSeed`
5. 冻结安全与治理边界：
   - 不保存原始 chain-of-thought
   - 不直接修改代码、提示词、权限和主配置
   - 不把未验证 learned rule 直接晋升长期事实
   - 不自动执行 evolution / meta-programming 写动作

## non_goals

本任务包不覆盖以下内容：

1. `MetaCognitionRuntime` 的完整代码实现。
2. 触发采集的实现细节与 hook 链路。
3. 结构化 reflection prompt 设计。
4. 反思对象的具体持久化文件格式定稿。
5. 元编程引擎实现。
6. 任何自动自修改能力。

## dependencies

1. [`docs/meta_cognition_runtime_outline.md`](./meta_cognition_runtime_outline.md)
2. [`docs/red_queen_master_plan.md`](./red_queen_master_plan.md)
3. [`docs/continuity_memory_os_outline.md`](./continuity_memory_os_outline.md)
4. [`docs/governed_evolution.md`](./governed_evolution.md)
5. 当前运行时锚点：
   - [OriginAgent/agent/cognitive_loop.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_loop.py)
   - [OriginAgent/agent/cognitive_events.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_events.py)
   - [OriginAgent/agent/self_model.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/self_model.py)
   - [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)
   - [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
   - [OriginAgent/agent/cognitive_audit.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_audit.py)

## files_or_modules

本任务包预期主要触达文档层：

1. [`docs/meta_cognition_runtime_outline.md`](./meta_cognition_runtime_outline.md)
2. [`docs/mc-001-meta-cognition-runtime-boundary.md`](./mc-001-meta-cognition-runtime-boundary.md)

后续实现预计主要影响：

1. [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)
2. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
3. 候选新模块：
   - `OriginAgent/agent/meta_cognition_runtime.py`
   - `OriginAgent/agent/meta_cognition_models.py`
   - `OriginAgent/agent/meta_cognition_audit.py`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. `MetaCognitionRuntime` 的职责边界已冻结。
2. 已明确：它是旁路反思系统，不是第二个 `AgentLoop`。
3. 已明确：它消费哪些输入、产出哪些结构化对象、写入哪些受治理落点。
4. 已明确：它与 `CognitiveLoop`、continuity、`SelfModelService`、`Dream`、governed evolution 的角色分工。
5. 已明确：当前阶段禁止直接自修改、直接写主配置、直接激活演进产物。
6. `MC-002`、`MC-003`、`MC-004`、`MC-005` 可直接引用本任务包作为上位边界。

## tests

本任务包为文档冻结任务，不要求新增自动化测试。

完成时应形成的后续测试约束包括：

1. 对象模型序列化/反序列化测试：
   - `ThoughtJournalEntry`
   - `ReflectionRecord`
   - `ConfidenceTrace`
   - `ErrorPattern`
   - `EvolutionSeed`
2. 治理边界测试：
   - 不保存原始 CoT
   - 不直接写主配置
   - 不直接触发 write 类 evolution action
3. 回写边界测试：
   - working memory 只接收轻量 caution / meta attention
   - memory candidates 只接收高置信 learned rule candidate

## rollback_plan

若本任务包定义被证明不适用于后续实现，回滚方式应为：

1. 保留当前边界文档历史版本。
2. 在新版本中显式记录对象边界、职责边界和治理边界的变更原因。
3. 不允许在未更新 `MC-001` 前，让实现静默漂移为“直接自修改”或“主链路反思执行器”。

## open_questions

1. `ThoughtJournalEntry` 是否需要从一开始就带 `retention_hint`，还是只在 `ReflectionRecord` 引入。
2. `ConfidenceTrace` 是否应作为独立对象落盘，还是先作为 `ReflectionRecord` 的内嵌字段。
3. `SelfModelService` 的动态运行指标是否在后续阶段回流为 meta 输入快照，而不是只保留静态自我知识。
