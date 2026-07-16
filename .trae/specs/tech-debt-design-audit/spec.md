# 设计级技术债与遗留问题审查 Spec

## Why

本项目迭代至今积累了大量技术债与历史遗留问题,前编码人员能力不足导致工程化程度降低,许多功能模块未真正体现到用户体验中。本 spec 对项目进行全面"设计级"审查(非语法/运行时错误),重点找出:设计缺陷、可用性/用户体验缺陷、反模式与技术债务(不含上帝对象/神类)。

审查范围覆盖 5 个维度:Agent 核心运行时架构、BDI/记忆/进化/元认知子系统、工具/技能/子代理/MCP/Domain Pack 系统、通道/Web/API/CLI/配置/i18n 用户接触面、未实现功能标记与反模式扫描。

## What Changes

本 spec 为**审查报告**,列出问题与修复方案。具体修复由后续 spec 分批处理。问题按严重度与依赖关系分类:

- **P0(高优先级)**:影响正确性、安全或核心功能未连接的问题
- **P1(中优先级)**:影响可维护性或用户体验的问题
- **P2(低优先级)**:设计债务,可逐步清理

## Impact

- Affected specs: `fix-bdi-core-defects`, `fix-cognition-architecture-defects`, `add-actr-utility-learning`, `add-epic-motor-buffering`, `add-soar-online-chunking`, `add-tiered-conversation-memory`
- Affected code: 见下文每条问题的具体文件路径

---

## 一、设计缺陷

### 1.1 EPIC motor 子系统在生产中完全未接入(过度设计 + 死代码) [P0]

**问题描述**:
EPIC motor 缓冲子系统(含 spec、checklist、实现、测试)在生产运行时从未被启用。
- [cognitive_loop.py:35,44,52-61,81-96](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_loop.py) — `CognitiveLoopConfig` 持有 `motor_tick_interval_ms=50`,但 `_run_motor_loop`(L81-96)永不执行
- [agent_loop_components.py:662-671](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_loop_components.py) — 构造 `CognitiveLoop` 时**不传 `motor_processor`**(默认 None),`run_forever` 总走 L52-55 的单循环分支
- [action_runtime.py:220,313-334,722-751](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/action_runtime.py) — `_enqueue_motor_command`(L722-751)永不执行,`SafeActionExecutor._motor_queue` 永远 None;`ActionIntent.duration_ms`/`requires_parallel` 字段(L73-74)无生产消费者
- `EpicMotorProcessor` 与 `EpicActionQueue` 仅在 `tests/agent/test_epic_motor_processor.py`、`test_epic_action_queue.py`、`test_safe_executor_motor_queue.py` 中实例化
- `motor_queue=` 参数仅在测试中传入 `SafeActionExecutor`
- [cognitive_loop.py:11-13](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_loop.py) 与 [action_runtime.py:36-39](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/action_runtime.py) 的 TYPE_CHECKING 注释明确说明"运行时不导入以避免与 epic_motor 形成循环依赖"

**影响分析**:
整个 motor 子系统是"为未来设计但当前无需求"的投机性抽象(违反规则32)。它制造了三类代价:(1) cognitive_loop 与 action_runtime 为它承担循环依赖,被迫用延迟 import 掩盖;(2) CognitiveLoop 类承担两个时间尺度相差 300 倍的不相关职责(15s 审议循环 + 50ms 运动循环),但实际只执行审议职责;(3) 死代码增加维护成本与认知负担。`action_runtime.py:330` 注释"该记忆逻辑属于 motor processor 侧,本 spec 不实现,留作 Out of Scope"——但 motor processor 整个子系统根本未启用,"Out of Scope"实际等价于"永远不会实现"。

**改进建议**:
**删除整个 EPIC motor 子系统**(推荐),或接入生产。删除范围:cognitive_loop._run_motor_loop、action_runtime._enqueue_motor_command、ActionIntent.duration_ms/requires_parallel、motor_queue/motor_processor 参数、EpicMotorProcessor/EpicActionQueue 类及相关测试、cognitive_loop.py 与 action_runtime.py 的延迟 import 与 TYPE_CHECKING 注释、CognitiveLoopConfig.motor_tick_interval_ms。删除后 cognitive_loop 与 action_runtime 的循环依赖自然消失。

**修复计划**(代码级):
1. 删除 `OriginAgent/agent/epic_motor.py`(整个文件)
2. 在 `cognitive_loop.py` 删除 `_run_motor_loop`(L81-96)、`motor_processor` 参数(L33-43)、`motor_tick_interval_ms` 配置(L19-20)、TYPE_CHECKING 块(L11-13)
3. 在 `action_runtime.py` 删除 `_enqueue_motor_command`(L722-751)、`motor_queue` 参数(L220)、`ActionIntent.duration_ms`/`requires_parallel`(L73-74)、TYPE_CHECKING 块(L36-39)
4. 在 `agent_loop_components.py:662-671` 删除 motor_processor 相关构造逻辑
5. 删除测试文件 `tests/agent/test_epic_motor_processor.py`、`test_epic_action_queue.py`、`test_safe_executor_motor_queue.py`
6. 删除 spec 文档 `.trae/specs/add-epic-motor-buffering/`(若确认不再需要)
7. 运行全量测试验证无回归

---

### 1.2 turn 状态机死状态(HANDLE_ERROR/HANDLE_TIMEOUT 不可达) [P0]

**问题描述**:
[agent_turn_pipeline.py:34-139](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_turn_pipeline.py) 定义了 `HANDLE_ERROR`、`HANDLE_TIMEOUT` 状态(L45-46)与 `ERROR`/`TIMEOUT`/`FATAL_ERROR`/`FATAL`/`MAX_ITERATIONS` 事件(L106-110),`TURN_PIPELINE_TRANSITIONS` 也声明了对应转换(L126-138)。

但实际 state handler 从不返回这些事件:
- `state_run`(L588-626)没有 try/except 把异常转为 `TurnEvent.ERROR`/`FATAL_ERROR`/`TIMEOUT`——异常直接抛出
- [turn_orchestrator.py:78-89](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/turn_orchestrator.py) 捕获异常后 `raise` 重新抛出,状态机根本不进入 `HANDLE_ERROR`/`HANDLE_TIMEOUT`
- 所有 state handler 只返回 `OK`/`DISPATCH`/`SHORTCUT`/`SKIP`(L439,444,463-464,586,626,712,892,894,932,948,964,1028,1044)

**影响分析**:
状态机声称支持错误恢复,但实际任何异常都会终止整个 turn。`HANDLE_ERROR`、`HANDLE_TIMEOUT` 状态及 5 条相关转换全部不可达,是死代码。这给开发者错误的"安全感"——以为系统有错误恢复机制,实际没有。未来若有人依赖状态机的错误恢复能力,会在生产事故时发现机制不存在。

**改进建议**:
**推荐方案**:在 `state_run` 加 try/except 捕获异常并返回 `TurnEvent.ERROR`,让状态机真正进入错误恢复路径。**备选方案**:若错误恢复不在当前需求内,删除死状态与死转换,明确状态机不支持错误恢复。

**修复计划**(代码级,推荐方案):
1. 在 `agent_turn_pipeline.py` 的 `state_run`(L588-626)外层包裹 try/except
2. 捕获 `TimeoutError` → 返回 `TurnEvent.TIMEOUT`
3. 捕获其他异常 → 返回 `TurnEvent.ERROR`,异常信息存入 state context
4. 实现 `state_handle_error`/`state_handle_timeout` handler,记录审计日志后返回 `TurnEvent.OK`(终止 turn)
5. 增加测试:模拟 state_run 抛异常,验证状态机进入 HANDLE_ERROR 而非崩溃

---

### 1.3 TurnOrchestratorDeps 的 getattr 黑魔法(meta_cognition_runtime 永远 None) [P0]

**问题描述**:
[turn_orchestrator.py:15-21,65-67,123-126](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/turn_orchestrator.py) — `TurnOrchestratorDeps` dataclass 只声明 5 个字段,但 L65 用 `getattr(self._deps, "meta_cognition_runtime", None)` 访问一个**不存在的字段**。[loop.py:377-383](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py) 构造 `TurnOrchestratorDeps` 时也未传入 `meta_cognition_runtime`。

因此 `meta_runtime` 永远为 `None`,`start_turn`(L67)与 `end_turn`(L125)调用全部跳过——**turn-scoped dedup isolation 从未生效**。

**影响分析**:
这是"模块定义了接口但无生产者"的典型案例,且用 `getattr` 掩盖了字段缺失,违反规则14(关键假设必须落地为断言)。开发者以为元认知的 turn-scoped 隔离已启用,实际从未生效。若未来元认知出现跨 turn 状态污染,根因排查会非常困难。

**改进建议**:
在 `TurnOrchestratorDeps` 声明 `meta_cognition_runtime` 字段并在 `loop.py` 构造时传入,或删掉 `start_turn`/`end_turn` 调用。推荐前者以激活隔离机制。

**修复计划**(代码级):
1. 在 `turn_orchestrator.py:15-21` 的 `TurnOrchestratorDeps` dataclass 增加 `meta_cognition_runtime: Any = None` 字段
2. 将 L65 的 `getattr(self._deps, "meta_cognition_runtime", None)` 改为 `self._deps.meta_cognition_runtime`
3. 在 `loop.py:377-383` 构造 `TurnOrchestratorDeps` 时传入 `meta_cognition_runtime=meta_runtime`(需从 agent_loop_components 获取)
4. 增加测试:验证 `start_turn`/`end_turn` 被调用且 meta_cognition_runtime 非 None

---

### 1.4 ContextAssemblerV2 影子实现(访问 ContextBuilder 15+ 私有属性) [P1]

**问题描述**:
[context_assembler.py:17-195](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context_assembler.py) 自称 "Thin continuity orchestration and audit layer",但其 `assemble` 方法(L34-195)访问了 `ContextBuilder` 的约 15 个私有属性/方法:`_build_user_content`、`build_runtime_context_block`、`timezone`、`prepare_prewarm_bundle`、`build_phase1_continuity_blocks`、`build_reference_context_blocks`、`build_internal_event_block`、`_last_media_block_audit`、`_last_retrieval_fusion`、`_last_governance_audit`、`_last_prewarm_audit`、`_context_config`、`world_state`、`_sessions`、`WORKING_MEMORY_CONTEXT_KIND`、`WORLD_STATE_CONTEXT_KIND`、`_last_context_assembly_audit`。

**影响分析**:
这不是 thin layer,而是 ContextBuilder 的影子实现。删除测试:删掉 `ContextAssemblerV2`,把 audit 逻辑合回 ContextBuilder,复杂度会降低而非升高——说明它是浅模块。私有属性被外部访问破坏了封装边界,ContextBuilder 的任何内部重构都会波及 ContextAssemblerV2。

**改进建议**:
将 ContextAssemblerV2 的 audit 职责合并回 ContextBuilder,删除 ContextAssemblerV2。或把 ContextBuilder 的内部状态显式暴露为公共接口。

**修复计划**(代码级):
1. 将 `context_assembler.py` 中的 audit 逻辑(`_last_*_audit` 字段收集)迁移到 `context.py` 的 `ContextBuilder`
2. 在 `ContextBuilder` 增加 `assemble_with_audit` 方法,整合原 `ContextAssemblerV2.assemble` 的逻辑
3. 更新所有 `ContextAssemblerV2` 调用方(如 agent_runtime.py)改为调用 `ContextBuilder.assemble_with_audit`
4. 删除 `context_assembler.py`
5. 运行测试验证 audit 行为不变

---

### 1.5 双重 prompt 预算机制冲突(ContextBudgetManager vs _snip_history) [P1]

**问题描述**:
两套独立的 prompt 预算裁剪机制顺序执行,算法不同且互相干扰:
- 第一次:[context_budget.py:31-106](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context_budget.py) 的 `ContextBudgetManager.apply`,由 [context.py:873](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py) 调用,在 `_build_initial_messages` 阶段执行,产出带 `audit` 字段的 `ContextBudgetResult`
- 第二次:[runner.py:1439-1507](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/runner.py) 的 `_snip_history`,在 `AgentRunner.run` 内部独立执行(L321),用完全不同的算法(从尾部保留 non-system 消息)

**影响分析**:
第二次裁剪可能撤销第一次的裁剪决策。而第一次写入 `state.last_context_assembly`([agent_runtime.py:1416,1439,1472](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_runtime.py))的 audit 反映的是**裁剪前**的状态,变成"过期但看起来正常"的值,违反规则5(缓存值必须声明生命周期)与规则7(状态变化审计)。开发者基于 audit 调试时会看到与实际发送给 LLM 的 messages 不一致的状态。

**改进建议**:
统一为 `ContextBudgetManager` 单一裁剪,移除 `_snip_history` 或将其作为 fallback。明确分层:ContextBudgetManager 负责精细裁剪 + audit,_snip_history 不再独立裁剪。

**修复计划**(代码级):
1. 评估 `_snip_history` 是否仍有价值(若 ContextBudgetManager 已覆盖则删除)
2. 若删除:移除 `runner.py:1439-1507` 的 `_snip_history` 方法及 L321 的调用
3. 若保留为 fallback:改为仅在 `ContextBudgetManager` 未执行时触发,且执行后更新 `state.last_context_assembly` audit
4. 确保 `state.last_context_assembly` 在最终裁剪后写入,反映真实发送状态
5. 增加测试:验证裁剪后 audit 与实际 messages 一致

---

### 1.6 BDI 三处 bridge 未连接(UtilityRewardBridge / BDIHeartbeatBridge / WorldStateWatcher) [P0]

**问题描述**:
三个 BDI bridge 模块都完成了定义侧,但装配侧遗漏实例化/事件发布:

**(a) UtilityRewardBridge 从未实例化**:
- [bdi/utility_reward_bridge.py:30-44](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/bdi/utility_reward_bridge.py) 定义 `UtilityRewardBridge` 类
- [bdi/deliberation.py:164-167,707](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/bdi/deliberation.py) — `use_actr_utility: bool = False`、`reward_bridge: Any = None` 均为默认空值,`_update_utilities`(L707)首行 `if not self._use_actr_utility or self._reward_bridge is None: return` 永远返回
- [agent/agent_host.py:481-544](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_host.py) — `_init_bdi_engine` 实例化 `DeliberationEngine` 时未传入 `use_actr_utility`、`reward_bridge`

**(b) BDIHeartbeatBridge 从未实例化**:
- [bdi/heartbeat_bridge.py](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/bdi/heartbeat_bridge.py) 定义 `BDIHeartbeatBridge` 类(93 行)
- [heartbeat/service.py:64,187-191](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/heartbeat/service.py) — `HeartbeatService.__init__` 接受 `bdi_bridge` 参数,L187-191 有 `if self._bdi_bridge: result = await self._bdi_bridge.tick()` 调用逻辑
- [cli/commands.py:947-955](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/cli/commands.py) — 创建 `HeartbeatService` 时未传入 `bdi_bridge`

**(c) WorldStateWatcher 事件源缺失**:
- [bdi/world_state_watcher.py:38,79-83](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/bdi/world_state_watcher.py) — `BELIEF_CHANGED_EVENT = "belief.changed"` 定义并订阅,但全仓库 grep 显示该事件**从未被任何模块发布**
- [bdi/deliberation.py:204-209](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/bdi/deliberation.py) — 创建 `WorldStateWatcher` 时 `event_bus=event_bus`,但 `event_bus` 参数默认为 `None`

**影响分析**:
(a) ACT-R 效用学习反馈回路完全断裂。`UtilityRewardBridge` 设计用于从 `ReflectionRecord` 提取奖励信号并更新 Desire utility(`U_new = U_old + α * (R - U_old)`),但从未实例化,整个效用学习回路"定义存在、执行缺失"。这与 `add-actr-utility-learning` spec 的目标直接冲突。

(b) BDI 与心跳服务的桥接断开。`HeartbeatService` 内的 `if self._bdi_bridge` 分支已就绪但永远走不到,心跳触发的 BDI 重评估从未发生。

(c) 事件驱动反应性失效。`WorldStateWatcher` 设计为监听 belief 变化并触发 BDI 重规划,但事件源完全缺失——没有任何代码发布 `belief.changed` 事件,且 `event_bus` 也未注入。组件处于"订阅了但没人发布"的死状态。

三处共同模式:先写模块、后接装配,但缺少"装配完整性"的集成测试来捕获未连接的 bridge。

**改进建议**:
在装配侧补全实例化与事件发布。若某 bridge 确实不需要,则删除该 bridge 模块及其调用方条件分支,避免"代码就绪但未连接"的误导。

**修复计划**(代码级):
1. (a) 在 `agent_host.py:481-544` 的 `_init_bdi_engine` 中,实例化 `UtilityRewardBridge(audit_ledger, desire_store)` 并传入 `DeliberationEngine(use_actr_utility=True, reward_bridge=bridge)`
2. (a) 增加集成测试:验证 Desire utility 在反思后被更新
3. (b) 在 `cli/commands.py:947-955` 创建 `HeartbeatService` 时,实例化 `BDIHeartbeatBridge` 并传入
4. (b) 增加测试:验证心跳触发 BDI 重评估
5. (c) 在 `world_state.py` 的 belief 更新路径中,发布 `BELIEF_CHANGED_EVENT` 到 event_bus
6. (c) 在 `deliberation.py:204-209` 创建 `WorldStateWatcher` 时注入真实 `event_bus`
7. (c) 增加测试:验证 belief 变化触发 BDI 重规划
8. 对三个 bridge 增加"装配完整性"集成测试,确保未来不再出现未连接情况

---

### 1.7 EvolutionModuleManager 整个类从未被实例化 [P0]

**问题描述**:
[evolution/manager.py:71](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/evolution/manager.py) 定义 `EvolutionModuleManager` 类(586 行)。全仓库 grep 显示该类**从未在 `evolution/` 目录外被实例化**,仅出现在自身定义与 `evolution/__init__.py` 导出(L72)。

所有子模块(`EvolutionStateBranchStore`、`EvolutionCapabilityGate`、`EvolutionTelemetryRecorder`、`EvolutionRecoveryManager`)都只在该类的方法内被实例化(L293-440)。由于外层类从未被实例化,这些子模块全部未被主流程调用。

同时,[agent/evolution.py](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/evolution.py)(1258 行)定义了另一套独立的演化系统 `OpportunitySignalStore`,已接入 `curator.py:41` 和 `meta_cognition_reflector.py`。`evolution/` 目录与 `agent/evolution_*.py` 职责重复但互不调用。

唯一例外:`evolution/memory_vault.py` 通过 `cli/commands.py:1617-1683` 被调用(export/inspect/verify/import 命令)——这是 `evolution/` 目录中唯一被外部调用的模块。

**影响分析**:
`evolution/` 目录的 `EvolutionModuleManager` 及其管理的全部子模块(capability_gate/code_scanner/verifier/ledger/state_branch/telemetry)整体未接入主流程,是"过度设计但从未触发"的进化机制。两套独立演化系统并存(`evolution/` vs `agent/evolution_*.py`),仅 `agent/` 版本生效,造成认知负担与维护成本。

**改进建议**:
评估 `EvolutionModuleManager` 是否有未来需求。若无,删除整个 `evolution/` 目录(保留 `memory_vault.py`,迁移到 `agent/` 或独立模块);若有,接入主流程并消除与 `agent/evolution_*.py` 的职责重复。

**修复计划**(代码级,推荐删除):
1. 确认 `EvolutionModuleManager` 无未来需求(需用户/团队确认)
2. 将 `evolution/memory_vault.py` 迁移到 `agent/memory_vault.py` 或 `security/memory_vault.py`
3. 更新 `cli/commands.py:1617-1683` 的 import 路径
4. 删除 `evolution/` 目录(除 memory_vault.py 外)
5. 清理 `evolution/__init__.py` 的导出
6. 删除相关测试
7. 运行全量测试验证

---

### 1.8 InnerMonologueEngine 的 Phase 1 placeholder 字段(产出无消费者) [P1]

**问题描述**:
[inner_monologue_engine.py:86-90,123-127,228](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/inner_monologue_engine.py) — `MonologueFrame` 的 3 个核心字段始终为空:
- `verification_needs: list = []`(L123,Phase 1 placeholder)
- `simulation_requests: list = []`(L124,Phase 1 placeholder)
- `self_critique: str = ""`(L127,Phase 1 placeholder)
- L228 `_ = trigger_refs  # unused in Phase 1` — `trigger_refs` 参数被显式忽略

**影响分析**:
这些字段通过 `substrate.open_frame(...)` 传递到 `ThoughtSubstrate`,但因为没有真实数据,下游任何依赖这些字段的逻辑(如 WorldSimulator 的 simulation 请求、自我批判回路)实际上都没有可消费的数据。这是"产出未被消费"——字段被定义并传递,但永远是空值,下游消费者无数据可处理。

**改进建议**:
评估是否有真实需求实现这些字段的数据采集。若无,删除 placeholder 字段与 `trigger_refs` 参数,明确 MonologueFrame 的当前能力边界。若有,实现真实数据采集。

**修复计划**(代码级,推荐删除):
1. 确认 `verification_needs`/`simulation_requests`/`self_critique` 是否有真实消费者(grep 下游使用)
2. 若无消费者:从 `MonologueFrame` 删除这 3 个字段,删除 `trigger_refs` 参数
3. 更新 `inner_monologue_engine.py` 的 docstring(L86-90)移除 placeholder 说明
4. 若有消费者:实现真实数据采集逻辑
5. 运行测试验证

---

### 1.9 _extract_active_goal 始终返回空字符串 [P1]

**问题描述**:
[meta_cognition_reflector.py:82-86](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/meta_cognition_reflector.py):
```python
def _extract_active_goal(session_key: str) -> str:
    """Extract an active goal string from the session metadata for the frame."""
    # Minimal Phase 1:  return empty — enriched by InnerMonologueEngine in Phase 2.
    _ = session_key  # placeholder for future session.metadata lookup
    return ""
```

函数始终返回空字符串,`session_key` 参数被显式标记为占位。

**影响分析**:
下游 `MetaReflectionArtifacts.active_goal` 永远是空值。元认知反思时无法获知当前 active goal,反思质量受限。注释说"enriched by InnerMonologueEngine in Phase 2"——但 Phase 2 没有时间表(违反规则25)。

**改进建议**:
从 `working_memory` 的 `current_goal` 读取 active_goal(working_memory 已有 current_goal 字段),或删除该字段。

**修复计划**(代码级):
1. 修改 `_extract_active_goal` 从 `WorkingMemoryManager` 读取 `current_goal`
2. 若 `current_goal` 为空或已过期(超过30分钟),返回空字符串
3. 更新 docstring 移除 Phase 2 说明
4. 增加测试:验证 active_goal 从 working_memory 读取

---

### 1.10 上下文管理分散在 4 个文件(seam 泄漏) [P2]

**问题描述**:
上下文管理职责分散在 4 个文件:
- [context.py](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context.py) — `ContextBuilder`(1000+ 行)
- [context_assembler.py](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context_assembler.py) — `ContextAssemblerV2`(见 1.4)
- [context_budget.py](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/context_budget.py) — `ContextBudgetManager`
- [agent_runtime_context.py](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_runtime_context.py) — `runtime_chat_id`/`snapshot_for_trigger`/`set_tool_context`

**影响分析**:
seam 泄漏,职责不清。ContextAssemblerV2 访问 ContextBuilder 的 15+ 私有属性(见 1.4),ContextBudgetManager 与 ContextBuilder 的裁剪边界模糊(见 1.5)。开发者难以判断"修改上下文构建逻辑应该改哪个文件"。

**改进建议**:
合并 ContextAssemblerV2 回 ContextBuilder(见 1.4),明确 ContextBudgetManager 与 ContextBuilder 的边界(ContextBudgetManager 仅负责 token 裁剪,ContextBuilder 负责内容组装),agent_runtime_context 保持为纯工具函数。

**修复计划**(代码级):
1. 完成 1.4 的 ContextAssemblerV2 合并
2. 在 ContextBuilder 与 ContextBudgetManager 的 docstring 中明确职责边界
3. 评估 agent_runtime_context 是否可合并到 context.py 或保持独立(若函数少且内聚则保持)

---

## 二、可用性 / 用户体验缺陷

### 2.1 cli/models.py 模型数据库整体 stub,onboard 自动补全失效 [P0]

**问题描述**:
[cli/models.py:13-26](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/cli/models.py)(31 行)— 整个模型数据库被 stub,`get_all_models()` 返回 `[]`、`find_model_info()` 返回 `None`、`get_model_context_limit()` 返回 `None`、`get_model_suggestions()` 返回 `[]`。注释称 "temporarily disabled while litellm is being replaced"。

**影响分析**:
onboard 向导的模型自动补全/上下文窗口自动填充完全失效,用户只能盲填模型名。新手用户不知道该填什么模型,也不知道上下文窗口该设多少,严重影响首次体验。这是"功能模块未真正体现到用户体验中"的典型案例。

**改进建议**:
至少提供静态常用模型列表兜底(如 OpenAI/Anthropic/开源模型的常见型号),或在 UI 明确提示"模型库暂不可用,请手动输入"。

**修复计划**(代码级):
1. 在 `cli/models.py` 增加静态常用模型列表(覆盖 OpenAI/Anthropic/OpenRouter 常见型号)
2. `get_all_models()` 返回静态列表
3. `find_model_info()`/`get_model_context_limit()` 从静态列表查找
4. `get_model_suggestions()` 基于输入前缀过滤静态列表
5. 在 onboard 向导中,若模型库为静态兜底,提示"模型库为静态版本,部分型号可能缺失"
6. 增加测试:验证静态列表非空且查找正常

---

### 2.2 启动时 allow_from 为空直接 SystemExit 退出,无修复指引 [P0]

**问题描述**:
[channels/manager.py:159-179](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/channels/manager.py) — `_validate_allow_from()` 在 `allow_from` 为空且未启用 pairing 时 `raise SystemExit`——启动直接退出,无修复指引。

**影响分析**:
用户只看到进程退出,不知道如何修复。新手无法定位问题,可能直接放弃使用。违反"错误提示应明确告知用户如何修复"的可用性原则。

**改进建议**:
改为打印明确的修复步骤(如何配置 allow_from 或启用 pairing)后退出,或降级为 warning + 默认拒绝策略。

**修复计划**(代码级):
1. 在 `_validate_allow_from()` 的 `raise SystemExit` 前,打印明确的修复指引:
   - 说明 allow_from 为空的风险
   - 给出配置 allow_from 的示例(如 `allow_from = ["127.0.0.1"]`)
   - 给出启用 pairing 的替代方案
2. 若担心用户忽略,可改为 warning + 默认拒绝所有入站连接(更安全且不阻塞启动)
3. 增加测试:验证 allow_from 为空时输出修复指引

---

### 2.3 msteams validate_inbound_auth=False 仅 warning 不阻断 [P0]

**问题描述**:
[channels/msteams.py:143-148](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/channels/msteams.py) — `validate_inbound_auth=False` 时仅 warning,**无强制阻断**。鉴权关闭后仍处理请求,安全边界形同虚设。

**影响分析**:
违反规则18(安全边界最低要求)。生产环境若误配 `validate_inbound_auth=False`,任何人都可以伪造 Teams 消息注入 Agent,可能导致敏感操作被执行。

**改进建议**:
生产 profile 下强制要求鉴权开启。开发 profile 下允许关闭但需显式声明。

**修复计划**(代码级):
1. 在 `msteams.py` 的配置加载时,检查 runtime profile
2. 若为生产 profile 且 `validate_inbound_auth=False`,拒绝启动并提示"生产环境必须开启鉴权"
3. 若为开发 profile,允许关闭但打印显眼 warning
4. 增加测试:验证生产 profile 下关闭鉴权时拒绝启动

---

### 2.4 onboard 非 wizard 模式直接覆盖配置,不可逆无警示 [P1]

**问题描述**:
[cli/commands.py](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/cli/commands.py) 的 onboard 命令——默认非 wizard 模式直接覆盖/刷新配置,只在 wizard 模式才引导。违反规则23(破坏性或不可逆操作交由用户执行)。

**影响分析**:
用户误用 `originagent onboard` 可能覆盖已有配置(如 API key、provider 配置),且不可逆无警示。新手用户可能丢失精心配置的环境。

**改进建议**:
检测到已有非默认配置时强制确认或自动进入 wizard 模式。

**修复计划**(代码级):
1. 在 onboard 命令开始时,检测现有配置是否为非默认值(检查关键字段如 provider/api_key/model 是否已设置)
2. 若检测到已有配置,提示"检测到已有配置,是否覆盖?[y/N]"默认为 N
3. 或自动进入 wizard 模式,引导用户逐项确认
4. 增加测试:验证已有配置时提示确认

---

### 2.5 i18n 形同虚设(仅 1 处使用) [P1]

**问题描述**:
[i18n/en.json](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/i18n/en.json)(79 行)与 [i18n/zh.json](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/i18n/zh.json) 仅覆盖 `command.*`(斜杠命令)和 `response.*`(少量响应文本)和 `help_header`。Grep 确认 `from OriginAgent.i18n` **整个代码库只有 1 处真正使用**([command/builtin.py:17](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/command/builtin.py))。

**影响分析**:
所有通道错误提示、CLI 输出、API 错误消息、onboard 文案、spinner 文案均未走 i18n。中文用户收到英文问候(msteams.py:74 "Hi — what can I help with?"),英文用户收到中文指令(api/server.py:284 "请分析上传的文件")。这与 project_memory 中"Agent 输出语言由用户设置决定"的约束直接冲突——i18n 框架存在但未使用,语言控制失效。

**改进建议**:
在通道基类、CLI 输出层强制要求走 `t()`,并补齐 en.json/zh.json 的 `channel.*`、`cli.*`、`api.*`、`error.*` 命名空间。建立"新增用户可见字符串必须走 i18n"的编码规范。

**修复计划**(代码级):
1. 在 `i18n/en.json` 与 `zh.json` 增加 `channel.*`、`cli.*`、`api.*`、`error.*` 命名空间,覆盖关键用户可见字符串
2. 优先修复中英文错位的字符串:
   - `channels/msteams.py:74` "Hi — what can I help with?" → `t("channel.msteams.mention_only_response")`
   - `api/server.py:284` "请分析上传的文件" → `t("api.analyze_uploaded_file")`
   - `cli/stream.py:37` "is thinking..." → `t("cli.thinking")`
3. 在 `BaseChannel` 增加 `_t(key, **kwargs)` 辅助方法,降低 i18n 使用门槛
4. 在编码规范中增加"新增用户可见字符串必须走 i18n"的检查项
5. 增加测试:验证关键字符串走 i18n

---

### 2.6 settings_update 不重新解析 ${VAR} 环境变量 [P1]

**问题描述**:
[config/loader.py:98](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/config/loader.py) 的 `${VAR}` 环境变量解析**仅在 load_config 时触发**。运行时通过 `rest_api.py` settings_update 修改的值直接 `setattr` 后 `save_config`,**不会重新解析环境变量**。

**影响分析**:
用户在 UI 中填入 `${MY_KEY}` 不会被解析为环境变量值,需重启才生效,且生效路径不透明。用户会以为配置已生效但实际未生效,导致排查困难。违反"配置变更的生效路径应清晰"原则。

**改进建议**:
settings_update 后对值重新走 `_resolve_env_refs`,并在 UI 提示"哪些项需重启生效"。

**修复计划**(代码级):
1. 在 `rest_api.py` 的 settings_update 端点,`setattr` 后调用 `_resolve_env_refs` 重新解析值
2. 对无法运行时生效的项(如需要重启的 provider 配置),在响应中返回 `requires_restart: true` 标记
3. 前端 UI 根据 `requires_restart` 标记提示用户
4. 增加测试:验证 settings_update 后 `${VAR}` 被解析

---

### 2.7 硬编码用户可见字符串(中英文错位) [P1]

**问题描述**:
多处硬编码用户可见字符串,中英文错位:
- [channels/msteams.py:74](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/channels/msteams.py) — `mention_only_response: str = "Hi — what can I help with?"` 硬编码英文
- [channels/routes/cognition.py:146](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/channels/routes/cognition.py) — `reason = _query_first(query, "reason") or "WebUI action"` 硬编码英文
- [api/server.py:284](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/api/server.py) — `text = "请分析上传的文件"` 硬编码中文
- [cli/stream.py:35,37](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/cli/stream.py) — `bot_name: str = "OriginAgent"`、`f"[dim]{bot_name} is thinking...[/dim]"` 硬编码英文
- [cli/commands.py:414-431](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/cli/commands.py) — onboard 命令末尾硬编码英文提示 + 硬编码 OpenRouter 链接

**影响分析**:
中文用户收到英文问候/提示,英文用户收到中文指令。违反 project_memory 中"Agent 输出语言由用户设置决定"的约束。用户体验不一致,显得不专业。

**改进建议**:
所有用户可见字符串走 i18n(见 2.5)。

**修复计划**(代码级):
1. 与 2.5 的 i18n 修复一并处理
2. 逐个字符串替换为 `t()` 调用
3. 增加测试:验证关键字符串走 i18n

---

### 2.8 .originagent vs .OriginAgent 命名大小写不一致 [P1]

**问题描述**:
[config/paths.py:17](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/config/paths.py) — `APP_DATA_DIR_NAME = ".originagent"`(小写),但 L85 `get_legacy_sessions_dir` 返回 `Path.home() / ".OriginAgent"`(大写 O)。

**影响分析**:
Windows 大小写不敏感可工作,但 Linux/macOS 上迁移路径找不到旧目录,导致用户升级后丢失历史会话。这是一个隐蔽的跨平台数据丢失风险。

**改进建议**:
统一为小写,在 `get_legacy_sessions_dir` 处加迁移逻辑(检查大写目录是否存在,若存在则迁移到小写或创建符号链接)。

**修复计划**(代码级):
1. 统一 `APP_DATA_DIR_NAME = ".originagent"`(小写)
2. 在 `get_legacy_sessions_dir` 中,若小写目录不存在但大写 `.OriginAgent` 存在,返回大写路径(向后兼容)
3. 增加迁移函数:启动时检查大写目录,若存在则提示用户迁移到小写
4. 增加测试:验证 Linux 下能找到 legacy 目录

---

## 三、反模式与技术债务

### 3.1 Strangler Fig 迁移无截止日期(RuntimeDependencies 半完成) [P1]

**问题描述**:
[agent_runtime.py:36-230](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_runtime.py) — `RuntimeDependencies` 同时持有 5 个子容器(`CoreServices`/`MetaCognitionServices`/`MemoryServices`/`BackgroundServices`/`StoreServices`,L134-139)与 70+ 个平铺字段(L141-230)。每个子容器字段都标注 `TODO(migrate)`(L44-61,77),但实际调用方一律使用平铺字段(如 `self._deps.tools`、`self._deps.provider`,见 L253-254,266,296,386-388,478-493)。

注释 "Strangler Fig — incremental migration"、"During migration: New code accesses deps.core.tools instead of deps.tools; Old flat fields remain as compat aliases"(L36-38,126-131),但无任何 `@DeferDecision` 注解、无技术债看板条目、无迁移完成判定标准。

同时,`CoreServices`/`MetaCognitionServices`/`MemoryServices` 三个 dataclass 使用 `Any = None` + `TODO(migrate)` 标注(L44-77),类型契约尚未冻结。

**影响分析**:
违反规则25(禁止"暂时搁置,未来实现"式规避)。子容器是死代码,平铺字段是实际 seam——两套访问路径同时存在,使接口面比实现更复杂。类型契约未冻结,IDE 类型提示失效,重构风险高。

**改进建议**:
设截止时间完成迁移(将所有平铺字段访问改为子容器访问),或删掉子容器(承认平铺字段是最终设计)。

**修复计划**(代码级):
1. 评估迁移进度:统计平铺字段 vs 子容器字段的使用比例
2. 若决定完成迁移:逐个将 `self._deps.tools` 改为 `self._deps.core.tools`,最后删除平铺字段
3. 若决定放弃迁移:删除 5 个子容器 dataclass 与 `TODO(migrate)` 注释,保留平铺字段
4. 无论哪种,补充类型注解(用具体类型替代 `Any = None`)
5. 增加类型检查测试

---

### 3.2 JSONL Fallback 双写过渡已固化(_jsonl_fallback_enabled 散布 6+ 文件) [P1]

**问题描述**:
`_jsonl_fallback_enabled` 标志散布到 6+ 个文件,各处默认值不一致:
- [agent/audit.py:161](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/audit.py) — `self._jsonl_fallback_enabled = True`(默认开启)
- [bdi/plan_library.py:42,50,177,186-188](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/bdi/plan_library.py) — `_jsonl_fallback_enabled` 标志
- [bdi/desire_store.py:29,36,76,85,110,117](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/bdi/desire_store.py) — 同样的双写标志
- [agent/agent_loop_components.py:399,416,525,527](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_loop_components.py) — 显式关闭 fallback
- [agent/agent_host.py:395,498](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_host.py) — `jsonl_fallback_enabled=False`
- [bdi/deliberation.py:192](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/bdi/deliberation.py) — `jsonl_fallback_enabled=False`

**影响分析**:
JSONL fallback 本意是 SQLite 切换期的过渡方案,但已散布到 6+ 文件。`audit.py` 默认 `True`,其他位置显式 `False`,呈现"临时方案固化"的典型征兆。无统一收口时间表,违反规则25。

**改进建议**:
评估 SQLite 稳定性。若稳定,移除 JSONL fallback(统一为 SQLite);若仍需双写,明确双写策略与收口时间表。

**修复计划**(代码级):
1. 评估 SQLite 在生产环境的稳定性(检查是否有 WAL 模式访问问题,见 project_memory 中的 Windows Defender 问题)
2. 若稳定:移除所有 `_jsonl_fallback_enabled` 标志与 JSONL 写入逻辑,统一为 SQLite
3. 若仍需双写:在 config 中增加 `jsonl_fallback_enabled` 全局配置项,统一默认值(建议 False),各处从 config 读取
4. 增加"双写收口时间表"到技术债看板
5. 运行测试验证

---

### 3.3 角色名字符串硬编码 25+ 文件 60+ 处 [P1]

**问题描述**:
`"user"`/`"system"`/`"assistant"` 在 25+ 个文件、60+ 处硬编码,没有统一的常量定义。涉及文件包括:`heartbeat/service.py`、`bdi/deliberation.py`、`utils/evaluator.py`、`agent/memory_phases.py`、`agent/memory.py`、`agent/subagent.py`、`agent/background_review.py`、`agent/meta_cognition_reflector.py`、`agent/warm_summarizer.py`、`agent/agent_runtime.py`、`agent/loop.py`、`agent/context.py`、`agent/snapshot_inspection.py`、`agent/runner.py`、`agent/agent_turn_persist.py`、`utils/runtime.py`、`utils/webui_titles.py`、`utils/webui_transcript.py`、`utils/helpers.py`、`providers/bedrock_provider.py`、`providers/anthropic_provider.py`、`providers/image_generation.py`、`providers/openai_responses/converters.py`、`api/server.py`、`agent/context_assembler.py`。

**影响分析**:
违反规则6(单一数据源,禁止部分遵守)。任何角色名变更(如未来支持 `developer` 角色)需要修改 60+ 处,极易遗漏。硬编码字符串也无法在编译期捕获拼写错误(如 `"assitant"`)。

**改进建议**:
定义 `RoleConstants` 统一常量(`USER = "user"`、`SYSTEM = "system"`、`ASSISTANT = "assistant"`),所有位置从常量导入。

**修复计划**(代码级):
1. 在 `OriginAgent/utils/constants.py`(或新建)定义 `RoleConstants` 类
2. 逐个文件替换硬编码字符串为 `RoleConstants.USER`/`SYSTEM`/`ASSISTANT`
3. 优先处理 `providers/` 下的文件(与 LLM API 契约直接相关)
4. 增加 lint 规则:禁止在代码中直接使用 `"user"`/`"system"`/`"assistant"` 字符串字面量(可选)
5. 运行全量测试验证

---

### 3.4 超时魔法值散布 20+ 文件且不一致 [P2]

**问题描述**:
超时值散布在 20+ 文件,大量内联魔法值,同类操作(HTTP 下载)的超时值在不同文件中不一致(10.0/15.0/20.0/30.0 都有):
- `channels/telegram.py:321-329` — `connect_timeout=30.0, read_timeout=30.0`(多处)
- `channels/email.py:248` — `timeout = 30`
- `channels/msteams.py:151,180,193,231` — `timeout=30.0, timeout=15, timeout=15, timeout=2`
- `channels/mochat.py:309,524` — `timeout=30.0, timeout=10`
- `channels/matrix.py:637` — `timeout=30000`
- `agent/tools/web.py:534,551,572,594,612,631,916,1053,1095` — `timeout=10.0/15.0/20.0/30.0`(9 处)
- `agent/local_awareness.py:569,582,843,1034` — `timeout=8, timeout=5, timeout=4, timeout=120.0`
- `agent/agent_runtime.py:1036` — `timeout=300`
- `gateway/rest_api.py:1789` — `tool_timeout=30`
- `evolution/ledger_sqlite.py:69` — `PRAGMA busy_timeout=10000`
- `session/search_index.py:386,388` — `timeout=5.0, busy_timeout=5000`

**影响分析**:
违反规则17(禁止硬编码,保证可扩展性)。同类操作超时值不统一,无配置层,用户无法统一调整。部分超时值可能过短(如 `timeout=2`)导致正常请求超时失败。

**改进建议**:
在 `config/schema.py` 增加 `TimeoutConfig`,按操作类型(HTTP 下载、API 调用、DB 操作)分组配置,各处从 config 读取。

**修复计划**(代码级):
1. 在 `config/schema.py` 增加 `TimeoutConfig` dataclass,包含 `http_download_timeout`、`http_api_timeout`、`db_busy_timeout` 等字段
2. 在 `ChannelsConfig`/`ToolsConfig` 中注入 `TimeoutConfig`
3. 逐个文件替换内联魔法值为 config 读取
4. 优先处理 `channels/` 下的 HTTP 下载超时(统一为 `http_download_timeout`)
5. 运行测试验证

---

### 3.5 设备后端选项三处独立维护 [P2]

**问题描述**:
同一选项集合 `{"none", "fake", "lighting_client"}` 在 3 个文件独立维护:
- [channels/websocket.py:270](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/channels/websocket.py) — `_DEVICE_BACKEND_OPTIONS = {"none", "fake", "lighting_client"}`
- [gateway/rest_api.py:92](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/gateway/rest_api.py) — `_DEVICE_BACKEND_OPTIONS = {"none", "fake", "lighting_client"}`
- [config/schema.py:1823](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/config/schema.py) — `backend: Literal["none", "fake", "lighting_client"] = "none"`

**影响分析**:
违反规则6(单一数据源)。新增设备后端时需修改 3 处,极易遗漏导致配置不一致。

**改进建议**:
以 `config/schema.py` 为权威来源,`websocket.py` 和 `rest_api.py` 从 schema 导入。

**修复计划**(代码级):
1. 在 `config/schema.py` 定义 `DEVICE_BACKEND_OPTIONS = ("none", "fake", "lighting_client")` 常量
2. `websocket.py:270` 与 `rest_api.py:92` 从 `config.schema` 导入该常量
3. 删除两处的本地 `_DEVICE_BACKEND_OPTIONS` 定义
4. 运行测试验证

---

### 3.6 _TRANSCRIPTION_PROVIDERS 两处定义不一致(2 vs 3 项) [P1]

**问题描述**:
- [config/doctor.py:25](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/config/doctor.py) — `_TRANSCRIPTION_PROVIDERS = {"groq", "openai"}`(2 项)
- [gateway/rest_api.py:94](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/gateway/rest_api.py) — `_TRANSCRIPTION_PROVIDER_OPTIONS = {"groq", "openai", "volcengine"}`(3 项,多 volcengine)

**影响分析**:
用户在 UI 选 volcengine 后,doctor 报"unsupported provider"误报。配置校验与 UI 选项不一致,用户体验混乱。

**改进建议**:
合并为单一常量并置于 schema 或 constants 模块。

**修复计划**(代码级):
1. 在 `config/schema.py` 或新建 `constants.py` 定义 `TRANSCRIPTION_PROVIDERS = {"groq", "openai", "volcengine"}`
2. `config/doctor.py:25` 与 `gateway/rest_api.py:94` 从该常量导入
3. 删除两处的本地定义
4. 增加测试:验证 doctor 与 UI 选项一致

---

### 3.7 dingtalk/qq 本地文件解析逻辑复制粘贴 [P2]

**问题描述**:
- [channels/dingtalk.py:482-495](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/channels/dingtalk.py)
- [channels/qq.py:382-400](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/channels/qq.py)

两段代码结构几乎完全相同(`file://` URL 解析 + `os.path.expanduser` 回退 + `is_file` 检查 + `read_bytes`),仅日志消息和返回值略有差异。

**影响分析**:
复制粘贴式代码,违反规则33(手术式变更)。修复 bug 时需同步修改两处,易遗漏。

**改进建议**:
提取到 `channels/_text_utils.py` 或 `BaseChannel` 的共享方法。

**修复计划**(代码级):
1. 在 `channels/_text_utils.py` 增加 `parse_local_media_ref(media_ref: str) -> Path | None` 函数
2. 在 `BaseChannel` 增加 `read_local_media(media_ref: str) -> tuple[bytes, str] | None` 辅助方法
3. `dingtalk.py` 与 `qq.py` 调用共享方法
4. 检查其他通道是否有类似逻辑,一并迁移
5. 运行测试验证

---

### 3.8 SkillBootstrapper 有状态类从未被实例化(死代码) [P1]

**问题描述**:
[agent/skill_bootstrapper.py:200-321](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/skill_bootstrapper.py) — `SkillBootstrapper` 类(带滚动窗口和 `ingest_chunk` 在线技能形成方法,约 120 行)的 docstring(L201-206)声称实现了"在线技能形成(Soar chunking)"。

全代码库搜索 `SkillBootstrapper(` 构造调用:**零命中**(仅在自身 `__init__` 内部引用 scanner/compiler)。

实际接入的是其**无状态组件**:[agent_loop_components.py:608-611](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_loop_components.py) 只实例化了 `SkillBootstrapperScanner()` 和 `SkillCandidateCompiler()`,存入 `values["_skill_bootstrapper_scanner"]` / `values["_skill_bootstrapper_compiler"]`。

**影响分析**:
有状态在线引导器 `SkillBootstrapper` 类(L200-L321,约 120 行)从未被实例化,是死代码。其 `ingest_chunk`/`_evict_if_needed`/`_build_pattern` 等在线管道从未运行。这是"为未来设计的复杂抽象,但没有第二个使用场景"(违反规则32)。

**改进建议**:
删除 `SkillBootstrapper` 类,保留其无状态组件 `SkillBootstrapperScanner`/`SkillCandidateCompiler`。

**修复计划**(代码级):
1. 确认 `SkillBootstrapper` 类无生产消费者(已确认)
2. 删除 `skill_bootstrapper.py:200-321` 的 `SkillBootstrapper` 类
3. 保留 `SkillBootstrapperScanner`/`SkillCandidateCompiler` 无状态组件
4. 删除相关测试(若有)
5. 运行全量测试验证

---

### 3.9 ToolLoader.discover() 自动发现机制对内置工具从未激活(死代码) [P2]

**问题描述**:
[agent/tools/loader.py:37-69](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/tools/loader.py) — `discover()` 方法在 `include_builtin=False`(默认值,L26)时直接返回空列表 `[]`(L40-41)。全代码库搜索 `include_builtin` 仅命中 `loader.py` 自身 3 处(L26/L33/L40),**从未有任何调用方传入 `True`**。

`register_plugin_tools()`([agent_tool_setup.py:139-147](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_tool_setup.py))调用 `ToolLoader().load(...)`,使用默认 `include_builtin=False`,因此 `discover()` 恒返回 `[]`,实际只有 `_discover_plugins()`(entry_points)路径在跑。

同时,`_SKIP_MODULES`(L14-17)含有失效条目:
- `"runtime_state"` — 实际文件名是 `runtime_status.py`(`runtime_status` 不在 skip 集中)
- `"config"` — tools 目录下并无 `config.py` 文件

且 entry_points 插件机制(`originagent.tools` group,L71-93)无任何真实消费者——全仓库 toml 搜索 `originagent.tools` 零命中。

**影响分析**:
`discover()` 自动发现机制 + `_plugin_discoverable` 属性([base.py:185](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/tools/base.py))对内置工具从未被激活。所有内置工具靠手动注册,自动发现是冗余设计(违反规则32)。`_SKIP_MODULES` 含失效条目,表明该集合已长期无人维护。entry_points 插件机制是完全没有真实插件注册的投机性扩展点。

**改进建议**:
删除 `discover()` 自动发现机制(对内置工具无价值),保留 entry_points 插件机制(轻量,未来可能有插件)。修正 `_SKIP_MODULES` 失效条目。

**修复计划**(代码级):
1. 删除 `loader.py:37-69` 的 `discover()` 方法与 `include_builtin` 参数
2. 删除 `base.py:185` 的 `_plugin_discoverable` 属性(若仅 discover 使用)
3. 修正 `_SKIP_MODULES`:删除 `"runtime_state"` 与 `"config"`,若需要则添加 `"runtime_status"`
4. 保留 `_discover_plugins()` 与 entry_points 机制(未来扩展点)
5. 运行测试验证

---

### 3.10 CognitiveLoopConfig.enabled 死配置项 [P2]

**问题描述**:
[cognitive_loop.py:18,664](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_loop.py) — `agent_loop_components.py:664` 设置 `enabled=bool(cognition_enabled)`,但 `cognitive_loop.py` 内部**从不读取 `self.config.enabled`**。`run_forever`(L46)只要被调用就无条件运行两个子循环。

**影响分析**:
无消费者的配置项。用户以为可以通过 `enabled=False` 关闭认知循环,实际无效。违反规则32(最小化实现)。

**改进建议**:
删除 `enabled` 字段,或在 `run_forever` 开头检查 `if not self.config.enabled: return`。

**修复计划**(代码级):
1. 若需要 enabled 功能:在 `run_forever` 开头增加 `if not self.config.enabled: return`
2. 若不需要:删除 `CognitiveLoopConfig.enabled` 字段与 `agent_loop_components.py:664` 的设置
3. 运行测试验证

---

### 3.11 active_intent_processor fallback 参数无 deprecation 路径 [P2]

**问题描述**:
[cognitive_loop.py:33-43](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_loop.py):
```python
def __init__(self, *, ..., active_intent_processor=None, session_processor=None, ...):
    self._session_processor = session_processor or active_intent_processor
    if self._session_processor is None:
        raise ValueError("cognitive loop requires a session processor")
```

`active_intent_processor` 是 `session_processor` 的 fallback 别名,二者表达同一依赖。生产构造方 `agent_loop_components.py:670` 只传 `session_processor`,`active_intent_processor` 完全无生产消费者。

**影响分析**:
无 deprecation 路径的向后兼容妥协,可能掩盖未来的接线错误(两个参数都传时 `active_intent_processor` 被静默丢弃)。

**改进建议**:
删除 `active_intent_processor` 参数,只保留 `session_processor`。

**修复计划**(代码级):
1. 确认 `active_intent_processor` 无生产消费者(已确认)
2. 删除 `active_intent_processor` 参数
3. 更新 docstring
4. 运行测试验证

---

### 3.12 action_runtime.py 的 notify_only 分支是历史兼容死路径 [P2]

**问题描述**:
[action_runtime.py:390-424](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/action_runtime.py) — 注释明确写:"The production safety gate does not emit notify_only today; this path only preserves compatibility for explicitly injected notification decisions."

生产 safety gate(`DefaultSafetyGate`/`ActionSafetyGate`)只返回 `allow`/`deny`/`ask_confirmation`(见 [action_safety.py:53-58](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/action_safety.py)),从不返回 `notify_only`。

**影响分析**:
该分支只对"显式注入的 notification decisions"生效——但无生产代码注入此类 decision。历史兼容死路径。

**改进建议**:
删除 `notify_only` 分支。

**修复计划**(代码级):
1. 确认无生产代码注入 `notify_only` decision
2. 删除 `action_runtime.py:390-424` 的 `notify_only` 分支
3. 删除 `SafetyDecision` 中的 `notify_only` 值(若存在)
4. 运行测试验证

---

### 3.13 ActionSafetyGate 是反模式兼容层(浅模块) [P2]

**问题描述**:
[action_safety.py:61-78](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/action_safety.py):
```python
class ActionSafetyGate:
    def __init__(self, presence_store=None, fact_store=None, *, extra_gates=None):
        gates = [DefaultSafetyGate()]
        gates.extend(extra_gates or [])
        if presence_store is not None or fact_store is not None:
            raise ValueError("domain-specific safety gates must be passed through extra_gates")
        self._composite = CompositeSafetyGate(gates)
```

构造函数接受 `presence_store`/`fact_store` 参数后**立即抛 ValueError**。类本身只是 `CompositeSafetyGate([DefaultSafetyGate(), *extra_gates])` 的包装。

**影响分析**:
浅模块,误导性构造函数。删除测试:直接用 `CompositeSafetyGate` 不损失任何功能,反而去掉一个会抛异常的误导性构造函数。

**改进建议**:
删除 `ActionSafetyGate`,直接用 `CompositeSafetyGate`。

**修复计划**(代码级):
1. 搜索 `ActionSafetyGate` 的所有使用位置
2. 替换为 `CompositeSafetyGate([DefaultSafetyGate(), *extra_gates])`
3. 删除 `ActionSafetyGate` 类
4. 运行测试验证

---

### 3.14 markdown 共享工具仅 telegram 使用 [P2]

**问题描述**:
[channels/_text_utils.py](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/channels/_text_utils.py) 已抽取 `strip_markdown_inline` 和 `strip_markdown_block` 共享函数,注释明确说是为了"eliminate copy-paste across channel implementations"。但搜索结果显示**仅 `telegram.py` 使用了这些共享函数**(L29,56,93,710,727),其他通道(dingtalk、qq、weixin 等)可能仍保留各自的 markdown 处理实现或未做处理。

同时,`channels/_stream_buffer.py`(32 行的 StreamBuffer)与 `_text_utils.py` 仅 telegram.py 和 discord.py 共 2 个通道使用,其余 18 个通道未复用。

**影响分析**:
"提取了共享工具但未全面推广"的中间状态。维护成本高,各通道可能重复实现或缺失 markdown 处理。

**改进建议**:
将 `StreamBuffer` 与 `strip_markdown_*` 注入 `BaseChannel` 或作为 `ChannelManager` 的共享工具,强制新通道复用。

**修复计划**(代码级):
1. 在 `BaseChannel` 增加 `_strip_markdown` 辅助方法,内部调用 `_text_utils.strip_markdown_inline`
2. 逐个通道迁移 markdown 处理逻辑到 `BaseChannel._strip_markdown`
3. 将 `StreamBuffer` 作为 `BaseChannel` 的可选 mixin 或工具属性
4. 运行测试验证各通道输出不变

---

### 3.15 _migrate_config 无版本号机制 [P2]

**问题描述**:
[config/loader.py:165-186](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/config/loader.py) — `_migrate_config` 仅迁移 2 项(restrictToWorkspace、myEnabled),**无版本号机制**。违反规则2(契约显式版本化)。

**影响分析**:
未来配置 schema 变更时,无法判断用户配置版本,无法构建迁移链。每次新增迁移都需修改 `_migrate_config` 函数,且无法处理"跳过多个版本"的场景。

**改进建议**:
引入 `config_version` 字段并建立迁移注册表。

**修复计划**(代码级):
1. 在 `config/schema.py` 的 `RootConfig` 增加 `config_version: int = 1` 字段
2. 在 `config/loader.py` 建立迁移注册表 `MIGRATIONS: dict[int, Callable]`,按版本号注册迁移函数
3. `_migrate_config` 改为:读取 `config_version`,依次执行从当前版本到最新版本的迁移函数
4. 增加 `config_version` 到默认配置
5. 增加测试:验证从 v0(无版本号)迁移到 v1 正常

---

### 3.16 apply_runtime_profile 用值比较判断用户改动(逻辑缺陷) [P2]

**问题描述**:
[config/profiles.py:40-59](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/config/profiles.py) — `apply_runtime_profile` 用 `== defaults` 判断用户是否未改。

**影响分析**:
用户显式配置成与默认值相同时,会被 profile 覆盖。例如用户显式设置 `restrict_to_workspace=True`(恰好等于某 profile 默认值),会被误判为"未改"而被 profile 覆盖。

**改进建议**:
基于字段 presence(raw dict 是否含 key)判断而非值比较。

**修复计划**(代码级):
1. 修改 `apply_runtime_profile`,改为检查 raw dict 中字段是否存在(而非值是否等于默认)
2. 仅对 raw dict 中未出现的字段应用 profile 默认值
3. 增加测试:验证用户显式设置为默认值时不被覆盖

---

### 3.17 AuditLogger 产出在生产路径中无人消费 [P1]

**问题描述**:
[agent/audit.py:278-312,362](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/audit.py) — `find_by_action_id`、`find_by_confirmation_id`、`explain_action`、`_read_all_events` 等查询接口,调用位置仅在:
- `scripts/run_lighting_pilot_demo.py:95` — demo 脚本
- `tests/agent/test_audit.py`、`test_action_runtime_resume.py`、`test_device_audit_privacy.py`、`test_confirmation.py`、`test_validation_lighting_pilot.py` — 测试代码

**生产运行时代码路径中没有任何消费者**。审计日志被持续写入(L314 `_log`),但生产路径不读取。

**影响分析**:
审计数据是"write-only"的,没有进入任何决策反馈或运行时检查回路。审计的核心价值(可追溯、可回溯决策)未实现——日志写了但没人看。违反规则1(全链路追踪)的精神。

**改进建议**:
明确审计定位:(a) 若为只写审计(供事后排查),在 docstring 说明并确保有外部查询工具(如 CLI 命令);(b) 若期望接入运行时检查,在关键路径(如 confirmation 流程)读取审计历史。

**修复计划**(代码级):
1. 评估审计的预期定位(只写审计 vs 运行时检查)
2. 若为只写审计:增加 CLI 命令 `originagent audit query --action-id <id>` 供事后查询,在 docstring 说明
3. 若接入运行时检查:在 `confirmation.py` 的审批流程中,调用 `find_by_action_id` 检查历史行为
4. 增加测试:验证审计查询在生产路径或 CLI 中可用

---

### 3.18 旧 BDI 引擎作为单租户 fallback 长期保留 [P2]

**问题描述**:
[agent/agent_host.py:103,353](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_host.py) — `self._legacy_bdi_engine: Any = None  # Single-tenant fallback` 与 `return self._legacy_bdi_engine  # Single-tenant fallback`。

**影响分析**:
旧 BDI 引擎保留作为单租户模式的回退路径,没有迁移时间表。违反规则25(禁止"暂时搁置,未来实现"式规避)。

**改进建议**:
设迁移时间表,或确认单租户 fallback 为最终设计并补充类型注解(替代 `Any`)。

**修复计划**(代码级):
1. 评估多租户 BDI 引擎的成熟度
2. 若成熟:设迁移时间表,逐步移除 legacy engine
3. 若不成熟:在注释中说明保留原因与迁移条件,补充类型注解
4. 增加"迁移到技术债看板"条目

---

### 3.19 LocalAwarenessBackend 仅写 1x1 透明 PNG(感知能力未实现) [P2]

**问题描述**:
[agent/local_awareness.py:366-372,876-1094](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/local_awareness.py) — `capture_camera_frame`、`capture_screen`、`record_audio_sample` 仅写 1x1 透明 PNG 占位文件。注释明确说 "Real camera/audio/screen implementations can replace this via loop overrides later"。

**影响分析**:
真实感知能力未实现,仅产出可审计的占位媒体。Agent 的"本地感知"能力实际不存在,但工具已暴露给 LLM,LLM 可能调用并得到空结果。

**改进建议**:
明确感知能力的当前状态:若不打算实现,在工具描述中说明"当前仅产出占位文件";若计划实现,设时间表。

**修复计划**(代码级):
1. 在 `local_awareness.py` 的工具描述中明确说明"当前仅产出占位文件,真实感知能力未实现"
2. 或在工具注册时标记为 `preview_only=True`,不暴露给 LLM
3. 评估是否有真实需求,若无则考虑移除相关工具

---

## 四、其他反模式(未归入上述类别)

### 4.1 函数体内大量延迟 import 掩盖循环依赖 [P2]

**问题描述**:
- [agent_runtime.py:291-293,316-317,322-323,349-350,360-362,414-416,509,537,550,557,562,862-866,924-937,1106-1131](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_runtime.py) — 10+ 处函数内 import
- [cognitive_loop.py:11-13](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_loop.py) — TYPE_CHECKING 注释明确说明"运行时不导入以避免与 epic_motor 形成循环依赖"
- [action_runtime.py:36-39](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/action_runtime.py) — 同上

**影响分析**:
延迟 import 是循环依赖的 symptom 而非 fix。`cognitive_loop` 与 `epic_motor`、`action_runtime` 与 `epic_motor` 之间的循环依赖说明模块边界划分不当。这与 1.1 的发现一致:epic_motor 在生产中未启用,却让 cognitive_loop/action_runtime 为它承担循环依赖的代价。

**改进建议**:
删除 epic_motor 后(见 1.1),cognitive_loop 与 action_runtime 的循环依赖自然消失。其他延迟 import 需评估是否有真实循环依赖,若有则通过抽象接口/依赖反转拆解。

**修复计划**(代码级):
1. 完成 1.1 的 epic_motor 删除,移除 cognitive_loop.py:11-13 与 action_runtime.py:36-39 的 TYPE_CHECKING 块
2. 评估 `agent_runtime.py` 的 10+ 处函数内 import,区分循环依赖 vs 延迟加载优化
3. 对真实循环依赖,通过抽象接口拆解
4. 对延迟加载优化,若性能影响不大则改为顶层 import
5. 运行测试验证

---

### 4.2 entry_points 插件机制无任何真实消费者(投机性扩展点) [P3]

**问题描述**:
[agent/tools/loader.py:71-93](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/tools/loader.py) — `_discover_plugins()` 通过 `entry_points(group="originagent.tools")` 发现外部插件。全仓库 toml 搜索 `originagent.tools` 零命中。

**影响分析**:
完全没有真实插件注册的投机性扩展点(违反规则32)。基础设施存在但无任何包声明该 entry point group。

**改进建议**:
保留(轻量,未来可能有插件),但不必维护。或在文档中说明"插件机制预留,当前无官方插件"。

**修复计划**(代码级):
1. 保留 entry_points 机制(轻量)
2. 在文档中说明"插件机制预留,当前无官方插件"
3. 不做额外维护

---

### 4.3 ToolLimits 定制点从未被使用 [P3]

**问题描述**:
各 Tool 的 `limits` 参数——所有工厂均用默认 `ToolLimits()`,`limits` 参数从未被外部传入。`register_default_tools` 中没有任何工厂传入 `limits`。

**影响分析**:
未被使用的定制点(轻微的投机性设计)。

**改进建议**:
删除 `limits` 参数,或接入配置(`ToolsConfig.limits`)。

**修复计划**(代码级):
1. 评估是否有定制 limits 的需求
2. 若无:删除 `limits` 参数与 `ToolLimits` 类
3. 若有:在 `ToolsConfig` 增加 `limits` 配置项,从 config 注入
4. 运行测试验证

---

## 未发现此类问题的领域(澄清)

以下领域经审查确认设计良好,无重大问题:

- **MCP 集成**(agent/tools/mcp.py):生产级实现,含 SSRF 防护/不可信内容隔离/能力快照/重试,非占位。
- **子代理系统**(agent/subagent*.py):完整闭环,产出通过 bus.publish_inbound + session_key_override 实现 mid-turn 注入,回流真实有效。
- **Domain Packs 治理**(domain_packs/ + agent/domain_pack_*.py):governance/scaffold/schema/manager 四件套完整连接且广泛使用。
- **工具安全边界**(agent/tools/security.py + registry.py):能力快照校验是强制的 P0 级边界,真实生效。
- **cron.py vs cron_desire_bridge.py**:职责正交且已连接,无重叠。
- **storage/ 三文件分层**(jsonl_fallback.py/jsonl_migration.py/sqlite_stores.py):工具-抽象-工厂清晰分层,无复制粘贴。
- **记忆系统主体**(memory/ 目录):pipeline/retrieval/store/segmenter/rolling/candidates/profile/policy/episode_bridge 均正常接入。
- **元认知主体**(meta_cognition_runtime.py/meta_programming.py/self_model.py/introspection/service.py):正常接入主流程,self_model 在决策路径中实际起作用。
- **SKILL.md 内容**:抽查 weather/cron/update-setup 均为真实可用内容,未发现空壳。

---

## 修复优先级总览

### P0(影响正确性、安全或核心功能未连接)— 8 项
1. 1.1 EPIC motor 子系统删除/接入
2. 1.2 turn 状态机死状态修复
3. 1.3 TurnOrchestratorDeps getattr 黑魔法修复
4. 1.6 BDI 三处 bridge 未连接
5. 1.7 EvolutionModuleManager 删除/接入
6. 2.1 cli/models.py 模型数据库恢复
7. 2.2 allow_from 为空时给出修复指引
8. 2.3 msteams 鉴权关闭强制阻断

### P1(影响可维护性或用户体验)— 12 项
1. 1.4 ContextAssemblerV2 影子实现合并
2. 1.5 双重 prompt 预算机制统一
3. 1.8 InnerMonologueEngine placeholder 字段处理
4. 1.9 _extract_active_goal 实现
5. 2.4 onboard 覆盖配置警示
6. 2.5 i18n 形同虚设修复
7. 2.6 settings_update 环境变量解析
8. 2.7 硬编码用户可见字符串
9. 2.8 .originagent 命名统一
10. 3.1 Strangler Fig 迁移收口
11. 3.2 JSONL Fallback 收口
12. 3.3 角色名常量化
13. 3.6 _TRANSCRIPTION_PROVIDERS 统一
14. 3.8 SkillBootstrapper 死代码删除
15. 3.17 AuditLogger 产出口接消费

### P2(设计债务,可逐步清理)— 11 项
1. 1.10 上下文管理统一
2. 3.4 超时魔法值统一
3. 3.5 设备后端选项统一
4. 3.7 dingtalk/qq 复制粘贴消除
5. 3.9 ToolLoader discover 死代码删除
6. 3.10 CognitiveLoopConfig.enabled 处理
7. 3.11 active_intent_processor 参数删除
8. 3.12 notify_only 死路径删除
9. 3.13 ActionSafetyGate 浅模块删除
10. 3.14 markdown 共享工具推广
11. 3.15 _migrate_config 版本号机制
12. 3.16 apply_runtime_profile 逻辑修复
13. 3.18 旧 BDI 引擎迁移
14. 3.19 LocalAwarenessBackend 明确状态
15. 4.1 延迟 import 清理

### P3(轻微债务)— 2 项
1. 4.2 entry_points 插件机制文档化
2. 4.3 ToolLimits 定制点处理
