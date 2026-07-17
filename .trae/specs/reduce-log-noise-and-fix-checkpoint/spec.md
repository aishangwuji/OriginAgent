# Reduce Log Noise and Fix Checkpoint Persistence Spec

## Why

上一轮 `enhance-full-agent-traceability` spec 暴露了三个根因问题：
1. **P0 数据丢失**：`continuity_checkpoint_v1` 使用 `session.metadata.setdefault(...)`，导致首次写入后永远不再更新。这是用户观察到"答非所问、上下文缺失"的根因——`recent_turns_summary` 保存了 4 条但加载时为 0 条。
2. **P1 日志噪音**：`WorkingMemoryManager.load` 每轮被调用 8 次（共 12 个调用点），无任何缓存，每次都触发完整反序列化 + 衰减 + hydration + `log_event`。8 次/turn 的 `working_memory.loaded` 输出几乎完全相同，淹没了真正有价值的事件。
3. **P2 审计缺口**：`context.py` 的 `_build_user_content` 产生的 text block 没有 `_meta.kind` 字段，导致 `context.assembled` 事件的 `block_kinds` 出现 `None`，无法审计用户内容块的存在。

用户反馈："现在有很多重复输出的内容，可以只让他输出一次，然后后续只有在有变化的时候产出日志，别每次都产出日志，产生很多噪音。"

## What Changes

### P0 修复（不可协商，规则0红线闭集——状态一致性）
- **修复 `setdefault` bug**：将 `session.metadata.setdefault("continuity_checkpoint_v1", checkpoint)` 改为直接赋值 `session.metadata["continuity_checkpoint_v1"] = checkpoint`，让 checkpoint 能在每轮真实更新。

### P1 日志去重（核心诉求——"只输出一次，后续只在变化时输出"）
- **为 `WorkingMemoryManager.load` 引入 turn-scoped 缓存**：同一 session_key 的连续 `load` 调用复用同一份 deserialized snapshot，避免 8 次/turn 的重复反序列化与 hydration。
- **让 `working_memory.loaded` 事件按状态签名去重**：仅在状态签名（goal + open_loops + attention_items + priority_facts 的哈希）发生变化时输出日志。
- **让 `working_memory.saved` 事件去重**：`save` 已记录 `goal_changed`，但当连续两次 save 未改变任何字段时不输出 `working_memory.saved` 日志。
- **在 session 边界清理缓存**：通过新增 `invalidate(session_key)` 方法或借由 session 重建触发清空，防止跨 turn 读取过期快照（规则5）。

### P2 审计完整性
- **为 `_build_user_content` 的 text block 添加 `_meta.kind = "user_text"`**：让 `context.assembled` 的 `block_kinds` 不再出现 `None`。

## Impact

- **Affected specs**:
  - `enhance-full-agent-traceability`（依赖本次修复的 `working_memory.loaded`/`saved` 去重行为，否则日志噪音仍存）
  - `enhance-log-observability`（`log_event` 函数本身不变）
  - `fix-context-assembly-pollution`（`_meta.kind` 补全后 `block_kinds` 审计完整）

- **Affected code**:
  - `OriginAgent/agent/agent_runtime.py`（line 644 setdefault → 直接赋值）
  - `OriginAgent/agent/working_memory.py`（引入 turn-scoped cache + 状态签名去重）
  - `OriginAgent/agent/context.py`（line 1104 附近，text block 补 `_meta.kind`）

- **Behavioral changes**:
  - **BREAKING（仅对调试日志读者）**：`working_memory.loaded` 不再每轮出现 8 条，仅首次出现 + 状态变化时出现。需在用户文档（如有）中提示日志读者这一变化。
  - checkpoint 持久化行为修正：之前看到的"saved 4 条 / loaded 0 条"假象消失。

## ADDED Requirements

### Requirement: Turn-scoped Working Memory Cache

The system SHALL cache `WorkingMemorySnapshot` per `session_key` inside `WorkingMemoryManager`, so that repeated `load()` calls within the same turn reuse the cached snapshot without re-deserializing session metadata, re-applying field decay, or re-hydrating goals/reminders.

#### Scenario: Multiple loads within same turn
- **WHEN** `load(session)` is called N times (N > 1) for the same `session.key` without any intervening `save()` or `invalidate()`
- **THEN** deserialization, `_apply_field_decay`, `_hydrate_goal`, `_hydrate_due_reminders` execute exactly once
- **AND** `working_memory.loaded` log event is emitted at most once per unique state signature

#### Scenario: Cache invalidation on save
- **WHEN** `save(session, snapshot)` is called
- **THEN** the cache for `session.key` is updated with the new snapshot
- **AND** subsequent `load()` returns the freshly saved snapshot

#### Scenario: Cache invalidation on clear
- **WHEN** `clear(session)` is called
- **THEN** the cache for `session.key` is evicted
- **AND** subsequent `load()` returns a fresh empty `WorkingMemorySnapshot`

### Requirement: State-Signature-Based Log Deduplication

The system SHALL compute a state signature for `WorkingMemorySnapshot` covering `current_goal`, `open_loops`, `attention_items`, `priority_facts`, and SHALL only emit `working_memory.loaded` / `working_memory.saved` events when the signature differs from the previously emitted signature for the same `session.key`.

#### Scenario: Identical state re-loaded
- **WHEN** `load()` is called and the resulting snapshot's state signature matches the last emitted signature for this `session.key`
- **THEN** no `working_memory.loaded` event is emitted
- **AND** a debug-level log line (not `log_event`) records the suppression for traceability

#### Scenario: State changes
- **WHEN** the snapshot's state signature differs from the last emitted signature
- **THEN** `working_memory.loaded` (or `working_memory.saved`) event is emitted as before
- **AND** the new signature becomes the comparison baseline

## MODIFIED Requirements

### Requirement: Continuity Checkpoint Persistence

The system SHALL persist `continuity_checkpoint_v1` to `session.metadata` via direct assignment (`session.metadata["continuity_checkpoint_v1"] = checkpoint`), NOT via `setdefault`. The checkpoint MUST reflect the latest state at each save call, so that subsequent loads within the same or later turns see updated `recent_turns_summary`, `cold_indices`, `current_goal`, `open_loops`, etc.

#### Scenario: Repeated saves update the checkpoint
- **WHEN** `_save_continuity_checkpoint` is called twice in sequence with different `recent_turns_summary` values
- **THEN** `session.metadata["continuity_checkpoint_v1"]` holds the second value
- **AND** `continuity.checkpoint.saved` event reflects the second save's counts

### Requirement: User Content Block Metadata

The system SHALL attach `_meta.kind = "user_text"` to every text block produced by `_build_user_content` in `context.py`, so that `ContextAssemblerV2.assemble`'s `block_kinds` audit field never contains `None` for user-originated content.

#### Scenario: User content block audit
- **WHEN** a user message with text content is assembled into context
- **THEN** the resulting text block contains `_meta: {"kind": "user_text"}`
- **AND** `context.assembled` event's `block_kinds` list includes `"user_text"` rather than `None`

## REMOVED Requirements

(none)
