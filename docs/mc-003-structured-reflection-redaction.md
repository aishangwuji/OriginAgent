# MC-003 任务包：结构化反思输出与 redaction / retention 策略

Date: 2026-06-12
Status: Proposed
Scope: MetaCognitionRuntime 的结构化 reflection 输出、脱敏规则与保留策略冻结

## title

`MC-003` 结构化反思输出与 redaction / retention 策略

## goal

冻结 `MetaCognitionRuntime` 的最小结构化输出契约，使系统能够在不暴露原始 chain-of-thought、不污染 prompt、不泄露敏感数据的前提下，产出可治理、可检索、可审计的元认知对象。

本任务包完成后，系统应能稳定回答：

1. 一次元认知输出最少应该长什么样。
2. 哪些字段允许保留，哪些字段必须裁剪或脱敏。
3. journal、reflection、confidence trace 之间如何分层。
4. 哪些结果只做短期保留，哪些结果允许进入后续治理链。

## scope

本任务包覆盖以下内容：

1. 冻结最小结构化输出对象：
   - `ThoughtJournalEntry`
   - `ReflectionRecord`
   - `ConfidenceTrace`
2. 冻结各对象的最小字段：
   - `ThoughtJournalEntry`
     - `entry_id`
     - `session_key`
     - `trigger_type`
     - `task_reference`
     - `strategy_summary`
     - `assumptions`
     - `evidence_refs`
     - `confidence`
     - `expected_outcome`
     - `actual_outcome`
     - `mismatch_summary`
     - `suggested_next_action`
     - `created_at`
   - `ReflectionRecord`
     - `reflection_id`
     - `source_entry_ids`
     - `reflection_kind`
     - `outcome_class`
     - `root_cause_hypotheses`
     - `what_worked`
     - `what_failed`
     - `learned_rule_candidate`
     - `confidence`
     - `retention_hint`
   - `ConfidenceTrace`
     - `trace_id`
     - `subject_type`
     - `subject_reference`
     - `initial_confidence`
     - `final_confidence`
     - `change_reason`
     - `evidence_refs`
3. 冻结输出风格约束：
   - 只保留结构化摘要
   - 不保留原始全文思维过程
   - 不保留自由文本长篇独白
4. 冻结 redaction 规则：
   - 复用现有 `redact_memory_text`
   - 复用 audit 侧 forbidden metadata 思路
   - evidence 只保留 ref，不保留原始 payload / prompt / source excerpt
   - 所有面向 introspection / audit 的文本都需裁剪
5. 冻结 retention 策略：
   - `ThoughtJournalEntry` 默认短期保留
   - `ReflectionRecord` 允许中期保留
   - `ConfidenceTrace` 默认只保留摘要或聚合结果
   - learned rule candidate 不等于直接长期记忆
6. 冻结 reflection prompt 输出约束：
   - 输出必须是结构化 JSON 或等价结构
   - 必须区分“假设”“证据”“偏差”“下一步”
   - 必须允许显式输出“不确定”

## non_goals

本任务包不覆盖以下内容：

1. trigger collector 实现。
2. working memory / introspection / memory_candidates 的具体回写。
3. `ErrorPattern` 与 `EvolutionSeed` 的 consolidation 实现。
4. 存储文件名或 JSONL schema 的最终实现定稿。
5. 多模态反思模板。
6. 模型供应商相关 reasoning block 的兼容层实现。

## dependencies

1. [`docs/meta_cognition_runtime_outline.md`](./meta_cognition_runtime_outline.md)
2. [`docs/mc-001-meta-cognition-runtime-boundary.md`](./mc-001-meta-cognition-runtime-boundary.md)
3. [`docs/mc-002-meta-trigger-collection.md`](./mc-002-meta-trigger-collection.md)
4. [`docs/governed_evolution.md`](./governed_evolution.md)
5. 当前 redaction / audit / memory 锚点：
   - [OriginAgent/agent/memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/memory.py)
   - [OriginAgent/agent/audit.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/audit.py)
   - [OriginAgent/agent/background_review.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/background_review.py)
   - [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)

## files_or_modules

本任务包预期主要触达文档层：

1. [`docs/mc-003-structured-reflection-redaction.md`](./mc-003-structured-reflection-redaction.md)
2. [`docs/meta_cognition_runtime_outline.md`](./meta_cognition_runtime_outline.md)

后续实现预计主要影响：

1. [OriginAgent/agent/memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/memory.py)
2. [OriginAgent/agent/audit.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/audit.py)
3. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
4. 候选新模块：
   - `OriginAgent/agent/meta_cognition_models.py`
   - `OriginAgent/agent/meta_cognition_runtime.py`
   - `OriginAgent/agent/meta_cognition_audit.py`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. `ThoughtJournalEntry`、`ReflectionRecord`、`ConfidenceTrace` 的最小字段集合已冻结。
2. 已明确：结构化反思输出不能保存原始 CoT，只能保存裁剪后的结构化摘要。
3. 已明确：evidence 默认保留 ref，不保留原始 payload、prompt、history 或 source excerpt。
4. 已明确：redaction 复用现有 memory / audit 规则，而不是另造一套脱敏体系。
5. 已明确：retention_hint 只表达保留建议，不直接等于长期记忆晋升。
6. `MC-004` 与后续 `MC-005` 可直接复用本任务包定义的结构化对象与 redaction 约束。

## tests

后续实现至少应覆盖以下测试：

1. 对象序列化/反序列化测试：
   - `ThoughtJournalEntry`
   - `ReflectionRecord`
   - `ConfidenceTrace`
2. redaction 测试：
   - 密钥、token、邮箱、长数字被脱敏
   - forbidden metadata key 被过滤
   - 原始 prompt / raw evidence 不进入输出对象
3. 长度裁剪测试：
   - 长摘要被截断
   - evidence ref 数量受限
   - 假设 / mismatch / next action 字段有上限
4. retention 测试：
   - `retention_hint=discard` 不进入持久候选
   - `retention_hint=review` 不自动晋升长期记忆

## rollback_plan

若本任务包的结构化输出定义被证明不适用于后续实现，回滚方式应为：

1. 保留现有对象兼容读取能力。
2. 在新版本文档中显式记录字段变更、脱敏边界变更与保留策略变更。
3. 不允许在未更新 `MC-003` 前，让实现回退成自由文本反思日志或保存原始 CoT。

## open_questions

1. `ConfidenceTrace` 首版是否独立落盘，还是仅作为 `ReflectionRecord` 的派生视图。
2. `retention_hint` 是否需要标准化为有限枚举，例如 `discard / short / review / candidate`。
3. `learned_rule_candidate` 是否首版仅允许单条紧凑结论，而不允许复合规则集。
