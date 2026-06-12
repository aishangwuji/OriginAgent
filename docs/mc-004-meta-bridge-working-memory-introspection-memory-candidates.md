# MC-004 任务包：元认知到 working memory / introspection / memory_candidates 的最小桥接

Date: 2026-06-12
Status: Proposed
Scope: MetaCognitionRuntime 结果向 continuity、可观测性与治理记忆链路的最小回写边界冻结

## title

`MC-004` 元认知到 working memory / introspection / memory_candidates 的最小桥接

## goal

冻结 `MetaCognitionRuntime` 输出结果的最小回写链路，使结构化反思不只是“写日志”，而能对下一轮上下文、运行时观测和受治理记忆产生实际影响。

本任务包完成后，系统应能稳定回答：

1. 哪些元认知结果可以进入 working memory。
2. 哪些结果只应该进入 introspection / audit。
3. 哪些结果有资格转成 `memory_candidates`。
4. 如何复用现有 `memory_candidates` kind、`session_search` block 和 nearline consumer，而不是另起一套存储。

## scope

本任务包覆盖以下内容：

1. 冻结 working memory 最小桥接规则：
   - 只允许轻量 `meta_attention` / caution / pending_question 风格写回
   - 不允许整段 journal / reflection 全量写回
   - 默认上限小于或等于 world attention 的同量级预算
2. 冻结 introspection 最小桥接规则：
   - continuity / cognition summary 暴露最近元认知摘要
   - 暴露最近触发、最近反思、最近 caution、重复模式计数
   - 只提供 redacted preview，不暴露原始证据
3. 冻结 `memory_candidates` 最小桥接规则：
   - 只允许高置信 `learned_rule_candidate`
   - 优先复用现有 kind：
     - `preference`
     - `task_pattern`
     - `constraint`
     - `fact`
   - 禁止把普通失败日志直接写入 `memory_candidates`
4. 冻结 retrieval / session_search 复用规则：
   - 进入 `memory_candidates` 的元认知结论应自动复用现有 memory block 链
   - 不新增独立“meta retrieval”源
5. 冻结与 nearline / profile / dream consumer 的关系：
   - `preference` / `task_pattern` 可被 nearline profile 消费
   - `fact` / `constraint` 继续由 Dream 消费
   - 元认知桥接只产出 candidate，不负责最终 apply
6. 冻结桥接阈值建议：
   - 普通 `ThoughtJournalEntry` 默认不进 `memory_candidates`
   - `ReflectionRecord.learned_rule_candidate` 需达到最小 confidence 门槛
   - 高敏感项默认 `requires review` 或降级为 introspection only

## non_goals

本任务包不覆盖以下内容：

1. `MetaCognitionRuntime` 的完整实现。
2. `ErrorPattern` 到 governed evolution signal 的桥接实现。
3. retrieval fusion 排序算法调整。
4. Dream / nearline consumer 的完整重构。
5. world model 到 meta bridge 的扩展策略。
6. 前端或 UI 展示实现。

## dependencies

1. [`docs/meta_cognition_runtime_outline.md`](./meta_cognition_runtime_outline.md)
2. [`docs/mc-001-meta-cognition-runtime-boundary.md`](./mc-001-meta-cognition-runtime-boundary.md)
3. [`docs/mc-002-meta-trigger-collection.md`](./mc-002-meta-trigger-collection.md)
4. [`docs/mc-003-structured-reflection-redaction.md`](./mc-003-structured-reflection-redaction.md)
5. 当前 continuity / governance / retrieval 锚点：
   - [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)
   - [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
   - [OriginAgent/agent/memory_governance.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/memory_governance.py)
   - [OriginAgent/memory/candidates.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/memory/candidates.py)
   - [OriginAgent/memory/profile.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/memory/profile.py)
   - [OriginAgent/agent/retrieval_fusion.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/retrieval_fusion.py)
   - [OriginAgent/session/search.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/session/search.py)

## files_or_modules

本任务包预期主要触达文档层：

1. [`docs/mc-004-meta-bridge-working-memory-introspection-memory-candidates.md`](./mc-004-meta-bridge-working-memory-introspection-memory-candidates.md)
2. [`docs/meta_cognition_runtime_outline.md`](./meta_cognition_runtime_outline.md)

后续实现预计主要影响：

1. [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)
2. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
3. [OriginAgent/agent/retrieval_fusion.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/retrieval_fusion.py)
4. [OriginAgent/session/search.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/session/search.py)
5. 候选新模块：
   - `OriginAgent/agent/meta_cognition_runtime.py`
   - `OriginAgent/agent/meta_cognition_bridge.py`
   - `OriginAgent/agent/meta_cognition_models.py`

## acceptance_criteria

本任务包完成时，必须同时满足以下条件：

1. 已明确 working memory 只接轻量 caution / meta attention，不接整段 reflection 文本。
2. 已明确 introspection 只暴露最近元认知摘要、计数和 redacted preview。
3. 已明确 `memory_candidates` 只接高置信 learned rule candidate，且复用现有 `fact / preference / task_pattern / constraint` kinds。
4. 已明确元认知 candidate 进入 `memory_candidates` 后应复用现有 `session_search` / retrieval / nearline consumer 机制。
5. 已明确 Dream、nearline profile、memory governance 各自消费哪些 kind，不新增平行消费者。
6. `MC-005` 可直接在本任务包基础上桥接 `ErrorPattern` 与 governed evolution seed。

## tests

后续实现至少应覆盖以下测试：

1. working memory 写回测试：
   - caution 项进入 working memory
   - journal / reflection 全文不会进入 working memory
   - item 数量与去重受控
2. introspection 测试：
   - 最近触发摘要可见
   - 最近 reflection preview 可见
   - 原始 evidence / prompt 不可见
3. `memory_candidates` 桥接测试：
   - 高置信 preference 进入 `preference`
   - 重复操作经验进入 `task_pattern`
   - 稳定限制进入 `constraint`
   - 普通失败日志不会进入 queue
4. retrieval / search 测试：
   - 元认知 candidate 可通过现有 memory block 被召回
   - block type 与 kind 映射正确
5. consumer 联动测试：
   - nearline profile 可消费 `preference` / `task_pattern`
   - Dream 可消费 `fact` / `constraint`

## rollback_plan

若本任务包实现导致上下文污染、召回噪音或 candidate 队列膨胀，回滚方式应为：

1. 先关闭 meta bridge 写回开关。
2. 保留 introspection 只读摘要，停止 working memory 和 `memory_candidates` 写回。
3. 优先回退到只保留 introspection preview 的最小模式。
4. 不允许在未修订 `MC-004` 前继续扩大可写回对象集合。

## open_questions

1. working memory 是否需要新增显式 `meta_attention` 字段，还是先复用现有 `attention_items` / `pending_questions`。
2. 元认知 candidate 的 `owner_id` 与 `scope` 是否默认沿用当前 runtime context，还是对 `task_pattern` 采用更保守的 `session` 范围。
3. `task_pattern` 首版是否只允许总结“下一次应先做什么”，而不允许生成复杂流程建议。
