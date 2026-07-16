# Tasks

本 tasks.md 按阶段组织,每阶段包含验证方式(规则34:验证先行)。

---

## 阶段 1:恢复代码路径(解除测试阻塞)

- [ ] Task 1.1: 恢复 `cognitive_loop.py` 的 EPIC motor 代码路径
  - [ ] SubTask 1.1.1: 增加 TYPE_CHECKING 块(`from OriginAgent.agent.epic_motor import EpicMotorProcessor`)
  - [ ] SubTask 1.1.2: `CognitiveLoopConfig` 增加 `motor_tick_interval_ms: int = 50` 字段
  - [ ] SubTask 1.1.3: `CognitiveLoop.__init__` 增加 `motor_processor: EpicMotorProcessor | None = None` 参数
  - [ ] SubTask 1.1.4: 恢复 `_run_motor_loop` 方法(50ms 节拍)
  - [ ] SubTask 1.1.5: 修改 `run_forever` 为双循环(motor_processor 非 None 时并发执行)
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_cognitive_loop_motor.py -v`

- [ ] Task 1.2: 恢复 `action_runtime.py` 的 EPIC motor 代码路径
  - [ ] SubTask 1.2.1: 增加 TYPE_CHECKING 块(`from OriginAgent.agent.epic_motor import EpicActionQueue, EpicMotorCommand`)
  - [ ] SubTask 1.2.2: `ActionIntent` 增加 `duration_ms: int = 0` 和 `requires_parallel: bool = False` 字段
  - [ ] SubTask 1.2.3: `SafeActionExecutor.__init__` 增加 `motor_queue: EpicActionQueue | None = None` 参数
  - [ ] SubTask 1.2.4: 增加 `_enqueue_motor_command` 方法
  - [ ] SubTask 1.2.5: 修改 `submit()` 在 `decision == "allow"` 且 `motor_queue` 非 None 时入队
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_safe_executor_motor_queue.py -v`

- [ ] Task 1.3: 恢复 `device_actions.py` 的 EPIC 字段
  - [ ] SubTask 1.3.1: `TypedDeviceAction` 增加 `duration_ms` 和 `requires_parallel` 字段
  - [ ] SubTask 1.3.2: `to_intent` 方法透传这两个字段
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_typed_device_action_epic_fields.py -v`

- [ ] Task 1.4: 恢复 `evolution/__init__.py` 的导出
  - [ ] SubTask 1.4.1: 导入并导出 `EvolutionModuleManager` 及相关符号(18 个)
  - **验证方式**: `.\.venv\Scripts\python.exe -c "from OriginAgent.evolution import EvolutionModuleManager; print('OK')"`

- [ ] Task 1.5: 恢复 `skill_bootstrapper.py` 的 `SkillBootstrapper` 类
  - [ ] SubTask 1.5.1: 追加 `SkillBootstrapper` 类(滚动窗口指纹 + 阈值触发编译)
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_skill_bootstrapper_online.py -v`

- [ ] Task 1.6: 恢复 `soar_chunker.py` 的顶层 import
  - [ ] SubTask 1.6.1: 将 `from OriginAgent.agent.skill_bootstrapper import SkillBootstrapper` 从 TYPE_CHECKING 移到顶层
  - **验证方式**: `.\.venv\Scripts\python.exe -c "from OriginAgent.agent.soar_chunker import SoarChunker; print('OK')"`

- [ ] Task 1.7: 恢复 `context.py` 的 ContextAssemblerV2 接入
  - [ ] SubTask 1.7.1: 删除 `assemble_with_audit` 方法(L777-938)和 `_block_trace` 静态方法(L941-964)
  - [ ] SubTask 1.7.2: 恢复 `self.assembler_v2 = ContextAssemblerV2(self)` 初始化
  - [ ] SubTask 1.7.3: 恢复 `assemble_user_content` 调用 `self.assembler_v2.assemble(...)`
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_context_prompt_cache.py -v`

- [ ] Task 1.8: 恢复 `agent_runtime.py` 的 Strangler Fig 子容器定义
  - [ ] SubTask 1.8.1: 增加 5 个子容器 dataclass(CoreServices/MetaCognitionServices/MemoryServices/BackgroundServices/StoreServices)
  - [ ] SubTask 1.8.2: `RuntimeDependencies` 增加子容器字段(默认 None,双路径共存)
  - **验证方式**: `.\.venv\Scripts\python.exe -c "from OriginAgent.agent.agent_runtime import RuntimeDependencies, CoreServices; print('OK')"`

- [ ] Task 1.9: 恢复 `loop.py` 的子容器构造
  - [ ] SubTask 1.9.1: 在 `RuntimeDependencies(...)` 调用处增加子容器构造块
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/ -k "not epic_motor" --tb=short -q`

---

## 阶段 2:EPIC motor 生产接入

- [ ] Task 2.1: 在 `agent_loop_components.py` 构造 EpicActionQueue/EpicMotorProcessor
  - [ ] SubTask 2.1.1: 新增 EpicActionQueue 实例构造
  - [ ] SubTask 2.1.2: 新增 EpicMotorProcessor 实例构造(注入 executor 和 queue)
  - [ ] SubTask 2.1.3: 传入 `CognitiveLoop(motor_processor=...)`
  - [ ] SubTask 2.1.4: 将 `motor_queue` 传入 SafeActionExecutor 构造路径(domain pack)
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_epic_motor_processor.py tests/agent/test_epic_action_queue.py -v`

- [ ] Task 2.2: 修复 EpicMotorProcessor 幂等性 gap(规则12 红线)
  - [ ] SubTask 2.2.1: 在 `EpicMotorProcessor.tick()` 的 dispatch 成功后调用 `executor._remember_successful_idempotency(intent, result)`
  - [ ] SubTask 2.2.2: 增加测试:验证 motor 派发成功后幂等键被记忆
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_epic_motor_idempotency.py -v`

---

## 阶段 3:SkillBootstrapper 接入

- [ ] Task 3.1: 在 `agent_loop_components.py` 实例化 SkillBootstrapper
  - [ ] SubTask 3.1.1: 构造 `SkillBootstrapper` 实例(注入 scanner/compiler/workspace)
  - [ ] SubTask 3.1.2: 构造 `SoarChunker` 实例(注入 SkillBootstrapper)
  - [ ] SubTask 3.1.3: 在 `SubagentManager` 构造时传入 `soar_chunker`
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_skill_bootstrapper_online.py tests/agent/test_soar_chunker.py -v`

---

## 阶段 4:EvolutionModuleManager 桥接

- [ ] Task 4.1: 在 `EvolutionControlPlane` 桥接 `EvolutionModuleManager`
  - [ ] SubTask 4.1.1: 在 `EvolutionControlPlane` 增加 `module_manager: EvolutionModuleManager | None` 字段
  - [ ] SubTask 4.1.2: `force_cleanup`/`rollback_artifact` 委派给 `module_manager.rollback_module()`(经 ConfirmationManager 审批)
  - [ ] SubTask 4.1.3: 在 `agent_loop_components.py` 构造 `EvolutionModuleManager` 并注入
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/evolution/ -v`

---

## 阶段 5:Strangler Fig 分阶段迁移(本 session 仅完成阶段 0)

- [ ] Task 5.0: 阶段 0 — 子容器定义与构造(双路径共存)
  - [ ] SubTask 5.0.1: 已在 Task 1.8/1.9 完成
  - [ ] SubTask 5.0.2: 验证 `deps.tools` 和 `deps.core.tools` 返回同一对象
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/ --tb=short -q`

- [ ] Task 5.1-5.6: 阶段 1-6(逐组迁移 + 删除平铺字段)— 留待后续 session

---

## 阶段 6:ContextAssemblerV2 封装边界修复(方案 B:依赖注入)

- [ ] Task 6.1: 引入 `ContextBuildArtifacts` 数据类
  - [ ] SubTask 6.1.1: 在 `context.py` 定义 `ContextBuildArtifacts`(blocks + audit)
  - **验证方式**: 单元测试断言 artifacts 包含 blocks 与 audit 两个字段

- [ ] Task 6.2: 修改 ContextBuilder 公共方法返回 `ContextBuildArtifacts`
  - [ ] SubTask 6.2.1: 新增 `build_user_content_with_artifacts` 公共方法(返回 ContextBuildArtifacts)
  - [ ] SubTask 6.2.2: `build_runtime_context_block`/`build_phase1_continuity_blocks` 等方法返回值携带 audit
  - **验证方式**: 单元测试验证公共方法返回 artifacts

- [ ] Task 6.3: 修改 ContextAssemblerV2 从公共方法返回值获取审计数据
  - [ ] SubTask 6.3.1: 删除所有 `self._builder._last_*` 私有属性访问
  - [ ] SubTask 6.3.2: 改为从 `ContextBuildArtifacts` 返回值获取审计数据
  - [ ] SubTask 6.3.3: `_last_context_assembly_audit` 由 `assemble_user_content` 从返回值回写
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_context_prompt_cache.py -v` + Grep 确认无私有属性访问

---

## 阶段 7:技术债持久化(规则35)

- [ ] Task 7.1: 为 Strangler Fig 迁移剩余阶段创建技术债文件
  - [ ] SubTask 7.1.1: 创建 `techdebt/TD-2026-001-strangler-fig-migration.md`
- [ ] Task 7.2: 为 ContextAssemblerV2 后续优化创建技术债文件(如有)

---

# Task Dependencies

## 阶段内依赖
- 阶段 1 各 Task 之间基本独立,可并行(Task 1.1/1.2/1.3 有关联但可顺序执行)
- Task 1.5 → Task 1.6(SoarChunker 依赖 SkillBootstrapper)
- Task 1.1/1.2 → Task 2.1(EPIC motor 接入依赖代码路径恢复)
- Task 1.5/1.6 → Task 3.1(SkillBootstrapper 接入依赖代码恢复)
- Task 1.4 → Task 4.1(Evolution 桥接依赖导出恢复)
- Task 1.8/1.9 → Task 5.0(Strangler Fig 阶段 0 依赖子容器定义)
- Task 1.7 → Task 6.1(ContextAssemblerV2 修复依赖接入恢复)

## 跨阶段依赖
- 阶段 2/3/4 依赖阶段 1 完成
- 阶段 5 依赖阶段 1 的 Task 1.8/1.9
- 阶段 6 依赖阶段 1 的 Task 1.7

## 建议执行顺序
1. 阶段 1 全部(恢复代码路径,解除测试阻塞)
2. 阶段 2(EPIC motor 接入 + 幂等性修复)
3. 阶段 3(SkillBootstrapper 接入)
4. 阶段 4(EvolutionModuleManager 桥接)
5. 阶段 5.0(Strangler Fig 阶段 0)
6. 阶段 6(ContextAssemblerV2 DI 修复)
7. 阶段 7(技术债持久化)
