# Checklist

## P0 止血——死循环与 LLM 失败

### Task 1: 位置合法性检查

- [x] Task 1: `_drop_orphan_tool_results` 新增位置合法性检查（tool 消息必须紧跟声明它的 assistant，中间不允许 user/system）
- [x] Task 1: 测试 `test_drop_orphan_tool_results_removes_tool_separated_from_assistant_by_user` 通过
- [x] Task 1: 测试 `test_drop_orphan_tool_results_keeps_tool_immediately_after_assistant` 通过
- [x] Task 1: 测试 `test_drop_orphan_tool_results_handles_multiple_tool_results_for_same_assistant` 通过
- [x] Task 1: 既有 `test_runner_context_governance.py` 或相关测试不回归（仅 2 个预存在 `test_runner_safety.py` 失败——`_run_tool` 签名漂移，与本次无关）

### Task 2: snip 后追加清理

- [x] Task 2: governance pipeline 在 `_snip_history` 后追加 `_drop_orphan_tool_results` → `_backfill_missing_tool_results` → `_drop_orphan_tool_results`
- [x] Task 2: 测试 `test_snip_then_drop_orphans_cleans_tools_left_by_removed_assistant` 通过
- [x] Task 2: 既有 runner 测试不回归（`test_runner_logging.py` 等全部通过）

### Task 3: BDI 认知循环熔断

- [x] Task 3: `AgentCognitiveRuntime.__init__` 新增 `self._session_failure_states: dict[str, dict]`
- [x] Task 3: 新增 `_check_session_cooldown(session_key) -> tuple[bool, str | None]`
- [x] Task 3: 新增 `_detect_last_turn_failure(session) -> bool`（含 `assert isinstance(messages, list)` 锁住假设，落实规则14）
- [x] Task 3: `run_cognitive_pass_for_session` 开头检查冷却状态，冷却中则跳过并发射 `cognitive.pass.skipped`（reason="cognitive_cooldown"）
- [x] Task 3: `run_cognitive_pass_for_session` 末尾追踪 emitted nudge，下一轮检测失败/成功并更新计数
- [x] Task 3: 连续 3 次失败触发 30 分钟冷却
- [x] Task 3: 冷却过期后恢复正常，`consecutive_failures` 重置
- [x] Task 3: 成功响应重置 `consecutive_failures`
- [x] Task 3: 测试 `test_session_enters_cooldown_after_threshold_failures` 通过
- [x] Task 3: 测试 `test_cooldown_expires_after_timeout` 通过
- [x] Task 3: 测试 `test_success_resets_failure_counter` 通过
- [x] Task 3: 既有 `test_cognitive_runtime_logging.py` 测试不回归

## P1 噪音消除

### Task 4: cron 通道静默丢弃

- [x] Task 4: `ChannelManager.__init__` 新增 `self._inbound_only_channels: frozenset[str] = frozenset({"cron"})`
- [x] Task 4: `_dispatch_outbound` 的 else 分支前检查 `msg.channel in self._inbound_only_channels`
- [x] Task 4: 只进不出通道的出站消息丢弃并记录 INFO 级日志
- [x] Task 4: 真正未知的通道仍输出 WARNING（行为不变）
- [x] Task 4: 测试 `test_inbound_only_channel_discarded_silently` 通过
- [x] Task 4: 测试 `test_truly_unknown_channel_still_warns` 通过

## 规则合规性自检

- [x] 规则3（边界数据校验）：本次变更不涉及外部输入 schema 校验
- [x] 规则5（缓存值声明生命周期）：`_session_failure_states` 是 manager 实例字段，生命周期与 AgentCognitiveRuntime 实例一致；`cooldown_until` 用 `time.time()` epoch 秒数，过期后自然失效
- [x] 规则7（状态变化审计）：`_session_failure_states` 的写入路径单一（`_update_failure_state`），读取路径单一（`_check_session_cooldown`），无并发竞争
- [x] 规则8（异步时序交叉）：BDI 认知 pass 与 LLM 调用是异步的——若 LLM 仍在进行，`session.messages` 末尾仍是上一轮的 user/nudge 消息（非 error），`_detect_last_turn_failure` 返回 False，不会误增失败计数。只有 LLM 完成并写入 error 响应后，下一轮认知 pass 才会检测到失败。这一时序是安全的。
- [x] 规则9（session token 隔离）：不涉及异步资源开关对
- [x] 规则12（幂等）：不涉及可重试写操作
- [x] 规则14（关键假设断言化）：`_detect_last_turn_failure` 中加了 `assert isinstance(messages, list)` 锁住"session.messages 是 list"假设
- [x] 规则18（安全边界）：不涉及身份操作/凭证/SQL
- [x] 规则27（变更交付清单）：本 spec 文档即清单，每 Task 对应独立 commit
- [x] 规则33（手术式变更）：仅修改与本次任务直接相关的代码（runner.py 单方法 + pipeline 一行 + agent_cognitive_runtime.py 新增方法 + manager.py 两处）
- [x] 规则34（验证先行）：每个 Task 的 SubTask.1 都是先写测试，并确认 FAIL 后再实现
- [x] 规则37（原子提交）：每个 Task 独立 commit

## 系统认知同步

- [x] 本次变更未触及 `systemmap/` 中已记录的业务规则/状态机——BDI 认知循环的状态机新增"冷却"状态，但 `systemmap/` 目录下当前无 `bdi-lifecycle.md`，无需更新。如未来创建该文件应记录"冷却"状态迁移。

## 预存在失败（非本次引入，按规则36 选项2 留痕）

- `tests/agent/test_runner_safety.py::test_prepare_call_exception_does_not_silently_pass` —— `_run_tool() missing 1 required positional argument: 'idempotency_keys'`（签名漂移，与本次无关）
- `tests/agent/test_runner_safety.py::test_prepare_call_exception_becomes_prep_error` —— 同上
- `tests/channels/` 中 46 个预存在失败（telegram/websocket/rest_api 模块的代码漂移，与本次 ChannelManager 改动无关）
