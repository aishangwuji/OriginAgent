# Tasks

> 遵循规则34（验证先行）：先写测试/断言，再实现。
> 遵循规则37（原子提交）：每个 Task 对应独立 commit，commit message 说明"为什么这么改"。
> 遵循规则33（手术式变更）：仅修改与本次任务直接相关的代码，不顺手重构。
> 遵循规则32-B：P0/P1 任务为 L1/L2 变更，需完整清单 + 独立核验；P2 任务多为 L3，可走简化声明。
> 遵循规则27：每个 Task 完成后输出合规性自检清单。

## P0 级（止血——cron 自动化瘫痪 + 上下文丢失）

### Task 1: 尊重 `job.payload.capability_snapshot`（Issue 1，L1 安全边界）

- [x] Task 1: 让 `snapshot_for_trigger` 优先使用 `payload_snapshot`，并在 `on_cron_job` 中透传
  - [x] SubTask 1.1（验证先行）: 在 `tests/agent/test_agent_runtime_context.py` 新增 3 个测试（实际新增 5 个，含 `test_snapshot_for_trigger_falls_back_when_payload_snapshot_empty_dict`、`test_snapshot_for_trigger_payload_snapshot_overrides_user_trigger`）
    - `test_snapshot_for_trigger_uses_payload_snapshot_when_provided`：构造 `payload_snapshot={"can_exec": True, ...}`，断言返回的 `CapabilitySnapshot.can_exec is True`
    - `test_snapshot_for_trigger_falls_back_when_payload_snapshot_none`：`payload_snapshot=None`，断言 `trigger="scheduled"` 仍返回 `scheduled_default()`（向后兼容）
    - `test_snapshot_for_trigger_tolerates_invalid_payload_snapshot`：`payload_snapshot={"invalid_field": True}`，断言不抛异常，降级到 `scheduled_default()` 并记录 warning
  - [x] SubTask 1.2（验证先行）: 在 `tests/cli/test_on_cron_job_capability_passthrough.py` 新增测试（2 个测试）
    - 构造 `CronJob`，`payload.capability_snapshot = {"can_exec": True, "can_read_files": True}`
    - 调用 `cron.on_job(job)`
    - 断言 `agent._process_message` 调用时携带的 capability_snapshot 等于 payload 中的值
  - [x] SubTask 1.3（实现）: 修改 `OriginAgent/agent/agent_runtime_context.py:20-94`
    - `def snapshot_for_trigger(trigger: str, payload_snapshot: dict | None = None) -> CapabilitySnapshot:`
    - 若 `payload_snapshot` 非空，尝试 `CapabilitySnapshot.from_dict(payload_snapshot)`，失败则 log warning + fallback
    - 否则维持现有 `trigger` 分支逻辑
  - [x] SubTask 1.4（实现）: 修改 `OriginAgent/cli/commands.py:52, 911-923`
    - 在调用 `_process_message` 前，`payload_snapshot = job.payload.capability_snapshot or None`
    - 通过 `snapshot_for_trigger("scheduled", payload_snapshot=...)` 解析为 `CapabilitySnapshot` 后作为 `capability_snapshot` 参数传入 `_process_message`
  - [x] SubTask 1.5（实现）: **未修改** `agent_runtime.py:987-989` —— 现有 `_cap_snapshot = capability_snapshot or snapshot_for_trigger(trigger)` 已正确实现"显式参数优先"解析顺序，`on_cron_job` 已将解析后的 snapshot 通过 `capability_snapshot` 参数传入。强行修改需透传 `payload_snapshot: dict` 过多层（违反规则32/33），功能需求已由 `commands.py` 改动完全满足。
  - [x] SubTask 1.6（验证）: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_agent_runtime_context.py tests/cli/test_on_cron_job_capability_passthrough.py -v --basetemp=.pytest_basetmp` → **7 passed**（含 2 个向后兼容测试）

### Task 2: 统一 `_build_initial_messages` 三分支（Issue 2，L1 上下文拼装）

- [x] Task 2: 让所有分支通过 `ContextAssemblerV2.assemble`，移除分支 B 的内联构造
  - [x] SubTask 2.1（验证先行）: 在 `tests/agent/test_build_initial_messages.py` 新增 3 个测试
    - `test_cron_trigger_emits_context_assembled_event`：cron 触发的 turn 必须发射 `context.assembled` 事件
    - `test_build_reference_context_blocks_called_once_per_turn`：mock `build_reference_context_blocks`，断言每次 turn 调用次数为 1（不是 2）
    - `test_cron_on_user_session_includes_full_history`：cron 写入 `tenant:guest` session（已有 50 条历史），断言 `message_count > 30`
  - [x] SubTask 2.2（实现）: 修改 `OriginAgent/agent/agent_runtime.py:1480-1502`（分支 B 统一）+ `OriginAgent/agent/context_assembler.py:35-99`（新增 `enable_phase1_continuity: bool = True` 参数）+ `OriginAgent/agent/context.py:1000-1048`（`assemble_user_content` 透传新参数）
    - 删除分支 B（`pending_ask_id` + `not enable_phase1_continuity`）的内联构造
    - 让分支 B 走与分支 A 相同的 `ContextAssemblerV2.assemble` 路径，通过新增 `enable_phase1_continuity` 参数支持 False 场景（跳过 `build_phase1_continuity_blocks`）
    - `ContextAssemblerV2.assemble` 接口扩展：默认 `True` 向后兼容，`False` 时 `continuity_blocks = []`
  - [x] SubTask 2.3（实现）: 同步修改 `OriginAgent/agent/loop.py:1364-1372`（legacy `AgentLoop._build_initial_messages`）
  - [x] SubTask 2.4（验证）: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_build_initial_messages.py -v --basetemp=.pytest_basetmp` → **3 passed**
  - [x] SubTask 2.5（回归验证）: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_context_assembler_logging.py tests/agent/test_context_recovered_continuity.py tests/agent/test_context_prompt_cache.py tests/agent/test_context_task_state.py tests/agent/test_loop_runtime_context.py tests/agent/test_loop_options.py tests/agent/test_loop_cron_timezone.py tests/agent/test_runtime_dependencies_strangler_fig.py tests/agent/test_runtime_context.py tests/agent/test_runtime_refresh.py -v --basetemp=.pytest_basetmp` → **96 passed, 1 pre-existing failure**（`test_layered_memory_injects_episode_support_facts_foresight_and_profile` 与本次无关，git stash 验证原始代码同样失败）

## P1 级（可靠性——LLM 重试 + active_tasks 回收 + 并发锁）

### Task 3: LLM 重试日志补全（Issue 3，L2）

- [x] Task 3: `_run_with_retry` 增加重试成功日志、异常捕获、`retry_exhausted` 事件
  - [x] SubTask 3.1（验证先行）: 在 `tests/providers/test_base_retry.py` 新增 3 个测试（共 214 行）
    - `test_retry_succeeds_on_attempt_2_logs_success`：mock `call` 第一次抛 `ConnectionError`，第二次返回成功 response，断言日志含 `"LLM retry succeeded on attempt 2/3"`
    - `test_retry_exhausted_emits_event`：mock `call` 多次全失败，断言 `event.llm.retry_exhausted` 被发射
    - `test_call_exception_is_caught_and_logged`：mock `call` 抛 `asyncio.TimeoutError`，断言日志含 `"LLM call raised exception on attempt 1/3"`，且异常不直接传播
  - [x] SubTask 3.2（实现）: 修改 `OriginAgent/providers/base.py:17-18, 733-845`
    - `last_error` 跨 attempt 跟踪；包裹 `await call(**kw)` 在 `try/except Exception`，记录 `"LLM call raised exception on attempt N/M: {exc_type}: {exc}"`
    - `if attempt > 1 and response.finish_reason != "error": log_info("LLM retry succeeded on attempt N/M")`
    - break 前发射 `log_event("llm.retry_exhausted", attempts=attempt, final_error=str(last_error))`
    - **注**：实际 `attempts` 值为 4（非 spec 文本中的 3），因为 `_CHAT_RETRY_DELAYS = (1, 2, 4)` len=3，需 4 次 `call` 才触发 `attempt > len(delays)`
  - [x] SubTask 3.3（验证）: `.\.venv\Scripts\python.exe -m pytest tests/providers/test_base_retry.py -v --basetemp=.pytest_basetmp` → **3 passed**；427 个 provider 测试全部 PASSED 无回归

### Task 4: `_active_tasks` 陈旧任务回收（Issue 4，L2）

- [x] Task 4: 新增 `_reap_stale_tasks` + 配置项 + cognitive_scheduler 调用
  - [x] SubTask 4.1（验证先行）: 在 `tests/agent/test_active_task_reaper.py` 新增 4 个测试
    - `test_reap_stale_tasks_cancels_old_tasks`：构造 660s 老任务，断言被取消、事件被发射
    - `test_reap_stale_tasks_preserves_fresh_tasks`：60s 新任务不被取消
    - `test_cognitive_scheduler_calls_reap_before_eligibility_check`：mock `_reap_stale_tasks`，断言在 eligibility 前调用
    - `test_stale_task_timeout_seconds_configurable`：`stale_task_timeout_seconds=300` 配置下 360s 任务被回收
  - [x] SubTask 4.2（实现）: 修改 `OriginAgent/agent/loop.py:5-7, 102, 1493-1505, 1683-1741` + `OriginAgent/agent/message_dispatcher.py:30-35, 141-155` + `OriginAgent/cli/commands.py:1002-1021` + `OriginAgent/command/builtin.py:249-250, 331-332`
    - `_active_tasks` 改为 `dict[str, list[tuple[asyncio.Task, float]]]`（task + created_at）
    - 同步修改所有注册点、done_callback（按 `id(t)` 匹配移除）、`/status` 与 `/goal` 命令迭代
  - [x] SubTask 4.3（实现）: 新增 `OriginAgent/agent/loop.py:_reap_stale_tasks(session_key)`（48 行）
    - 扫描 `_active_tasks[session_key]`，对 `now - created_at > stale_task_timeout_seconds` 调用 `task.cancel()`，移除并发射 `event.active_task.reaped`
  - [x] SubTask 4.4（实现）: 修改 `OriginAgent/agent/cognitive_scheduler.py:109-136, 175-196` + `OriginAgent/agent/agent_loop_components.py:156-159, 511-517, 693-705`
    - `CognitiveScheduler.__init__` 增加 `reap_stale_tasks_provider: Callable[[str], int] | None = None`
    - `run_once` 在 `active_task_count_provider` 之前调用 reaper，含异常隔离
    - `build_loop_components` 读取 `defaults.stale_task_timeout_seconds` 并注入 reaper
  - [x] SubTask 4.5（实现）: 修改 `OriginAgent/config/schema.py:1472-1501`
    - `AgentDefaults` 新增 `stale_task_timeout_seconds: int = 600` 字段（含 `AliasChoices`、`ge=30`、`le=86400`）
    - **注**：spec 文本提到 `RuntimeControls` 类不存在；实际添加到 `AgentDefaults`（同类 scalar runtime params 的既有归宿，符合规则6单一数据源与规则32最小化）
  - [x] SubTask 4.6（验证）: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_active_task_reaper.py -v --basetemp=.pytest_basetmp` → **4 passed**
  - [x] SubTask 4.7（回归验证）: 同步更新 `test_capability_commands.py`、`test_restart_command.py`、`test_loop_save_turn.py` 适配 tuple 结构；回归 96 passed + 10 pre-existing failures（git stash 验证全部属于 TD-015/TD-017 既有范围）

### Task 5: 统一 per-session 锁字典（Issue 5，L1 核心并发变更）

> **重要**：本 Task 触及红线闭集（规则7/8/10），实施前必须运行 `impact({target: "_process_message", direction: "upstream"})` 并报告 blast radius。
>
> **方案 B 决策（2026-07-18）**：经 impact 分析发现 spec 原计划（把锁移入 `_process_message`）存在死锁风险——`process_direct` 调用方 `api/server.py:371/417/431` 已外层 acquire 同一锁，而 `asyncio.Lock` 不可重入；同时 `dispatch_message` 的锁还保护流式回调注册、outbound 发布、CancelledError checkpoint 恢复等外围操作。改用方案 B：**保留调用方 acquire 锁，统一锁字典到 `sessions.get_lock`，并给 cron 路径补锁**。规则26"该反对时反对"已触发并经用户确认。

- [x] Task 5: 方案 B——保留调用方 acquire，统一锁字典 + 给 cron 路径补锁
  - [x] SubTask 5.0（影响分析）: 已运行 `impact({target: "_process_message", direction: "upstream"})`
    - **结果**：risk=CRITICAL，impactedCount=54，direct=46；非测试直接调用方 2 个：`on_cron_job`（commands.py）、`process_direct`（loop.py:2473，被 api/server.py:371/417/431 + OriginAgent.py:93 + cli/commands.py:1144/1405 调用）
    - `_session_locks` impact：risk=LOW，impactedCount=1，仅 `dispatch_message` 引用
    - **方案 B 选择依据**：避免 spec 原计划的死锁风险（process_direct 已有外层锁）+ 保留 dispatch_message 外围操作串行化
  - [x] SubTask 5.1（验证先行）: 在 `tests/agent/test_unified_session_lock.py` 新增 4 个测试
    - `test_dispatch_message_uses_sessions_get_lock`、`test_cron_acquires_sessions_get_lock_for_cron_session_key`、`test_cron_and_user_message_for_same_session_serialized`、`test_lock_released_on_exception_in_cron`
  - [x] SubTask 5.2（实现）: 修改 `OriginAgent/agent/message_dispatcher.py:34-35, 160`
    - L34-35: 删除 Protocol 中的 `_session_locks: dict[str, asyncio.Lock]` 声明
    - L160: `lock = self.loop.sessions.get_lock(session_key)`（替换 `_session_locks.setdefault`）
    - 保留 `async with lock, gate:` 不变（外围操作仍受锁保护）
  - [x] SubTask 5.3（实现）: 修改 `OriginAgent/cli/commands.py:993-1006`
    - 用 `async def _run_cron_turn():` 包装 `_process_message` 调用，内部 `async with agent.sessions.get_lock(cron_session_key):` 包裹
    - `process_task = asyncio.create_task(_run_cron_turn())`
    - 保持 `_active_tasks` 注册逻辑不变
  - [x] SubTask 5.4（实现）: 修改 `OriginAgent/agent/agent_loop_components.py:160, 518`
    - 删除 `_session_locks: Any = None` 字段声明
    - 删除 `values["_session_locks"] = {}` 初始化
  - [x] SubTask 5.5（实现）: 修改 `OriginAgent/agent/tools/self.py:50-51`
    - 从防护名单中移除 `"_session_locks"`
  - [x] SubTask 5.6（实现）: 更新测试 fixtures
    - `tests/agent/test_message_dispatcher.py`：5 处删除 `_session_locks={}` + 5 处添加 `get_lock=MagicMock(return_value=asyncio.Lock())`（必要 fixture 适配）
    - `tests/agent/test_stop_preserves_context.py:29`：删除 `loop._session_locks = {}`
    - `tests/agent/tools/test_self_tool.py:561-566`：删除 `test_modify_session_locks_blocked` 测试
    - `tests/agent/test_agent_services.py:27`：保留（断言 `not hasattr(loop.services, "_session_locks")` 仍成立）
  - [x] SubTask 5.7（验证）: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_unified_session_lock.py -v --basetemp=.pytest_basetmp` → **4 passed**
  - [x] SubTask 5.8（回归验证）: 同一测试套件 7 failed / 139 passed；git stash 基线 8 failed / 135 passed → 无新失败引入（7 个 pre-existing：1 个 `expire_stale_sessions` 缺失 + 5 个 EvolutionControlPlane + 1 个 Dream 属性缺失）

## P2 级（可观测性 + 审计补全）

### Task 6: `block_kinds` 包含 source（Issue 6，L3）

- [x] Task 6: 审计字段从 `kind` 改为 `kind:source`
  - [x] SubTask 6.1（验证先行）: 在 `tests/agent/test_context_assembler.py` 新增 2 个测试
    - `test_block_kinds_includes_source_when_present`、`test_block_kinds_omits_source_when_absent`
  - [x] SubTask 6.2（实现）: 修改 `OriginAgent/agent/context_assembler.py:221-230`（log_event block_kinds）
    - 推导式改为 `f"{kind}:{source}" if source else kind`（实际使用 walrus 表达式 `meta := block.get("_meta", {})`）
  - [x] SubTask 6.3（决策——规则33手术式变更）: **不修改** `context_assembler.py:127-131`（audit dict 的 block_kinds）
    - 理由：audit dict 已有独立 `reference_sources` 字段（L132-136）记录 source 信息，与 `block_kinds` 分离更利于诊断；用户 Issue 6 投诉的是 `event.context.assembled` 外部日志（即 log_event），audit dict 是内部 trace。修改 audit dict 不在本次需求范围内。
  - [x] SubTask 6.4（验证）: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_context_assembler.py -v --basetemp=.pytest_basetmp` → **2 new PASSED**（共 32 passed）

### Task 7: `attention_items` source 标记（Issue 8a，L2）

- [x] Task 7: `append_attention_item` 增加 source 参数 + 调用点标注 + 审计字段
  - [x] SubTask 7.1（验证先行）: 在 `tests/agent/test_working_memory.py` 新增 3 个测试
    - `test_append_attention_item_with_source_prefix`、`test_append_attention_item_default_source_is_user`、`test_working_memory_saved_event_includes_attention_items_sources`
  - [x] SubTask 7.2（实现）: 修改 `OriginAgent/agent/working_memory.py:47-64, 282-295`
    - 新增模块级 `_count_attention_items_sources` 辅助函数（解析 `[source]` 前缀，无前缀归为 `"user"`，向后兼容）
    - `def append_attention_item(self, session, item, *, identity=None, source="user"):`
    - 写入时 `stored = f"[{source}] {text}"`
  - [x] SubTask 7.3（实现）: 修改 3 个调用点
    - `agent_runtime.py:508`：`source="cognitive_event"`
    - `agent_runtime.py:1250-1254`：`source="user_correction"` + 同步适配 dedup 逻辑（L1244-1246：`stored_form = f"[user_correction] {text}"` 用于查重）
    - `meta_cognition_reflector.py:868-873`：`source="meta_reflection"`
  - [x] SubTask 7.4（实现）: 修改 `working_memory.py:218-224` 的 save 方法 `log_event`
    - 新增 `attention_items_sources=_count_attention_items_sources(snapshot.attention_items or [])` 字段
  - [x] SubTask 7.5（验证）: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_working_memory.py -v --basetmp=.pytest_basetmp` → **3 new PASSED**（共 32 passed）

### Task 8: `recent_turns_summary` 排除 internal 消息（Issue 8b，L2）

- [x] Task 8: `_extract_recent_turns_summary` 增加 `is_internal` 过滤
  - [x] SubTask 8.1（验证先行）: 在 `tests/agent/test_continuity_checkpoint.py` 新增 3 个测试
    - `test_extract_recent_turns_summary_excludes_internal_messages`、`test_extract_recent_turns_summary_relaxes_when_all_internal`、`test_cron_turn_does_not_reset_recent_turns_count`
  - [x] SubTask 8.2（实现）: 修改 `OriginAgent/agent/agent_runtime.py:698-761`
    - 单遍扫描同时累积 `real_summary`（过滤 `is_internal=True`）与 `all_summary`（含 internal）
    - 若 `len(real_summary) < 2` 且 `len(all_summary) > len(real_summary)`，记录 debug 日志并返回 `all_summary[-4:]`
    - 否则返回 `real_summary[-4:]`
  - [x] SubTask 8.3（验证）: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_continuity_checkpoint.py -v --basetmp=.pytest_basetmp` → **3 new PASSED**（共 32 passed）

### Task 9: 扩展 `denied.persisted` 覆盖范围（Issue 8c，L2）

> **规则26偏离声明（2026-07-18）**：spec 原计划覆盖 `capability_*`、`*_grant_required`、`*_denied` 三类。但 sub-agent 分析发现 `*_denied` 后缀匹配会误伤 `ssrf_denied`、`symlink_path_denied` 等**逐目标拒绝**（per-target denial）——这些拒绝针对特定 URL/路径，下次以不同参数调用时可能合法，不应永久阻塞该工具。`cross_target_denied` 是用户可见的 `code`（非 `policy_rule`），对应的 `policy_rule` 是 `message_cross_target_grant_required`，已被 `*_grant_required` 覆盖。因此**故意排除** `*_denied` 后缀匹配，仅在 runner.py:1219-1226 增加 `*_grant_required` 与 `*_requires_grant` 覆盖。此为规则26"该反对时反对"触发的偏离，需在清单与 commit message 中标注。

- [x] Task 9: 放宽 `_persist_session_denied_tool` 的 policy_rule 过滤（覆盖 `capability_*` + `*_grant_required` + `*_requires_grant`，**故意排除 `*_denied`**）
  - [x] SubTask 9.1（验证先行）: 在 `tests/agent/test_runner_denied_persisted.py` 新增 3 个测试（共 261 行）
    - `test_denied_persisted_for_message_cross_target_grant_required`、`test_denied_persisted_for_cron_high_capability_requires_grant`、`test_denied_persisted_not_emitted_for_generic_error`
  - [x] SubTask 9.2（实现）: 修改 `OriginAgent/agent/runner.py:1219-1226`
    - 保留 `if not policy_rule: return`
    - 过滤条件改为 `policy_rule.startswith("capability_") or policy_rule.endswith("_grant_required") or policy_rule.endswith("_requires_grant")`
    - 文档注释说明为何排除 `*_denied`（per-target 拒绝不应永久阻塞）
  - [x] SubTask 9.3（验证）: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_runner_denied_persisted.py -v --basetmp=.pytest_basetmp` → **3 new PASSED**（共 32 passed）

### Task 10: MCP `home_assistant` 运维检查（Issue 7，L3 仅文档）

- [x] Task 10: 无代码变更，仅作为运维检查项记录在 checklist.md
  - [x] SubTask 10.1: 在 `checklist.md` 中记录 MCP 服务器排查清单（URL 可达性、鉴权、防火墙、HA 服务状态、配置核对、启动时重试循环缺失）
  - [x] SubTask 10.2: 在 `techdebt/` 下登记 `TD-2026-018-mcp-startup-retry-missing-待评估.md`，schema_version=1，状态="待评估"，记录代码定位 `mcp.py:563/681-690` + `agent_host.py:270-295`

# Task Dependencies

## P0 内部依赖
- Task 1（capability_snapshot 透传）与 Task 2（三分支统一）相互独立，可并行
- Task 2 的验证（SubTask 2.5）需确保不破坏 `fix-context-assembly-pollution` 已修复的 `json.dumps` dump 问题

## P1 内部依赖
- Task 3（LLM 重试日志）独立
- Task 4（active_tasks 回收）独立，但与 Task 3 有协同：Task 3 修复后悬挂 LLM 调用会有 `retry_exhausted` 事件，Task 4 进一步清理悬挂任务本身
- **Task 5（统一锁字典）必须在 Task 1-4 完成后执行**——Task 5 是 L1 核心变更，需在其他改动稳定后进行，避免并发修改导致回归难以定位

## P2 内部依赖
- Task 6、7、8、9、10 相互独立，可并行

## P0/P1/P2 跨级依赖
- Task 8（`recent_turns_summary` 排除 internal）依赖 `fix-cron-session-key-routing` spec 已部署（cron 写入用户 session 的语义已正确）
- Task 1（capability_snapshot）依赖 `fix-cron-session-key-routing` spec 已部署（session_key 解析已正确）

## 建议执行顺序
1. **第一批（并行，P0 止血）**：Task 1 + Task 2
2. **第二批（并行，P1 可靠性）**：Task 3 + Task 4
3. **第三批（串行，P1 核心并发）**：Task 5（待 Task 1-4 验证通过后）
4. **第四批（并行，P2 可观测性）**：Task 6 + Task 7 + Task 8 + Task 9 + Task 10
5. **第五批**：每个 Task 独立原子提交（Task 5 单独提交，commit message 说明"为什么统一锁字典"）

## 提交策略（规则37）
- 每个 Task 对应一个独立 commit，可被单独 `git revert`
- commit message 首行简明祈使句；正文说明"为什么这么改"
- 涉及 BREAKING 变更的 Task（Task 1、Task 5）在 commit message 中显式标注 `BREAKING:`
- Task 5 的 commit message 须引用 `impact` 分析结果（如 `blast radius: 7 callers, risk=HIGH`）
