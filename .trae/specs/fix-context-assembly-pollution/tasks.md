# Tasks

> 遵循规则34（验证先行）：每个任务先写/更新测试，再实现，最后确认验证通过。
> 遵循规则37（原子提交）：每个 Task 对应一个独立可 revert 的 commit。

## P0：移除 recovered_continuity 整体 JSON dump（根因 1：聊起很久之前的事）

- [x] Task 1: 移除 `build_recovered_continuity_context` 的整体 JSON dump 并改为结构化渲染
  - [ ] SubTask 1.1（验证先行）: 在 `tests/agent/test_context_recovered_continuity.py`（如不存在则新建）新增/更新测试，断言：
    - 输出文本不包含 `json.dumps` 生成的完整 JSON（如不包含 `"session_key":` 这类 JSON 键值对格式）
    - 输出包含 `## Current Goal` / `## Open Loops` 等结构化标题（当字段非空时）
    - 输出仍包含 `## Recent Turns Summary` 和 `## Cold Indices`（当对应数据存在时）
    - 部分字段为空时，对应标题不渲染
  - [ ] SubTask 1.2（实现）: 修改 `OriginAgent/agent/context.py` 的 `build_recovered_continuity_context`（约 line 554-597）：
    - 移除 `f"{json.dumps(dict(snapshot), ensure_ascii=False, indent=2)}\n"` 行
    - 新增结构化渲染：`current_goal`/`current_plan`/`open_loops`/`active_constraints`/`pending_confirmation_refs`/`updated_at`，每个字段非空时渲染为 `## {Field Name}\n{content}` 格式
    - 保留已有的 `recent_turns_text` 和 `cold_indices_text` 渲染逻辑不变
  - [ ] SubTask 1.3（验证）: 运行 `.\.venv\Scripts\python.exe -m pytest tests/agent/test_context_recovered_continuity.py -v`，确认所有测试通过

## P1：Working Memory 字段时间衰减（根因 2：答非所问/表现颠）

- [x] Task 2: 给 `WorkingMemoryManager.load` 加基于 `updated_at` 的 30 分钟时间衰减
  - [ ] SubTask 2.1（验证先行）: 在 `tests/agent/test_working_memory.py`（如不存在则新建）新增测试，断言：
    - `updated_at` 距当前时间超过 30 分钟的 snapshot，load 后 `attention_items`/`open_loops`/`active_constraints`/`pending_questions`/`tool_residue` 为空列表
    - `updated_at` 距当前时间在 30 分钟内的 snapshot，load 后上述字段保留原值
    - `updated_at` 为空或格式错误时，不阻塞加载，保留所有字段原值
    - `current_goal` 不受时间衰减影响（由 `_hydrate_goal` 独立判断）
  - [ ] SubTask 2.2（实现）: 修改 `OriginAgent/agent/working_memory.py` 的 `load` 方法（约 line 101-110）：
    - 在 `WorkingMemorySnapshot.from_json` 之后、`_hydrate_goal` 之前，新增 `_apply_field_decay(snapshot)` 调用
    - 新增 `_apply_field_decay` 静态方法：解析 `snapshot.updated_at`，若距当前 UTC 时间超过 30 分钟，则将 `attention_items`/`open_loops`/`active_constraints`/`pending_questions`/`tool_residue` 清空为 `[]`
    - 时间解析失败时不清空（容错降级）
    - 加 `logger.debug` 记录衰减触发事件，便于排查
  - [ ] SubTask 2.3（验证）: 运行 `.\.venv\Scripts\python.exe -m pytest tests/agent/test_working_memory.py -v`，确认所有测试通过
  - [ ] SubTask 2.4（回归）: 运行 `.\.venv\Scripts\python.exe -m pytest tests/ -k "working_memory or context" -v`，确认无回归

## P2：recent_turns_summary 截断长度可配置（根因 3：上下文缺失）

- [x] Task 3: 将 `_extract_recent_turns_summary` 截断长度改为配置项
  - [ ] SubTask 3.1（验证先行）: 在 `tests/agent/test_continuity_checkpoint.py`（如不存在则新建）新增测试，断言：
    - 默认配置下，每条消息截断到 800 字符
    - 配置 `recent_turns_summary_max_chars = 1200` 时，截断到 1200 字符
  - [ ] SubTask 3.2（实现）: 修改 `OriginAgent/config/schema.py` 的 `ContextConfig`，新增 `recent_turns_summary_max_chars: int = 800` 字段
  - [ ] SubTask 3.3（实现）: 修改 `OriginAgent/agent/agent_runtime.py` 的 `_extract_recent_turns_summary`（约 line 682-709）：
    - 方法签名新增 `max_chars: int = 800` 参数（保持向后兼容）
    - 将 `content = str(content)[:500]` 改为 `content = str(content)[:max_chars]`
    - 调用方 `_save_continuity_checkpoint`（约 line 632）传入配置值
  - [ ] SubTask 3.4（验证）: 运行 `.\.venv\Scripts\python.exe -m pytest tests/agent/test_continuity_checkpoint.py -v`，确认所有测试通过

# Task Dependencies

- Task 1、Task 2、Task 3 之间无依赖，可并行执行
- 建议执行顺序：Task 1（P0）→ Task 2（P1）→ Task 3（P2），按优先级串行提交
