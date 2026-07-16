# OriginAgent 业务域全景

> **last_verified**: 2026-07-16
> **schema_version**: 1
> **状态**: 初始版本(渐进式补全中,按规则 38.3)
> **核验方法**: 代码静态分析 + 执行流程追踪 + 已有技术债交叉验证

## 概述

OriginAgent 是一个具备 BDI 认知引擎、记忆固化、进化能力的智能 Agent 平台。核心定位为"持续运行的个人助手",通过多通道接入用户,具备长期记忆、自主推理、技能学习与模块进化能力。

---

## 一、核心业务域

### 1. Agent 主循环(Turn Pipeline)

**职责**: 接收用户消息 → 构建上下文 → 调用 LLM → 执行工具 → 返回响应

**核心实体**:
- `AgentLoop`([loop.py:160](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/loop.py#L160)) — 门面层,兼容旧代码
- `AgentRuntime`([agent_runtime.py:270](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/agent_runtime.py#L270)) — 无状态消息路由器
- `TurnOrchestrator` — 状态机驱动的 turn 管线
- `TurnState` / `TurnEvent` — turn 状态机(转换表 `_TRANSITIONS`)

**关键流程**:
```
用户消息 → MessageBus → AgentLoop._process_message()
  → AgentRuntime._process_message()
    → TurnOrchestrator.process_message()
      → 状态机驱动:build_context → call_llm → execute_tools → respond
```

**边界与异常**:
- Turn 中断恢复:`_restore_runtime_checkpoint` / `_restore_pending_user_turn` 从崩溃中恢复未完成 turn
- 工具执行失败:stop_reason ∈ {`tool_error`, `max_iterations`, `empty_final_response`}
- Ask-user 暂停:`pending_ask_id` 机制,等待用户补充信息后恢复

### 2. BDI 认知引擎

**职责**: 持续的 Belief→Desire→Intention 推理循环,自主管理目标与行动

**核心实体**:
- `DeliberationEngine`([bdi/deliberation.py:119](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/bdi/deliberation.py#L119)) — 推理引擎
- `Desire` — 目标/承诺,带优先级与截止时间
- `IntentionStack` — 嵌套 suspend/resume 栈(最大深度 10)
- `PlanLibrary` — 缓存的 means-ends 推理(自动学习)
- `WorldStateWatcher` — 事件驱动反应(烟雾报警、门开等关键信念变化触发立即重思)

**Desire 状态机**:
```
ACTIVE ──→ SATISFIED    (目标达成)
       ──→ SUSPENDED    (被阻塞/打断,入栈)
       ──→ CANCELLED    (取消)
SUSPENDED ──→ ACTIVE    (中断清除,出栈恢复)
```

**推理周期(8 步)**:
0. 检查 IntentionStack 可恢复的 suspended desires
1. 收集 active desires
2. foresights → desires 自动同步
3. 优先级排序(按 priority + deadline)
4. PlanLibrary 缓存命中 vs LLM-needed 分流
5. LLM deliberation(对未命中缓存的 desires)
6. 状态转换(含 IntentionStack push/pop)
7. 学习新 plans(LLM 成功结果自动入库)
8. 发出 intentions 供执行

**边界与异常**:
- 引擎禁用:`enabled=False` 时直接跳过,返回 skipped 结果
- IntentionStack 持久化:服务重启后从 `intention_stack.jsonl` 恢复 SUSPENDED 状态
- 脏状态:`dirty_rollback` 状态需人工清理残留资源

### 3. 记忆系统(Memory)

**职责**: 长期记忆固化、事实管理、会话归档

**核心子域**:

#### 3.1 Dream(记忆固化)
- `Dream`([memory.py:1616](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/memory.py#L1616)) — 周期性记忆固化
- **Phase 1**: LLM 事实提案 → JSON 解析 → 应用到 FactStore([memory_phases.py:70](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/memory_phases.py#L70))
- **Phase 2**: AgentRunner 维护(非 MEMORY 类)([memory_phases.py:242](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/memory_phases.py#L242))
- **Cursor 推进保护**: 只在 `stop_reason == "completed"` 且 fact hashes 未被 Phase 2 脏写时推进
- **Snapshot 回滚**: 解析/应用失败时 `snapshot.restore()` 回滚到一致状态
- **空响应处理**: LLM 返回空内容时提前返回 `phase1_empty_response`(2026-07-16 修复)

#### 3.2 FactStore(事实存储)
- 事实生命周期: `upsert` → `deprecated` → (可选 `auto_flip` 矛盾翻转)
- 7 个 feature flag 控制实验性子能力(semantic_retrieval、fact_graph、confidence_v2 等),默认全关

#### 3.3 NearlineMemory(近线记忆)
- 状态: Phase 1 脚手架,通过 `nearline_runtime_enabled` 门控
- 默认禁用(`enabled=False`),启用后提供分层记忆对象(memcell/episode/foresight/agent_case/profile/event)

### 4. Evolution 进化系统

**职责**: 模块包的暂存、验证、激活、回滚——让 Agent 能安全地自我进化

**核心实体**:
- `EvolutionModuleManager`([evolution/manager.py:71](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/evolution/manager.py#L71)) — 暂存管理(不激活不执行)
- `EvolutionModuleActivator`([evolution/activation.py](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/evolution/activation.py)) — 激活与回滚
- `EvolutionControlPlane`([evolution_control_plane.py:174](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/evolution_control_plane.py#L174)) — 治理层包装(按需构造,非常驻)
- `EvolutionLedger` — 事件账本(SQLite + JSONL fallback)

**模块生命周期**:
```
proposed ──→ staged ──→ verified ──→ active
                                         │
                                   rollback │
                                         ↓
                              rolled_back / dirty_rollback
```

**模块类型**:
- `skill` — 技能包
- `domain_pack` — 域包(经 DomainPackGovernanceService 治理)

**边界与异常**:
- `dirty_rollback`: 回滚失败,残留资源需人工清理,后续 stage 同 module_id 会被阻断
- **已知技术债**: TD-2026-003,模块级 force_cleanup/rollback 跳过 ConfirmationManager 审批

### 5. 工具系统(Tools)

**职责**: 工具注册、执行、安全管控

**核心实体**:
- `ToolContext` — 工具上下文(注册表 + 能力快照)
- `SafeActionExecutor`([action_runtime.py:206](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/action_runtime.py#L206)) — 安全执行器,集成 motor_queue
- `ActionBackend` / `SafetyGate` / `ConfirmationPromptBuilder` — Protocol 接口

**工具来源**:
- 内置工具(`register_default_tools`)
- 域包工具(`register_domain_tools`,smart_home/robot 等)
- 插件工具(`register_plugin_tools`)

**安全边界**:
- `CapabilitySnapshot` 控制工具能力范围
- `ConfirmationManager` 审批高危操作
- `automation_dry_run_only` / `real_execution_enabled` 控制执行模式

### 6. 通道系统(Channels)

**职责**: 多渠道接入(IM/语音/WebSocket等)

**核心实体**:
- `ChannelBootstrapAdapter`([channels/bootstrap.py](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/channels/bootstrap.py)) — 通道引导适配器
- `ChannelManager`([channels/manager.py](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/channels/manager.py)) — 通道管理器
- pkgutil 自动发现 + `discover_all()` 注册

**支持通道**: matrix / qq / msteams / mochat / telegram / feishu / slack / email / websocket / discord / wecom / dingtalk / weixin / whatsapp(均默认禁用,需显式启用)

### 7. 技能与学习系统

**职责**: 从重复行为中挖掘技能候选,Soar 在线块化

**核心实体**:
- `SkillBootstrapper`([skill_bootstrapper.py:204](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/skill_bootstrapper.py#L204)) — 技能挖掘
- `SkillBootstrapperScanner` — 扫描重复动作模式
- `SoarChunker`([soar_chunker.py](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/soar_chunker.py)) — Soar 块化,注入 SubagentManager

**配置**: `skill_bootstrapper_enabled`(默认 True)

**已知技术债**: TD-2026-002,`SoarChunker.tool_record_store=None`,工具序列提取降级

### 8. 元认知系统(MetaCognition)

**职责**: 从触发器产生结构化制品,不阻塞前台 turn

**核心实体**:
- `MetaCognitionReflector`([meta_cognition_reflector.py:107](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/meta_cognition_reflector.py#L107)) — 反思器
- `MetaCognitionRuntime` / `MetaCognitionRegulator` / `MetaCognitionCoordinator`
- `MetaCognitionFacade` — 门面

**Fallback 机制**:
- 优先取 `content`,空则取 `reasoning_content`(reasoning 模型可能把 JSON 放此字段)
- 三者均空时优雅降级为 `minimal_only`
- `finish_reason == "error"` 时返回 error 结果并记入 task_report

### 9. EPIC Motor 子系统

**职责**: 缓冲动作派发,认知节拍器驱动

**核心实体**:
- `EpicActionQueue`([epic_motor.py:79](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/epic_motor.py#L79)) — 使用 `threading.Lock`(同步+异步双向访问)
- `EpicMotorProcessor`([epic_motor.py:146](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/epic_motor.py#L146)) — 节拍器处理
- `TypedDeviceAction` — 含 `duration_ms` / `requires_parallel` 字段

**节拍**: `motor_tick_interval_ms=50`([cognitive_loop.py:27](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/cognitive_loop.py#L27))

**集成点**: `SafeActionExecutor._motor_queue` 注入,`asyncio.gather` 并发派发 + 异常隔离

### 10. 定时任务(Cron)

**职责**: 周期性后台任务调度

**核心实体**:
- `CronService`([cron/service.py](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/cron/service.py)) — 定时器
- `CronJob` — 任务定义(schedule: every / at / cron_expr)

**已知任务**:
- `dream` — 每 2h 触发记忆固化
- `heartbeat` — 心跳检查

**幂等性**: turn-scoped idempotency keys 防止单 turn 内重复创建 job

### 11. 配置系统(Config)

**职责**: 分层配置,profile 覆盖,doctor 校验

**核心实体**:
- `schema.py`(2240 行)— Pydantic 模型
- `profiles.py` — runtime profile 覆盖默认值(safe / household_safe)
- `doctor.py` — 配置校验

**分层**: `agents.defaults` / `gateway` / `tools` / `channels` / `security` / `storage` / `evolution` 等

### 12. 身份与安全

**职责**: 身份识别、配对认证、能力管控、操作审批

**核心实体**:
- `SpeakerRecognitionPlugin`(Protocol,需外部注入)
- `PairingConfig` — 配对认证(默认禁用)
- `CapabilitySnapshot` — 能力快照
- `ConfirmationManager` — 操作审批

---

## 二、关键业务规则与约束

1. **Agent 输出语言**由用户设置(BCP-47 tag)决定,通过 Jinja2 模板注入
2. **LogOut 组件**与 `onLogout` prop 必须保留(为多租户支持)
3. **内部事件**(如 `active_intent` nudge)必须以 `role: system` 注入,不得混入 `role: user`
4. **Cron 工具**必须实现 turn-scoped 幂等 key,防止单 turn 内重复创建 job
5. **工作记忆**必须包含 30 分钟过期检查,前缀 `[reminder]` 防止 confabulation 持久化
6. **工具循环**必须维护 turn-scoped 幂等 key 集合,阻断重复执行同一 tool call
7. **continuity_checkpoint_v1** 必须包含 `recent_turns_summary`,跨会话保留近期对话上下文

---

## 三、已知技术债索引

> 完整技术债文件位于 `techdebt/` 目录。此处仅索引,详情见对应文件。

| 编号 | 标题 | 优先级 | 状态 |
|------|------|--------|------|
| TD-2026-001 | Strangler Fig 子容器迁移停滞 Phase 0 | P2 | 已确认 |
| TD-2026-002 | SoarChunker.tool_record_store=None | P2 | 已确认 |
| TD-2026-003 | EvolutionModuleManager 跳过 ConfirmationManager 审批 | P1 | 已确认 |
| TD-2026-004 | AgentLoop._last_context_assembly 从未被写入 | P2 | 已确认 |
| TD-2026-005 | RobotG1Config schema 字段无实现读取 | P2 | 待评估 |
| TD-2026-006 | DesktopVoiceAssistant 未接入主线 | P2 | 待评估 |
| TD-2026-007 | AudioPlayback 类部分未接入 | P3 | 待评估 |
| TD-2026-008 | 多租户路径 InnerMonologueEngine 未接入 | P2 | 待评估 |
| TD-2026-009 | VoicePipeline 启动条件与 schema 偏差 | P3 | 待评估 |
| TD-2026-010 | 三处静默吞异常影响可观测性 | P0 | 待评估 |
| TD-2026-011 | LocalAwareness 配置镜像潜在违规 | P3 | 待评估 |

---

## 四、渐进式补全计划

本文件为初始版本,按规则 38.3 渐进式补全。后续每触及一个未被详细记录的子域,应顺手沉淀该子域认知。建议的后续补全顺序:

1. **session/ 会话管理** — Session 生命周期、状态持久化、多租户路径
2. **gateway/ 网关** — REST API、WebSocket、runtime_controls 聚合视图
3. **domain_packs/ 域包** — smart_home / robot 的能力边界与治理
4. **introspection/ 自省服务** — 状态查询、诊断接口
5. **identity/ 身份** — 说话人识别、resolver、身份绑定

每个子域补全时,应创建独立的 `systemmap/<subdomain>.md` 文件,并在本文件中添加索引。

---

## 五、陈旧度门槛

按规则 38.2,本文件超过 90 天未被重新确认时,Agent 在引用其内容做决策前必须先对照代码现状重新核验,不得直接采信文档描述。

任何变更了本文件中已记录业务规则/状态机的 P0/P1 层改动,必须在同一提交中同步更新本文件。
