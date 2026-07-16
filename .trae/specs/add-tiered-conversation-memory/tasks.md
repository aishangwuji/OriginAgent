# Tasks

## Phase 1：热区 50 轮永远可见

- [ ] Task 1: 实现 `Session.get_hot_history` 按 user turn 计数截断
  - [ ] SubTask 1.1: 在 `OriginAgent/session/manager.py` 新增 `get_hot_history(max_turns=50, *, max_tokens=0)` 方法，按 user message 计数，向前扩展到包含完整 assistant 响应（含 tool_calls/tool_results）
  - [ ] SubTask 1.2: 边界对齐逻辑：从尾部向前数 50 个 user message，确保不从 assistant tool_call 中间截断；若截断点在 tool_call 序列中间，向前扩展到上一个 user turn
  - [ ] SubTask 1.3: 保留现有 `get_history` 的 `max_messages`/`max_tokens` 逻辑作为 fallback
  - [ ] SubTask 1.4: 新增单元测试覆盖：53 轮取最后 50 轮、tool_call 完整性、不足 50 轮全返回

- [ ] Task 2: 修改 `state_build` 使用 `get_hot_history` 注入热区
  - [ ] SubTask 2.1: 在 `OriginAgent/agent/agent_turn_pipeline.py` 的 `state_build`（约 line 414-449）中，将 `session.get_history(**hist_kwargs)` 改为 `session.get_hot_history(max_turns=50, max_tokens=hist_kwargs["max_tokens"])`
  - [ ] SubTask 2.2: 确认热区消息作为独立 message 注入（保持现有 `build_messages` 的 `*history` 展开）
  - [ ] SubTask 2.3: 运行现有 `test_continuity_phase1.py` 回归测试，确认热区注入不破坏现有行为

## Phase 2：温区缓冲区管理

- [ ] Task 3: 实现 `WarmStore` 温区缓冲区管理器
  - [ ] SubTask 3.1: 新建 `OriginAgent/agent/warm_store.py`，定义 `WarmStore` 类，管理温区缓冲区（session-scoped）
  - [ ] SubTask 3.2: `WarmStore` 持久化温区状态到 `session.metadata["warm_buffer"]`（包含消息列表 + 轮次计数）
  - [ ] SubTask 3.3: `WarmStore.append(session, user_msg, assistant_msg)` 方法：追加一轮对话到温区，返回当前温区轮次
  - [ ] SubTask 3.4: `WarmStore.is_full(session, max_turns=50)` 方法：检查温区是否满 50 轮
  - [ ] SubTask 3.5: `WarmStore.drain(session)` 方法：取出温区全部消息并清空缓冲区
  - [ ] SubTask 3.6: `WarmStore.load(session)` / `WarmStore.save(session, buffer)` 方法：从 session.metadata 加载/保存温区状态
  - [ ] SubTask 3.7: 新增单元测试覆盖：追加、满阈值检测、drain 清空、跨会话持久化

- [ ] Task 4: 在 turn 结束时追加到温区
  - [ ] SubTask 4.1: 在 `agent_turn_pipeline.py` 的 `state_save`（约 line 535-611）中，当热区超过 50 轮时，将超出部分的消息追加到温区
  - [ ] SubTask 4.2: 具体逻辑：`session.messages` 保留最后 100 条（热区 50 轮），前面超出部分通过 `WarmStore.append` 追加到温区
  - [ ] SubTask 4.3: 确保热区边界对齐到 user turn，不截断 tool_call
  - [ ] SubTask 4.4: 新增集成测试覆盖：53 轮后温区有 3 轮、100 轮后温区满 50 轮

## Phase 3：温区结构化总结

- [ ] Task 5: 实现 `WarmSummarizer` 温区总结器
  - [ ] SubTask 5.1: 新建 `OriginAgent/agent/warm_summarizer.py`，定义 `WarmSummarizer` 类
  - [ ] SubTask 5.2: `WarmSummarizer.summarize(warm_messages, hot_messages) -> dict` 方法：调用 LLM 生成结构化 JSON 总结，prompt 包含温区 50 轮 + 热区 50 轮的完整内容
  - [ ] SubTask 5.3: 结构化模板字段：`turn_range`、`summary`、`commitments`、`decisions`、`open_questions`、`key_entities`、`timestamp_range`
  - [ ] SubTask 5.4: 使用 `auxiliary_llm.call_llm(task="warm_summary")` 调用 LLM，走 tiered routing
  - [ ] SubTask 5.5: 复用 `MetaCognitionReflector._load_json_payload` 的 JSON 解析逻辑（空内容/invalid JSON 优雅降级）
  - [ ] SubTask 5.6: 新增单元测试覆盖：正常总结、LLM 返回空内容降级、结构化字段完整性

- [ ] Task 6: 温区总结触发与执行
  - [ ] SubTask 6.1: 在 `state_save` 中检查 `WarmStore.is_full(session)`，满 50 轮触发总结
  - [ ] SubTask 6.2: 异步执行总结（复用 `_schedule_background` 调度），不阻塞用户
  - [ ] SubTask 6.3: 总结完成后：`WarmStore.drain` 清空温区，总结结果写入 `warm_summaries.jsonl`，原始消息移到 `warm_archive/{session_key}.jsonl`
  - [ ] SubTask 6.4: 将结构化字段的 `commitments`/`open_questions` 同步写入 `working_memory.open_loops` / `priority_facts`
  - [ ] SubTask 6.5: 新增集成测试覆盖：满 50 轮触发总结、异步执行、温区清空、归档文件生成

## Phase 4：冷区索引渐进式暴露

- [ ] Task 5: 冷区索引持久化与被动注入
  - [ ] SubTask 5.1: 新建 `workspace/warm_summaries.jsonl` 文件，每行一个结构化索引条目（含 session_key、turn_range、summary、key_entities、timestamp_range、locator）
  - [ ] SubTask 5.2: 在 `agent_runtime.py` 的 `_save_continuity_checkpoint` 中，将最近 5 条冷区索引（从 `warm_summaries.jsonl` 读取）写入 checkpoint 的 `cold_indices` 字段
  - [ ] SubTask 5.3: 在 `context.py` 的 `build_recovered_continuity_context` 中，渲染 `cold_indices` 为 `[turn_range] summary (key_entities)` 格式的索引视图
  - [ ] SubTask 5.4: 新增单元测试覆盖：索引写入、checkpoint 包含 cold_indices、渲染格式正确

- [ ] Task 6: `session_search` 支持 `warm_archive` / `warm_summaries` source
  - [ ] SubTask 6.1: 在 `OriginAgent/session/search.py` 的 `SessionSearchService` 中新增 `warm_archive` 和 `warm_summaries` 两个 source
  - [ ] SubTask 6.2: `warm_archive` source 从 `workspace/warm_archive/{session_key}.jsonl` 检索原始消息
  - [ ] SubTask 6.3: `warm_summaries` source 从 `workspace/warm_summaries.jsonl` 检索结构化索引条目
  - [ ] SubTask 6.4: 在 `OriginAgent/agent/tools/session_search.py` 的工具 schema 中暴露新 source
  - [ ] SubTask 6.5: 新增单元测试覆盖：`warm_archive` 检索、`warm_summaries` 检索、与现有 source 不冲突

## Phase 5：废弃旧机制

- [ ] Task 7: 废弃 `maybe_consolidate_by_tokens` 的 token 估算触发
  - [ ] SubTask 7.1: 在 `memory.py` 的 `maybe_consolidate_by_tokens` 中，废弃 token 估算循环压缩逻辑，改为检查温区轮次
  - [ ] SubTask 7.2: 当温区满 50 轮时触发温区总结（复用 Task 5 的 `WarmSummarizer`），而非循环 `archive` 直到 token 低于 target
  - [ ] SubTask 7.3: 保留 `Consolidator.archive` 方法用于温区总结的底层调用，但触发逻辑改为温区轮次
  - [ ] SubTask 7.4: 运行现有 `test_memory.py` 回归测试，确认不破坏现有行为（必要时更新测试）

- [ ] Task 8: 废弃 `AutoCompact._archive` 的删除行为
  - [ ] SubTask 8.1: 在 `autocompact.py` 的 `_archive` 中，将 `session.messages = kept_msgs` 改为：归档消息移到 `warm_archive/{session_key}.jsonl`，`session.messages` 保留热区 50 轮
  - [ ] SubTask 8.2: 保留 `cold_archive` 归档逻辑作为兜底
  - [ ] SubTask 8.3: 运行现有 `test_autocompact.py`（如存在）回归测试

## Phase 6：跨会话三级重建

- [ ] Task 9: 实现 session 恢复时的三级重建
  - [ ] SubTask 9.1: 在 `agent_runtime.py` 的 `_load_continuity_checkpoint` 中，从 `session.metadata["warm_buffer"]` 恢复温区状态
  - [ ] SubTask 9.2: 从 `warm_summaries.jsonl` 读取最近 5 条冷区索引，写入 checkpoint 的 `cold_indices` 字段
  - [ ] SubTask 9.3: 确认热区从 `session.messages` 尾部取 50 轮（`get_hot_history`）
  - [ ] SubTask 9.4: 新增集成测试覆盖：进程重启后热区/温区/冷区状态正确恢复

## Phase 7：验证与回归

- [ ] Task 10: 运行完整测试套件
  - [ ] SubTask 10.1: 运行 `.\.venv\Scripts\python.exe -m pytest tests/ -x --basetemp=.pytest_tmp` 确认无阻断性失败
  - [SubTask 10.2: 重点验证 `tests/agent/test_continuity_phase1.py`、`tests/session/`、`tests/agent/test_memory.py`（如存在）
  - [ ] SubTask 10.3: 验证新增的温区/冷区测试全部通过

# Task Dependencies
- Task 2 依赖 Task 1（热区注入需要 `get_hot_history` 先实现）
- Task 4 依赖 Task 3（温区追加需要 `WarmStore` 先实现）
- Task 5（WarmSummarizer）独立于 Task 3/4，可并行
- Task 6 依赖 Task 3 + Task 5（温区总结触发需要 `WarmStore` + `WarmSummarizer`）
- Task 5（冷区索引）依赖 Task 6（温区总结生成冷区索引）
- Task 6（session_search）依赖 Task 5（冷区索引）的文件格式
- Task 7 依赖 Task 6（温区总结触发替代 token 估算）
- Task 8 独立于其他任务，可并行
- Task 9 依赖 Task 3 + Task 5（冷区索引）+ Task 1（热区）
- Task 10 依赖所有其他任务完成
