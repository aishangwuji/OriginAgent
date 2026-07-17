# Tasks

> 遵循规则34（验证先行）：先写测试，再实现。
> 遵循规则37（原子提交）：每个 Task 对应独立 commit。
> 遵循规则33（手术式变更）：仅修改与本次任务直接相关的代码，不顺手重构。

## P0 级（止血——死循环与 LLM 失败）

### Task 1: 增强 `_drop_orphan_tool_results` 位置合法性检查

- [x] Task 1: 让 `_drop_orphan_tool_results` 不仅检查 `tool_call_id` 存在性，还检查 tool 消息位置是否紧跟在声明它的 assistant 消息之后（中间允许其它 tool 消息，但不允许 user/system）
  - [x] SubTask 1.1（验证先行）: 在 `tests/agent/test_runner_context_governance.py`（新建或复用既有测试文件）新增 `test_drop_orphan_tool_results_removes_tool_separated_from_assistant_by_user`
    - 构造 messages: `[assistant(tool_calls=[id1]), user, tool(tool_call_id=id1)]`
    - 调用 `AgentRunner._drop_orphan_tool_results(messages)`
    - 断言返回的列表中不包含 `tool(tool_call_id=id1)`（位置不合法，删除）
  - [x] SubTask 1.2（验证先行）: 新增 `test_drop_orphan_tool_results_keeps_tool_immediately_after_assistant`
    - 构造 messages: `[assistant(tool_calls=[id1]), tool(tool_call_id=id1), user]`
    - 调用 `AgentRunner._drop_orphan_tool_results(messages)`
    - 断言返回的列表保留 `tool(tool_call_id=id1)`（位置合法）
  - [x] SubTask 1.3（验证先行）: 新增 `test_drop_orphan_tool_results_handles_multiple_tool_results_for_same_assistant`
    - 构造 messages: `[assistant(tool_calls=[id1, id2]), tool(id1), tool(id2), user]`
    - 调用 `AgentRunner._drop_orphan_tool_results(messages)`
    - 断言两个 tool 消息都保留（位置合法）
  - [x] SubTask 1.4（实现）: 修改 `OriginAgent/agent/runner.py:1463` 的 `_drop_orphan_tool_results`：
    - 维护 `declared: set[str]`（已声明的 tool_call_id）
    - 新增 `interrupted_since_last_assistant: bool` 标志，遇到 user/system 设 True，遇到 assistant 重置 False，遇到 tool 时若标志为 True 则删除（位置孤儿）
  - [x] SubTask 1.5（验证）: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_runner_context_governance.py -v --basetemp=.pytest_basetemp` → 3 个新测试全部 PASSED

### Task 2: 在 `_snip_history` 之后追加 `_drop_orphan_tool_results` + `_backfill_missing_tool_results`

- [x] Task 2: 确保 snip 产生的孤儿被清理
  - [x] SubTask 2.1（验证先行）: 在 `tests/agent/test_runner_context_governance.py` 新增 `test_snip_then_drop_orphans_cleans_tools_left_by_removed_assistant`
    - 采用"替代测试构造"直接模拟 snip 后状态（孤儿 tool 在列表首位）
    - 调用 `_drop_orphan_tool_results` → `_backfill_missing_tool_results` → `_drop_orphan_tool_results` 三步
    - 断言最终 `messages_for_model` 不包含 `tool(tool_call_id=id1)`
    - 断言 `messages_for_model` 的起始消息合法（不是孤立 tool）
  - [x] SubTask 2.2（实现）: 修改 `OriginAgent/agent/runner.py:337-340` 的 governance pipeline，在 snip 后追加第三次 `_drop_orphan_tool_results` 调用 + 注释
  - [x] SubTask 2.3（验证）: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_runner_context_governance.py -v --basetemp=.pytest_basetemp` → 4 个测试全部 PASSED

### Task 3: BDI 认知循环 session 级熔断

- [x] Task 3: 在 `AgentCognitiveRuntime` 中新增 session 级连续失败计数器与冷却机制
  - [x] SubTask 3.1（验证先行）: 新建 `tests/agent/test_cognitive_circuit_breaker.py` 新增 `test_session_enters_cooldown_after_threshold_failures`
    - 构造 `AgentCognitiveRuntime`，mock deps
    - 模拟同一 session_key 连续 3 次 LLM 失败（通过 mock session.messages 末尾是 error 响应）
    - 第 4 次调用 `run_cognitive_pass_for_session`
    - 断言返回的 decision 的 `action == "skip"`，`suppression_reason == "cognitive_cooldown"`
    - 断言 `cognitive.pass.skipped` 事件被发射，`reason="cognitive_cooldown"`
  - [x] SubTask 3.2（验证先行）: 新增 `test_cooldown_expires_after_timeout`
    - 触发熔断后，通过 `patch` 模块级 `time` 控制时间前进 31 分钟
    - 再次调用 `run_cognitive_pass_for_session`
    - 断言正常执行（不跳过），`consecutive_failures` 重置为 0
  - [x] SubTask 3.3（验证先行）: 新增 `test_success_resets_failure_counter`
    - 触发 2 次失败（未达阈值 3）
    - 第 3 次模拟成功（session.messages 末尾是正常 assistant 响应，非 error）
    - 断言 `consecutive_failures` 重置为 0
  - [x] SubTask 3.4（实现）: 修改 `OriginAgent/agent/agent_cognitive_runtime.py`：
    - `__init__` 中新增 `self._session_failure_states: dict[str, dict] = {}` 和 `self._pending_nudges: dict[str, str] = {}`
    - 新增模块级常量 `_COGNITIVE_FAILURE_THRESHOLD = 3` 和 `_COGNITIVE_COOLDOWN_SECONDS = 30 * 60`
    - 新增 `_check_session_cooldown(session_key) -> tuple[bool, str | None]`（用 `time.time()` 比较 epoch 秒数）
    - 新增 `_detect_last_turn_failure(session) -> bool`（含 `assert isinstance(messages, list)` 锁住假设）
    - 新增 `_update_failure_state(session_key, session) -> None`
    - `run_cognitive_pass_for_session` 开头调用 `_update_failure_state` + `_check_session_cooldown`，冷却中则发射 `cognitive.pass.skipped` 并返回
    - `bus.publish_inbound` 后记录 `self._pending_nudges[session_key] = self._deps.utcnow_iso()`
  - [x] SubTask 3.5（验证）: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_cognitive_runtime_logging.py tests/agent/test_cognitive_circuit_breaker.py -v --basetemp=.pytest_basetemp` → 4 个测试全部 PASSED（无回归）

## P1 级（噪音消除）

### Task 4: cron 等只进不出通道的静默丢弃

- [x] Task 4: 让 `ChannelManager` 对 cron 等只进不出通道的出站消息静默丢弃
  - [x] SubTask 4.1（验证先行）: 在 `tests/channels/test_manager_dispatch.py` 新增 `test_inbound_only_channel_discarded_silently`
    - 构造 `ChannelManager`，不注册 cron 通道
    - 发送一条 `OutboundMessage(channel="cron", ...)` 到 bus
    - 断言 WARNING 级别日志中不包含 "Unknown channel: cron"
    - 断言 INFO 级别日志中包含 "inbound-only channel" 或 "discarding"
  - [x] SubTask 4.2（验证先行）: 新增 `test_truly_unknown_channel_still_warns`
    - 发送一条 `OutboundMessage(channel="nonexistent", ...)` 到 bus
    - 断言 WARNING 级别日志中包含 "Unknown channel: nonexistent"
  - [x] SubTask 4.3（实现）: 修改 `OriginAgent/channels/manager.py`：
    - `__init__` 中新增 `self._inbound_only_channels: frozenset[str] = frozenset({"cron"})`
    - `_dispatch_outbound` 的 `else` 分支前检查 `msg.channel in self._inbound_only_channels`：是 → INFO 级日志；否 → 保持既有 WARNING
  - [x] SubTask 4.4（验证）: `.\.venv\Scripts\python.exe -m pytest tests/channels/test_manager_dispatch.py -v --basetemp=.pytest_basetemp` → 2 个新测试全部 PASSED（loguru sink 模式断言日志）

# Task Dependencies

## P0 内部依赖
- Task 1 和 Task 2 紧密相关：Task 1 增强检查逻辑，Task 2 在 pipeline 中应用。建议先 Task 1 再 Task 2。
- Task 3 独立于 Task 1/2，可并行。

## P1 依赖
- Task 4 完全独立，可并行。

## 建议执行顺序
1. **第一批（并行）**：Task 1（位置检查）+ Task 3（熔断）+ Task 4（通道静默）
2. **第二批**：Task 2（pipeline 追加，依赖 Task 1）
