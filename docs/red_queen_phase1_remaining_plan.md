# OriginAgent 红后化 Phase 1 剩余部分落地计划书

Date: 2026-06-09
Status: In Progress
Last Reviewed: 2026-06-13
Scope: 红后化总主线 `Phase 1` 的收口索引、状态同步与子任务包入口

## 1. 文档定位

本文件不再承担 `Phase 1` 的核心边界定义职责，而是作为收口索引页存在。

截至 2026-06-13，红后化总主线 `Phase 1` 的正确状态是：

1. 运行时与模型骨架已大面积落地。
2. 当前重点是收口、验收、证据核对与差距登记。
3. 核心口径以 [`red_queen_phase1_closeout_plan.md`](./red_queen_phase1_closeout_plan.md) 为准。

## 2. 当前阶段判断

当前不应再把 `Phase 1` 描述为“待从零实现”的大项，更准确的判断是：

1. 后台认知主链已进入代码、测试与 introspection。
2. 连续性与记忆最小骨架已落地，不再属于本文件的剩余施工重点。
3. `Phase 2` 世界视图能力当前只能按 `P2A` 原型理解，不应回写为 `Phase 1` 已关闭的理由。

## 3. 当前收口入口

`Phase 1` 收口统一通过以下文档组织：

1. 总包：
   - [`red_queen_phase1_closeout_plan.md`](./red_queen_phase1_closeout_plan.md)
2. 子包：
   - [`rq-001-cognitive-orchestration-boundary.md`](./rq-001-cognitive-orchestration-boundary.md)
   - [`rq-005-cognitive-event-audit-model.md`](./rq-005-cognitive-event-audit-model.md)
   - [`p1-backend-cognition-integration-task-package.md`](./p1-backend-cognition-integration-task-package.md)

## 4. 本文件不再重复定义的内容

以下内容以收口总包和子包为准，本文件不再重复展开：

1. 主路径与 fallback 路径的详细责任定义。
2. `CognitiveEvent`、`CognitiveDecision` 与 JSONL 审计合同。
3. producer 集合、working memory 写回与 introspection 细项。
4. 双轨审计、开关绑定、`scheduler_runs.jsonl` 路径差异等 gap 细项。

## 5. 当前建议的下一步

当前 `Phase 1` 的建议动作顺序是：

1. 先以收口总包为基准完成文档状态统一。
2. 再按收口矩阵核对已有测试证据与显式差距。
3. 最后再决定是否需要独立的 closeout patch 去补最小测试或局部代码卫生修复。
