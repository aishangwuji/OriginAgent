# Tasks

## Phase 0: 前置已完成项（验证存在，不重做）

- [x] Task 0.1: 修复 working_memory.py load() 的 owner_id 覆盖逻辑（P0 根因 #1）
  - 已落地于 [working_memory.py:147-162](file:///d:\Demo\OpenHome\OriginAgentclient\OriginAgent\agent\working_memory.py#L147-L162)
  - 5 个测试用例已通过（tests/agent/test_working_memory.py）
- [x] Task 0.2: 补充三处可观测性日志（P1）
  - message.py 路径 A 发送日志
  - agent_runtime.py 路径 B 抑制日志
  - commands.py cron Path B delivery 日志

## Phase 1: Prompt Cache 命中率修复（P0）

- [x] Task 1.1: 修改 current_time 精度为分钟级（system prompt 部分）
  - [x] SubTask 1.1.1: 在 `OriginAgent/agent/context.py` 的 `build_runtime_context_text` 中，将 `current_time` 截断到 `HH:MM`
  - [x] SubTask 1.1.2: 不保留秒级字段在 prompt 中（spec 要求秒级字段不进入 system prompt），改为新增 `time_precision` 注释字段
  - [x] SubTask 1.1.3: 验证 BDI deliberation 与 cron 工具不依赖 prompt 中的秒级时间（核验通过，均通过 `datetime.now()` 获取）
  - [x] SubTask 1.1.4: 新增 4 个单元测试全部通过

- [x] Task 1.2: 调整 block 顺序为稳定性梯度
  - [x] SubTask 1.2.1: 在 `ContextAssemblerV2.assemble` 中重排 block 顺序（实际文件名为 `context_assembler.py`）
  - [x] SubTask 1.2.2: 顺序符合目标梯度（user_profile → archived_session_summary → closed_episode_summaries → prewarm_seed → working_memory → world_state → continuity → recovered_continuity → recent_history → memory_retrieval → session_search → runtime_context → user_text）
  - [x] SubTask 1.2.3: gitnexus 不可用，改用静态分析确认爆炸半径 LOW
  - [x] SubTask 1.2.4: 新增 2 个测试（`test_block_order_stable_first` + `test_runtime_context_not_first`）全部通过
  - [x] SubTask 1.2.5: 回归测试 9 个全部通过

- [x] Task 1.3: DeepSeek 缓存控制分支接入
  - [x] SubTask 1.3.1: 在 `OriginAgent/providers/registry.py` L288-302 设置 DeepSeek `supports_prompt_caching=True`
  - [x] SubTask 1.3.2: 修改 `openai_compat_provider.py:504-512` 区分注入与可观测性，DeepSeek 不注入 cache_control
  - [x] SubTask 1.3.3: 在 `agent/runner.py:868-882` 的 `event.llm.response` 日志中输出 `cached_tokens` 字段
  - [x] SubTask 1.3.4: 在 `agent/runner.py:883-902` + L86-91 实现 WARN 日志（5 分钟去重）
  - [x] SubTask 1.3.5: 新增 3 个测试全部通过

- [x] Task 1.4: 缓存命中率监控端到端验证
  - [x] SubTask 1.4.1: `test_same_minute_produces_identical_prefix` 通过
  - [x] SubTask 1.4.2: `test_different_minute_only_runtime_context_changes` 通过
  - [x] SubTask 1.4.3: `test_cached_tokens_in_llm_response_log` 通过 + `tests/providers/test_cached_tokens.py` 14 个全部通过

## Phase 2: 租户认领管道打通（P1）

- [x] Task 2.1: 新增 `claimable_by_pairing` 字段
  - [x] SubTask 2.1.1: 在 `TenantConfig` schema.py:1647 添加 `claimable_by_pairing: bool = False`
  - [x] SubTask 2.1.2: 在 `Tenant` dataclass tenant.py:26-27 添加对应字段
  - [x] SubTask 2.1.3: 在 `TenantRegistry._register_from_config` tenant.py:63 传递该字段
  - [x] SubTask 2.1.4: 3 个测试全部通过

- [x] Task 2.2: 接入 `/pairing claim <tenant_id>` 命令
  - [x] SubTask 2.2.1: 在 `pairing/store.py:handle_pairing_command` 中新增 `claim` 子命令分支（签名扩展为 keyword-only 参数 sender_id/tenant_registry）
  - [x] SubTask 2.2.2: 调用 `TenantRegistry.claim_pairing(channel, sender_id, tenant_id)`
  - [x] SubTask 2.2.3: 校验 tenant_id 存在且 `claimable_by_pairing=True`
  - [x] SubTask 2.2.4: 校验 sender 已通过 `pairing approve`
  - [x] SubTask 2.2.5: 校验 sender 未已绑定到其他 tenant（幂等性，关键决策：幂等检查先于授权检查，避免预绑定 sender 收到误导性 "not approved" 错误）
  - [x] SubTask 2.2.6: 11 个测试全部通过（合法认领 + 未授权 + 未批准 + 重复认领 + 缺参数等场景）
  - [x] SubTask 2.2.7: 同步修改 `command/builtin.py:_pairing_command_allowed` 允许 claim 从任何通道执行（实际授权在 handle_pairing_command 内强制）

- [x] Task 2.3: 触发 BDI 懒加载与 workspace 迁移
  - [x] SubTask 2.3.1: 在 `agent_host.py:458-554` 新增 `post_claim_init(channel, sender_id, tenant)` 方法，调用 `_init_bdi_engine_for_tenant(tenant)`
  - [x] SubTask 2.3.2: 迁移 `__pairing_pending__` workspace 的 session 文件（`_migrate_pairing_session_file`）
  - [x] SubTask 2.3.3: 不迁移 DesireStore/CronObservationStore（因为 `__pairing_pending__` tenant `bdi_enabled=False`，从未写入这些数据）
  - [x] SubTask 2.3.4: 通过 `on_claim_success` callback 解耦 pairing 层与 agent 层（规则16 领域隔离）
  - [x] SubTask 2.3.5: 10 个测试全部通过（含端到端 claim → callback → BDI 初始化 + session 迁移）

- [x] Task 2.4: `__pairing_pending__` 状态首次进入提示
  - [x] SubTask 2.4.1: 在 `agent/loop.py:AgentLoop._dispatch` 中检测 `tenant.tenant_id == "__pairing_pending__"` 时调用 `_maybe_show_claim_hint`
  - [x] SubTask 2.4.2: 提示内容包含可认领 tenant 列表（仅 `claimable_by_pairing=True`，规则18 防止泄露）
  - [x] SubTask 2.4.3: 提示格式为 `/pairing claim <tenant_id>   # <display_name>`
  - [x] SubTask 2.4.4: 在 `pairing.json` 新增 `hint_shown` 字段持久化去重状态，同一 sender 只提示一次
  - [x] SubTask 2.4.5: 9 个测试全部通过

## Phase 3: 非极客友好注册辅助（P2，已延后）

> **状态**：已延后，登记为技术债 TD-2026-020-phase3-non-geek-onboarding-deferred-待评估.md

- [ ] Task 3.1: CLI 安装向导添加家庭成员步骤 → 延后到下个迭代
- [ ] Task 3.2: tenants.yaml 运行时增量写入 → 延后到下个迭代
- [ ] Task 3.3: WebUI pending 配对管理 → 延后到下个迭代

# Task Dependencies

- Task 1.x 系列相互独立，可并行
- Task 1.1 与 1.2 建议先做（最小化改动，立竿见影）
- Task 1.3 依赖 Task 1.1 完成（缓存控制需基于稳定 prompt 前缀）
- Task 1.4 依赖 Task 1.1-1.3 全部完成
- Task 2.1 是 Phase 2 的前置（其他 Task 依赖 claimable_by_pairing 字段）
- Task 2.2 依赖 Task 2.1
- Task 2.3 依赖 Task 2.2（claim 成功后才触发 BDI 与 workspace 迁移）
- Task 2.4 依赖 Task 2.2（提示中需引用 claim 命令）
- Task 3.x 全系列依赖 Phase 2 完成
- Task 3.x 可延后到下个迭代
