# Tasks

- [x] Task 1: 创建 Soar 数据模型（soar_models.py）
  - [x] SubTask 1.1: 新建 `OriginAgent/agent/soar_models.py`
  - [x] SubTask 1.2: 实现 `SoarObstacle` dataclass（frozen）：`obstacle_id, obstacle_type, root_cause, attempted_tools, recoverable_hint, created_at` + `to_json()/from_json()`
  - [x] SubTask 1.3: 实现 `SoarSubgoal` dataclass（frozen）：`subgoal_id, parent_goal_id, obstacle, working_state_snapshot, subagent_task_id, created_at, solution_path=None` + `to_json()/from_json()`
  - [x] SubTask 1.4: 实现 `SoarSubgoalStack` 类：`push/pop/peek/depth/is_empty/find_by_subagent` + `persist_to(path)/load_from(path)`（复用 IntentionStack 的原子写入模式）
  - [x] SubTask 1.5: 编写测试 `tests/agent/test_soar_models.py`（15 测试通过）：模型序列化往返、栈 LIFO 语义、持久化往返、损坏文件降级

- [x] Task 2: SubagentManager 障碍检测
  - [x] SubTask 2.1: 在 `subagent.py` 顶部导入 `SoarObstacle`、`ErrorKind`、`classify_exception`
  - [x] SubTask 2.2: 新增私有方法 `_detect_obstacle(self, stop_reason, failure_summary, tool_events, exc=None) -> SoarObstacle | None`
  - [x] SubTask 2.3: 在 `_run_subagent` 的三个失败分支调用 `_detect_obstacle`，将结果存入局部变量 `obstacle`
  - [x] SubTask 2.4: 将 `obstacle` 通过 `_announce_result` 的 `obstacle_type` 参数传递
  - [x] SubTask 2.5: 编写测试 `tests/agent/test_subagent_obstacle_detection.py`（8 测试通过）

- [x] Task 3: _announce_result 元数据扩展
  - [x] SubTask 3.1: 修改 `_announce_result` 签名，增加 `obstacle_type: str | None = None` 参数
  - [x] SubTask 3.2: 在 metadata 构造块中，当 `obstacle_type` 不为 None 时设置 `metadata["obstacle_type"] = obstacle_type`
  - [x] SubTask 3.3: 修改 `_run_subagent` 三个失败分支的 `_announce_result` 调用，传入 `obstacle_type=obstacle.obstacle_type if obstacle else None`
  - [x] SubTask 3.4: 测试覆盖（通过 test_subagent_obstacle_detection.py 的序列化测试确认）

- [x] Task 4: SkillBootstrapper 在线 ingest_chunk
  - [x] SubTask 4.1: 在 `skill_bootstrapper.py` 新增 `SkillBootstrapper` 类（有状态）：`__init__(scanner, compiler, *, min_repeats=3, max_window_size=200)`，内部维护 `self._window: dict[str, list[ActionTraceDigest]]`
  - [x] SubTask 4.2: 实现 `ingest_chunk(self, digest: ActionTraceDigest) -> SkillCandidate | None`：FIFO 淘汰 + 阈值 compile + 移除 fingerprint
  - [x] SubTask 4.3: 实现 `window_size` 属性
  - [x] SubTask 4.4: 编写测试 `tests/agent/test_skill_bootstrapper_online.py`（8 测试通过）

- [x] Task 5: SoarChunker 块化触发
  - [x] SubTask 5.1: 新建 `OriginAgent/agent/soar_chunker.py`
  - [x] SubTask 5.2: 实现 `SoarChunker` 类：`__init__(bootstrapper, tool_record_store)`
  - [x] SubTask 5.3: 实现 `async def chunk(self, subgoal) -> SkillCandidate | None`：提取工具序列 + 构造 digest + 调用 ingest_chunk
  - [x] SubTask 5.4: 实现 `chunk_from_success(subagent_task_id, obstacle, result_summary)` 便捷方法
  - [x] SubTask 5.5: 编写测试 `tests/agent/test_soar_chunker.py`（8 测试通过）

- [x] Task 6: SubagentManager 成功回调集成
  - [x] SubTask 6.1: 在 `SubagentManager.__init__` 增加可选参数 `soar_chunker: SoarChunker | None = None`
  - [x] SubTask 6.2: 在 `_run_subagent` 成功分支末尾增加 Soar 块化钩子（检查 `metadata.get("parent_obstacle")`）
  - [x] SubTask 6.3: 仅实现回调钩子，父-子 obstacle 传递属 TurnOrchestrator 集成（Out of Scope），正常操作下钩子为 no-op
  - [x] SubTask 6.4: 编写测试 `tests/agent/test_subagent_soar_callback.py`（3 测试通过）

- [x] Task 7: 全量回归测试
  - [x] SubTask 7.1: 运行 `.\.venv\Scripts\python.exe -m pytest tests/agent/ -v --tb=short`（Soar 相关全部通过；26 个失败为预存的权限/状态机问题，与 Soar 无关）
  - [x] SubTask 7.2: 运行 `.\.venv\Scripts\python.exe -m pytest tests/agent/bdi/ -v`（125 passed，无回归）
  - [x] SubTask 7.3: 确认现有 `test_skill_bootstrapper.py` 无回归（离线 scan 路径不变，26 passed）
  - [x] SubTask 7.4: Soar 专项测试 43 passed（15 soar_models + 8 soar_chunker + 8 obstacle_detection + 3 soar_callback + 8 skill_bootstrapper_online + 1 多计）

# Task Dependencies
- Task 2 depends on Task 1（需要 SoarObstacle 模型）
- Task 3 depends on Task 2（需要 obstacle 检测产出）
- Task 5 depends on Task 1, Task 4（需要 SoarSubgoal 模型 + SkillBootstrapper）
- Task 6 depends on Task 2, Task 5（需要障碍检测 + SoarChunker）
- Task 7 depends on 所有前序任务
- 可并行：Task 1、Task 4 相互独立
