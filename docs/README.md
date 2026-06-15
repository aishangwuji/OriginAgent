# OriginAgent Docs

这个目录正在整理为 OriginAgent 通用智能体运行时文档。

原 OriginAgent v1 文档会作为历史迁移参考保留；后续新增文档应围绕 OriginAgent 的通用 Agent 内核、插件系统、领域能力包和演进机制编写。智能家居相关内容应沉降为 first-party domain plugin，而不是核心架构身份。

## 当前文档

- [`red_queen_master_plan.md`](./red_queen_master_plan.md)：OriginAgent 向“红后式智能体”演进的总计划书与进度对齐文档，覆盖后台认知、分层感知、世界模型、动作安全，并明确与 continuity、元认知、受治理演进和未来元编程主线的边界关系。
- [`red_queen_phase1_remaining_plan.md`](./red_queen_phase1_remaining_plan.md)：红后化总主线 `Phase 1` 剩余部分收口计划书，聚焦后台认知闭环、认知事件模型、统一编排与进入 `P2A` 世界视图原型后的验收边界。
- [`continuity_memory_os_outline.md`](./continuity_memory_os_outline.md)：连续性与记忆操作系统独立架构提纲，覆盖身份层、作用域、工作记忆、检索融合、上下文构造、晋升与遗忘。
- [`meta_cognition_runtime_outline.md`](./meta_cognition_runtime_outline.md)：显式元认知运行时已落地主线总纲，定义结构化反思、置信度跟踪、错误模式沉淀以及与 governed evolution / 未来元编程引擎的桥接边界。
- [`cognisphere_master_plan.md`](./cognisphere_master_plan.md)：CogniSphere 高阶总纲，定义 OriginAgent 在红后化、continuity、元认知之上的内在思维、世界模拟、元编程、技能自举、架构重构与不可变安全核心主线。
- [`mc-001-meta-cognition-runtime-boundary.md`](./mc-001-meta-cognition-runtime-boundary.md)：`MC-001` 任务包，冻结 `MetaCognitionRuntime` 的职责边界、输入输出对象与治理约束。
- [`mc-002-meta-trigger-collection.md`](./mc-002-meta-trigger-collection.md)：`MC-002` 任务包，冻结 `tool_failure`、`user_correction`、`task_completion` 三类元认知触发的采集与标准化边界。
- [`mc-003-structured-reflection-redaction.md`](./mc-003-structured-reflection-redaction.md)：`MC-003` 任务包，冻结结构化 reflection 输出对象、redaction 规则与 retention 策略。
- [`mc-004-meta-bridge-working-memory-introspection-memory-candidates.md`](./mc-004-meta-bridge-working-memory-introspection-memory-candidates.md)：`MC-004` 任务包，冻结元认知结果向 working memory、introspection 与 `memory_candidates` 的最小桥接边界。
- [`mc-005-error-pattern-evolution-seed-bridge.md`](./mc-005-error-pattern-evolution-seed-bridge.md)：`MC-005` 任务包，冻结重复错误模式向 governed evolution signal / proposal seed 的最小桥接边界。
- [`continuity_memory_phase1_plan.md`](./continuity_memory_phase1_plan.md)：连续性与记忆主线 `Phase 1` 已落地阶段说明，聚焦 identity、scope、working memory、context assembler 骨架的验收口径与收口方向。
- [`continuity_memory_phase2_plan.md`](./continuity_memory_phase2_plan.md)：连续性与记忆主线 `Phase 2` 收口计划书，覆盖 `P2A` 文件快照原型与 `P2B` 真实 ingress / anomaly bridge 的完成态边界。
- [`rq-011-p2b-ingress-anomaly-bridge.md`](./rq-011-p2b-ingress-anomaly-bridge.md)：`RQ-011` 任务包，定义并记录 `P2B` 的真实 ingress 与感知异常桥接收口结果。
- [`rq-012-p3a-world-model-stabilization-promotion-fusion.md`](./rq-012-p3a-world-model-stabilization-promotion-fusion.md)：`RQ-012` 任务包，记录 `P3A` 的世界模型稳定化、长期晋升 gating 与 retrieval/prewarm/world 融合闭环。
- [`rq-001-cognitive-orchestration-boundary.md`](./rq-001-cognitive-orchestration-boundary.md)：`RQ-001` 任务包，定义后台认知编排层的职责边界与现有 runtime 的关系。
- [`plan1.md`](./plan1.md)：历史 OriginAgent 第一阶段改造清单，保留作迁移参考。
- [`originagent-v1-development-plan.md`](./originagent-v1-development-plan.md)：历史 OriginAgent v1 总体开发任务清单与技术落地方案。
- [`rq-002-continuity-memory-os-boundary.md`](./rq-002-continuity-memory-os-boundary.md)：`RQ-002` 任务包，冻结连续性与记忆操作系统的 Phase 1 术语、四层视图与作用域传播默认规则。
- [`rq-003-identity-scope-model.md`](./rq-003-identity-scope-model.md)：`RQ-003` 任务包，定义 identity 与 scope 的最小实现边界。
- [`rq-004-working-memory-context-assembler-v2.md`](./rq-004-working-memory-context-assembler-v2.md)：`RQ-004` 任务包，定义 working memory 与 `ContextAssembler` v2 的最小落地方案。
- [`rq-005-cognitive-event-audit-model.md`](./rq-005-cognitive-event-audit-model.md)：`RQ-005` 任务包，定义后台认知事件、决策与审计模型。
- [`rq-006-perception-layered-data-models.md`](./rq-006-perception-layered-data-models.md)：`RQ-006` 任务包，冻结 `SceneSnapshot`、`InspectionResult`、`WorldSummary` 三层感知对象模型与边界。
- [`rq-007-visual-snapshot-ingress-retention.md`](./rq-007-visual-snapshot-ingress-retention.md)：`RQ-007` 任务包，冻结 path-first snapshot ingress、本地留存与 sidecar metadata 规则。
- [`rq-008-inspect-snapshot-tool-prototype.md`](./rq-008-inspect-snapshot-tool-prototype.md)：`RQ-008` 任务包，定义 `inspect_snapshot` 的最小输入输出契约、service/fallback 路径与写回语义。
- [`rq-009-world-model-query-freshness.md`](./rq-009-world-model-query-freshness.md)：`RQ-009` 任务包，冻结最小 `world_view` 查询面、freshness 规则与过滤语义。
- [`rq-010-world-summary-continuity-bridge.md`](./rq-010-world-summary-continuity-bridge.md)：`RQ-010` 任务包，定义世界摘要注入 continuity 主线、working memory 与 introspection 的桥接规则。
- [`p1-backend-cognition-integration-task-package.md`](./p1-backend-cognition-integration-task-package.md)：`P1 Integration` 任务包，聚焦后台认知闭环的实现型集成。
- [`runtime_security.md`](./runtime_security.md)：runtime/tools 安全边界与审计说明。
- [`governed_evolution.md`](./governed_evolution.md)：受治理自进化的控制面、trial 隔离、日志保留、健康历史与硬安全边界。
- [`runtime_profiles.md`](./runtime_profiles.md)：runtime profile presets 与适用场景。
- [`device_gateway.md`](./device_gateway.md)：dry-run lighting device gateway 配置和边界。
- [`release_notes_runtime_ergonomics.md`](./release_notes_runtime_ergonomics.md)：runtime ergonomics RC 收口说明。

