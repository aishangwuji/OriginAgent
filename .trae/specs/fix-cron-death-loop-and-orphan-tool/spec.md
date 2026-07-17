# Fix Cron Session Death Loop and Orphan Tool Messages Spec

## Why

cron 通道触发的 reminder 在 `cron:8d88e717` session 上产生了死循环：BDI 认知循环每 15 秒检测到 `pending_confirmation` 候选 → 发布 nudge 到 bus → nudge 被当作新 inbound message 进入 LLM 调用 → LLM 因"孤立 tool 消息"报错 `Messages with role 'tool' must be a response to a preceding message with 'tool_calls'` → 错误响应追加到 session 历史 → 15 秒后 BDI 再次扫描 → `pending_confirmation` 仍存在 → 回到起点。session 历史每轮增长 1 条（77→78→79...），永远无法自愈，且每次都触发 `Unknown channel: cron` 警告。

三个根因交织：
1. **P0 孤立 tool 消息未清理**：`_drop_orphan_tool_results` 只检查 `tool_call_id` 是否在 `declared` 集合中，不检查 tool 消息是否紧跟在对应 assistant tool_calls 之后。当 `_snip_history` 删除了 assistant 消息但保留了后续的 tool 消息时，`find_legal_message_start` 会把 start 推到 tool 消息之后，但 `_drop_orphan_tool_results` 在 snip 之前运行，导致 snip 后产生的新孤儿未被清理。
2. **P0 BDI 死循环无熔断**：`passes_cooldown` 的 `session_cooldown_seconds=600` / `intent_cooldown_seconds=300` 理论上应阻止 15 秒内重复发射，但日志显示每 15 秒就发射一次——说明 cooldown 检查未生效或 `cooldown_key` 每次不同。即使 cooldown 生效，连续 LLM 失败的 session 应该被标记为"不可恢复"，停止继续 nudge。
3. **P1 cron 通道未注册**：`ChannelManager._init_channels` 通过 `discover_all()` 发现通道，cron 不是 `BaseChannel` 子类，不会注册到 `self.channels`。当 Agent 尝试回复 `cron` 通道时，`channels/manager.py:380` 输出 `Unknown channel: cron` 警告。

## What Changes

### P0-1: 增强孤立 tool 消息清理（位置合法性检查）
- **修改 `_drop_orphan_tool_results`**：除了现有的"id 集合检查"，新增"位置合法性检查"——tool 消息必须紧跟在声明了对应 `tool_call_id` 的 assistant 消息之后（中间允许插入其它 tool 消息，但不允许插入 user/system 消息）。位置不合法的 tool 消息视为孤儿，删除。
- **在 `_snip_history` 之后追加一次 `_drop_orphan_tool_results`**：当前代码在 snip 前后都调用了 `_drop_orphan_tool_results`，但 snip 后只调用了一次且紧接着 `_backfill_missing_tool_results`。需要在 snip 后再追加一次 `_drop_orphan_tool_results`，清理 snip 产生的新孤儿。

### P0-2: BDI 认知循环连续失败熔断
- **在 `AgentCognitiveRuntime.run_cognitive_pass_for_session` 中新增 session 级连续失败计数器**：当某 session 的 LLM 调用连续失败 N 次（默认 3），标记该 session 为"认知冷却中"，在接下来 M 分钟（默认 30 分钟）内跳过该 session 的认知 pass，不再发布 nudge。
- **失败计数存储**：在 `AgentCognitiveRuntime` 实例上维护 `self._session_failure_counts: dict[str, dict]`，键为 `session_key`，值为 `{"consecutive_failures": int, "last_failure_at": str, "cooldown_until": str}`。
- **失败检测**：认知 pass 本身不直接调用 LLM——它发布 nudge 到 bus，nudge 被当作 inbound message 触发 LLM 调用。因此失败检测需要在 nudge 发布后异步追踪结果。方案：在 `bus.publish_inbound` 后记录"待确认 nudge"，如果同一 session_key 的下一次认知 pass 开始时该 nudge 仍未产生成功响应（通过检查 session.messages 末尾是否是 LLM error），则递增失败计数。

### P1: cron 通道回复静默丢弃
- **在 `ChannelManager._dispatch_loop` 中，对 `Unknown channel` 的消息改为静默丢弃并记录 INFO 级日志**：当 `msg.channel` 不在 `self.channels` 中且 `msg.channel` 属于已知的"只进不出"通道（如 `cron`）时，不输出 WARNING，改为 INFO 级"cron channel is inbound-only, discarding outbound message"。
- **维护 `inbound_only_channels` 集合**：在 `ChannelManager` 中新增 `self._inbound_only_channels: frozenset[str] = frozenset({"cron"})`，用于区分"配置错误导致的未知通道"与"设计上只进不出的通道"。

## Impact

- **Affected specs**:
  - `enhance-full-agent-traceability`（`cognitive.event.emitted` 事件在熔断后不再发射，需在日志中体现"熔断"状态）
  - `fix-bdi-core-defects`（BDI 认知循环熔断与该 spec 的 BDI 修复方向一致）

- **Affected code**:
  - `OriginAgent/agent/runner.py`（`_drop_orphan_tool_results` 增强位置检查 + snip 后追加清理）
  - `OriginAgent/agent/agent_cognitive_runtime.py`（新增 session 级失败计数器与熔断逻辑）
  - `OriginAgent/channels/manager.py`（cron 等只进不出通道的静默丢弃）

- **Behavioral changes**:
  - **BREAKING（对调试日志读者）**：`Unknown channel: cron` WARNING 不再出现，改为 INFO 级"inbound-only channel, discarding"。
  - cron session 在连续 3 次 LLM 失败后停止接收 nudge，持续 30 分钟。
  - 孤立 tool 消息（位置不合法）会被删除，可能导致部分历史 tool 结果丢失——但这些结果本身就是无效的（没有对应 assistant tool_calls），删除它们比保留导致 LLM 报错更安全。

## ADDED Requirements

### Requirement: Orphan Tool Message Position Validation

The system SHALL validate that every `tool` role message in `messages_for_model` is positioned immediately after the `assistant` message that declared its `tool_call_id` (possibly with other `tool` messages in between, but no `user`/`system` messages interrupting the pair). Tool messages that fail this position check SHALL be dropped before sending to the LLM.

#### Scenario: Tool message separated from its assistant by a user message
- **WHEN** messages contain `[assistant(tool_calls=[id1]), user, tool(tool_call_id=id1)]`
- **THEN** the `tool` message is identified as positionally orphaned
- **AND** it is dropped from `messages_for_model`
- **AND** no LLM error about "Messages with role 'tool' must be a response to a preceding message with 'tool_calls'" occurs

#### Scenario: Tool message immediately follows its assistant
- **WHEN** messages contain `[assistant(tool_calls=[id1]), tool(tool_call_id=id1), user]`
- **THEN** the `tool` message is retained
- **AND** the LLM accepts the message sequence without error

#### Scenario: Snip creates new orphans
- **WHEN** `_snip_history` removes an `assistant` message that had `tool_calls`, leaving its `tool` result messages orphaned
- **THEN** a subsequent `_drop_orphan_tool_results` pass removes those orphaned `tool` messages
- **AND** `messages_for_model` starts at a legal position (user or assistant without unresolved tool_calls)

### Requirement: Cognitive Pass Session-Level Circuit Breaker

The system SHALL track consecutive LLM failure counts per `session_key` in `AgentCognitiveRuntime`. When a session accumulates `cognitive_failure_threshold` (default 3) consecutive failures, the system SHALL enter a `cognitive_cooldown` state for that session, skipping all cognitive pass candidate emission for `cognitive_cooldown_minutes` (default 30) minutes.

#### Scenario: First failure does not trigger cooldown
- **WHEN** a session's nudge triggers an LLM error
- **THEN** `consecutive_failures` for that session increments to 1
- **AND** the next cognitive pass still runs normally

#### Scenario: Threshold reached triggers cooldown
- **WHEN** `consecutive_failures` reaches 3 for a session
- **THEN** the session enters `cognitive_cooldown` until `now + 30 minutes`
- **AND** `cognitive.pass.skipped` event is emitted with `reason="cognitive_cooldown"`
- **AND** no `cognitive.event.emitted` events are produced for that session during cooldown

#### Scenario: Cooldown expires
- **WHEN** the cooldown period elapses
- **THEN** the next cognitive pass runs normally
- **AND** `consecutive_failures` is reset to 0

#### Scenario: Success resets counter
- **WHEN** a session's nudge triggers a successful LLM response (no error)
- **THEN** `consecutive_failures` for that session resets to 0

### Requirement: Inbound-Only Channel Outbound Discard

The system SHALL maintain an `inbound_only_channels` set in `ChannelManager` containing channels that are designed to only receive inbound messages (e.g., `cron`). When the dispatcher encounters an outbound message targeting an `inbound_only` channel, it SHALL silently discard the message and log at INFO level (not WARNING).

#### Scenario: Outbound message to cron channel
- **WHEN** an outbound message with `channel="cron"` is dispatched
- **THEN** the message is discarded (not delivered)
- **AND** an INFO-level log "inbound-only channel 'cron', discarding outbound message" is emitted
- **AND** no WARNING-level "Unknown channel" log is emitted

#### Scenario: Outbound message to truly unknown channel
- **WHEN** an outbound message with `channel="nonexistent"` is dispatched
- **THEN** the existing WARNING-level "Unknown channel" log is emitted (unchanged behavior)

## MODIFIED Requirements

### Requirement: Context Governance Pipeline

The context governance pipeline in `AgentRunner.run` SHALL execute `_drop_orphan_tool_results` both before AND after `_snip_history`, with the post-snip pass validating both `tool_call_id` existence AND positional legality (tool message must follow its declaring assistant without intervening user/system messages).

## REMOVED Requirements

(none)
