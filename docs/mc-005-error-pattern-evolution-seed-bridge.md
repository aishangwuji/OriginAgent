# MC-005 任务包：ErrorPattern consolidation 与 governed evolution seed 桥接

Date: 2026-06-12
Status: Proposed
Scope: MetaCognitionRuntime 重复问题模式向 governed evolution signal / proposal seed 的最小桥接边界冻结

## title

`MC-005` ErrorPattern consolidation 与 governed evolution seed 桥接

## goal

冻结 `MetaCognitionRuntime` 的高阶产物如何接入受治理自演进链路，使 OriginAgent 能把“重复犯错”“重复低效”“反复缺 guardrail”的模式，转成可审查、可抑制、可回滚的演进候选，而不是直接触发自修改。

本任务包完成后，系统应能稳定回答：

1. 什么样的元认知产物有资格变成 evolution signal。
2. evolution seed 与现有 `OpportunitySignal` 有什么关系。
3. 如何复用现有 `OpportunitySignalStore -> Curator -> ReviewProposalStore` 链路。
4. 为什么这一步仍然不是自动自改造，而只是受治理候选生成。

## scope

本任务包覆盖以下内容：

1. 冻结最小高阶输入对象：
   - `ErrorPattern`
   - `ReflectionRecord`
   - `EvolutionSeed`
2. 冻结 `ErrorPattern` 的最小职责：
   - 汇总跨多次执行重复出现的问题
   - 对 pattern 做频次、严重度和能力域归类
   - 为 signal 生成提供稳定目标键，而不是一次性失败日志
3. 冻结 `EvolutionSeed` 的最小职责：
   - 表达一个“值得被 review 的改进假设”
   - 不直接等于 proposal
   - 不直接等于代码改动
4. 冻结桥接方式：
   - `MetaCognitionRuntime` 产出 `EvolutionSeed`
   - seed 被标准化为 evolution candidate
   - evolution candidate 写入现有 `OpportunitySignalStore`
   - `Curator` 再按现有规则把高分 signal 转成 review proposal
5. 冻结目标类型边界：
   - 首版只允许桥接到现有受支持的两类提案方向：
     - `workflow_candidate`
     - `skill_candidate`
   - 不允许首版直接生成“改源码”“改主配置”“改权限”的演进候选
6. 冻结证据与风控规则：
   - signal evidence 必须来自多个 journal / reflection / pattern 片段
   - evidence 必须 redacted
   - risk level、priority score、feedback 状态继续交给现有 evolution 体系治理
7. 冻结 operator loop 关系：
   - 元认知生成的 signal 应进入现有 `inspect_signal`、`list_signals`、`operator_recommendations`
   - 允许后续被 suppress / resume / retry-trial / review

## non_goals

本任务包不覆盖以下内容：

1. `MetaProgrammingEngine` 的实现。
2. 自动生成代码补丁或配置补丁。
3. 让元认知直接写入 `ReviewProposalStore` 绕过 signal 层。
4. 新增新的 evolution target type。
5. 自动激活任何 workflow、skill 或代码工件。
6. 对当前 Curator / PromotionGate / TrialRunner 的大规模重构。

## dependencies

1. [`docs/meta_cognition_runtime_outline.md`](./meta_cognition_runtime_outline.md)
2. [`docs/mc-001-meta-cognition-runtime-boundary.md`](./mc-001-meta-cognition-runtime-boundary.md)
3. [`docs/mc-002-meta-trigger-collection.md`](./mc-002-meta-trigger-collection.md)
4. [`docs/mc-003-structured-reflection-redaction.md`](./mc-003-structured-reflection-redaction.md)
5. [`docs/mc-004-meta-bridge-working-memory-introspection-memory-candidates.md`](./mc-004-meta-bridge-working-memory-introspection-memory-candidates.md)
6. [`docs/governed_evolution.md`](./governed_evolution.md)
7. 当前 evolution 锚点：
   - [OriginAgent/agent/evolution.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/evolution.py)
   - [OriginAgent/agent/curator.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/curator.py)
   - [OriginAgent/agent/evolution_operator.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/evolution_operator.py)
   - [OriginAgent/agent/evolution_control_plane.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/evolution_control_plane.py)

## files_or_modules

本任务包预期主要触达文档层：

1. [`docs/mc-005-error-pattern-evolution-seed-bridge.md`](./mc-005-error-pattern-evolution-seed-bridge.md)
2. [`docs/meta_cognition_runtime_outline.md`](./meta_cognition_runtime_outline.md)

后续实现预计主要影响：

1. [OriginAgent/agent/evolution.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/evolution.py)
2. [OriginAgent/agent/curator.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/curator.py)
3. [OriginAgent/agent/evolution_operator.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/evolution_operator.py)
4. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
5. 候选新模块：
   - `OriginAgent/agent/meta_cognition_runtime.py`
   - `OriginAgent/agent/meta_cognition_patterns.py`
   - `OriginAgent/agent/meta_cognition_evolution_bridge.py`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. 已明确 `ErrorPattern`、`EvolutionSeed` 与现有 `OpportunitySignal` 的角色关系。
2. 已明确元认知桥接只写 signal / seed，不直接写 proposal、代码或主配置。
3. 已明确首版只桥接到现有 `workflow_candidate` / `skill_candidate` 两类演进方向。
4. 已明确 signal evidence 必须来自多条 redacted 元认知片段，而不是单次失败日志。
5. 已明确生成的 signal 继续走现有 `Curator -> ReviewProposalStore -> PromotionGate / Trial / Review` 链路。
6. 已明确 operator 可以通过现有 evolution control plane 对这类 signal 做 inspect、suppress、resume 和 report。

## tests

后续实现至少应覆盖以下测试：

1. pattern consolidation 测试：
   - 相同类型重复失败聚合为单个 `ErrorPattern`
   - 不同能力域错误不会错误合并
   - 频次、严重度、example refs 统计正确
2. evolution seed 标准化测试：
   - `workflow_candidate` seed 映射正确
   - `skill_candidate` seed 映射正确
   - 不支持的 target type 被拒绝
3. signal store 桥接测试：
   - seed 可写入 `OpportunitySignalStore`
   - 重复 seed 走 upsert 而不是无限新增
   - evidence 被 redacted 且数量受限
4. curator 联动测试：
   - 高分 signal 可被 Curator 读取
   - 转换出的 proposal 带 evolution origin 与 operator insights
   - 低分 signal 不会过早转 proposal
5. control plane / operator 测试：
   - `inspect_signal` 可看到元认知来源 signal
   - suppress / resume 仍按现有策略工作
   - recommendations 不会自动执行

## rollback_plan

若本任务包实现导致 signal 噪音过高、proposal 队列污染或演进方向失真，回滚方式应为：

1. 先关闭 meta-to-evolution bridge 开关。
2. 保留 `ErrorPattern` 与 `EvolutionSeed` 只读记录，停止写入 `OpportunitySignalStore`。
3. 必要时仅保留 introspection 中的 pattern 汇总，不进入 governed evolution。
4. 不允许在未修订 `MC-005` 前，扩大到自动 proposal 生成之外的新自动写路径。

## open_questions

1. 首版 `EvolutionSeed.change_target_type` 是否只允许 `workflow` / `skill`，还是预留 `prompt_policy` 但默认禁用。
2. `ErrorPattern.frequency` 的提案阈值是按绝对次数，还是按时间窗口内重复率。
3. 对“用户多次纠正同一类回答”的 pattern，首版更适合生成 `skill_candidate` 还是 `workflow_candidate`。
