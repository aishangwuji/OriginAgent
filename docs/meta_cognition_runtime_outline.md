# OriginAgent MetaCognitionRuntime 架构草案

Date: 2026-06-12
Status: Proposed
Scope: OriginAgent 显式元认知、结构化反思、置信度跟踪与受治理自演进桥接的独立架构提纲

## 1. 文档目标

本草案用于定义 OriginAgent 的 `MetaCognitionRuntime` 主线，为后续显式反思、错误归因、置信度治理和未来受治理自改造能力提供统一边界。

它回答的问题不是“让模型想得更多”，而是：

1. Agent 如何对自己刚刚做过的判断、工具调用和任务结果进行结构化复盘。
2. Agent 如何区分“我知道”和“我不确定”。
3. Agent 如何在失败、纠错、重复卡住时触发反思，而不是只继续下一轮生成。
4. 反思结果如何进入 working memory、记忆治理和 governed evolution，而不是停留在一次性黑盒推理里。

## 2. 为什么需要单独建模

当前 OriginAgent 已经具备元认知的若干部件，但还没有形成统一、持续、可回写的元认知闭环。

现有能力已经覆盖：

1. `SelfModelService` 提供“我拥有什么能力、限制和运行时状态”的静态自我知识。
2. `CognitiveLoop` 提供对目标、待确认项、提醒等任务状态的后台巡检。
3. continuity 主线提供 working memory、world view、context assembly 等上下文治理骨架。
4. `Dream`、nearline memory、governed memory writer 提供反思结果沉淀的长期存储去向。
5. `governed_evolution.md` 已定义受治理自进化边界，为未来策略修正和能力演进提供安全落点。

但当前仍缺少一个统一中枢，去回答：

1. 我刚才的策略是什么。
2. 我当时依赖了哪些假设。
3. 工具结果是否和预期不一致。
4. 用户纠正我之后，我应该沉淀出什么经验。
5. 这些经验应该进入记忆、进入告警，还是进入后续演进提案。

因此，`MetaCognitionRuntime` 应被视为并行基础主线，而不是 `CognitiveLoop` 的一个小补丁。

## 3. 与现有总计划的关系

红后化总计划和连续性主线解决的是“长期运行的认知地基”和“上下文操作系统”问题。

`MetaCognitionRuntime` 解决的是另一层问题：

1. 对推理过程的自我观察。
2. 对结果偏差的结构化归因。
3. 对不确定性的显式表达。
4. 对反思产物的受治理应用。

可以把关系理解为：

1. 红后化总计划是持续认知、感知、世界模型和动作治理的总施工图。
2. continuity & memory OS 是“这轮推理应该看到什么”的上下文操作系统。
3. `MetaCognitionRuntime` 是那个持续记录、复盘、提醒和纠偏的“内心导师”。

它不替代已有主线，而是消费这些主线产生的副产品，并把反思结果再写回这些主线。

## 4. 设计目标

这条主线的目标不是构造一个无限自言自语的 Agent，而是实现：

1. 显式记录关键策略、假设、证据和置信度。
2. 在失败、纠错、重复混乱时稳定触发反思。
3. 把反思结果沉淀为可查询、可审计、可治理的结构化对象。
4. 让下一轮推理真正受上一轮经验影响，而不是只靠模型隐式记住。
5. 为未来 `MetaProgrammingEngine` 提供高质量、受治理的演进线索。

## 5. 非目标

本草案明确不追求以下目标：

1. 记录或暴露模型原始、无约束、全文式 chain-of-thought。
2. 让元认知模块直接参与主回复生成并拉长每一轮延迟。
3. 让元认知模块直接修改源代码、系统提示、工具权限或主配置。
4. 把每一次轻微波动都放大成高成本深反思。
5. 在没有治理边界的前提下实现自动自改造。

## 6. 核心设计判断

### 6.1 元认知不是“更长的黑盒思考”

我们需要的不是更多不可见推理，而是更可治理的结构化元认知痕迹。

因此应优先记录如下结构化字段：

1. 当前策略
2. 关键假设
3. 证据引用
4. 置信度
5. 预期与实际偏差
6. 建议下一步

而不是追求保存原始自由文本式内心独白。

### 6.2 元认知默认走旁路，不挤占主回复路径

`MetaCognitionRuntime` 默认应作为 sidecar 运行：

1. 主回复路径仍由 `AgentLoop` 驱动。
2. `CognitiveLoop` 仍负责后台巡检和有限主动跟进。
3. 元认知更多消费历史、工具结果、任务 outcome、用户纠错和 introspection 产物。

只有极少数低成本信号，例如“当前置信度很低，建议先查证”，才应以轻量结构影响本轮策略。

### 6.3 反思必须有去向

如果反思结果只停留在一次性日志里，它就不构成真正的元认知闭环。

最小可落地去向应包括：

1. working memory 中的短期注意项或 caution 项。
2. `memory_candidates` 中的待治理记忆候选。
3. introspection / audit 中的反思记录。
4. governed evolution 的 signal / proposal seed。

### 6.4 元认知与元编程必须分层

推荐把未来两条线严格区分：

1. `MetaCognitionRuntime` 负责发现、复盘、归因、总结。
2. `MetaProgrammingEngine` 负责把高质量改进线索转成受治理的变更候选。
3. `Governed Evolution` 负责验证、试运行、审查、激活与回滚。

这意味着“能反思”不等于“能直接改自己”。

### 6.5 结构化痕迹优先于高频全文存储

元认知对象默认应短小、结构化、可去重、可过期。

避免以下失败模式：

1. 每轮都写长篇反思，成本失控。
2. 把反思内容整体塞回 prompt，造成二次污染。
3. 将瞬时困惑误晋升为长期规则。

## 7. 架构总览

```text
AgentLoop / Tool Results / User Corrections / Task Outcomes
          ↓
Meta Signal Collector
          ↓
MetaCognitionRuntime
  ├─ uncertainty monitor
  ├─ mismatch detector
  ├─ reflection planner
  ├─ journal writer
  └─ pattern consolidator
          ↓
Structured Meta Artifacts
  ├─ thought journal entry
  ├─ reflection record
  ├─ confidence trace
  ├─ error pattern
  ├─ memory candidate
  └─ evolution seed
          ↓
Governed Sinks
  ├─ working memory
  ├─ introspection / audit
  ├─ memory_candidates
  ├─ nearline / dream
  └─ governed evolution / future meta-programming
```

## 8. 输入面

`MetaCognitionRuntime` 最小输入面建议包括：

1. 最近对话与当前任务 outcome。
2. 工具调用结果、失败信息、重试轨迹和结果偏差。
3. `CognitiveEvent` / `CognitiveDecision` 等后台认知事件与决策痕迹。
4. working memory 中的当前目标、计划、待确认项、attention items。
5. `WorldSummary` 与相关世界关注点。
6. `SelfModelService` 或 runtime snapshot 暴露的自我状态与限制。
7. 用户纠正、否定、补充事实等显式反馈。
8. introspection / audit 中已有的局部统计，如连续失败、低置信、反复追问。

这些输入中，建议优先复用现有结构化对象，而不是重新解析自由文本。

## 9. 核心子模块

### 9.1 `MetaSignalCollector`

职责：

1. 监听用户纠正、工具失败、任务完成、重复重试、认知异常等信号。
2. 将离散 runtime 事件标准化为统一的 meta trigger。
3. 做最小去重和冷却，避免每个失败都触发昂贵反思。

### 9.2 `UncertaintyMonitor`

职责：

1. 跟踪回答、计划或工具结论中的置信度。
2. 识别“缺证据却在下结论”的情况。
3. 识别“连续不确定仍未切换策略”的情况。

### 9.3 `ReflectionPlanner`

职责：

1. 判断当前是否只需要轻量记录，还是需要一次深反思。
2. 选择反思模板：
   - 失败归因
   - 用户纠错复盘
   - 任务完成总结
   - 重复困惑诊断
3. 控制频率、预算和保留级别。

### 9.4 `ThoughtJournalWriter`

职责：

1. 把关键策略、假设、证据、偏差、下一步写成结构化 journal。
2. 生成可供 introspection、working memory 和检索使用的紧凑摘要。
3. 对敏感内容做最小脱敏和裁剪。

### 9.5 `PatternConsolidator`

职责：

1. 汇总重复失败、常见误判和高频不确定模式。
2. 输出更稳定的 `ErrorPattern` 或 `ReflectionRuleCandidate`。
3. 为 `Dream`、memory candidate 和 governed evolution 提供较高置信输入。

## 10. 输出对象建议

推荐最小对象集合如下。

### 10.1 `ThoughtJournalEntry`

用于记录一次关键推理或执行片段的结构化元认知痕迹。

建议字段：

1. `entry_id`
2. `session_key`
3. `trigger_type`
4. `task_reference`
5. `strategy_summary`
6. `assumptions`
7. `evidence_refs`
8. `confidence`
9. `expected_outcome`
10. `actual_outcome`
11. `mismatch_summary`
12. `suggested_next_action`
13. `created_at`

### 10.2 `ReflectionRecord`

用于记录一次完整复盘。

建议字段：

1. `reflection_id`
2. `source_entry_ids`
3. `reflection_kind`
4. `outcome_class`
5. `root_cause_hypotheses`
6. `what_worked`
7. `what_failed`
8. `learned_rule_candidate`
9. `confidence`
10. `retention_hint`

### 10.3 `ConfidenceTrace`

用于记录关键阶段的置信度变化，而不是只保留最终结果。

建议字段：

1. `trace_id`
2. `subject_type`
3. `subject_reference`
4. `initial_confidence`
5. `final_confidence`
6. `change_reason`
7. `evidence_refs`

### 10.4 `ErrorPattern`

用于表达跨多次执行复现的稳定问题。

建议字段：

1. `pattern_id`
2. `pattern_type`
3. `affected_capability`
4. `example_refs`
5. `frequency`
6. `severity`
7. `suggested_guardrail`
8. `promotion_candidate`

### 10.5 `EvolutionSeed`

用于向 governed evolution 或未来元编程引擎提供受治理线索。

建议字段：

1. `seed_id`
2. `source_reflection_ids`
3. `change_target_type`
4. `change_hypothesis`
5. `expected_benefit`
6. `risk_notes`
7. `required_review_level`

## 11. 触发模型

推荐把触发分成四类主信号和一类保守兜底。

| 触发类型 | 典型信号 | 默认动作 |
|---|---|---|
| `tool_failure` | 工具报错、返回空、与预期冲突 | 轻量 journal，必要时失败归因反思 |
| `user_correction` | 用户否定答案、补充事实、指出误解 | 纠错反思，生成 memory candidate 候选 |
| `task_completion` | 任务完成、有明确 outcome | 总结性 reflection，提炼可复用规则 |
| `repeated_confusion` | 连续低置信、反复尝试、重复提问 | 深反思或策略切换建议 |
| `periodic_review` | 定时抽样复盘 | 仅低频执行，主要做 pattern consolidation |

当前阶段不建议把“每轮都反思”作为默认模式。

## 12. 与现有架构的衔接

### 12.1 与 `CognitiveLoop` 的关系

`CognitiveLoop` 关注的是“当前有哪些事情值得继续跟进”。

`MetaCognitionRuntime` 关注的是“我刚才为什么会这样判断、哪里偏了、下次如何更稳”。

两者关系建议为：

1. `CognitiveLoop` 可以提供触发信号。
2. `MetaCognitionRuntime` 可以输出 caution / follow-up hints 给 `CognitiveLoop`。
3. 两者都不替代 `AgentLoop` 的统一执行入口。

### 12.2 与 continuity & memory OS 的关系

continuity 主线提供元认知的上下文载体和回写位置。

最小衔接面建议为：

1. working memory 增加轻量 `meta_attention` 或 caution 项。
2. `ContextAssembler` 可以按预算选择性注入少量元认知提示。
3. 元认知对象默认不整体灌回 prompt，只注入当前相关结论。

### 12.3 与 `SelfModelService` 的关系

`SelfModelService` 负责“我是谁、我有哪些能力和限制”。

`MetaCognitionRuntime` 负责“我在这次任务中实际怎么用了这些能力，以及哪里出了偏差”。

因此：

1. `SelfModelService` 是输入。
2. `MetaCognitionRuntime` 是动态运行反馈层。
3. 后续可考虑把部分动态健康指标回流进 self model 的 runtime snapshot。

### 12.4 与 `Dream` / nearline memory 的关系

元认知记录不应直接等于长期事实。

推荐路径为：

1. journal 与 reflection 先进入审计或近线层。
2. 只有稳定、高价值的 learned rule candidate 才进入 `memory_candidates`。
3. `Dream` 再决定是否将其晋升为长期规则、稳定偏好或经验事实。

### 12.5 与 governed evolution 的关系

`MetaCognitionRuntime` 可以发现：

1. 某类错误正在重复发生。
2. 某类 guardrail 缺失。
3. 某类 workflow 或 prompt policy 可能需要调整。

但它只能产出 signal、seed 或 review proposal 候选，不能直接应用变更。

### 12.6 与未来 `MetaProgrammingEngine` 的关系

未来若加入 `MetaProgrammingEngine`，推荐关系为：

1. `MetaCognitionRuntime` 负责发现问题和改进方向。
2. `MetaProgrammingEngine` 负责把方向转成可验证的变更工件。
3. `Governed Evolution` 负责试运行、审查、激活和回滚。

这样可以避免把“能反思”直接变成“能不受约束地改自己”。

## 13. 安全与治理边界

这条主线的硬边界建议如下：

1. 不保存模型原始 chain-of-thought，只保存结构化、裁剪后的元认知对象。
2. 不直接修改代码、工具权限、系统提示或主配置。
3. 不把未验证的 learned rule 直接提升为长期事实。
4. 不让反思记录默认大规模注入 prompt。
5. 所有写入 governed evolution 的动作都只产生候选，不自动执行。
6. 元认知存储必须有 retention、采样、去重和脱敏策略。
7. 需要通过 introspection 暴露最小可解释视图，而不是形成新的黑盒。

## 14. 分阶段落点建议

### MC0

冻结本草案与对象边界。

### MC1

实现最小 trigger collector 与 `ThoughtJournalEntry` 写入链路。

目标：

1. 能捕获工具失败、用户纠错、任务完成三类核心触发。
2. 能生成结构化 journal，而不是自由文本日志。

### MC2

实现 `ReflectionPlanner` 与 `ReflectionRecord`。

目标：

1. 能对失败、纠错、完成做分型复盘。
2. 能输出 confidence trace 和短期 caution。

### MC3

接入 continuity 与记忆治理。

目标：

1. working memory 可承载最小 meta attention。
2. `memory_candidates` 可接收高置信 learned rule candidate。
3. introspection 可展示最近反思摘要和重复模式。

### MC4

接入 governed evolution 与未来元编程桥。

目标：

1. 能输出 `EvolutionSeed`。
2. 能把重复问题转成 reviewable 改进候选。
3. 仍保持“发现”和“应用”分离。

## 15. 建议任务包前缀与首批拆包

为避免与现有 `RQ-*` 编号冲突，建议这条主线单独使用 `MC-*` 前缀。

第一批建议任务：

1. `MC-001` `MetaCognitionRuntime` 边界、触发面与对象模型冻结。
2. `MC-002` tool failure / user correction / task completion 三类触发采集。
3. `MC-003` 结构化 reflection prompt 与 journal/redaction 策略。
4. `MC-004` working memory / introspection / memory_candidates 最小桥接。
5. `MC-005` error pattern consolidation 与 governed evolution seed 桥接。

## 16. 当前建议的下一步

最稳妥的下一步不是立刻做元编程引擎，而是：

1. 先冻结本草案，使其与红后化总计划、continuity 主线和 governed evolution 对齐。
2. 先做 `MC-001` 和 `MC-002`，证明系统已经能稳定采集和记录结构化反思信号。
3. 再做 `MC-003` 和 `MC-004`，证明反思结果真的能影响下一轮上下文与记忆治理。
4. 最后再决定何时引入 `MetaProgrammingEngine`，并始终让它落在 governed evolution 的硬边界内。

如果这条线站稳，OriginAgent 才会从“有元认知部件”进化到“有持续自我复盘能力的系统”。
