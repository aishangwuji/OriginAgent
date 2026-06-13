# OriginAgent MetaCognitionRuntime 架构总纲

Date: 2026-06-13
Status: Implemented (functional mainline landed, release validation pending)
Scope: OriginAgent 显式元认知、结构化反思、记忆桥接与受治理演进桥接的已落地主线现状与收官方向；不等于元编程主线

## 0. 当前实现状态（2026-06-13）

`MC-001` 到 `MC-005` 的主线已经功能打通，当前代码状态为：

1. 已落地 `MetaCognitionRuntime` sidecar，负责 trigger 采集、去重、cooldown、per-turn accepted limit 与审计。
2. 已落地 `MetaCognitionReflector` turn-end sidecar，负责 minimal journal、结构化 reflection、confidence trace 和后续桥接。
3. 已落地三类首版触发：
   - `tool_failure`
   - `user_correction`
   - `task_completion`
4. 已落地结构化产物：
   - `ThoughtJournalEntry`
   - `ReflectionRecord`
   - `ConfidenceTrace`
   - `ErrorPattern`
   - `EvolutionSeed`
5. 已落地三类下游桥接：
   - introspection / audit
   - working memory / `memory_candidates`
   - governed evolution signal
6. 已落地统一 JSONL 审计：
   - `triggers.jsonl`
   - `decisions.jsonl`
   - `journals.jsonl`
   - `reflections.jsonl`
   - `confidence_traces.jsonl`
   - `patterns.jsonl`
   - `evolution_seeds.jsonl`
7. 当前仍保持硬边界不变：
   - 不保存原始 CoT
   - 不保存 raw prompt / history / tool payload / source excerpt
   - 不整段回灌 prompt
   - 不新增独立 meta retrieval 源
   - 不直接写长期事实
   - 不自动改源码、主配置、权限或 prompt
8. 当前未收官的主要不是能力空缺，而是工程验收：
   - 目标测试尚未在可运行 Python 环境中补跑
   - 真实样本下的噪音、阈值和 operator 体验尚未完成一轮 soak

## 1. 这条主线现在解决了什么

完成五个阶段后，Agent 相比原先多出了一条明确的元认知总线：

1. Agent 不再只是在主回复里“隐式记住”，而是能把 accepted trigger 转成结构化 journal / reflection / confidence trace。
2. Agent 不再只记录失败事件，而是能把少量高价值结论回写到 working memory 和 `memory_candidates`。
3. Agent 不再只能看单次失败，而是能把重复反思规则式汇总为 `ErrorPattern`。
4. Agent 不再把演进想法停留在日志里，而是能把重复模式规范化为受治理的 `OpportunitySignal` 候选。
5. Agent 仍然不会自动自修改，所有 evolution 相关动作仍停留在 candidate / signal 层。

## 2. 已落地架构总览

```text
Tool observer / turn-end scan / complete_goal event
          ↓
MetaCognitionRuntime
  ├─ trigger record
  ├─ duplicate / cooldown / turn limit
  └─ accepted trigger staging
          ↓
MetaCognitionReflector (turn-end sidecar)
  ├─ minimal journal
  ├─ auxiliary structured reflection
  ├─ confidence trace
  ├─ working memory bridge
  ├─ memory candidate bridge
  ├─ pattern consolidation
  └─ evolution signal bridge
          ↓
Governed Sinks
  ├─ introspection / audit
  ├─ working memory
  ├─ memory_candidates
  └─ OpportunitySignalStore -> Curator -> ReviewProposalStore -> Control Plane
```

## 3. 已冻结的核心边界

### 3.1 主回复边界

1. 元认知继续是 sidecar，不是第二个 `AgentLoop`。
2. 不进入主回复 `RUN` 链。
3. 不修改 `AgentTurnPipeline` 拓扑。
4. foreground reply 不等待 evolution 结果。

### 3.2 数据安全边界

1. 不保存原始 CoT。
2. 不保存 raw prompt、raw history、raw tool payload、raw source excerpt。
3. evidence 默认保留 ref 和 redacted preview。
4. 所有 introspection / audit / bridge 文本都经过统一 redaction。

### 3.3 作用边界

1. 可以写 working memory 的轻量 caution / pending question。
2. 可以写受治理的 `memory_candidates`。
3. 可以写 governed evolution 的 signal 候选。
4. 不直接写长期记忆结论。
5. 不直接写 proposal、代码、权限或主配置。

## 4. 五个阶段的落地结果

### MC-001

已完成 `MetaCognitionRuntime` sidecar 边界冻结与基础运行时。

### MC-002

已完成三类触发采集、去重、冷却、turn limit 与 trigger 审计。

### MC-003

已完成结构化 journal / reflection / confidence trace 契约、旁路反思、redaction 与 retention 边界。

### MC-004

已完成 working memory、introspection 与 `memory_candidates` 的最小桥接。

### MC-005

已完成 `ErrorPattern` consolidation 与 governed evolution signal bridge。

## 5. 仍然没有做的事

这条总线虽然已经打通，但下面这些能力仍然明确不在当前范围内：

1. `ErrorPattern` 到自动 proposal / 自动激活的跨越。
2. `MetaProgrammingEngine`。
3. 自动生成代码补丁、配置补丁或权限补丁。
4. working memory / retrieval / evolution policy 的大规模重构。
5. 任何形式的无治理自修改。

## 6. 下阶段建议

下一阶段不建议继续扩 scope，而应做“收官验收阶段”：

1. 测试验收。
   - 先在有 Python / pytest 的环境里跑通元认知主线测试。
   - 重点覆盖 runtime、introspection、retrieval、Dream、nearline profile、Curator / control plane 联动。
2. 运行观测。
   - 打开 feature flag 做一轮真实样本 soak。
   - 观察 `meta_cognition_summary()`、signal 噪音、suppression 命中率、working memory 写回噪音。
3. 阈值与默认值定稿。
   - 基于真实数据微调 `memory_candidate_min_confidence`、`pattern_min_frequency`、`pattern_min_distinct_turns`、`max_signal_upserts_per_turn`。
   - 不新增新类型 sink，只定稿当前阈值。
4. 运维与操作文档。
   - 补一份 operator / developer 使用说明。
   - 说明如何查看 `meta_cognition_summary()`、如何检查 `patterns.jsonl` / `evolution_seeds.jsonl`、如何 suppress / resume meta-origin signal。
5. 最后再决定是否开启下一条主线。
   - 只有在这条总线经过测试和真实样本验证后，才讨论更后的 `MetaProgrammingEngine` 或更主动的 governed evolution 策略。

## 7. 当前结论

就“总主线是否已经做完”这个问题，当前最准确的结论是：

1. 功能主线已经做完，元认知总线已经从 trigger 一直打通到 governed evolution signal。
2. 工程收官还没完全做完，差的是测试验收、阈值定稿和 operator 侧运行观测。
3. 这条已落地主线不应再被记作红后剩余待施工项，也不应与未来 `MetaProgrammingEngine` 混为一谈。
4. 因此当前状态适合叫“主线功能收官，进入验收与硬化阶段”，不适合叫“所有后续工作都结束”。
