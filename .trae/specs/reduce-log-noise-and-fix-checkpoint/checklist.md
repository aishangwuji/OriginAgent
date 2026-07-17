# Checklist

## P0 红线闭集——状态一致性

- [x] Task 1: `agent_runtime.py:644` 的 `setdefault` 已改为直接赋值 `session.metadata["continuity_checkpoint_v1"] = checkpoint`
- [x] Task 1: 新增测试 `test_repeated_save_updates_checkpoint` 验证第二次 save 会覆盖第一次的值
- [x] Task 1: 既有 `test_continuity_checkpoint.py` 测试不回归（8/8 PASSED）
- [x] Task 1: checkpoint 不再出现"saved 4 条 / loaded 0 条"的不一致

## P1 日志去重——核心诉求

### Task 2: turn-scoped cache

- [x] Task 2: `WorkingMemoryManager.__init__` 新增 `self._cache: dict[str, WorkingMemorySnapshot]`
- [x] Task 2: `load` 开头检查缓存，命中则直接返回，不触发反序列化/hydration/log_event
- [x] Task 2: `load` 末尾写入缓存
- [x] Task 2: `save` 末尾更新缓存
- [x] Task 2: `clear` 末尾清除缓存
- [x] Task 2: 测试 `test_load_caches_snapshot_per_session` 通过
- [x] Task 2: 测试 `test_save_invalidates_cache` 通过
- [x] Task 2: 测试 `test_clear_invalidates_cache` 通过
- [x] Task 2: 既有 `test_working_memory.py` 测试不回归（10/10 PASSED，71 回归测试全通过）

### Task 3: 状态签名去重

- [x] Task 3: 新增 `_compute_signature(snapshot) -> str` 方法（基于 `current_goal + open_loops + attention_items + priority_facts` 的稳定 md5 哈希，显式排除 `updated_at`）
- [x] Task 3: `__init__` 新增 `self._last_emitted_signature: dict[str, str]`
- [x] Task 3: `load` 末尾按签名对比决定是否输出 `log_event("working_memory.loaded", ...)`
- [x] Task 3: `save` 末尾按签名对比决定是否输出 `log_event("working_memory.saved", ...)`
- [x] Task 3: `clear` 末尾清除 `self._last_emitted_signature[session.key]`
- [x] Task 3: 抑制时输出 `logger.debug` 记录抑制事件（保持可追溯性，规则1）
- [x] Task 3: 测试 `test_loaded_event_deduped_on_identical_state` 通过
- [x] Task 3: 测试 `test_loaded_event_emitted_on_state_change` 通过
- [x] Task 3: 测试 `test_saved_event_deduped_on_no_change` 通过

## P2 审计完整性

- [x] Task 4: `context.py:1104` 附近的 text block 添加 `_meta: {"kind": "user_text"}`
- [x] Task 4: `context_assembler.py` 的 `block_kinds` 提取逻辑已从 `_meta.kind` 读取，无需改动
- [x] Task 4: 测试 `test_user_text_block_has_meta_kind` 通过
- [x] Task 4: 测试 `test_context_assembled_block_kinds_no_none_for_user_text` 通过
- [x] Task 4: 既有 `test_context_assembler_logging.py` 测试不回归
- [x] Task 4: 既有 `test_context.py` / `test_context_documents.py` / `test_context_prompt_cache.py` 测试不回归（12/12 PASSED，3 处精确断言已同步更新）

## 规则合规性自检

- [x] 规则3（边界数据校验）：本次变更不涉及外部输入，无需新增校验
- [x] 规则5（缓存值声明生命周期）：`WorkingMemoryManager._cache` 的生命周期在 `save` / `clear` 时显式失效；同一 turn 内复用，跨 turn 通过 `save` 触发更新；不存在"过期快照"风险，因为 `load` 总是从 session.metadata 重新反序列化（缓存未命中时）
- [x] 规则7（状态变化审计）：新增的 `_cache` 与 `_last_emitted_signature` 是 manager 实例字段，单一写入路径（`load`/`save`/`clear`），无并发竞争（manager 在单 Agent 实例内使用）
- [x] 规则9（session token 隔离）：不涉及异步资源开关对
- [x] 规则12（幂等）：不涉及可重试写操作
- [x] 规则14（关键假设断言化）：假设"同一 turn 内 session.metadata 不被外部修改"——通过 `save` 写入缓存+session 同步维护，无需新增断言
- [x] 规则18（安全边界）：不涉及身份操作/凭证/SQL
- [x] 规则27（变更交付清单）：本 spec 文档即清单，每 Task 对应独立 commit
- [x] 规则33（手术式变更）：仅修改与本次任务直接相关的代码，不顺手重构 working_memory.py 的其它方法
- [x] 规则34（验证先行）：每个 Task 的 SubTask.1 都是先写测试
- [x] 规则37（原子提交）：每个 Task 独立 commit，commit message 说明"为什么"（如"修复 setdefault 导致 checkpoint 不更新"）

## 系统认知同步

- [x] 本次变更未触及 `systemmap/` 中已记录的业务规则/状态机——working_memory 的状态机未变，仅缓存层与日志去重新增
