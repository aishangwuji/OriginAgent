# Tasks

> 遵循规则34（验证先行）：先写测试，再实现。
> 遵循规则37（原子提交）：每个 Task 对应独立 commit。
> 遵循规则33（手术式变更）：仅修改与本次任务直接相关的代码，不顺手重构。
> 遵循规则32（最小化实现）：不引入未被需求要求的功能开关或抽象层。

## P0 级（红线闭集——状态一致性）

### Task 1: 修复 continuity checkpoint 的 `setdefault` bug

- [x] Task 1: 将 `agent_runtime.py:644` 的 `session.metadata.setdefault("continuity_checkpoint_v1", checkpoint)` 改为直接赋值
  - [x] SubTask 1.1（验证先行）: 在 `tests/agent/test_continuity_checkpoint.py` 新增 `test_repeated_save_updates_checkpoint`
    - 构造一个 session，调用 `_save_continuity_checkpoint` 两次，第二次使用不同的 `recent_turns_summary`
    - 断言 `session.metadata["continuity_checkpoint_v1"]` 等于第二次的 checkpoint 内容（特别是 `recent_turns_summary`）
    - 断言两次都触发了 `continuity.checkpoint.saved` 事件
  - [x] SubTask 1.2（实现）: 修改 `OriginAgent/agent/agent_runtime.py:644`，把 `setdefault` 改为 `session.metadata["continuity_checkpoint_v1"] = checkpoint`
  - [x] SubTask 1.3（验证）: 8/8 PASSED

## P1 级（日志去重——核心诉求）

### Task 2: 为 `WorkingMemoryManager.load` 引入 turn-scoped 缓存

- [x] Task 2: 在 `WorkingMemoryManager` 内部缓存 `WorkingMemorySnapshot`，按 `session.key` 索引
  - [x] SubTask 2.1（验证先行）: 在 `tests/agent/test_working_memory.py` 新增 `test_load_caches_snapshot_per_session`
  - [x] SubTask 2.2（验证先行）: 新增 `test_save_invalidates_cache`
  - [x] SubTask 2.3（验证先行）: 新增 `test_clear_invalidates_cache`
  - [x] SubTask 2.4（实现）: `__init__` 新增 `self._cache`；`load` 开头命中短路；`load`/`save` 末尾写入；`clear` 末尾 pop
  - [x] SubTask 2.5（验证）: 10/10 PASSED（含 3 新增），71 回归测试全通过

### Task 3: 让 `working_memory.loaded` / `working_memory.saved` 按状态签名去重

- [x] Task 3: 引入状态签名机制，仅在签名变化时输出 `log_event`
  - [x] SubTask 3.1（验证先行）: 在 `tests/agent/test_working_memory.py` 新增 `test_loaded_event_deduped_on_identical_state`
  - [x] SubTask 3.2（验证先行）: 新增 `test_loaded_event_emitted_on_state_change`
  - [x] SubTask 3.3（验证先行）: 新增 `test_saved_event_deduped_on_no_change`
  - [x] SubTask 3.4（实现）: 新增 `_compute_signature` 静态方法（md5 of json-dumped goal+open_loops+attention_items+priority_facts）；`load`/`save` 包裹签名比对；`clear` 末尾 pop 签名 baseline
  - [x] SubTask 3.5（验证）: 13/13 PASSED（含 3 新增），无回归

## P2 级（审计完整性）

### Task 4: 为 `_build_user_content` 的 text block 添加 `_meta.kind`

- [x] Task 4: 修改 `context.py` 的 `_build_user_content`，给 text block 添加 `_meta: {"kind": "user_text"}`
  - [x] SubTask 4.1（验证先行）: 在 `tests/agent/test_context_assembler_logging.py` 新增 `test_user_text_block_has_meta_kind`
  - [x] SubTask 4.2（验证先行）: 新增 `test_context_assembled_block_kinds_no_none_for_user_text`
  - [x] SubTask 4.3（实现）: 修改 `OriginAgent/agent/context.py:1104`，给 text block 添加 `_meta: {"kind": "user_text"}`；`context_assembler.py` 的 `block_kinds` 提取逻辑已从 `_meta.kind` 读取，无需改动
  - [x] SubTask 4.4（验证）: 12/12 PASSED（同步更新 3 处既有测试的精确断言）

# Task Dependencies

## P0 → P1
- Task 2、3 不依赖 Task 1（独立修复路径），但建议先完成 Task 1（P0 优先）

## P1 内部依赖
- Task 3 依赖 Task 2（缓存机制让"状态签名 baseline"自然落在 manager 实例上，且去重测试需要缓存已就位才能稳定复现"同状态多次 load"场景）

## P2 独立
- Task 4 与 Task 1-3 无依赖，可并行

## 建议执行顺序
1. **第一批**：Task 1（P0 修复）+ Task 4（P2 审计补全）—— 两者独立，可并行
2. **第二批**：Task 2（P1 缓存）
3. **第三批**：Task 3（P1 去重，依赖 Task 2）
