# 恢复并接入设计模块 Spec

## Why

之前的技术债审查错误地将 5 个"未接入但有设计价值"的模块当作死代码删除。这些模块的设计意图是:
- **EPIC motor**:实现"感知-认知-运动"三层架构分离(EPIC 认知架构),50ms 节拍异步缓冲动作执行
- **EvolutionModuleManager**:代码自演化的执行层(模块包的 stage/verify/activate/rollback)
- **SkillBootstrapper**:Soar 式在线技能形成,从子代理成功经验中提取可复用技能
- **Strangler Fig 子容器**:将 70+ 平铺字段分组为 5 个类型安全子容器,提升可维护性
- **ContextAssemblerV2**:分离上下文组装与审计收集职责

本 spec 恢复这些模块并接入生产路径,同时对 ContextAssemblerV2 修复封装边界(从访问私有属性改为依赖注入)。

## What Changes

### 恢复(解除测试阻塞)
- 恢复 `cognitive_loop.py`/`action_runtime.py`/`device_actions.py` 的 EPIC motor 代码路径
- 恢复 `evolution/__init__.py` 的导出(18 个符号)
- 恢复 `skill_bootstrapper.py` 的 `SkillBootstrapper` 类
- 恢复 `soar_chunker.py` 的顶层 import
- 恢复 `agent_runtime.py`/`loop.py` 的 Strangler Fig 子容器定义与构造
- 恢复 `context.py` 的 ContextAssemblerV2 接入(删除合并时新增的 `assemble_with_audit`)

### 接入生产路径
- **EPIC motor**:在 `agent_loop_components.py` 新增 `EpicActionQueue`/`EpicMotorProcessor` 构造,解决跨层依赖(领域包构造 SafeActionExecutor → 核心层消费),修复幂等性 gap(规则12 红线)
- **SkillBootstrapper**:在 `agent_loop_components.py` 实例化并注入 `SubagentManager`,激活 `subagent.py` 中已有的 SoarChunker 钩子
- **EvolutionModuleManager**:在 `EvolutionControlPlane` 中桥接作为执行层,委派模块包级 activate/rollback 操作

### Strangler Fig 分阶段迁移
- 阶段 0:恢复子容器定义与构造(双路径共存)
- 阶段 1-5:逐组迁移平铺字段访问到子容器(StoreServices → BackgroundServices → MetaCognitionServices → MemoryServices → CoreServices)
- 阶段 6:删除平铺字段

### ContextAssemblerV2 封装边界修复(方案 B:依赖注入)
- 引入 `ContextBuildArtifacts` 数据类(blocks + audit)
- 修改 ContextBuilder 公共方法返回审计数据
- ContextAssemblerV2 从公共方法返回值获取审计数据,不再访问私有属性
- `_last_context_assembly_audit` 由 `assemble_user_content` 从返回值回写

## Impact

- Affected specs: `tech-debt-design-audit`(部分回退)
- Affected code:
  - `OriginAgent/agent/cognitive_loop.py`、`action_runtime.py`、`agent_loop_components.py`、`device_actions.py`
  - `OriginAgent/evolution/__init__.py`、`evolution/manager.py`(已恢复)
  - `OriginAgent/agent/skill_bootstrapper.py`、`soar_chunker.py`
  - `OriginAgent/agent/agent_runtime.py`、`loop.py`
  - `OriginAgent/agent/context.py`、`context_assembler.py`(已恢复)
  - `OriginAgent/agent/epic_motor.py`(已恢复)
  - `OriginAgent/agent/evolution_control_plane.py`(桥接点)

## ADDED Requirements

### Requirement: EPIC motor 生产接入
系统 SHALL 在 `agent_loop_components.py` 构造 `EpicMotorProcessor` 并传入 `CognitiveLoop`,使动作执行从同步阻塞改为 50ms 节拍异步缓冲。`motor_queue=None` 时保持同步执行(向后兼容)。

#### Scenario: motor_queue 启用时动作异步派发
- **WHEN** `SafeActionExecutor.submit()` 被调用且 `motor_queue` 非 None
- **THEN** 动作入队返回 `queued` 状态,`EpicMotorProcessor.tick()` 在 50ms 节拍内派发

#### Scenario: motor_queue 为 None 时同步执行(向后兼容)
- **WHEN** `motor_queue` 为 None
- **THEN** `submit()` 直接调用 `_execute_allowed()`,行为与当前一致

#### Scenario: 幂等键在 motor 派发成功后被记忆
- **WHEN** `EpicMotorProcessor.tick()` 成功执行动作
- **THEN** 调用 `_remember_successful_idempotency()` 记忆幂等键,防止重试导致重复执行

### Requirement: SkillBootstrapper 在线技能形成
系统 SHALL 在 `agent_loop_components.py` 实例化 `SkillBootstrapper` 并通过 `SoarChunker` 注入 `SubagentManager`,使子代理成功解决障碍后自动压缩为可复用技能候选。

#### Scenario: 子代理成功后触发技能形成
- **WHEN** 子代理成功完成(`status.phase == "ok"`)且 `parent_obstacle` 存在
- **THEN** `SoarChunker.chunk_from_success()` 调用 `SkillBootstrapper.ingest_chunk()`,累计达到 `min_repeats` 时触发编译

### Requirement: EvolutionModuleManager 模块包级回滚
系统 SHALL 在 `EvolutionControlPlane` 中桥接 `EvolutionModuleManager`,使 `force_cleanup`/`rollback_artifact` 等模块包级操作通过已有的治理层(ConfirmationManager + CapabilityGrantStore)执行。

#### Scenario: 模块包级回滚经治理层审批
- **WHEN** 用户请求回滚模块包
- **THEN** `EvolutionControlPlane` 委派给 `EvolutionModuleManager.rollback_module()`,且需通过 ConfirmationManager 审批

### Requirement: Strangler Fig 分阶段迁移
系统 SHALL 将 `RuntimeDependencies` 的 70+ 平铺字段按 5 个子容器分组,分 6 个阶段逐组迁移访问路径,最终删除平铺字段。

#### Scenario: 迁移期间双路径共存
- **WHEN** 阶段 0 完成
- **THEN** `deps.tools` 和 `deps.core.tools` 返回同一对象,现有测试全部通过

### Requirement: ContextAssemblerV2 封装边界修复
系统 SHALL 修改 ContextAssemblerV2 使其通过公共方法返回值获取审计数据,不再访问 ContextBuilder 的私有属性。

#### Scenario: ContextAssemblerV2 不访问私有属性
- **WHEN** `ContextAssemblerV2.assemble()` 执行
- **THEN** 审计数据从 `ContextBuildArtifacts` 返回值获取,无 `self._builder._last_*` 访问

## MODIFIED Requirements

### Requirement: CognitiveLoop 双循环架构
`CognitiveLoop.run_forever` SHALL 并发执行审议循环(15s 节拍)与运动循环(50ms 节拍),当 `motor_processor` 为 None 时仅执行审议循环。

### Requirement: SafeActionExecutor 动作入队
`SafeActionExecutor.submit()` SHALL 在 `motor_queue` 非 None 时将动作入队返回 `queued` 状态,在 `motor_queue` 为 None 时直接执行。

## REMOVED Requirements

### Requirement: ContextBuilder.assemble_with_audit
**Reason**: 此方法是错误合并 ContextAssemblerV2 时新增的,恢复 ContextAssemblerV2 后应删除
**Migration**: 调用方改回 `ContextAssemblerV2.assemble()`
