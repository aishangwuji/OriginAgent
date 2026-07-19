# OriginAgent 业务域全景

> **last_verified**: 2026-07-18T22:45:00+08:00
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

**投递机制(2026-07-17 新增)**:
- Cron 通道是 inbound-only(`_inbound_only_channels = frozenset({"cron"})`),OutboundMessages 到 cron 通道被静默丢弃
- **Path B(最终响应投递)**:`on_cron_job` 从 resolved session(`job.payload.session_key or f"cron:{job.id}"`,2026-07-18 修复前硬编码为 `cron:{job.id}`)提取最后一条 assistant 消息,包装为 `OutboundMessage(channel=job.payload.channel, chat_id=job.payload.to)` 经 bus 投递。`MessageTool._sent_in_turn=True` 时跳过 Path B(Agent 已主动发送)。注意:denied_tools 扫描仍用 `cron:{job.id}` session(cron-job-specific 能力边界历史,与 turn 执行 session 解耦)
- **主动通知(delivery_target 机制)**:`on_cron_job` 调用 `MessageTool.set_delivery_target(channel, chat_id)` 设置 ContextVar 覆盖。Agent 在 turn 中途调用 `message(content="...")` 无显式 channel/chat_id 时,自动路由到 delivery_target(用户真实通道如 Telegram)。向 delivery_target 发送视为 same-target,豁免 `can_send_cross_target` 检查。仅允许向该特定目标发送,向其他通道发送仍被 `PolicyDeniedError` 拒绝
- **安全授权链**:delivery_target 来源是 `job.payload.to`(用户创建 cron job 时显式配置的投递目标),属受信任配置数据,非 Agent 可控输入

### 11. 配置系统(Config)

**职责**: 分层配置,profile 覆盖,doctor 校验

**核心实体**:
- `schema.py`(2240 行)— Pydantic 模型
- `profiles.py` — runtime profile 覆盖默认值(safe / household_safe)
- `doctor.py` — 配置校验

**分层**: `agents.defaults` / `gateway` / `tools` / `channels` / `security` / `storage` / `evolution` 等

### 12. 身份与安全

**职责**: 身份识别、配对认证、能力管控、操作审批、租户认领

**核心实体**:
- `SpeakerRecognitionPlugin`(Protocol,需外部注入)
- `PairingConfig` — 配对认证(默认禁用)
- `CapabilitySnapshot` — 能力快照
- `ConfirmationManager` — 操作审批
- `TenantRegistry` — 租户注册表,维护 `(channel, sender_id) → tenant_id` 映射
- `Tenant` — 租户实体,含 `bdi_enabled`、`claimable_by_pairing`、`workspace_dir`、`unified_session_key`
- `__pairing_pending__` — 合成 Tenant,表示已通过 `pairing approve` 但未 `claim` 的临时状态

**关键流程**:
```
未注册 sender 发消息
  → IdentityResolver 在 registry 中找不到匹配
  → 回退到 guest tenant (bdi_enabled=False)
  → 触发 pairing 流程,生成 8 位 code
  → Owner 通过 `/pairing approve <code>` 批准
  → sender 下次发消息时,被标记为 `__pairing_pending__` tenant
  → AgentLoop 检测到 `__pairing_pending__` 状态,首次发消息时提示可认领 tenant 列表
  → sender 发送 `/pairing claim <tenant_id>`
  → 校验: tenant 存在 + claimable_by_pairing=True + sender 已 approved + 未已绑定(幂等)
  → 调用 `TenantRegistry.claim_pairing(channel, sender_id, tenant_id)`
  → 触发 `AgentHost.post_claim_init` 回调
    → 调用 `_init_bdi_engine_for_tenant(tenant)` 懒加载 BDI 引擎
    → 迁移 `__pairing_pending__` workspace 的 session 文件到目标 tenant workspace
  → sender 下次发消息时被解析为正式 tenant,获得 BDI 能力
```

**关键状态机**(租户 onboarding):
```
[未注册/未配对] → [配对码生成] → [Owner 批准] → [__pairing_pending__] → [/pairing claim] → [正式 Tenant]
       ↓                  ↓              ↓                  ↓                  ↓
   guest 兜底         10 分钟过期     写入 approved     首次提示认领       BDI 启用 + workspace 迁移
```

**关键约束**:
- 仅 `claimable_by_pairing=True` 的 tenant 可通过配对认领(防止冒充 admin 等敏感身份)
- `claim` 命令幂等: 已绑定的 sender 再次 claim 会被拒绝
- 配对批准后,`hint_shown` 字段记录已提示的 sender,避免每次发消息都重复提示
- `__pairing_pending__` workspace 仅含 session 文件(无 BDI/DesireStore 数据,因为该 tenant `bdi_enabled=False`)

---

### 13. Prompt Cache 策略

**职责**: 最大化 LLM prompt 缓存命中率,降低 token 成本

**核心设计**:
- **稳定性梯度排序**: system prompt 的 user_content blocks 按"稳定在前、易变在后"排列,让 DeepSeek 自动前缀缓存命中稳定段
- **时间精度分层**: system prompt 中的 `current_time` 为分钟级(`HH:MM`),工具调用与 BDI 内部决策使用 `datetime.now()` 获取秒级精度
- **provider 适配**: Anthropic 通过显式 `cache_control` 标记注入;DeepSeek 通过 `supports_prompt_caching=True` 启用可观测性(无需显式标记,自动前缀命中)

**block 稳定性梯度**:
```
[稳定段] user_profile → archived_session_summary → closed_episode_summaries
         → retrieval_prewarm_seed
[中稳定段] working_memory_context → world_state_context → continuity_context
          → recovered_continuity_context
[易变段] recent_history → memory_retrieval → retrieval_session_search
         → runtime_context → internal_event → user_text
```

**可观测性**:
- `event.llm.response` 日志输出归一化 `cached_tokens` 字段
- 缓存命中率为 0 时输出 WARN 日志(5 分钟去重)
- DeepSeek/Anthropic 等支持 prompt cache 的 provider 都启用可观测性

**关键约束**:
- `runtime_context` 仍必须在 system prompt 内(LLM 需读取 actor/scope/trigger)
- 标签化结构(`<runtime_context>...</runtime_context>`)保持不变
- cron 工具与 BDI deliberation 不依赖 prompt 中的 `current_time`,通过 `datetime.now()` 独立获取

---

## 二、关键业务规则与约束

1. **Agent 输出语言**由用户设置(BCP-47 tag)决定,通过 Jinja2 模板注入
2. **LogOut 组件**与 `onLogout` prop 必须保留(为多租户支持)
3. **内部事件**(如 `active_intent` nudge)必须以 `role: system` 注入,不得混入 `role: user`
4. **Cron 工具**必须实现 turn-scoped 幂等 key,防止单 turn 内重复创建 job
5. **工作记忆**必须包含 30 分钟过期检查,前缀 `[reminder]` 防止 confabulation 持久化
6. **工具循环**必须维护 turn-scoped 幂等 key 集合,阻断重复执行同一 tool call
7. **continuity_checkpoint_v1** 必须包含 `recent_turns_summary`,跨会话保留近期对话上下文
8. **Cron 主动通知**通过 `MessageTool.set_delivery_target` 机制实现,delivery_target 来源必须是 `job.payload.to`(用户预设投递目标),Agent 不得自行指定任意通道。向非 delivery_target 的通道发送仍受 `can_send_cross_target` 约束
9. **行动-结果因果链**(三层架构, BDI/ACT-R/Soar/EPIC 具象体现):
   - **数据层 `action_trace`**: runner 自动捕获每次 `(tool_call, result, event)` 到 `session.metadata["_action_trace"]`(FIFO 50),Agent 无需主动调用。敏感参数自动脱敏(规则 18)
   - **评估层 `evaluate_action`**: Agent 主动调用工具评估 action_id 的 outcome(progressed/no_progress/regressed/blocked/uncertain)和 goal_alignment,持久化到 `session.metadata["_action_evaluations"]`(FIFO 50)
   - **状态机层 `task_state`**: EXPLORE→BLOCKED→WAITING→{RECOVERED|ABANDONED}→SATISFIED 状态机,持久化到 `session.metadata["_task_state"]`(history FIFO 20)。transition 可关联 action_id 和 evaluation_id 形成完整 `action→result→evaluation→state` 因果链
   - **上下文注入**: `<task_state>` block 在 `_task_state` 存在时条件注入(独立 block,非 recovered_continuity 内),包含最近 5 条 action_trace 摘要供 Agent 引用 action_id

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
| TD-2026-012 | BDI WorldStateWatcher 部分空转 + MetaCognition policy_denied 过滤器未集成 | P2 | 待评估 |
| TD-2026-013 | denied_tools reminder 与 note 工具功能重叠 | P3 | 待评估 |
| TD-2026-014 | MetaCognition 反思桥与新因果链三层架构功能重叠 | P3 | 待评估 |

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
