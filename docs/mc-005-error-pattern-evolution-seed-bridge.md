# MC-005 任务包：ErrorPattern consolidation 与 governed evolution seed 桥接

Date: 2026-06-13
Status: Implemented (verification pending)
Scope: MetaCognitionRuntime 重复问题模式向 governed evolution signal / proposal seed 的最小桥接边界已落地

## title

`MC-005` ErrorPattern consolidation 与 governed evolution seed 桥接

## goal

冻结 `MetaCognitionRuntime` 的高阶产物如何接入受治理自演进链路，使 OriginAgent 能把“重复犯错”“重复低效”“反复缺 guardrail”的模式，转成可审查、可抑制、可回滚的演进候选，而不是直接触发自修改。

## implemented_state

当前代码已经按最小桥接链落地：

1. `ReflectionRecord` 已可被规则式汇总为 `ErrorPattern`，不新增额外 LLM 调用。
2. `ErrorPattern` 与 `EvolutionSeed` 已升级为显式字段模型。
3. 模式汇总固定按 `owner_id` 分区，避免不同用户上下文互相污染。
4. 时间窗口语义已固定为“先按 `pattern_window_days` 过滤，再保留最新的 `pattern_window_max_reflections` 条”。
5. `patterns.jsonl` 与 `evolution_seeds.jsonl` 已进入统一 meta audit ledger。
6. eligible pattern 会被规范化为现有 `OpportunitySignalCandidate`，并写入既有 `OpportunitySignalStore`。
7. bridge 已把 `evidence_refs` 转换为兼容 `_merge_evidence_sources` 的 `evidence_sources: list[dict]`。
8. 若同一 `target_key` 的现有 signal 状态为 `suppressed`，当前 turn 不再 upsert。
9. 每个 turn 最多 upsert 1 个 signal，优先级遵循 `severity -> frequency -> trigger type`。

## landed_contract

### pattern consolidation

1. consolidation 固定只读取最近窗口内的 redacted `ReflectionRecord`。
2. 排除：
   - `policy_denied` only turn
   - 没有 `root_cause_hypotheses`、`what_failed`、`learned_rule_candidate` 的 reflection
   - `learned_rule_candidate.kind == "preference"`
3. 归一化文本优先级固定为：
   - `learned_rule_candidate.summary`
   - `root_cause_hypotheses[0]`
   - `what_failed[0]`
4. `candidate_target_type` 固定规则：
   - `task_pattern -> workflow_candidate`
   - `fact / constraint -> skill_candidate`
   - 重复同类 `user_correction` 且无 learned rule -> `skill_candidate`
5. 只有 `severity in {"medium", "high"}` 且满足频次、distinct turn 阈值的 pattern 才进入 seed 阶段。

### evolution seed

1. `EvolutionSeed.change_target_type` 首版只允许：
   - `workflow_candidate`
   - `skill_candidate`
2. `target_key` 已固定为语义可读、稳定去重的格式：
   - `meta.workflow.<capability_domain>.<dominant_trigger_type>.<pattern_slug>.<pattern_key8>`
   - `meta.skill.<capability_domain>.<dominant_trigger_type>.<pattern_slug>.<pattern_key8>`
3. `pattern_slug` 为空时固定为 `unknown`，不回退成纯哈希。
4. `title` / `summary` 均使用确定性模板，不新增 LLM。
5. `summary` 首行固定保留 `Origin: meta_cognition`。

### signal bridge

1. bridge 只复用现有：
   - `OpportunitySignalStore`
   - `Curator`
   - `ReviewProposalStore`
   - `EvolutionControlPlane`
2. `OpportunitySignal` schema 没有新增字段。
3. 每条 evidence source 固定包含：
   - `cursor`
   - `session_key`
   - `timestamp`
   - `preview`
4. evidence 只来自 redacted meta 片段，不包含 raw prompt / history / tool payload / source excerpt。
5. operator 仍通过现有 `inspect_signal / list_signals / suppress / resume / recommendations` 控制，没有 auto-apply。

## affected_modules

本任务包当前主要落在：

1. [OriginAgent/agent/meta_cognition_patterns.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/meta_cognition_patterns.py)
2. [OriginAgent/agent/meta_cognition_evolution_bridge.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/meta_cognition_evolution_bridge.py)
3. [OriginAgent/agent/meta_cognition_reflector.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/meta_cognition_reflector.py)
4. [OriginAgent/agent/meta_cognition_models.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/meta_cognition_models.py)
5. [OriginAgent/agent/meta_cognition_audit.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/meta_cognition_audit.py)
6. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
7. [OriginAgent/agent/evolution.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/evolution.py)

## acceptance_status

本任务包的设计性验收标准已经在代码层满足：

1. `ErrorPattern`、`EvolutionSeed` 与现有 `OpportunitySignal` 的角色关系已固定并落地。
2. 元认知桥接只写 signal / seed，不直接写 proposal、代码或主配置。
3. 首版只桥接到现有 `workflow_candidate` / `skill_candidate` 两类演进方向。
4. signal evidence 来自多条 redacted 元认知片段，而不是单次失败日志。
5. 生成的 signal 继续走现有 `Curator -> ReviewProposalStore -> PromotionGate / Trial / Review` 链路。
6. operator 仍通过既有 evolution control plane 做 inspect、suppress、resume 和 report。

## validation

当前仍待完成的主要是运行验收与噪音观察：

1. pattern consolidation、seed 生成、signal bridge 的测试需要在可运行 Python 环境下执行。
2. `OpportunitySignalStore.upsert_candidates()` 后的 evidence merge 兼容性需要实际跑通。
3. Curator / control plane / recommendations 的 meta-origin 联动需要做回归验证。
4. 开关打开后的 signal 噪音、抑制率和重复 upsert 行为需要做一轮真实样本观察。

## rollback_plan

若后续验证发现 signal 噪音高、proposal 队列污染或方向失真，回滚边界仍保持：

1. 先关闭 meta-to-evolution bridge 开关。
2. 保留 `ErrorPattern` 与 `EvolutionSeed` 只读记录，停止写入 `OpportunitySignalStore`。
3. 必要时仅保留 introspection 中的 pattern 汇总，不进入 governed evolution。
4. 不扩大到自动 proposal 之外的新自动写路径。

## next_closeout

收官前还应完成：

1. 跑完 pattern / seed / signal / curator 联动测试。
2. 基于真实元认知样本评估 `pattern_min_frequency`、`pattern_min_distinct_turns` 和 `max_signal_upserts_per_turn`。
3. 编写一份 operator 侧使用说明，覆盖 `inspect_signal`、`suppress`、`resume` 和 meta 来源判读。
