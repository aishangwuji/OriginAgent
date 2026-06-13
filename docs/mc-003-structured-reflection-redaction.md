# MC-003 任务包：结构化反思输出与 redaction / retention 策略

Date: 2026-06-13
Status: Implemented (verification pending)
Scope: MetaCognitionRuntime 的结构化 reflection 输出、脱敏规则与保留边界已落地

## title

`MC-003` 结构化反思输出与 redaction / retention 策略

## goal

冻结 `MetaCognitionRuntime` 的最小结构化输出契约，使系统能够在不暴露原始 chain-of-thought、不污染 prompt、不泄露敏感数据的前提下，产出可治理、可检索、可审计的元认知对象。

## implemented_state

当前代码已经按 sidecar 方案落地了本任务包的核心边界：

1. `ThoughtJournalEntry`、`ReflectionRecord`、`ConfidenceTrace` 已升级为显式字段模型。
2. 三个对象仍保留 `summary` 与 `payload` 兼容字段，用于旧 JSONL 读取和 redacted preview，但不再承载主语义。
3. `from_json()` 已兼容旧骨架记录，新写入以显式字段为真值。
4. 每个 accepted trigger 会先写入 deterministic minimal journal，不依赖 LLM。
5. `structured_reflection_enabled=true` 时，`MetaCognitionReflector` 会通过 `AuxiliaryLLMRouter` 的 `task="meta_cognition"` 调用旁路反思。
6. 反思 prompt 固定复用单模板 [`OriginAgent/templates/agent/meta_cognition_reflection.md`](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/templates/agent/meta_cognition_reflection.md)。
7. 旁路输出使用 fenced-JSON tolerant 解析；JSON 解析失败、schema 校验失败或 LLM 失败时，只保留 minimal journal。
8. `journals.jsonl`、`reflections.jsonl`、`confidence_traces.jsonl` 已进入统一的 meta audit ledger。

## landed_contract

### 结构化对象

1. `ThoughtJournalEntry` 已落地最小显式字段：
   - `entry_id`
   - `session_key`
   - `created_at`
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
2. `ReflectionRecord` 已落地最小显式字段：
   - `reflection_id`
   - `session_key`
   - `created_at`
   - `source_entry_ids`
   - `reflection_kind`
   - `outcome_class`
   - `root_cause_hypotheses`
   - `what_worked`
   - `what_failed`
   - `learned_rule_candidate`
   - `confidence`
   - `retention_hint`
3. `ConfidenceTrace` 已落地最小显式字段：
   - `trace_id`
   - `session_key`
   - `created_at`
   - `subject_type`
   - `subject_reference`
   - `initial_confidence`
   - `final_confidence`
   - `change_reason`
   - `evidence_refs`

### 输出风格与安全边界

1. 不保存原始 CoT。
2. 不保存 raw prompt、raw history、raw tool payload、raw source excerpt。
3. evidence 默认只保留 ref，不保留原始内容。
4. introspection / audit / bridge 只暴露 redacted preview。
5. `ConfidenceTrace.initial_confidence = null` 合法，表示首条快照而非差分。

### redaction / retention

1. `meta_cognition_redact` 已统一复用 [`OriginAgent/agent/memory.py`](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/memory.py) 中的 `redact_memory_text()`。
2. forbidden metadata key 过滤复用了 memory / audit 的既有思路，而不是另造一套脱敏体系。
3. `retention_hint` 已标准化为有限枚举：
   - `discard`
   - `short`
   - `review`
   - `candidate`
4. `learned_rule_candidate` 首版固定为单条紧凑结论，不允许复合规则集。

## affected_modules

本任务包当前主要落在：

1. [OriginAgent/agent/meta_cognition_models.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/meta_cognition_models.py)
2. [OriginAgent/agent/meta_cognition_reflector.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/meta_cognition_reflector.py)
3. [OriginAgent/agent/meta_cognition_audit.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/meta_cognition_audit.py)
4. [OriginAgent/agent/meta_cognition_redact.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/meta_cognition_redact.py)
5. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)

## acceptance_status

本任务包的设计性验收标准已经在代码层满足：

1. 三类结构化对象的最小字段集合已冻结并写入实现。
2. 结构化反思输出不保存原始 CoT，只保存裁剪后的结构化摘要。
3. evidence 默认保留 ref，不保留 payload / prompt / history / source excerpt。
4. redaction 复用现有 memory / audit 规则。
5. `retention_hint` 只表达保留建议，不直接等于长期记忆晋升。
6. `MC-004` 与 `MC-005` 已直接复用这些结构化对象与 redaction 约束。

## validation

当前仍未完成的是运行验收，不是契约设计：

1. 针对 `ThoughtJournalEntry` / `ReflectionRecord` / `ConfidenceTrace` 的兼容性与 round-trip 测试已补充到测试面。
2. redaction、长度裁剪、retention 路径的测试也已纳入目标测试范围。
3. 但当前环境缺少可运行的 Python / pytest，尚未完成实际执行验收。

## rollback_plan

若后续验证表明这套结构化输出定义不适用，回滚边界仍保持：

1. 保留现有对象兼容读取能力。
2. 在文档中显式记录字段或脱敏边界的版本变更。
3. 不允许回退成自由文本反思日志或保存原始 CoT。

## next_closeout

收官前还应完成：

1. 在可运行 Python 环境下执行元认知结构化产物相关测试。
2. 用真实运行样本复核 redaction、preview 长度和 evidence ref 上限。
3. 视验收结果再决定是否调整默认阈值，但不扩大对象边界。
