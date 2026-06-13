# MC-004 任务包：元认知到 working memory / introspection / memory_candidates 的最小桥接

Date: 2026-06-13
Status: Implemented (verification pending)
Scope: MetaCognitionRuntime 结果向 continuity、可观测性与治理记忆链路的最小回写边界已落地

## title

`MC-004` 元认知到 working memory / introspection / memory_candidates 的最小桥接

## goal

冻结 `MetaCognitionRuntime` 输出结果的最小回写链路，使结构化反思不只是“写日志”，而能对下一轮上下文、运行时观测和受治理记忆产生实际影响。

## implemented_state

当前代码已经按最小 sidecar 桥接方案落地：

1. working memory bridge 已固定复用 `append_attention_item()` 与 `append_pending_question()`。
2. 每个 reflection 最多写入 1 条 caution 和 1 条 pending question。
3. working memory 预算与 `context.world_attention_max_items` 同量级；超预算时丢弃并记录桥接决策。
4. `inspect_context()` / `meta_cognition_summary()` 已暴露最近 journals、reflections、confidence traces、bridge 状态和计数。
5. `memory_candidates` bridge 只从 `ReflectionRecord.learned_rule_candidate` 产出，不接 journal 或普通失败日志。
6. `memory_candidates` 仍复用现有 `fact / preference / task_pattern / constraint` kinds。
7. `session_search(result_shape="memory_blocks")`、`RetrievalFusion`、nearline profile、Dream 消费链保持不变，没有新增独立 meta retrieval 源。

## landed_contract

### working memory

1. 不新增 `meta_attention` 字段。
2. 不把整段 journal / reflection 文本写回 working memory。
3. 首版只写轻量 caution / pending question 风格的短项。
4. 若当前 `attention_items` 和 `pending_questions` 同时达到预算，桥接跳过并记录 `dropped_budget`。

### introspection

1. `meta_cognition_summary()` 已在现有字段上扩展：
   - `structured_reflection_enabled`
   - `artifact_status`
   - `recent_journals`
   - `recent_reflections`
   - `recent_confidence_traces`
   - `working_memory_bridge`
   - `memory_candidate_bridge`
   - `bridge_decision_counts`
   - `recent_patterns`
   - `recent_evolution_seeds`
   - `last_signal_upserts`
2. continuity disabled 时仍返回稳定空结构，而不是报错或缺字段。
3. 对 introspection / audit 暴露的内容只包含 redacted preview，不包含原始 evidence、prompt 或 payload。

### memory_candidates

1. 只有同时满足以下条件的 `learned_rule_candidate` 才能入队：
   - `retention_hint == "candidate"`
   - 置信度达到 `meta_cognition.memory_candidate_min_confidence`
   - `kind` 属于 `preference | task_pattern | constraint | fact`
   - `sensitivity` 不是 `review-only`
2. `task_pattern` 固定写为：
   - `scope="session"`
   - `owner_id=runtime_context.user_id`
   - `source_session_key=current session_key`
3. `MemoryCandidate.source_excerpt` 固定写成 redacted、紧凑的 meta rationale。
4. `metadata.origin="meta_cognition"`，并带 `reflection_id`、`trigger_types`、`retention_hint`。

## affected_modules

本任务包当前主要落在：

1. [OriginAgent/agent/meta_cognition_reflector.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/meta_cognition_reflector.py)
2. [OriginAgent/agent/introspection/service.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/introspection/service.py)
3. [OriginAgent/agent/working_memory.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/working_memory.py)
4. [OriginAgent/memory/candidates.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/memory/candidates.py)
5. [OriginAgent/memory/profile.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/memory/profile.py)
6. [OriginAgent/agent/retrieval_fusion.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/retrieval_fusion.py)
7. [OriginAgent/session/search.py](/D:/Demo/OpenHome/OriginAgentclient/OriginAgent/session/search.py)

## acceptance_status

本任务包的设计性验收标准已经在代码层满足：

1. working memory 只接轻量 caution / pending question，不接整段 reflection。
2. introspection 只暴露最近元认知摘要、计数和 redacted preview。
3. `memory_candidates` 只接高置信 `learned_rule_candidate`，并复用现有 kind。
4. 元认知 candidate 已复用既有 `session_search` / retrieval / nearline consumer 机制。
5. Dream、nearline profile、memory governance 没有新增平行消费者。
6. `MC-005` 已直接建立在本任务包的桥接边界之上。

## validation

当前仍待完成的主要是运行验收：

1. working memory 写回测试需要在可运行 Python 环境下执行。
2. introspection summary 与 continuity disabled 稳定字段测试需要执行。
3. `memory_candidates` 与 nearline profile / Dream / retrieval 的联动测试需要补跑。

## rollback_plan

若后续验证发现上下文污染、召回噪音或 candidate 队列膨胀，回滚边界仍保持：

1. 先关闭 meta bridge 写回开关。
2. 保留 introspection 只读摘要，停止 working memory 与 `memory_candidates` 写回。
3. 优先回退到只保留 introspection preview 的最小模式。
4. 不扩大可写回对象集合，直到文档与阈值重新审定。

## next_closeout

收官前还应完成：

1. 运行 working memory / retrieval / consumer 联动测试。
2. 用真实会话样本检查写回噪音与去重效果。
3. 依据实际噪音再微调桥接阈值，而不是扩展新 sink。
