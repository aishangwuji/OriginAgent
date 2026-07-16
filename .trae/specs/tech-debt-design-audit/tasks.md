# Tasks

本 tasks.md 分两部分:
1. **审查任务**(已完成)— 本次设计级审查的执行步骤
2. **推荐修复任务**(待执行)— 基于 spec.md 审查结果的修复任务清单,按优先级与依赖关系组织,建议分批由后续 spec 处理

---

## 一、审查任务(已完成)

- [x] Task 1: 探索 Agent 核心运行时架构(cognitive_loop/agent_runtime/runner/turn_orchestrator)
  - [x] SubTask 1.1: 审查架构分层与模块耦合
  - [x] SubTask 1.2: 审查状态机与数据流
  - [x] SubTask 1.3: 审查过度设计与临时方案固化
- [x] Task 2: 探索 BDI/记忆/进化/元认知子系统
  - [x] SubTask 2.1: 审查 BDI bridge 连接完整性
  - [x] SubTask 2.2: 审查记忆系统产出消费闭环
  - [x] SubTask 2.3: 审查进化机制接入状态
  - [x] SubTask 2.4: 审查元认知产出消费闭环
- [x] Task 3: 探索工具/技能/子代理/MCP/Domain Pack 系统
  - [x] SubTask 3.1: 审查工具注册与发现机制
  - [x] SubTask 3.2: 审查安全边界有效性
  - [x] SubTask 3.3: 审查技能系统死代码
  - [x] SubTask 3.4: 审查子代理产出回流
- [x] Task 4: 探索通道/Web/API/CLI/配置/i18n 用户接触面
  - [x] SubTask 4.1: 审查通道注册与复用
  - [x] SubTask 4.2: 审查可用性与用户体验缺陷
  - [x] SubTask 4.3: 审查 i18n 覆盖范围
  - [x] SubTask 4.4: 审查配置生效路径
- [x] Task 5: 系统性扫描未实现功能与反模式
  - [x] SubTask 5.1: 扫描 TODO/FIXME/NotImplemented/placeholder
  - [x] SubTask 5.2: 扫描临时方案固化痕迹
  - [x] SubTask 5.3: 扫描产出未被消费的模块
  - [x] SubTask 5.4: 扫描硬编码与魔法值
  - [x] SubTask 5.5: 扫描复制粘贴式代码
  - [x] SubTask 5.6: 对照 docs/ 计划文档与实际代码差距
- [x] Task 6: 整合审查结果,撰写 spec.md
- [x] Task 7: 撰写 tasks.md 与 checklist.md

---

## 二、推荐修复任务(待执行)

修复任务按优先级分批,建议每个 P0/P1 问题由独立 spec 处理。以下为推荐的任务拆分与依赖关系。

### 批次 A:P0 核心架构修复(建议优先)

- [x] Task A1: 删除 EPIC motor 子系统(spec 1.1)
  - [x] SubTask A1.1: 删除 `OriginAgent/agent/epic_motor.py`
  - [x] SubTask A1.2: 清理 `cognitive_loop.py` 的 motor 相关代码与 TYPE_CHECKING 块
  - [x] SubTask A1.3: 清理 `action_runtime.py` 的 motor 相关代码与 TYPE_CHECKING 块
  - [x] SubTask A1.4: 清理 `agent_loop_components.py` 的 motor_processor 构造逻辑
  - [x] SubTask A1.5: 删除相关测试文件
  - [x] SubTask A1.6: 运行全量测试验证无回归
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/ -k "not epic_motor" --tb=short`

- [x] Task A2: 修复 turn 状态机死状态(spec 1.2)
  - [x] SubTask A2.1: 在 `state_run` 外层包裹 try/except
  - [x] SubTask A2.2: 实现 `state_handle_error`/`state_handle_timeout` handler
  - [x] SubTask A2.3: 增加测试:模拟 state_run 抛异常,验证状态机进入 HANDLE_ERROR
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_turn_state_machine.py -v`

- [x] Task A3: 修复 TurnOrchestratorDeps getattr 黑魔法(spec 1.3)
  - [x] SubTask A3.1: 在 `TurnOrchestratorDeps` 增加 `meta_cognition_runtime` 字段
  - [x] SubTask A3.2: 在 `loop.py` 构造时传入 `meta_cognition_runtime`
  - [x] SubTask A3.3: 增加测试:验证 `start_turn`/`end_turn` 被调用
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_turn_orchestrator.py -v`

- [x] Task A4: 连接 BDI 三处 bridge(spec 1.6)
  - [x] SubTask A4.1: 在 `agent_host.py` 实例化 `UtilityRewardBridge` 并传入 `DeliberationEngine`
  - [x] SubTask A4.2: 在 `cli/commands.py` 创建 `HeartbeatService` 时传入 `BDIHeartbeatBridge`
  - [x] SubTask A4.3: 在 `world_state.py` 发布 `BELIEF_CHANGED_EVENT`,在 `deliberation.py` 注入 `event_bus`
  - [x] SubTask A4.4: 增加三个 bridge 的"装配完整性"集成测试
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/ -v`
  - **依赖**: 依赖 `fix-bdi-core-defects` spec 先完成

- [x] Task A5: 处理 EvolutionModuleManager 死代码(spec 1.7)
  - [x] SubTask A5.1: 确认 `EvolutionModuleManager` 无未来需求(需用户确认)
  - [x] SubTask A5.2: 迁移 `evolution/memory_vault.py` 到 `agent/` 或 `security/`
  - [x] SubTask A5.3: 删除 `evolution/` 目录(除 memory_vault.py)
  - [x] SubTask A5.4: 更新 `cli/commands.py` 的 import 路径
  - [x] SubTask A5.5: 运行全量测试验证
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/evolution/ -v`

- [x] Task A6: 恢复 cli/models.py 模型数据库(spec 2.1)
  - [x] SubTask A6.1: 增加静态常用模型列表(OpenAI/Anthropic/OpenRouter)
  - [x] SubTask A6.2: 实现 `get_all_models`/`find_model_info`/`get_model_context_limit`/`get_model_suggestions`
  - [x] SubTask A6.3: 在 onboard 向导中提示"模型库为静态版本"
  - [x] SubTask A6.4: 增加测试:验证静态列表非空且查找正常
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/cli/ -v`

- [x] Task A7: 修复 allow_from 为空时的启动退出(spec 2.2)
  - [x] SubTask A7.1: 在 `_validate_allow_from` 打印明确修复指引后退出
  - [x] SubTask A7.2: 增加测试:验证 allow_from 为空时输出修复指引
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/channels/ -v`

- [x] Task A8: 修复 msteams 鉴权关闭不阻断(spec 2.3)
  - [x] SubTask A8.1: 在 `msteams.py` 配置加载时检查 runtime profile
  - [x] SubTask A8.2: 生产 profile 下 `validate_inbound_auth=False` 时拒绝启动
  - [x] SubTask A8.3: 增加测试:验证生产 profile 下关闭鉴权时拒绝启动
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/channels/ -v`

### 批次 B:P1 可维护性与用户体验修复

- [x] Task B1: 合并 ContextAssemblerV2 回 ContextBuilder(spec 1.4)
- [x] Task B2: 统一 prompt 预算机制(spec 1.5)
  - [x] SubTask B2.1: 在 `AgentRunResult` 增加 `last_sent_messages` 字段,捕获治理后实际发送给 LLM 的消息快照
  - [x] SubTask B2.2: 在 `AgentRunner.run()` 治理管线后捕获 `last_sent_messages` 快照
  - [x] SubTask B2.3: 在 `_run_agent_loop` 中根据 `last_sent_messages` 刷新 `state.last_context_assembly` audit
  - [x] SubTask B2.4: 评估 `_snip_history` 价值 → 保留为 fallback(处理 GLM-1214 错误恢复 + 孤儿 tool result 清理)
  - [x] SubTask B2.5: 编写测试 `tests/agent/test_unified_prompt_budget.py`(4 个测试,验证先行)
  - **验证方式**: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_unified_prompt_budget.py -v --basetemp=.\.pytest_tmp_b2`
- [x] Task B3: 处理 InnerMonologueEngine placeholder 字段(spec 1.8)
- [x] Task B4: 实现 _extract_active_goal(spec 1.9)
- [x] Task B5: 修复 onboard 覆盖配置警示(spec 2.4)
- [x] Task B6: 修复 i18n 形同虚设(spec 2.5 + 2.7)
  - [x] SubTask B6.1: 补齐 en.json/zh.json 的 channel.*/cli.*/api.*/error.* 命名空间
  - [x] SubTask B6.2: 修复中英文错位字符串(msteams/api/server/cli/stream)
  - [x] SubTask B6.3: 在 BaseChannel 增加 `_t` 辅助方法
- [x] Task B7: 修复 settings_update 环境变量解析(spec 2.6)
- [x] Task B8: 统一 .originagent 命名(spec 2.8)
- [x] Task B9: 收口 Strangler Fig 迁移(spec 3.1)
- [x] Task B10: 收口 JSONL Fallback(spec 3.2)
- [x] Task B11: 角色名常量化(spec 3.3)
- [x] Task B12: 统一 _TRANSCRIPTION_PROVIDERS(spec 3.6)
- [x] Task B13: 删除 SkillBootstrapper 死代码(spec 3.8)
- [x] Task B14: 明确 AuditLogger 产出口消费(spec 3.17)

### 批次 C:P2 设计债务清理

- [x] Task C1: 统一上下文管理(spec 1.10)— 依赖 Task B1
- [x] Task C2: 统一超时魔法值(spec 3.4)
- [x] Task C3: 统一设备后端选项(spec 3.5)
- [x] Task C4: 消除 dingtalk/qq 复制粘贴(spec 3.7)
- [x] Task C5: 删除 ToolLoader discover 死代码(spec 3.9)
- [~] Task C6: 处理 CognitiveLoopConfig.enabled(spec 3.10)— **阻塞:spec 前提错误,enabled 实际被 agent_cognitive_runtime.py 消费**
- [x] Task C7: 删除 active_intent_processor 参数(spec 3.11)
- [x] Task C8: 删除 notify_only 死路径(spec 3.12)
- [x] Task C9: 删除 ActionSafetyGate 浅模块(spec 3.13)
- [x] Task C10: 推广 markdown 共享工具(spec 3.14)
- [x] Task C11: 增加 _migrate_config 版本号机制(spec 3.15)
- [x] Task C12: 修复 apply_runtime_profile 逻辑(spec 3.16)
- [x] Task C13: 处理旧 BDI 引擎迁移(spec 3.18)
- [x] Task C14: 明确 LocalAwarenessBackend 状态(spec 3.19)
- [x] Task C15: 清理延迟 import(spec 4.1)— 依赖 Task A1

### 批次 D:P3 轻微债务

- [x] Task D1: 文档化 entry_points 插件机制(spec 4.2)
- [x] Task D2: 处理 ToolLimits 定制点(spec 4.3)

---

# Task Dependencies

## 批次 A 内部依赖
- Task A4 (BDI bridge) 依赖 `fix-bdi-core-defects` spec 先完成
- Task A5 (Evolution) 需用户确认后再执行

## 跨批次依赖
- Task C1 (上下文管理统一) 依赖 Task B1 (ContextAssemblerV2 合并)
- Task C15 (延迟 import 清理) 依赖 Task A1 (EPIC motor 删除)

## 并行执行建议
- 批次 A 的 Task A1/A2/A3/A6/A7/A8 可并行(无相互依赖)
- 批次 A 的 Task A4 需等 `fix-bdi-core-defects` 完成
- 批次 B 的各任务可并行(无相互依赖)
- 批次 C 的各任务可并行(除上述依赖外)

## 建议执行顺序
1. 先执行批次 A 的 P0 修复(核心架构 + 安全)
2. 再执行批次 B 的 P1 修复(可维护性 + 用户体验)
3. 最后执行批次 C/D 的 P2/P3 清理(设计债务)
