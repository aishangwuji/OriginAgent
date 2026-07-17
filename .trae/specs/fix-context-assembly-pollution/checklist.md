# Checklist

## P0: recovered_continuity 结构化渲染

- [x] `build_recovered_continuity_context` 输出不再包含 `json.dumps(dict(snapshot))` 生成的完整 JSON
- [x] 输出包含 `current_goal`/`current_plan`/`open_loops`/`active_constraints`/`pending_confirmation_refs`/`updated_at` 的结构化渲染（非空字段）
- [x] `recent_turns_text` 和 `cold_indices_text` 渲染保留不变
- [x] 部分字段为空时对应标题不渲染
- [x] 测试 `tests/agent/test_context_recovered_continuity.py` 全部通过（8 passed）
- [ ] commit message 说明"为什么"（移除整体 dump 避免历史归档污染上下文）— 待提交时验证

## P1: Working Memory 字段时间衰减

- [x] `WorkingMemoryManager.load` 检查 `updated_at` 超过 30 分钟时清空易过期字段
- [x] 清空的字段为：`attention_items`/`open_loops`/`active_constraints`/`pending_questions`/`tool_residue`
- [x] `current_goal` 不受时间衰减影响（由 `_hydrate_goal` 独立判断）
- [x] `updated_at` 缺失或解析失败时不阻塞（容错降级）
- [x] 衰减触发时有 `logger.debug` 记录
- [x] 测试 `tests/agent/test_working_memory.py` 全部通过（5 passed）
- [x] 回归测试无破坏（含 anti_confabulation 6 用例，共 27 passed）

## P2: recent_turns_summary 截断可配置

- [x] `ContextConfig` 新增 `recent_turns_summary_max_chars: int = 800` 字段
- [x] `_extract_recent_turns_summary` 使用配置值而非硬编码 500
- [x] 方法签名向后兼容（`max_chars` 有默认值）
- [x] 测试 `tests/agent/test_continuity_checkpoint.py` 全部通过（5 passed）

## 通用

- [ ] 每个 Task 对应独立原子 commit（规则37）— 待提交时验证
- [x] 无投机性设计/过度抽象（规则32）
- [x] 无任务范围外的越界改动（规则33）— Task 3 修复的 test helper 回归属本次改动直接导致
- [x] 测试先于实现编写（规则34）— 三个 Sub-Agent 均确认 TDD 红→绿
- [x] 无硬编码敏感信息（规则18）
