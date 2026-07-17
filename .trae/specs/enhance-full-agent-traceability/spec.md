# Agent 全链路行为追踪日志增强 Spec

## Why

用户反馈"想让 Agent 一举一动都在日志里看到，要清晰无比，无所遁形，这样才好定位问题"。当前日志覆盖度调查发现 5 个 P0 级盲区：LLM 返回结果未记录、单次工具执行结果完全缺失、上下文组装审计从未输出到 logger、continuity checkpoint 保存/加载完全静默、工具循环"继续 vs 停止"决策日志缺失。

实际使用中暴露的问题直接对应这些盲区：改简历场景下 Agent 连续调用 22 次工具（其中 18+ 次 exec），但日志看不到工具结果、看不到循环决策、exec 参数全被 `<redacted>` 隐藏，无法判断 Agent 在干什么。

## What Changes

### P0 级（必须，解决"看不到 Agent 在干什么"）

- **新增** `llm.response` 事件：记录 LLM 返回的 `finish_reason`、`tool_call_count`、`content_chars`、`reasoning_chars`、`usage_completion`
- **新增** `tool.complete` 事件：记录单次工具执行的 `status`、`duration_ms`、`result_size`、`error_kind`
- **新增** `context.assembled` 事件：记录上下文组装的 `block_count`、`block_kinds`、`retrieval_sources`、`retrieved_total`、`trimmed_count`、`recovered_continuity_included`
- **新增** `continuity.checkpoint.saved` 和 `continuity.checkpoint.loaded` 事件：记录 checkpoint 的字段数、recent_turns/cold_indices 条数、加载来源
- **新增** `loop.continue` / `loop.finalize` / `loop.max_iterations` 事件：记录工具循环的每次决策（继续因为 tool_calls / 结束因为无 tool_calls / 达到 max_iterations）
- **修改** exec 工具的日志脱敏策略：从完全 `<redacted>` 改为分层记录——INFO 输出命令形状摘要（首个词 + 词数 + 管道操作符，复用已有 `_command_shape`），DEBUG 输出脱敏后的命令前 80 字符

### P1 级（应该，提升排查效率）

- **新增** `working_memory.loaded` 和 `working_memory.saved` 事件
- **新增** `retrieval.fused` 事件：记录长期记忆检索的各源召回数、去重数、top_score
- **新增** `bdi.cycle.complete` 事件：记录 BDI 单轮决策摘要
- **新增** `heartbeat.decided` 事件：记录心跳决策结果
- **修改** `cognitive.pass.skipped` 事件：补充 `active_task_count` 和 `running_subagents` attrs
- **新增** 工具被策略拒绝时的 WARNING 日志
- **修改** 子 Agent hook：统一应用 `sensitive_tool_log_names` 脱敏策略（规则18 安全边界一致性）

## Impact

- **Affected code**:
  - `OriginAgent/agent/runner.py` — `_request_model`（新增 llm.response）、`_run_tool`（新增 tool.complete）、`run` 主循环（新增 loop.* 事件）
  - `OriginAgent/agent/context_assembler.py` — `assemble`（新增 context.assembled）
  - `OriginAgent/agent/agent_runtime.py` — `_save_continuity_checkpoint`/`_load_continuity_checkpoint`（新增 checkpoint 事件）
  - `OriginAgent/agent/progress_hook.py` — exec 脱敏策略改造（line 139-144）
  - `OriginAgent/agent/working_memory.py` — `load`/`save`（新增 working_memory.* 事件）
  - `OriginAgent/agent/retrieval_fusion.py` — `retrieve`（新增 retrieval.fused）
  - `OriginAgent/bdi/deliberation.py` — `run_cycle`（新增 bdi.cycle.complete）
  - `OriginAgent/heartbeat/service.py` — `_decide`（新增 heartbeat.decided）
  - `OriginAgent/agent/agent_cognitive_runtime.py` — cognitive.pass.skipped 补充 attrs
  - `OriginAgent/agent/subagent.py` — `_SubagentHook` 统一脱敏策略
  - `OriginAgent/agent/tools/registry.py` — 复用 `_command_shape` 到 logger 层
- **Affected specs**: `enhance-log-observability`（前一轮的基础设施，本 spec 在其上扩展）
- **Risk level**: LOW（全部是新增日志事件，不改变业务逻辑）
- **Breaking changes**: 无

## ADDED Requirements

### Requirement: LLM 响应事件

`AgentRunner._request_model` SHALL 在 LLM 返回后、return 前，输出 `llm.response` 事件，包含 `finish_reason`、`has_tool_calls`、`tool_call_count`、`content_chars`、`reasoning_chars`、`usage_completion` attrs。

#### Scenario: LLM 返回工具调用

- **WHEN** LLM 返回 `finish_reason="tool_use"` 且包含 2 个 tool_calls
- **THEN** 日志输出 `event.llm.response | finish_reason=tool_use has_tool_calls=True tool_call_count=2 content_chars=0 reasoning_chars=150 usage_completion=300`

#### Scenario: LLM 正常结束

- **WHEN** LLM 返回 `finish_reason="stop"` 且无 tool_calls
- **THEN** 日志输出 `event.llm.response | finish_reason=stop has_tool_calls=False tool_call_count=0 content_chars=500 reasoning_chars=0 usage_completion=120`

### Requirement: 工具执行完成事件

`AgentRunner._run_tool` SHALL 在工具执行完成后、return 前，输出 `tool.complete` 事件，包含 `name`、`status`、`duration_ms`、`result_size`、`error_kind`（仅失败时）attrs。

#### Scenario: 工具成功执行

- **WHEN** `read_file` 工具成功执行，耗时 50ms，返回 2000 字符
- **THEN** 日志输出 `event.tool.complete | name=read_file status=success duration_ms=50.0 result_size=2000`

#### Scenario: 工具执行失败

- **WHEN** `exec` 工具执行失败，耗时 100ms，错误类型为 `CommandTimeoutError`
- **THEN** 日志输出 `event.tool.complete | name=exec status=error duration_ms=100.0 result_size=0 error_kind=CommandTimeoutError`

### Requirement: 上下文组装事件

`ContextAssemblerV2.assemble` SHALL 在组装完成后，输出 `context.assembled` 事件，包含 `block_count`、`block_kinds`、`retrieval_sources`、`retrieved_total`、`trimmed_count`、`recovered_continuity_included` attrs。

#### Scenario: 完整组装

- **WHEN** ContextAssemblerV2 组装了 5 个 block（system/continuity/reference/runtime/user），检索召回 8 条，无裁剪，有 recovered_continuity
- **THEN** 日志输出 `event.context.assembled | block_count=5 block_kinds=['system','continuity','reference','runtime','user'] retrieval_sources=['nearline','session_search'] retrieved_total=8 trimmed_count=0 recovered_continuity_included=True`

### Requirement: Continuity Checkpoint 保存/加载事件

`_save_continuity_checkpoint` SHALL 在保存后输出 `continuity.checkpoint.saved` 事件；`_load_continuity_checkpoint` SHALL 在加载后输出 `continuity.checkpoint.loaded` 事件。

#### Scenario: 保存 checkpoint

- **WHEN** 保存一个包含 4 条 recent_turns、2 条 cold_indices、1 个 current_goal 的 checkpoint
- **THEN** 日志输出 `event.continuity.checkpoint.saved | field_count=6 recent_turns_count=4 cold_indices_count=2 current_goal_present=True open_loops_count=3`

#### Scenario: 加载 checkpoint

- **WHEN** 从 session.metadata 加载 checkpoint
- **THEN** 日志输出 `event.continuity.checkpoint.loaded | source=checkpoint_embedded field_count=6 recent_turns_count=4 cold_indices_count=2`

### Requirement: 工具循环决策事件

`AgentRunner.run` SHALL 在工具循环的每个决策点输出对应事件：
- 有 tool_calls 时：`loop.continue`（含 `reason=tool_calls`、`iteration`、`tool_count`）
- 无 tool_calls 时：`loop.finalize`（含 `reason=no_tool_calls`、`iteration`、`finish_reason`）
- 达到 max_iterations 时：`loop.max_iterations`（含 `iteration`、`tools_used_count`）+ WARNING 级别日志

#### Scenario: 继续循环

- **WHEN** 第 3 轮迭代 LLM 返回 2 个 tool_calls
- **THEN** 日志输出 `event.loop.continue | reason=tool_calls iteration=3 tool_count=2`

#### Scenario: 达到 max_iterations

- **WHEN** 循环达到 max_iterations=200
- **THEN** 日志输出 WARNING 级别 `event.loop.max_iterations | iteration=200 tools_used_count=22 session_key=xxx`

### Requirement: exec 工具分层日志

`progress_hook.py` 中的敏感工具日志 SHALL 从完全 `<redacted>` 改为分层记录：
- exec：INFO 输出命令形状摘要（首个词 + 词数 + 管道操作符，复用 `registry._command_shape`）
- message：INFO 输出 `channel=xxx chat_id=xxx content_chars=N`
- web_fetch：INFO 输出 URL 的 netloc + path（不含 query string）

#### Scenario: exec 工具调用

- **WHEN** Agent 调用 `exec` 工具执行 `git status`
- **THEN** 日志输出 `Tool call: exec(git:2:)` 而非 `Tool call: exec(<redacted>)`

#### Scenario: 包含管道的 exec 调用

- **WHEN** Agent 调用 `exec` 工具执行 `cat foo.txt | grep bar`
- **THEN** 日志输出 `Tool call: exec(cat:3:|)`

### Requirement: 工作记忆加载/保存事件

`WorkingMemoryManager.load` SHALL 在加载后输出 `working_memory.loaded` 事件；`save` SHALL 在保存后输出 `working_memory.saved` 事件。

#### Scenario: 加载工作记忆

- **WHEN** 加载一个包含 current_goal、3 个 open_loops、2 个 attention_items 的 snapshot
- **THEN** 日志输出 `event.working_memory.loaded | has_goal=True open_loops_count=3 attention_items_count=2 priority_facts_count=5`

### Requirement: 长期记忆检索事件

`RetrievalFusion.retrieve` SHALL 在检索完成后输出 `retrieval.fused` 事件，包含 `sources`、`per_source_counts`、`deduped_count`、`trimmed_count`、`final_block_count`、`top_score` attrs。

#### Scenario: 多源检索

- **WHEN** RetrievalFusion 从 nearline 召回 5 条、session_search 召回 3 条，去重后 6 条，裁剪到 4 条
- **THEN** 日志输出 `event.retrieval.fused | sources=['nearline','session_search'] per_source_counts={'nearline':5,'session_search':3} deduped_count=2 trimmed_count=2 final_block_count=4 top_score=0.85`

### Requirement: BDI 单轮决策摘要事件

`DeliberationEngine.run_cycle` SHALL 在 cycle 结束后输出 `bdi.cycle.complete` 事件，包含 `desires_evaluated`、`cached_intentions`、`llm_desires`、`intentions_emitted`、`elapsed_ms` attrs。

### Requirement: 心跳决策事件

`HeartbeatService._decide` SHALL 在决策后输出 `heartbeat.decided` 事件，包含 `action`、`tasks_preview`、`tasks_len` attrs。

### Requirement: 认知 pass 跳过补充 attrs

`cognitive.pass.skipped` 事件 SHALL 补充 `active_task_count` 和 `running_subagents` attrs。

### Requirement: 工具策略拒绝 WARNING

`AgentRunner._run_tool` SHALL 在工具被策略拒绝时输出 WARNING 级别日志，包含工具名、policy_rule、session_key。

#### Scenario: exec 被拒绝

- **WHEN** exec 工具因 `capability_exec_denied` 策略被拒绝
- **THEN** 日志输出 WARNING `Tool exec denied by policy rule='capability_exec_denied' for session=xxx`

### Requirement: 子 Agent 脱敏策略统一

`_SubagentHook` SHALL 接受并应用 `sensitive_tool_log_names` 配置，与主 Agent 的脱敏策略保持一致（规则18 安全边界）。

## MODIFIED Requirements

### Requirement: log_event 输出包含 attrs 文本

（已在 `enhance-log-observability` spec 中定义，本 spec 不修改 `log_event` 本身，仅新增更多调用点。）

## REMOVED Requirements

无。
