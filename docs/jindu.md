# CogniSphere 推进进度

Date: 2026-07-01
Status: Active
Phase: CS-002 → CS-003 → CS-004 → CS-005 → CS-007 → CS-009 (已完成)
Coverage: Phase 0-4 累计 7 个任务包完成，130 个测试通过

---

## 一、整体进度总览

```
Phase 0  边界冻结与术语基线     ████████████████ 90%
Phase 1  思维底座与痕迹层        ██████████████░░ 80%  CS-002 ✅
Phase 2  内在思维引擎 MVP        ██████████░░░░░░ 60%  CS-004 ✅
Phase 3  世界模拟器与假设空间    ██████████████░░ 80%  CS-005 ✅
Phase 4  元编程引擎与技能自举    ████████████████ 75%  MetaProgrammingEngine ✅ CS-009 ✅
Phase 5  架构重构与历史 Replay   ░░░░░░░░░░░░░░░░  0%
Phase 6  长期运行与产品化治理    ░░░░░░░░░░░░░░░░  0%
```

---

## 二、已完成任务包

### CS-002 — ThoughtFrame 对象模型 + ThoughtSubstrate 写入链路

| 模块 | 文件 | 行数 | 职责 |
|------|------|------|------|
| ThoughtFrame 模型 | `agent/thought_substrate_models.py` | 70 | 12 字段冻结 dataclass，to/from JSON |
| ThoughtSubstrate 存储 | `agent/thought_substrate_store.py` | 254 | 生命周期管理器 + JSONL 持久化 |
| 测试 | `tests/agent/test_thought_substrate.py` | 310 | 32 个测试（模型 12 + 存储 20） |

**波及文件：** `meta_cognition_models.py`（+3 字段）、`meta_cognition_reflector.py`（open/close frame）、`config/schema.py`（3 配置项）、`agent_loop_components.py`（构造注入）

**关键交付：**
- `ThoughtFrame`：frame_id / session_key / trigger_refs / observation_summary / active_goal / candidate_hypotheses / intended_strategy / verification_needs / simulation_requests / confidence / uncertainty_flags / created_at
- `ThoughtJournalEntry` 增加 frame_id / event_refs / retention_hint（向后兼容）
- 线程安全的 open_frame / close_frame / recent / enforce_retention
- 双写兼容：既写 `thought_substrate/journals.jsonl`，也镜像到现有 audit 路径

**验收：** 12 个模型测试通过，向后兼容旧 JSONL。

---

### CS-003 — 多源 Runtime Event → Thought Trigger 标准化采集

| 模块 | 文件 | 行数 | 职责 |
|------|------|------|------|
| RuntimeEvent 模型 | `agent/perception_event_models.py` | 95 | 统一事件信封 |
| PerceptionEventFusion | `agent/perception_event_fusion.py` | 195 | 5 源适配器 + 分发 |
| 测试 | `tests/agent/test_perception_event_fusion.py` | 310 | 41 个测试 |

**波及文件：** `meta_cognition_models.py`（扩展 MetaTriggerType）、`meta_cognition_triggers.py`（+bridge_runtime_event_to_trigger）、`config/schema.py`（+perception_fusion_enabled）、`agent_loop_components.py`（构造注入）、`loop.py`（3 处触发采集点 fusion 优先）

**5 个源适配器：**

| 适配器 | 输入 | 输出 event_type |
|--------|------|-----------------|
| bridge_cognitive_event() | CognitiveEvent | cognitive_nudge |
| bridge_tool_result() | 工具名+状态+参数 | tool_failure / tool_result |
| bridge_world_change() | 变更摘要+diff | world_change |
| bridge_user_message() | 用户文本（含纠错检测） | user_message |
| bridge_device_event() | 设备 ID+事件类型 | device_event（白名单校验） |

**数据流：**
```
源 → PerceptionEventFusion.process() → RuntimeEvent → bridge_runtime_event_to_trigger() → MetaTrigger → MetaCognitionRuntime
```

**验收：** 41 个测试通过，5 源适配器全部覆盖正常/边界/null 分支。

---

### CS-004 — InnerMonologueEngine 输出契约

| 模块 | 文件 | 行数 | 职责 |
|------|------|------|------|
| MonologueFrame 模型 | `agent/inner_monologue_models.py` | 70 | 12 字段结构化输出契约 |
| InnerMonologueEngine | `agent/inner_monologue_engine.py` | 160 | 桥接函数 + 置信度推导 + 引擎服务 |
| 测试 | `tests/agent/test_inner_monologue.py` | 330 | 27 个测试 |

**波及文件：** `config/schema.py`（+inner_monologue_enabled）、`bdi/deliberation.py`（+on_cycle_complete 回调）、`loop.py`（构造注入）

**BDI → MonologueFrame 字段映射：**

| MonologueFrame | 来源 |
|---------------|------|
| cycle_id | DeliberationResult.cycle_id |
| observation_summary | 参数或 reasoning 回退 |
| active_goal | 参数或第一个 Desire.content |
| candidate_hypotheses | [intent.reasoning for intent in intentions] |
| intended_strategy | result.reasoning |
| confidence | derive_confidence_and_uncertainty() |
| uncertainty_flags | derive_confidence_and_uncertainty() |

**确定性置信度推导（无额外 LLM 调用）：**
| 条件 | 置信度 | 不确定标志 |
|------|--------|-----------|
| 基准 | 0.5 | — |
| 有意图 (>=1) | +0.3 | — |
| 无意图 + 有愿望 | -0.2 | no_action_planned |
| 推理 < 20 字符 | — | empty_reasoning |
| 愿望 >= 3 | — | conflicting_desires |
| 含 "not sure/maybe" | — | reasoning_uncertain |

**验收：** 27 个测试通过，7 条确定性规则全部覆盖。

---

### CS-005 — 世界模拟器契约完善

| 模块 | 文件 | 行数 | 职责 |
|------|------|------|------|
| 模型模块 | `agent/world_simulator_models.py` | 295 | 7 个冻结 dataclass，全部对齐 §9 |
| 测试 | `tests/agent/test_world_simulator_models.py` | 190 | 25 个测试 |

**波及文件：** `world_simulator.py`（模型迁移+冻结 CausalEdge+replace 模式）

**§9 契约对齐明细：**

| 对象 | 变更 | §9 |
|------|------|----|
| HypothesisRecord 🆕 | hypothesis_id, statement, basis_refs, confidence, status(active/confirmed/refuted/expired), competing_hypotheses, expires_at | §9.3 |
| EntityState | +scope, +owner_id, +stale_at | §9.5 |
| CausalEdge ⚡ frozen | +source_kind, +contradiction_refs, +last_validated_at | §9.4 |
| SimulationRequest | +purpose, +active_hypotheses, +budget_tier, +requested_by_frame_id | §9.6 |
| SimulationTrace | +risk_flags, +confidence, +observed_mismatch_refs | §9.7 |

**验收：** 25 个测试通过，CausalEdge 变为不可变，全部向后兼容。

---

### CS-007 — MetaCognitionRegulator 调节器

| 模块 | 文件 | 行数 | 职责 |
|------|------|------|------|
| Regulator 服务 | `agent/meta_cognition_regulator.py` | 204 | 信号监控 + 7 种推荐规则 |
| 测试 | `tests/agent/test_meta_cognition_regulator.py` | 130 | 17 个测试 |

**波及文件：** `config/schema.py`（+regulator_enabled）、`agent_loop_components.py`（构造注入）

**7 种调节建议：**

| 动作 | 触发条件 | 优先级 |
|------|---------|--------|
| cool_down | 抑制率 ≥ 60% | medium |
| cool_down | 连续 ≥ 3 次反思失败 | **high** |
| cool_down | Token 负载 ≥ 80% | medium |
| switch_to_slow | 接受率 < 30% | **high** |
| verify_first | 纠错率 ≥ 30% | **high** |
| deepen_reflection | 失败率 ≥ 25% | medium |
| generate_evolution_seed | 模式密度 ≥ 10 | low |

**验收：** 17 个测试通过，所有规则基于确定性算法。

---

### CS-009 — SkillBootstrapper 技能自举

| 模块 | 文件 | 行数 | 职责 |
|------|------|------|------|
| 数据模型 | `agent/skill_bootstrapper_models.py` | 216 | ActionTraceDigest / RepeatedPattern / SkillCandidate |
| 引擎 | `agent/skill_bootstrapper.py` | 196 | 指纹生成 + 扫描 + 编译 + proposal bundle |
| 测试 | `tests/agent/test_skill_bootstrapper.py` | 195 | 26 个测试 |

**波及文件：** `config/schema.py`（+3 配置项）、`agent_loop_components.py`（构造注入）

**管线流程：**
```
ThoughtJournal → ActionTraceDigest → 指纹分组 → RepeatedPattern → 阈值过滤 →
SkillCandidateCompiler → SkillCandidate → compile_to_proposal_bundle →
CompiledProposalBundle (进入 governed evolution review 路径)
```

**确定性置信度推导：** 置信度 = min(0.95, 0.5 + repeats * 0.1)，重复数 >= 3 时可生成候选。

**危险工具检测：** exec/shell/write_file 等自动标记 `danger_review`。

**验收：** 26 个测试通过，完整端到端管线（digest→scan→compile→bundle）验证。

---

## 三、已实现系统全貌

```
External Signals
  ├─ User messages ──→ PerceptionEventFusion ──→ RuntimeEvent ──→ MetaTrigger ──→ MetaCognitionRuntime
  ├─ Tool results  ──→ PerceptionEventFusion ───      │                             │
  ├─ CognitiveEvent ─→ PerceptionEventFusion ───      │                    ┌────────┘
  └─ World changes ─→ PerceptionEventFusion ───      │                    ▼
                                                    │            MetaCognitionReflector
                                                    │                 │
                                                    │           ┌──────┴──────┐
                                                    │           ▼             ▼
                                                    │    ThoughtJournal   ReflectionRecord
                                                    │         │                │
                                                    │         ▼                ▼
                                                    │   ThoughtSubstrate   MetaProgrammingEngine
                                                    │    (open/close)     (skill/workflow candidates)
                                                    │         │
                                                    │         ▼
                                                    │   MonologueFrame
                                                    │         ▲
                                                    │         │
                                               BDI DeliberationEngine
                                               InnerMonologueEngine
                                                    │
                                                    ▼
                                            MetaCognitionRegulator
                                              (冷却/加深/验证/减速)
```

---

## 四、尚未完成的 Phase

### Phase 5 — 架构重构与历史 Replay（0%）

所需前置：CS-004 / CS-005 稳定、trial / rollback / report / health 都可用。

待交付：
- ArchitectureRefactorEngine
- 模块拓扑描述
- bottleneck analyzer
- replay evaluator
- ArchitectureProposal

### Phase 6 — 长期运行与产品化治理（0%）

待交付：
- 长期观测指标
- thought / simulation / patch candidate retention 治理
- watchdog 与 kill switch
- 角色化权限和人类审批位
- 运行成本与演化健康趋势面板

### 其他未完成任务包

| 编号 | 名称 | 状态 |
|------|------|------|
| CS-001 | 边界/术语/层级冻结 | 基本完成（含在总纲中） |
| CS-006 | 实体跟踪+因果边+假设空间 | 部分完成（模型已建，WorldSimulator 需扩展） |
| CS-008 | MetaProgrammingEngine patch candidate 扩展 | 部分完成（引擎已有，契约需完善） |
| CS-009 | SkillBootstrapper | ✅ 已完成 |
| CS-010 | ArchitectureProposal / replay / report | ❌ 未开始 |
| CS-011 | 不可变安全核心 | ❌ 未开始 |
| CS-012 | 观测性指标与长期治理 | ❌ 未开始 |

---

## 五、代码统计

| 维度 | 值 |
|------|-----|
| 新建文件 | 10 |
| 修改文件 | 14 |
| 新增代码行 | ~2200 |
| 测试用例 | 130 通过（0 代码失败） |
| ruff lint | 全部通过 |
| 配置项新增 | 9 个 |
| 适配器/桥接函数 | 7 个 |

### 文件清单

```
New:
  agent/thought_substrate_models.py        (CS-002) ThoughtFrame
  agent/thought_substrate_store.py         (CS-002) ThoughtSubstrate
  agent/perception_event_models.py         (CS-003) RuntimeEvent
  agent/perception_event_fusion.py         (CS-003) 5 source bridges
  agent/inner_monologue_models.py          (CS-004) MonologueFrame
  agent/inner_monologue_engine.py          (CS-004) BDI→Mono bridge
  agent/world_simulator_models.py          (CS-005) 7 model classes
  agent/meta_cognition_regulator.py        (CS-007) Regulator
  agent/skill_bootstrapper_models.py       (CS-009) ActionTraceDigest/RepeatedPattern/SkillCandidate
  agent/skill_bootstrapper.py              (CS-009) Scanner + Compiler + Bridge

Modified:
  config/schema.py                         (+9 config fields)
  agent/meta_cognition_models.py           (+trigger types +3 journal fields)
  agent/meta_cognition_triggers.py         (+bridge_runtime_event_to_trigger)
  agent/meta_cognition_reflector.py        (+ThoughtFrame open/close)
  agent/agent_loop_components.py           (+4 service w irings)
  agent/loop.py                            (+3 fusion calls +IME wiring)
  agent/world_simulator.py                 (model extraction + frozen)
  bdi/deliberation.py                      (+on_cycle_complete callback)
```

---

## 六、推荐下一步

详细路线图见 [`cognisphere_remaining_roadmap.md`](./cognisphere_remaining_roadmap.md)

按优先级排序：

1. **CS-011 不可变安全核心** — 独立安全底线，无任何前置依赖，最快可交付
2. **CS-010 ArchitectureProposal** — Phase 5 前置，需要 CS-005 稳定
3. **CS-006 实体跟踪+假设空间运行时** — 模型已就绪，需补全 WorldSimulator 逻辑
