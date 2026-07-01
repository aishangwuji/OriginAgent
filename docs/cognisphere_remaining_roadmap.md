# CogniSphere 剩余任务路线图

Date: 2026-07-01
Status: Active
Based-on: cognisphere_master_plan.md (v2026-06-12)
Completed: CS-002, CS-003, CS-004, CS-005, CS-007, CS-009 (7 个任务包)
Remaining: CS-001, CS-006, CS-008, CS-010, CS-011, CS-012 (6 个任务包)
Total tests: 130 passing, 0 failing

---

## 一、总体状态

```
已完成: ████████████████████ 55%  (7/13 任务包)
进行中: ░░░░░░░░░░░░░░░░░░░░   0%
未开始: ████████████████░░░░ 45%  (6/13 任务包)
```

### 完成度按 Phase

| Phase | 进度 | 已交付 | 未交付 |
|-------|------|--------|--------|
| Phase 0 边界冻结 | 90% | 总纲 + CS-001 基本完成 | 正式验收 |
| Phase 1 思维底座 | 80% | CS-002 | ActionTrace 对象 |
| Phase 2 内在引擎 | 60% | CS-004 | 全量 InnerMonologueEngine |
| Phase 3 世界模拟 | 80% | CS-005 | CS-006 (运行时逻辑) |
| Phase 4 元编程/技能自举 | 75% | MetaProgrammingEngine + CS-009 | CS-008 (扩展) |
| Phase 5 架构重构 | 0% | — | CS-010 |
| Phase 6 产品化治理 | 0% | — | CS-011 + CS-012 |

---

## 二、剩余任务包详细计划

### 优先级 P0 — 低依赖、高价值

#### CS-011: 不可变安全核心

**前置依赖：** 无（独立于所有其他任务包）
**工作量估计：** ~400 行 / 2-3 天
**风险：** 低

**交付内容：**
1. `agent/immutable_safety_core.py` — 安全核心文档 + watchdog 设计
2. 冻结清单：PermissionResolver / ActionSafetyGate / ConfirmationManager / 核心审计写入器 / rollback 服务 / evolution control-plane apply=false 原则 / overlay "只能收紧安全" 规则 / tool/exec/device 高风险 blocklist / watchdog / kill switch
3. 运行时断言：启动时验证核心模块未被绕过
4. 配置：`immutable_core_enabled` + `immutable_core_watchdog_interval`

**验收标准：**
- 安全核心清单文档化并可通过 status 端点查询
- 启动断言可检测核心模块缺失或绕过
- watchdog 在配置间隔内执行健康检查
- 所有测试通过

---

#### CS-010: ArchitectureProposal / Replay / Report

**前置依赖：** CS-005 稳定、trial/rollback/report/health 可用
**工作量估计：** ~600 行 / 4-5 天
**风险：** 中

**交付内容：**
1. `agent/architecture_refactor_models.py` — `ArchitectureProposal` / `BottleneckReport` / `ReplayResult` frozen dataclass
2. `agent/architecture_refactor_engine.py` — 瓶颈分析 / replay evaluator / 对比报告生成
3. 与 EvolutionOperator 和 EvolutionControlPlane 集成

**验收标准：**
- ArchitectureProposal 包含 source_patterns / target_modules / change_hypothesis / expected_benefit / risk_notes / required_replay_suite / review_level
- Replay evaluator 可在沙箱中回放历史任务
- 对比报告包含任务完成率 / 平均延迟 / token 成本 / 错误率
- 所有产出走 governed review 路径

---

### 优先级 P1 — 需要部分前置

#### CS-006: 实体跟踪 / 因果边 / 假设空间运行时

**前置依赖：** CS-005（已完成，模型层已就绪）
**工作量估计：** ~500 行 / 3-4 天
**风险：** 中

**交付内容：**
1. 扩展 `WorldSimulator` 运行时逻辑：
   - 实体跟踪：跨 session 维护 EntityState 的增删改查
   - 因果边传播：基于 CausalEdge 的贝叶斯链式推理
   - 假设空间：HypothesisRecord 生命周期的完整管理（创建/验证/确认/反驳/过期）
   - 矛盾检测：匹配 CausalEdge.contradiction_refs 与 HypothesisRecord.competing_hypotheses
2. 与 ThoughtSubstrate 桥接：SimulationRequest → ThoughtFrame.simulation_requests

**验收标准：**
- EntityState 可跨 session 持久化和查询
- 因果边支持链式传播（A→B, B→C 可推理 A→C）
- HypothesisRecord 支持状态机：active → confirmed/refuted/expired
- 矛盾边可被检测和记录
- 所有测试通过

---

#### CS-008: MetaProgrammingEngine Patch Candidate 扩展

**前置依赖：** MC 主线稳定、MetaProgrammingEngine 已就绪
**工作量估计：** ~350 行 / 2-3 天
**风险：** 低-中

**交付内容：**
1. 扩展 `CompiledProposalBundle` 支持所有 5 种 target_type：workflow / skill / config_overlay / prompt_policy / architecture_patch
2. 为 prompt_policy 和 architecture_patch 添加自动 test fixture 生成
3. 完善 compile_to_proposal_bundle 的证据链收集

**验收标准：**
- 5 种 target_type 都有对应的 artifact_preview 和 trial_fixtures
- test fixture 自动生成包含至少输入/输出/验证步骤
- 编译产物可被 Curator 正确消费

---

### 优先级 P2 — 长尾

#### CS-012: 观测性指标与长期治理

**前置依赖：** CS-011（安全核心）、CS-010（架构重构）
**工作量估计：** ~400 行 / 3 天
**风险：** 低

**交付内容：**
1. 长期观测指标采集（thought 产生率 / 模拟准确率 / 技能候选质量 / 架构漂移）
2. thought / simulation / patch candidate retention 治理
3. 运行成本 / 错误率 / 演化健康趋势面板数据
4. 与 WebUI MetaCognitionView 集成

---

## 三、推荐推进顺序

```
Phase 5 ─── CS-011 (独立安全核心) ──→ CS-010 (架构重构提案)
              ↓ 无依赖                  ↓ 依赖 CS-005
Phase 3 ─── CS-006 (实体/因果运行时)
              ↓
Phase 4 ─── CS-008 (Patch扩展)
              ↓ 依赖 CS-011 + CS-010
Phase 6 ─── CS-012 (观测性治理)
```

### 建议的 3 批交付

| 批次 | 任务包 | 估计工作量 | 累积测试 |
|------|--------|-----------|---------|
| 第一批 | CS-011 安全核心 | 2-3 天 | +~20 |
| 第二批 | CS-010 架构提案 + CS-006 实体推理 | 7-8 天 | +~50 |
| 第三批 | CS-008 扩展 + CS-012 观测性 | 5-6 天 | +~30 |
| **总计** | **6 个任务包** | **14-17 天** | **+~100** |

---

## 四、风险矩阵

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| CS-010 replay evaluator 不稳定 | 中 | 高 | 先做只读 repla y，沙箱隔离 |
| CS-006 假设空间膨胀 | 中 | 中 | 设置 TTL 和最大活跃假设数 |
| CS-008 多 target_type 互斥 | 低 | 中 | 每个类型独立测试 |
| CS-012 指标维度膨胀 | 中 | 低 | 限制核心指标 <= 15 个 |
| 多任务包并行依赖冲突 | 低 | 高 | 严格按照顺序执行 |
