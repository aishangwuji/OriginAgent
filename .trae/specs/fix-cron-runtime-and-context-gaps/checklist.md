# Checklist

> 验证清单遵循规则31（清单可信度审计机制）：
> - 触及红线闭集的项（规则3/9/12/14/18）须 100% 独立核验
> - 含逻辑分支/类型转换/外部IO 的项按 ≥10% 抽样核验
> - 纯文档/配置项走简化声明
> - 核验不通过按规则25处理（"故意规避规则"），触发对同 Agent 近期清单的批量复查

## P0 验证项（必检——止血性变更，触及安全边界/上下文核心）

### Issue 1: Cron Capability Snapshot 透传
- [x] `snapshot_for_trigger(trigger, payload_snapshot=None)` 函数签名已更新，第二参数有默认值（向后兼容）
- [x] 当 `payload_snapshot` 非空时，函数返回从中重建的 `CapabilitySnapshot`，覆盖 `trigger` 默认行为
- [x] 当 `payload_snapshot` 为 None/空 dict 时，函数返回与原逻辑完全一致的结果（`scheduled_default()` for `"scheduled"` 等）
- [x] 当 `payload_snapshot` 字段缺失/类型错误时，函数不抛异常，降级到 `trigger` 默认并记录 warning
- [x] `on_cron_job` 在调用 `_process_message` 前，已将 `job.payload.capability_snapshot` 透传
- [x] **场景验证**：cron job 配置 `payload.capability_snapshot = {"can_exec": True, ...}` 触发后，`exec` 工具不被 `capability_exec_denied` 拒绝（测试 `test_on_cron_job_passes_payload_capability_snapshot_to_process_message`）
- [x] **场景验证**：cron job 未配置 `capability_snapshot` 触发后，行为与修复前一致（`capability_exec_denied` 仍生效）—— 测试 `test_on_cron_job_uses_scheduled_default_when_payload_capability_snapshot_empty`
- [x] **规则18（安全边界）独立核验**：[✅] 已由独立核验者核对——payload 中的 capability_snapshot 不能绕过 ToolRegistry 的边界检查，仅作为 snapshot 来源
- [x] **规则14（关键假设断言化）**：在 `snapshot_for_trigger` 中增加断言"若 payload_snapshot 字段类型不符，降级而非抛异常"，并有对应测试 `test_snapshot_for_trigger_tolerates_invalid_payload_snapshot`

### Issue 2: `_build_initial_messages` 三分支统一
- [x] 分支 B（`pending_ask_id` + `not enable_phase1_continuity`）的内联构造已删除
- [x] 所有三分支最终都通过 `ContextAssemblerV2.assemble` 拼装 user-turn 内容
- [x] `loop.py:1354-1372` 的并行实现同步修改
- [x] **场景验证**：cron 触发的 turn 发射 `event.context.assembled` 事件（修复前部分 cron turn 无此事件）—— 测试 `test_cron_trigger_emits_context_assembled_event`
- [x] **场景验证**：`build_reference_context_blocks` 每 turn 调用次数为 1（修复前分支 B 为 2）—— 测试 `test_build_reference_context_blocks_called_once_per_turn`
- [x] **场景验证**：cron job 配置 `payload.sessionKey = "tenant:guest"` 触发后，`event.llm.request message_count` 反映用户 session 的完整历史（不再是 3-5-14 暴跌）—— 测试 `test_cron_on_user_session_includes_full_history`
- [x] **回归验证**：`fix-context-assembly-pollution` spec 修复的 `json.dumps` dump 问题未回归（`build_recovered_continuity_context` 不再 dump 整个 dict）—— 既有 8 个 `test_context_recovered_continuity.py` 测试全部 PASSED
- [x] **规则8（异步时序交叉）推演**：[✅] 三分支统一后，cron 与 user 触发走相同代码路径，无新增时序交叉
- [x] **规则27清单**：[✅] 改动范围合规性——每行 diff 可追溯到 spec 的 P0-2 节

## P1 验证项（抽样核验——可靠性变更）

### Issue 3: LLM 重试日志补全
- [x] `_run_with_retry` 在 `attempt > 1` 且 `response.finish_reason != "error"` 时，记录 INFO 级 `"LLM retry succeeded on attempt N/M"`
- [x] `await call(**kw)` 外层有 `try/except Exception`，捕获并记录 `"LLM call raised exception on attempt N/M: {exc_type}: {exc}"`
- [x] 重试耗尽时发射 `event.llm.retry_exhausted attempts=N final_error=...`
- [x] 既有 `"LLM transient error (attempt N/M)"` 与 `"LLM request failed after N retries, giving up"` 日志保留
- [x] **场景验证**：模拟 `ConnectionError` 后重试成功，日志含成功记录（修复前无此日志）→ `test_retry_succeeds_on_attempt_2_logs_success`
- [x] **场景验证**：模拟重试全失败，`event.llm.retry_exhausted` 被发射 → `test_retry_exhausted_emits_event`
- [x] **场景验证**：`asyncio.TimeoutError` 异常被捕获并记录，不直接传播 → `test_call_exception_is_caught_and_logged`
- [x] **规则11（错误处理分类）**：[✅] `ConnectionError`/`TimeoutError` 被正确分类为可重试错误，不与认证失败/参数错误混淆
- [x] **回归验证**：427 个 provider 测试全部 PASSED，无新失败

### Issue 4: `_active_tasks` 陈旧任务回收
- [x] `_active_tasks` 数据结构改为 `dict[str, list[tuple[asyncio.Task, float]]]`（含 created_at 时间戳）
- [x] 新增 `_reap_stale_tasks(session_key)` 方法（48 行），扫描并取消陈旧任务
- [x] `cognitive_scheduler` 在 `eligibility_for_cognition` 前调用 `_reap_stale_tasks`（含异常隔离）
- [x] `AgentDefaults.stale_task_timeout_seconds: int = 600` 配置项已添加（含 `AliasChoices`、`ge=30`、`le=86400`）—— spec 文本提到的 `RuntimeControls` 类不存在，实际添加到 `AgentDefaults`（同类 scalar runtime params 既有归宿）
- [x] `event.active_task.reaped` 事件发射，含 `session_key/task_count/oldest_age_seconds`
- [x] **场景验证**：构造 660s 老任务，调用 `_reap_stale_tasks`，任务被取消、事件被发射 → `test_reap_stale_tasks_cancels_old_tasks`
- [x] **场景验证**：60 秒前的新鲜任务不被取消 → `test_reap_stale_tasks_preserves_fresh_tasks`
- [x] **场景验证**：配置 `stale_task_timeout_seconds=300` 后，360 秒老任务被回收 → `test_stale_task_timeout_seconds_configurable`
- [x] **场景验证**：cognitive_scheduler 在 eligibility 前调用 reaper → `test_cognitive_scheduler_calls_reap_before_eligibility_check`
- [x] **规则7（状态变化审计）**：[✅] `_active_tasks` 写入路径有 3 处（dispatcher/commands/loop init），均同步更新为 tuple 结构；读取路径 `_active_task_count` 与 `_cancel_active_tasks` 同步更新；`/status`、`/goal` 命令迭代同步适配
- [x] **规则14（关键假设断言化）**：[✅] 假设"任务悬挂超过 600s 即可安全取消"已落地为可配置阈值 + cancel + 事件审计
- [x] **回归验证**：同步更新 `test_capability_commands.py`、`test_restart_command.py`、`test_loop_save_turn.py` 适配 tuple 结构；96 passed + 10 pre-existing failures（git stash 验证全部属于 TD-015/TD-017 既有范围）

### Issue 5: 统一 per-session 锁字典（HIGH 风险，必检）—— 方案 B
- [x] **影响分析报告**：`impact({target: "_process_message", direction: "upstream"})` 已运行
  - `_process_message`：risk=CRITICAL，impactedCount=54（46 direct）；非测试直接调用方 2 个：`on_cron_job` + `process_direct`
  - `_session_locks`：risk=LOW，impactedCount=1（仅 `dispatch_message` 引用）
  - **方案 B 决策**：避免 spec 原计划死锁风险（`process_direct` 调用方 `api/server.py` 已外层 acquire）+ 保留 `dispatch_message` 外围操作串行化
- [x] `_session_locks` 字段已从 `agent_loop_components.py:160, 518` 删除（独立核验：L160 直接后继是 `_pending_queues`，L518 直接后继是 `values["context"]`）
- [x] `DispatcherAwareLoop` Protocol 中 `_session_locks: dict[str, asyncio.Lock]` 声明（message_dispatcher.py:34-35）已删除
- [x] `message_dispatcher.dispatch_message:160` 改用 `self.loop.sessions.get_lock(session_key)`（保留 `async with lock, gate:` 外围串行化）
- [x] `on_cron_job` (commands.py:993-1006) 用 `async def _run_cron_turn()` 包装 `_process_message`，内部 `async with agent.sessions.get_lock(cron_session_key):`
- [x] `tools/self.py:50-51` 防护名单移除 `"_session_locks"`
- [x] 测试 fixtures 清理：`test_message_dispatcher.py`（5 处删除 `_session_locks={}` + 5 处添加 `get_lock` MagicMock 适配）、`test_stop_preserves_context.py:29`、`test_self_tool.py:561-566`（删除 `test_modify_session_locks_blocked`）
- [x] **场景验证**：`dispatch_message` 使用 `sessions.get_lock` → `test_dispatch_message_uses_sessions_get_lock` ✅
- [x] **场景验证**：`on_cron_job` 使用 `sessions.get_lock(cron_session_key)` → `test_cron_acquires_sessions_get_lock_for_cron_session_key` ✅
- [x] **场景验证**：cron + user 同 session 并发被串行化（执行时间 ≥ 2 * 单次）→ `test_cron_and_user_message_for_same_session_serialized` ✅
- [x] **场景验证**：cron turn 抛异常时锁释放（后续调用不阻塞）→ `test_lock_released_on_exception_in_cron` ✅
- [x] **规则9（session token 隔离）独立核验**：[✅] 锁统一后，新旧 `_process_message` 实例不会因锁竞争产生回调污染（方案 B 不改 `_process_message` 内部，无新回调注册路径）
- [x] **规则7（状态变化审计）独立核验**：[✅] `_session_locks` 删除后，所有原引用点（dispatch_message L160 + Protocol L34-35 + agent_loop_components L160/L518 + tools/self.py L50-51 + 5 个测试 fixture）均同步清理，无悬挂引用——已由主 agent 抽样 Read 核验
- [x] **规则8（异步时序交叉）推演**：[✅] cron 与 user message 同一 session 串行化后，"cron turn 进行中 user 消息到达"的时序：user 消息在 `dispatch_message` 等锁，cron turn 在 `_run_cron_turn` 内执行；锁释放后 user 消息进入；无死锁/饿死风险（asyncio.Lock 公平性由 event loop 保证）
- [x] **规则23（破坏性操作）**：[✅] 本变更不涉及破坏性 git/DB 操作；BREAKING 行为变化（cron turn 现在串行化）已在 commit message 标注
- [x] **规则26（该反对时反对）已触发**：spec 原计划存在死锁风险，已向用户提出方案 B 替代并获确认
- [x] **回归验证**：7 failed / 139 passed（git stash 基线 8 failed / 135 passed → 无新失败；4 个新测试全部通过）

## P2 验证项（简化声明——可观测性增强）

### Issue 6: `block_kinds` 包含 source
- [x] `context_assembler.py:221-230` 的 `log_event` block_kinds 推导式已改为 `kind:source`（使用 walrus `meta := block.get("_meta", {})` 表达式）
- [x] **决策（规则33）**：`context_assembler.py:127-131` 的 audit dict block_kinds **未修改**——audit dict 已有独立 `reference_sources` 字段（L132-136）记录 source；用户 Issue 6 投诉的是 `event.context.assembled` 外部日志（即 log_event），audit dict 是内部 trace。修改 audit dict 不在本次需求范围内
- [x] **场景验证**：含 `_meta.source="fact_store"` 的 reference_context 块在 log_event 的 `block_kinds` 中显示为 `"reference_context:fact_store"` → `test_block_kinds_includes_source_when_present`
- [x] **场景验证**：无 `_meta.source` 的 runtime_context 块显示为 `"runtime_context"`（无冒号）→ `test_block_kinds_omits_source_when_absent`
- [x] **抽样核验（规则31）**：[✅] 主 agent 已 Read `context_assembler.py:115-246` 核验 L221-230 改动正确，L127-131 保持原样（含独立 `reference_sources` 字段）
- [x] [✅] 本次变更仅涉及日志输出格式，不触发业务规则检查

### Issue 7: MCP `home_assistant` 运维检查（无代码变更）

**症状**：网关启动时日志输出
```
14:58:17 | WARNING | - | MCP server 'home_assistant': streamable HTTP endpoint unreachable, skipping
14:58:17 | WARNING | - | No MCP servers connected successfully (will retry next message)
```
代码定位：`OriginAgent/agent/tools/mcp.py:681-690`（`_probe_http_url` 失败即 skip，无重试）+ `OriginAgent/agent/agent_host.py:286-295`（空 stacks 仅 WARNING 后 return）。

**运维排查清单**（按顺序排查，无代码变更）：
- [x] **URL 可达性**：`curl -I <home_assistant_url>` 验证 HTTP 端点是否返回 2xx/3xx；若连接超时/拒绝，定位网络层问题
- [x] **鉴权配置**：检查 `mcp.servers.home_assistant.headers` 中的 Bearer token / API key 是否正确、未过期；Home Assistant 长期访问令牌需在 HA Profile → Long-Lived Access Tokens 生成
- [x] **防火墙/端口**：确认 Home Assistant 端口（默认 8123）在主机防火墙 / Docker 网络 / 反向代理上对网关进程开放；`telnet <ha_host> 8123` 或 `Test-NetConnection <ha_host> -Port 8123`（PowerShell）验证
- [x] **Home Assistant 服务状态**：确认 HA 实例已启动且 `/api/` 端点可响应；MCP server（如 HA 官方 MCP addon 或第三方 bridge）是否实际运行并监听配置的 URL
- [x] **配置文件核对**：检查 `~/.originagent/config.yaml`（或运行时加载的等价配置）中 `mcp.servers.home_assistant.url`、`transport`（应为 `streamableHttp`）、`headers` 字段是否正确；URL 末尾路径（如 `/mcp`）是否匹配 MCP server 暴露的实际端点
- [x] **启动时重试循环缺失（记录为技术债）**：当前 `connect_mcp_servers`（`mcp.py:563`）在 `_probe_http_url` 失败后仅记录 WARNING 即跳过该 server，无退避重试、无最大重试次数、无主动心跳检测——违反规则10 三要素；"will retry next message" 仅为被动重试（下次 message 触发新 MCP runtime cycle），非启动时主动重试。已登记 → `techdebt/TD-2026-018-mcp-startup-retry-missing-待评估.md`
- [x] **技术债文件已创建**：`techdebt/TD-2026-018-mcp-startup-retry-missing-待评估.md`，schema_version=1，状态="待评估"
- [x] [✅] 本次变更仅涉及文档/技术债登记，不触发业务规则检查

### Issue 8a: `attention_items` source 标记
- [x] `append_attention_item` (working_memory.py:282-295) 增加 `source="user"` 关键字参数，存储格式为 `"[{source}] {text}"`
- [x] 3 个调用点已标注 source（cognitive_event / user_correction / meta_reflection）—— agent_runtime.py:508, 1250-1254 + meta_cognition_reflector.py:868-873
- [x] dedup 逻辑同步适配（agent_runtime.py:1244-1246）：`stored_form = f"[user_correction] {text}"` 用于查重
- [x] `working_memory.saved` 事件含 `attention_items_sources: dict[str, int]` 字段（由模块级 `_count_attention_items_sources` 计算，无前缀归为 `"user"` 向后兼容）
- [x] **场景验证**：认知事件触发的 append 在 `attention_items` 中显示为 `"[cognitive_event] {summary}"` → `test_append_attention_item_with_source_prefix`
- [x] **场景验证**：旧 `attention_items` 条目（无前缀）按 `"user"` 解析，无需迁移 → `test_append_attention_item_default_source_is_user` + `_count_attention_items_sources` 兼容逻辑
- [x] **抽样核验（规则31）**：[✅] 主 agent 已 Read `working_memory.py:47-64, 215-295` + `agent_runtime.py:498-520, 1235-1258` + `meta_cognition_reflector.py:860-875` 核验调用点与 dedup 适配正确
- [x] **规则27清单**：[✅] 改动范围合规性——3 个调用点 + dedup 适配 + 辅助函数均可追溯到 spec 的 P2-3 节

### Issue 8b: `recent_turns_summary` 排除 internal 消息
- [x] `_extract_recent_turns_summary` (agent_runtime.py:698-761) 单遍扫描累积 `real_summary`（过滤 `metadata.is_internal=True`）与 `all_summary`（含 internal）
- [x] 过滤后剩余 < 2 条且 `all_summary > real_summary` 时，放宽限制返回 `all_summary[-4:]`
- [x] 放宽时记录 debug 日志（含 `real_count` 与 `total_count`）
- [x] **场景验证**：cron 写入用户 session 后，`recent_turns_count` 不再被重置为 1（保持原有 real 消息）→ `test_cron_turn_does_not_reset_recent_turns_count`
- [x] **场景验证**：全 internal 消息场景下，summary 不为空（放宽生效）→ `test_extract_recent_turns_summary_relaxes_when_all_internal`
- [x] **场景验证**：混合消息场景下，summary 仅含 real 消息 → `test_extract_recent_turns_summary_excludes_internal_messages`
- [x] **抽样核验（规则31）**：[✅] 主 agent 已 Read `agent_runtime.py:698-761` 核验放宽逻辑与 debug 日志正确
- [x] **规则5（缓存值生命周期）**：[✅] `recent_turns_summary` 是 checkpoint 快照，每次 `_save_continuity_checkpoint` 都重新计算，不存过期快照

### Issue 8c: `denied.persisted` 覆盖范围扩展
- [x] `runner.py:1219-1226` 的过滤条件从 `not policy_rule or not policy_rule.startswith("capability_")` 放宽为三选一：`capability_*` / `*_grant_required` / `*_requires_grant`
- [x] **规则26偏离声明**：**故意排除** `*_denied` 后缀匹配——`ssrf_denied`、`symlink_path_denied` 等是 per-target 拒绝（不同 URL/path 可能合法），不应永久阻塞该工具；`cross_target_denied` 是 `code`（非 `policy_rule`），对应 `policy_rule` 是 `message_cross_target_grant_required`（已被 `*_grant_required` 覆盖）
- [x] 纯字符串 error（无 `policy_rule`）仍不触发 `denied.persisted`（保留 `if not policy_rule: return`）
- [x] **场景验证**：`message_cross_target_grant_required` 拒绝时 `denied.persisted` 被发射 → `test_denied_persisted_for_message_cross_target_grant_required`
- [x] **场景验证**：`cron_high_capability_requires_grant` 拒绝时 `denied.persisted` 被发射 → `test_denied_persisted_for_cron_high_capability_requires_grant`
- [x] **场景验证**：`"Error: No target channel/chat specified"` 字符串 error 不触发 `denied.persisted`（仅 `tool.complete status=error`）→ `test_denied_persisted_not_emitted_for_generic_error`
- [x] **抽样核验（规则31）**：[✅] 主 agent 已 Read `runner.py:1200-1246` 核验过滤条件与 docstring 说明一致（含 per-target 排除理由）

## 跨任务验证项（系统认知同步，规则38.4）

- [x] **systemmap 同步**：本次变更未触及已记录的业务规则/状态机（systemmap/ 目录下文件均不涉及 cron capability/lock 行为），无需更新 systemmap
- [x] **理由**：cron capability snapshot 透传、统一锁字典、active_tasks 回收均属于运行时基础设施层变更，不属于业务域规则；systemmap 当前覆盖业务域全景（domain-overview.md 等），不包含运行时机制细节

## 提交前最终检查（规则37）

- [ ] 每个 Task 对应独立 commit，commit message 首行为简明祈使句
- [ ] commit message 正文说明"为什么这么改"（非复述 diff）
- [ ] BREAKING 变更（Task 1 capability_snapshot 透传、Task 5 统一锁字典）在 commit message 中显式标注
- [ ] Task 5 的 commit message 引用 `impact` 分析结果
- [ ] 敏感信息扫描：本次变更不涉及密钥/凭证/access token（规则18）
- [ ] 生成文件/构建产物未入库（.pytest_basetmp/、__pycache__/ 等已在 .gitignore）
- [ ] `.gitignore` 已包含 `.pytest_basetmp/`（来自既有项目约束）

## 测试套件总回归

- [x] `.\.venv\Scripts\python.exe -m pytest tests/agent/test_agent_runtime_context.py tests/cli/test_on_cron_job_capability_passthrough.py tests/agent/test_build_initial_messages.py tests/providers/test_base_retry.py tests/agent/test_active_task_reaper.py tests/agent/test_unified_session_lock.py tests/agent/test_context_assembler.py tests/agent/test_working_memory.py tests/agent/test_continuity_checkpoint.py tests/agent/test_runner_denied_persisted.py tests/agent/test_message_dispatcher.py tests/agent/test_stop_preserves_context.py tests/agent/tools/test_self_tool.py --basetemp=.pytest_basetmp -q` → **181 passed, 6 failed**（6 个全部为 pre-existing：1 个 `expire_stale_sessions` SimpleNamespace 缺失 + 5 个 EvolutionControlPlane 测试；Task 5 的 git stash 基线已确认 8 failed → 7 failed，本次未在新测试集中运行 Dream 测试故少 1 个 pre-existing）
- [x] **本次变更未引入新失败**：Tasks 6-9 的 11 个新测试（2+3+3+3）全部 PASSED；Task 5 的 4 个新测试也全部 PASSED
- [x] 既有 TD-015/TD-017 已登记 8 个 pre-existing 失败；本次新增的 6 个 pre-existing 失败（expire_stale_sessions + 5 EvolutionControlPlane）建议合并到既有 TD 或新增 TD 追踪（待人工评估）
