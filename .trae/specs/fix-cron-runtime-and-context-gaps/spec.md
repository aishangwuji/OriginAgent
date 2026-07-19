# Cron 触发策略与运行时上下文缺口修复 Spec

## Why

生产日志（截至 2026-07-18 15:03:00）暴露 8 个相互关联的问题，集中表现为：
1. **cron 触发的自动化任务完全瘫痪**——同一 session 在用户触发时 `exec`/`read_file` 可用，cron 触发时全部被 `capability_*_denied` 拒绝；
2. **cron 触发时 LLM 上下文严重不足**——`message_count` 暴跌至 3/5/14（用户触发为 55~75）；
3. **LLM 重试无成功日志 + `unified:default` 持续活跃任务**——两者共同导致认知巡检被无限阻塞；
4. **用户/cron 并发处理时序混乱**——cron 在 `commands.py:978` 绕过 MessageBus 直调 `_process_message`，且存在两个互不互斥的 per-session 锁字典；
5. **`reference_context` 在审计日志中"重复"7 次**——实为 5 个 fusion source + user_profile + recent_history，但 `block_kinds` 只记录 `kind` 不记录 `source`，造成误导；
6. **MCP `home_assistant` 启动不可达**——无启动时重试循环；
7. **`attention_items_count` 在无用户消息时增长**——后台认知事件/meta-cognition 反思会追加；
8. **`recent_turns_count` 在 cron 处理后被重置为 1**——cron 写入用户 session 时挤占了 4-消息窗口；
9. **`message` 工具 `status=error` 但无 `denied.persisted` 事件**——`_persist_session_denied_tool` 仅对 `capability_*` 规则记录日志。

这些问题独立但相互放大：Issue 3 的悬挂 LLM 调用 → Issue 4 的 `_active_tasks` 永不释放 → 认知巡检被阻塞 → Issue 8a 的后台反思继续追加 `attention_items`。本 spec 一次性修复所有根因。

## What Changes

### P0-1：尊重 `job.payload.capability_snapshot`（Issue 1）

- **修改 `OriginAgent/agent/agent_runtime_context.py:36-45`**：`snapshot_for_trigger(trigger)` 增加可选参数 `payload_snapshot: dict | None = None`。若 `payload_snapshot` 非空，则优先从中重建 `CapabilitySnapshot`；否则维持现有 `trigger` 分支逻辑（含 `scheduled_default()` fallback）。
- **修改 `OriginAgent/cli/commands.py:979-1003`**：在调用 `_process_message` 前，将 `job.payload.capability_snapshot` 透传给 `agent_runtime_context.snapshot_for_trigger(...)`。
- **修改 `OriginAgent/agent/agent_runtime.py:987-989`**：`_cap_snapshot` 解析逻辑改为：优先 `capability_snapshot` 参数 → 其次 `snapshot_for_trigger(trigger, payload_snapshot=job.payload.capability_snapshot)` → 最后 fallback。
- **向后兼容**：若 `job.payload.capability_snapshot` 为空/None，行为完全不变（继续使用 `scheduled_default()`）。

### P0-2：排查并修复 `_build_initial_messages` 三分支（Issue 2）

- **审计 `OriginAgent/agent/agent_runtime.py:1436-1538`**：三分支（A: `pending_ask_id + enable_phase1_continuity`、B: `pending_ask_id + not enable_phase1_continuity`、C: 无 `pending_ask_id`）在 cron 触发时实际进入哪条路径。
- **关键修复点**：分支 B 在 `agent_runtime.py:1489` 和 `:1502` 重复调用 `build_reference_context_blocks` 两次（一次用于消息、一次用于审计），且不经过 `ContextAssemblerV2.assemble`，因此不发射 `context.assembled` 事件——这与日志观察到的"部分 cron 请求没有 `context.assembled` 事件"一致。
- **统一三分支行为**：所有分支最终都通过 `ContextAssemblerV2.assemble` 拼装，移除分支 B 的内联构造与重复 `build_reference_context_blocks` 调用。`loop.py:1354-1372` 的并行实现同步修改。
- **验证 cron 路径的历史拼接**：确认 cron 触发时 `session.messages` 历史被正确拼入 `messages_for_model`，而非仅发送 system + user 单条。

### P1-1：LLM 重试日志补全（Issue 3）

- **修改 `OriginAgent/providers/base.py:732-819`**（`_run_with_retry`）：
  - 在 `attempt > 1` 且 `response.finish_reason != "error"` 时，记录 INFO 级 `"LLM retry succeeded on attempt N/M"`。
  - 在 `await call(**kw)` 外层增加 `try/except Exception`，捕获 `ConnectionError`/`asyncio.TimeoutError` 等异常，记录 `"LLM call raised exception on attempt N/M: {exc}"`，按既有重试策略继续重试或最终失败。
  - 在重试耗尽后（`attempt > len(delays)`）的 break 前记录 `"LLM request failed after {N} retries, giving up"`（当前已有），但同步发射 `event.llm.retry_exhausted` 事件供审计。

### P1-2：`_active_tasks` 陈旧任务回收（Issue 4）

- **修改 `OriginAgent/agent/loop.py:1674-1677`**（`_active_task_count`）：在计算 `not task.done()` 之外，增加基于 `task.created_at`（或外部记录的注册时间戳）的陈旧度检查——超过 `stale_task_timeout_seconds`（默认 600s = 10 分钟）的未完成任务视为陈旧，计入 `stale_count` 单独上报。
- **新增 `OriginAgent/agent/loop.py:_reap_stale_tasks(session_key)`**：扫描 `_active_tasks[session_key]`，对陈旧任务调用 `task.cancel()` 并从列表中移除，发射 `event.active_task.reaped` 事件。
- **在 `cognitive_scheduler.py` 的 pass 触发前调用 `_reap_stale_tasks`**：确保陈旧任务被回收后再判断 `active_task_count`，避免被悬挂任务永久阻塞。
- **配置项**：`RuntimeControls.stale_task_timeout_seconds: int = 600`，可配置。

### P1-3：统一 per-session 锁字典（Issue 5）

- **删除 `OriginAgent/agent/agent_loop_components.py:158, 511` 的 `_session_locks` 字段**，改为通过 `SessionManager.get_lock(session_key)` 统一获取锁。
- **修改 `OriginAgent/agent/message_dispatcher.py:151, 157`**：移除本地 `self.loop._session_locks.setdefault(...)`，改用 `self.loop._sessions.get_lock(session_key)`。
- **修改 `OriginAgent/cli/commands.py:979-1003`**：在 `asyncio.create_task(agent._process_message(...))` 前，`async with agent._sessions.get_lock(cron_session_key):` 包裹整个 cron turn，确保与用户消息/dispatcher 互斥。
- **修改 `OriginAgent/agent/loop.py:1841-1875`**（`_process_message`）：在入口处 `async with self._sessions.get_lock(session_key):` 包裹，作为统一互斥点。同时移除 `message_dispatcher.py:157` 的外层锁（避免双重获取死锁，`asyncio.Lock` 不可重入）。
- **影响分析**：这是 L1 核心变更，触及红线闭集的并发/状态一致性（规则7/8/10）。提交前必须运行 `impact({target: "_process_message", direction: "upstream"})` 并在清单中报告 blast radius。
- **BREAKING**：API 路径（`api/server.py:335, 369, 414`）原本独立获取 `sessions.get_lock(...)`，现在改为复用同一锁——若 API 调用与 dispatcher/cron 同时到达同一 session，将串行化而非并发。这是预期行为（消除并发响应丢失/时序混乱）。

### P2-1：`reference_context` 审计包含 source（Issue 6）

- **修改 `OriginAgent/agent/context_assembler.py:209-232`**：`block_kinds` 改为 `[f"{block.get('_meta', {}).get('kind')}:{block.get('_meta', {}).get('source')}" if block.get('_meta', {}).get('source') else block.get('_meta', {}).get('kind') for block in merged if isinstance(block, dict)]`。
- 输出示例：`['runtime_context', 'reference_context:user_profile', 'reference_context:fact_store', 'reference_context:nearline_retrieval', ..., 'user_text']`。
- 不改变实际块内容，仅增强审计可观测性。

### P2-2：MCP `home_assistant` 启动重试——仅文档（Issue 7）

- **无代码变更**（规则32：无第二使用场景不新增抽象）。MCP 端点不可达是环境/配置问题。
- 在 `checklist.md` 中作为运维检查项记录：用户应检查 MCP 服务器 URL 是否可达、是否需要鉴权、防火墙是否拦截。

### P2-3：`attention_items` 增加 source 标记（Issue 8a）

- **修改 `OriginAgent/agent/working_memory.py:261-272`**（`append_attention_item`）：增加 `source: str = "user"` 参数，写入 `attention_items` 时改为 `f"[{source}] {item}"` 格式。
- **修改 3 个调用点**：
  - `agent_runtime.py:508`：`source="cognitive_event"`
  - `agent_runtime.py:1217`：`source="user_correction"`
  - `meta_cognition_reflector.py:868`：`source="meta_reflection"`
- 现有 `attention_items` 字段无需迁移（旧条目无前缀，按 `[user]` 解析容忍）。
- **审计**：`working_memory.saved` 事件增加 `attention_items_sources` 字段，记录各 source 的计数。

### P2-4：`recent_turns_summary` 排除 internal 消息（Issue 8b）

- **修改 `OriginAgent/agent/agent_runtime.py:699-731`**（`_extract_recent_turns_summary`）：在 `role` 过滤基础上，增加 `metadata.get("is_internal")` 检查——若消息标记为 internal（cron/cognitive/nudge），跳过。
- **回退策略**：若过滤后剩余消息 < 2 条，放宽限制保留所有 USER/ASSISTANT 消息（避免空 summary）。
- **不影响 cron 自身 session**：仅当 cron 写入用户 session（`payload.sessionKey` 指向用户 session）时生效。

### P2-5：扩展 `denied.persisted` 覆盖范围（Issue 8c）

- **修改 `OriginAgent/agent/runner.py:1204`**：将 `if not policy_rule or not policy_rule.startswith("capability_"): return` 放宽为 `if not policy_rule: return`，并新增 `policy_rule.startswith("message_")` 或 `policy_rule.endswith("_grant_required")` 也走 `denied.persisted` 路径。
- **具体覆盖**：`message_cross_target_grant_required`、`cron_high_capability_requires_grant` 等非 `capability_*` 但属于策略拒绝的规则。
- **不影响普通 error**：纯字符串 `"Error: ..."` 返回（无 `policy_rule`）仍不触发 `denied.persisted`，仅记录 `tool.complete status=error`。

## Impact

### Affected code

| 文件 | 修改范围 | 风险等级 |
|------|----------|----------|
| `OriginAgent/agent/agent_runtime_context.py` | L36-45（`snapshot_for_trigger` 增加 payload_snapshot 参数） | L1（安全边界） |
| `OriginAgent/cli/commands.py` | L979-1003（透传 capability_snapshot）+ L979-1003（cron 加锁） | L1（并发） |
| `OriginAgent/agent/agent_runtime.py` | L987-989（cap snapshot 解析）+ L1436-1538（`_build_initial_messages` 三分支统一）+ L699-731（`_extract_recent_turns_summary` 排除 internal）+ L508/L1217（attention_items source） | L1+L2 |
| `OriginAgent/providers/base.py` | L732-819（`_run_with_retry` 日志补全 + try/except） | L2 |
| `OriginAgent/agent/loop.py` | L1674-1677（`_active_task_count` 陈旧度）+ 新增 `_reap_stale_tasks` + L1841-1875（`_process_message` 加锁）+ L1354-1372（三分支统一） | L1 |
| `OriginAgent/agent/agent_loop_components.py` | L158/L511（删除 `_session_locks`） | L1 |
| `OriginAgent/agent/message_dispatcher.py` | L151/L157（改用 `sessions.get_lock`） | L1 |
| `OriginAgent/agent/context_assembler.py` | L209-232（`block_kinds` 包含 source） | L3 |
| `OriginAgent/agent/working_memory.py` | L261-272（`append_attention_item` source 参数） | L2 |
| `OriginAgent/agent/meta_cognition_reflector.py` | L868（source 标记） | L3 |
| `OriginAgent/agent/runner.py` | L1204（放宽 `denied.persisted` 覆盖） | L2 |
| `OriginAgent/agent/cognitive_scheduler.py` | pass 触发前调用 `_reap_stale_tasks` | L2 |
| `OriginAgent/config/schema.py` | `RuntimeControls.stale_task_timeout_seconds` 新增 | L3 |
| `tests/` | 新增/更新测试覆盖上述变更 | — |

### Affected specs

- `fix-cron-death-loop-and-orphan-tool`：本 spec 的 P1-2（陈旧任务回收）与该 spec 的"连续失败熔断"互补——前者清理悬挂任务，后者阻止持续 nudge 失败 session。
- `fix-cron-session-key-routing`：本 spec 的 P0-1（capability_snapshot 透传）依赖该 spec 的 session_key 解析；P0-2（`_build_initial_messages`）假设该 spec 已部署。
- `fix-cognition-architecture-defects`：本 spec 的 P2-3（attention_items source）与该 spec 的"working_memory 防自我强化"方向一致。
- `fix-context-assembly-pollution`：本 spec 的 P0-2（三分支统一）应避免破坏该 spec 已修复的 `json.dumps` dump 问题。
- `reduce-log-noise-and-fix-checkpoint`：本 spec 的 P2-4（`recent_turns_summary` 排除 internal）依赖该 spec 修复的 `setdefault` bug 已不存在。

### Behavioral changes

- **BREAKING（对 cron job 配置者）**：cron job 若在 payload 中显式配置 `capability_snapshot`，现在会被真正使用。之前该字段被静默忽略，cron 始终使用 `scheduled_default()`。用户需审查现有 cron job 的 `capability_snapshot` 配置是否合理（避免误开 `can_exec=true`）。
- **BREAKING（对 API 调用者）**：API 路径与 dispatcher/cron 现在共享同一 per-session 锁——同一 session 的并发 API 请求将串行化。这是修复并发混乱的必要代价。
- **BREAKING（对日志读者）**：`block_kinds` 字段从纯 `kind` 改为 `kind:source`（当 source 存在时）；`working_memory.saved` 新增 `attention_items_sources` 字段；新增 `event.active_task.reaped` 与 `event.llm.retry_exhausted` 事件。
- **非破坏性**：`denied.persisted` 覆盖范围扩大、`_run_with_retry` 日志补全、`_active_tasks` 陈旧回收——均为新增日志/事件，不改变既有行为。

### Risk level

- **HIGH**：P1-3（统一锁字典）触及并发核心，需 impact 分析 + 完整集成测试。
- **MEDIUM**：P0-1（capability_snapshot 透传，安全边界变更）、P0-2（三分支统一，上下文拼装核心）、P1-2（active_tasks 回收，可能取消正在执行的长任务）。
- **LOW**：P1-1（仅日志）、P2-1/P2-3/P2-4/P2-5（审计/可观测性增强）。

## ADDED Requirements

### Requirement: Cron Capability Snapshot from Payload

`snapshot_for_trigger` SHALL accept an optional `payload_snapshot: dict | None` parameter. When `payload_snapshot` is non-empty, the function SHALL reconstruct a `CapabilitySnapshot` from it and return that snapshot, overriding the default `trigger`-based selection. When `payload_snapshot` is None/empty, the existing `trigger`-based logic (including `scheduled_default()` for `"scheduled"` trigger) SHALL remain unchanged.

`on_cron_job` SHALL pass `job.payload.capability_snapshot` through to `snapshot_for_trigger` when invoking `_process_message`.

#### Scenario: Cron job with explicit capability_snapshot
- **WHEN** a cron job's `payload.capability_snapshot = {"can_exec": true, "can_read_files": true, ...}` is non-empty
- **AND** `on_cron_job` triggers `_process_message`
- **THEN** `snapshot_for_trigger("scheduled", payload_snapshot=...)` returns a `CapabilitySnapshot` with `can_exec=True`
- **AND** `ToolRegistry._assert_capability` permits `exec` tool execution
- **AND** `event.tool.complete name=exec status=success` is logged (not `capability_exec_denied`)

#### Scenario: Cron job without capability_snapshot (backward compat)
- **WHEN** a cron job's `payload.capability_snapshot` is None or empty dict
- **THEN** `snapshot_for_trigger("scheduled", payload_snapshot=None)` returns `CapabilitySnapshot.scheduled_default()` (all False)
- **AND** existing `capability_*_denied` behavior is preserved

#### Scenario: Invalid capability_snapshot dict
- **WHEN** `payload.capability_snapshot` is non-empty but missing required fields
- **THEN** `snapshot_for_trigger` SHALL tolerate missing fields (use defaults) and log a warning
- **AND** SHALL NOT raise an exception (fail-safe to `scheduled_default()` if reconstruction fails)

### Requirement: Unified Context Assembly Path for All Triggers

`AgentRuntime._build_initial_messages` SHALL route all three branches (pending_ask + phase1, pending_ask + no phase1, no pending_ask) through `ContextAssemblerV2.assemble` for the user-turn content construction. The inline construction in branch B (`agent_runtime.py:1489-1502`) SHALL be removed to eliminate the duplicate `build_reference_context_blocks` call and ensure `context.assembled` event is emitted for every turn.

`AgentLoop._build_initial_messages` (`loop.py:1354-1372`) SHALL apply the same unification.

#### Scenario: Cron trigger emits context.assembled
- **WHEN** a cron job triggers `_process_message`
- **AND** the turn enters `_build_initial_messages`
- **THEN** `event.context.assembled` is emitted with `block_count`, `block_kinds`, `reference_sources`
- **AND** `build_reference_context_blocks` is called exactly once per turn (not twice)

#### Scenario: User trigger behavior preserved
- **WHEN** a user message triggers `_process_message`
- **THEN** the assembled context is identical to pre-change behavior (no regression in user-facing content)
- **AND** `event.context.assembled` continues to be emitted as before

#### Scenario: History correctly prepended for cron-on-user-session
- **WHEN** a cron job with `payload.sessionKey = "tenant:guest"` triggers
- **AND** `tenant:guest` session has 50+ historical messages
- **THEN** `event.llm.request message_count` reflects the full history (not just 3-5)
- **AND** the LLM has access to conversation context for reasoning

### Requirement: LLM Retry Outcome Logging

`Provider._run_with_retry` SHALL log the outcome of every retry sequence:
- On successful retry (attempt N > 1 returns non-error response): INFO-level `"LLM retry succeeded on attempt N/M"`.
- On exception raised by `call(**kw)` (e.g., `ConnectionError`, `asyncio.TimeoutError`): caught and logged as `"LLM call raised exception on attempt N/M: {exc_type}: {exc}"`, then retried per existing strategy.
- On retry exhaustion (attempt > len(delays)): emit `event.llm.retry_exhausted` event with `attempts=N`, `final_error=...`.

#### Scenario: Retry succeeds on attempt 2
- **WHEN** attempt 1 raises a transient error
- **AND** attempt 2 returns a successful response
- **THEN** log line `"LLM retry succeeded on attempt 2/3"` is emitted
- **AND** the response is returned to the caller

#### Scenario: Retry exhausted
- **WHEN** all 3 retry attempts fail
- **THEN** `event.llm.retry_exhausted attempts=3 final_error=...` is emitted
- **AND** the existing `"LLM request failed after 3 retries, giving up"` log is preserved

#### Scenario: Exception during call
- **WHEN** `await call(**kw)` raises `ConnectionError` on attempt 1
- **THEN** the exception is caught and logged as `"LLM call raised exception on attempt 1/3: ConnectionError: ..."`
- **AND** the retry strategy continues (sleep + retry) per existing logic
- **AND** the exception does NOT propagate out of `_run_with_retry` until retries are exhausted

### Requirement: Stale Active Task Reaper

`AgentLoop` SHALL track a creation timestamp for every task registered in `_active_tasks`. Tasks whose `not task.done()` AND `now - created_at > stale_task_timeout_seconds` (default 600s) SHALL be considered stale.

`AgentLoop._reap_stale_tasks(session_key)` SHALL:
1. Scan `_active_tasks[session_key]` for stale tasks.
2. Call `task.cancel()` on each stale task.
3. Remove the cancelled tasks from the list.
4. Emit `event.active_task.reaped session_key={} task_count={} oldest_age_seconds={}`.

`cognitive_scheduler` SHALL call `_reap_stale_tasks(session_key)` before evaluating `eligibility_for_cognition`, so that stale tasks no longer block cognitive passes.

`_active_task_count(session_key)` SHALL continue to return the count of `not task.done()` tasks (now excluding reaped tasks).

#### Scenario: Hung LLM call reaped after 10 minutes
- **WHEN** a dispatcher task for `unified:default` is hung on `await call(**kw)` for 11 minutes
- **AND** `cognitive_scheduler` triggers a pass for `unified:default`
- **THEN** `_reap_stale_tasks("unified:default")` cancels the hung task
- **AND** `event.active_task.reaped session_key=unified:default task_count=1 oldest_age_seconds=660` is emitted
- **AND** `_active_task_count("unified:default")` returns 0
- **AND** the cognitive pass proceeds (not skipped with `reason=active_tasks`)

#### Scenario: Fresh tasks not reaped
- **WHEN** a task has been running for 60 seconds (well under the 600s threshold)
- **THEN** `_reap_stale_tasks` does NOT cancel it
- **AND** `_active_task_count` continues to include it

#### Scenario: Configurable timeout
- **WHEN** `RuntimeControls.stale_task_timeout_seconds = 300` is configured
- **THEN** tasks older than 300 seconds are considered stale
- **AND** the default 600s is overridden

### Requirement: Unified Per-Session Lock

`AgentLoop._process_message(session_key, ...)` SHALL acquire `self._sessions.get_lock(session_key)` at entry, releasing it on exit (including exception paths). This is the single canonical per-session lock for all turn execution.

The `_session_locks` dict in `agent_loop_components.py` SHALL be removed. `message_dispatcher.dispatch_message` SHALL NOT acquire a separate lock (the inner `_process_message` already acquires the canonical lock).

`on_cron_job` SHALL acquire `agent._sessions.get_lock(cron_session_key)` before invoking `_process_message`, ensuring cron turns are serialized with user turns for the same session.

`process_direct` (API path) SHALL continue to use `sessions.get_lock(...)` (now the same lock as `_process_message`).

#### Scenario: Cron and user message for same session serialized
- **WHEN** a user message arrives for `tenant:guest` while a cron turn is in progress for the same session
- **THEN** the user message's `_process_message` call blocks until the cron turn releases the lock
- **AND** no interleaved outbound messages are produced
- **AND** both turns execute to completion in serial order

#### Scenario: Concurrent API requests for same session serialized
- **WHEN** two API requests arrive simultaneously for `tenant:guest`
- **THEN** the second request blocks until the first completes
- **AND** no race condition on `session.messages` occurs

#### Scenario: Different sessions not blocked
- **WHEN** a cron turn for `cron:job-A` is in progress
- **AND** a user message arrives for `tenant:guest`
- **THEN** the user message's `_process_message` does NOT block on the cron turn's lock
- **AND** both turns execute concurrently (different session keys)

### Requirement: Context Audit Block Kinds Include Source

`ContextAssemblerV2.assemble` SHALL emit `block_kinds` as a list of strings where each string is either:
- `"{kind}"` when `_meta.source` is absent, OR
- `"{kind}:{source}"` when `_meta.source` is present.

#### Scenario: reference_context blocks with sources
- **WHEN** the assembled context contains 5 `reference_context` blocks with sources `user_profile`, `fact_store`, `nearline_retrieval`, `memory_candidates`, `session_search`
- **THEN** `block_kinds` includes `"reference_context:user_profile"`, `"reference_context:fact_store"`, etc.
- **AND** the audit consumer can distinguish between different reference sources

#### Scenario: Blocks without source
- **WHEN** a block has `_meta.kind = "runtime_context"` but no `_meta.source`
- **THEN** `block_kinds` includes `"runtime_context"` (no colon suffix)

### Requirement: Attention Items Source Tracking

`WorkingMemoryManager.append_attention_item(session, item, identity=..., source="user")` SHALL accept a `source` parameter and prefix the stored item with `"[{source}] {item}"`.

Callers SHALL pass the appropriate source:
- `_write_cognitive_event_to_working_memory`: `source="cognitive_event"`
- `_handle_user_correction_fast_path`: `source="user_correction"`
- `meta_cognition_reflector._bridge_to_working_memory`: `source="meta_reflection"`
- All other call sites: `source="user"` (default, backward compatible)

`working_memory.saved` event SHALL include `attention_items_sources: dict[str, int]` counting items by source.

#### Scenario: Cognitive event appends with source
- **WHEN** a cognitive event triggers `_write_cognitive_event_to_working_memory`
- **THEN** the new `attention_items` entry is `"[cognitive_event] {summary}"`
- **AND** `working_memory.saved attention_items_sources={"cognitive_event": 1, ...}` is emitted

#### Scenario: Existing items without source prefix
- **WHEN** `attention_items` contains a legacy entry `"背题"` (no source prefix)
- **THEN** it is treated as `source="user"` for counting purposes
- **AND** no migration is required

### Requirement: Recent Turns Summary Excludes Internal Messages

`_extract_recent_turns_summary(session, max_chars)` SHALL skip messages whose `metadata.is_internal == True` (cron/cognitive/nudge messages). Only real USER/ASSISTANT messages SHALL be included in the summary.

If filtering leaves fewer than 2 messages, the filter SHALL be relaxed to include all USER/ASSISTANT messages (avoiding empty summary).

#### Scenario: Cron turn written to user session
- **WHEN** a cron turn writes `[user(is_internal=True), assistant]` to `tenant:guest` session
- **AND** the previous 4 messages were `[user, assistant, user, assistant]` (real user conversation)
- **THEN** `_extract_recent_turns_summary` returns the 4 real user/assistant messages (not the cron internal message)
- **AND** `recent_turns_count` is 4 (not 1 or 2)

#### Scenario: All recent messages are internal
- **WHEN** the last 4 messages are all `is_internal=True` (e.g., multiple cron turns in succession)
- **THEN** the filter relaxes and includes them all (better than empty summary)
- **AND** a debug log notes the relaxation

### Requirement: Denied Persisted Coverage for Non-Capability Policy Rules

`AgentRunner._persist_session_denied_tool` SHALL persist `denied.persisted` events for any `policy_rule` matching:
- `capability_*` (existing)
- `*_grant_required` (e.g., `message_cross_target_grant_required`, `cron_high_capability_requires_grant`)
- `*_denied` (e.g., `cross_target_denied`)

Pure string errors (no `policy_rule`) SHALL continue to be logged only as `tool.complete status=error` (no `denied.persisted`).

#### Scenario: Message tool cross-target denial
- **WHEN** the `message` tool raises `PolicyDeniedError(policy_rule="message_cross_target_grant_required")`
- **THEN** `event.tool.denied.persisted session_key={} tool=message policy_rule=message_cross_target_grant_required` is emitted
- **AND** the denial is recorded in session denied_tools history

#### Scenario: Generic error remains uncovered
- **WHEN** the `message` tool returns `"Error: No target channel/chat specified"` (string error, no policy_rule)
- **THEN** only `event.tool.complete name=message status=error` is emitted
- **AND** no `denied.persisted` event is emitted (preserves existing behavior)

## MODIFIED Requirements

### Requirement: AgentLoop _process_message Entry Lock

`AgentLoop._process_message` SHALL acquire `self._sessions.get_lock(session_key)` at entry as the single canonical per-session lock. All callers (dispatcher, cron, process_direct, subagent) SHALL rely on this lock for serialization, NOT on external lock acquisition.

The previous `_session_locks` dict in `agent_loop_components.py` is REMOVED.

### Requirement: snapshot_for_trigger Trigger Selection

`snapshot_for_trigger(trigger, payload_snapshot=None)` SHALL:
1. If `payload_snapshot` is non-empty, reconstruct and return a `CapabilitySnapshot` from it (overriding trigger-based selection).
2. Else, fall back to the existing trigger-based mapping (`scheduled_default()` for `"scheduled"`, `user_turn()` for user, etc.).

### Requirement: _build_initial_messages Branch Unification

`AgentRuntime._build_initial_messages` and `AgentLoop._build_initial_messages` SHALL route all branches through `ContextAssemblerV2.assemble` for user-turn content construction. The branch B inline construction (`agent_runtime.py:1489-1502` and `loop.py:1364-1365`) is REMOVED.

## REMOVED Requirements

### Requirement: Branch B Inline Context Construction

**Reason**: Branch B duplicated `build_reference_context_blocks` calls and skipped `ContextAssemblerV2.assemble`, causing inconsistent `context.assembled` event emission and missing context audit for some cron turns.

**Migration**: All branches now route through `ContextAssemblerV2.assemble`, which produces both the user content blocks and the audit event in a single pass.
