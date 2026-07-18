# Fix Cron Session Key Routing & Background Review Token Budget Spec

## Why

生产日志暴露两个独立根因：

1. **「两个对话重复」根因（P0）**：`on_cron_job` 在 [commands.py:974](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/cli/commands.py#L974) 硬编码 `cron_session_key = f"cron:{job.id}"`，完全忽略 `job.payload.session_key` 字段。用户的 cron job `8d88e717` 配置了 `"sessionKey": "tenant:guest"` 期望在用户 session 共享上下文，但 cron 实际创建独立 `cron:8d88e717` session——cron session 与 user session 独立处理类似内容（confirmation summary、邮件检查），日志里交替出现两套 `event.context.assembled` / `event.llm.request`，且 cron session 的 `_active_tasks` 注册到错误 key，cognitive_scheduler 无法正确感知该 session 的活动状态。

2. **Background review invalid JSON（P2）**：[13:51:14 日志](file:///d:/Demo/OpenHome/OriginAgentclient) 显示 `Background review returned invalid JSON in both content (2281 chars) and reasoning_content (32547 chars)`——deepseek-v4-flash 的 reasoning model 在 `reasoning_content` 里耗尽 token 预算（32547 chars ≈ 8000+ tokens），导致 `content` 字段的 JSON 不完整。当前 [schema.py:399-404](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/config/schema.py#L399-L404) 默认 `max_tokens=8192` 不够 reasoning model 使用。

## What Changes

### P0：on_cron_job 尊重 payload.sessionKey
- **修改 [commands.py:974](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/cli/commands.py#L974)**：将 `cron_session_key = f"cron:{job.id}"` 改为 `cron_session_key = job.payload.session_key or f"cron:{job.id}"`
- **修改 [commands.py:1019](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/cli/commands.py#L1019)**：将 `_last_assistant_content_from_session(f"cron:{job.id}")` 改为使用 resolved `cron_session_key` 变量
- **保持 [commands.py:913](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/cli/commands.py#L913) 不变**：`cron_session = agent.sessions.get_or_create(f"cron:{job.id}")` 仍用 `cron:{job.id}` 扫描 denied_tools——这是 cron job 自身的能力边界历史记录，与 turn 执行 session 解耦

### P2：BackgroundReviewConfig.max_tokens 默认值提升
- **修改 [schema.py:399-404](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/config/schema.py#L399-L404)**：`BackgroundReviewConfig.max_tokens` 默认值从 `8192` 提高到 `16384`
- **修改 [background_review.py:1543](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/background_review.py#L1543)**：fallback 默认值从 `8192` 同步提高到 `16384`
- **更新相关测试**：[test_background_review.py:741-766](file:///d:/Demo/OpenHome/OriginAgentclient/tests/agent/test_background_review.py#L741-L766) 的 `test_review_turn_default_max_tokens_is_8192` 需重命名为 `test_review_turn_default_max_tokens_is_16384` 并断言 `16384`

### 文档说明（无代码变更）
- **用户手动清理 `unified_default.jsonl`**：`C:\Users\15216\.originagent\workspace\sessions\unified_default.jsonl`（2.89MB，7/3 创建，`unified_session=True` 时的残留）不再使用，cognitive_scheduler 仍扫描它浪费周期。建议用户删除该文件。本 spec 不改代码（规则32：无第二使用场景支撑的抽象不引入）。
- **用户审查 cron job `8d88e717` 配置**：该 cron job 的 message 要求用 `agently-cli message +list` 检查邮件，但 `capabilitySnapshot.can_exec=false`——每分钟触发后 Agent 无法执行 `agently-cli`，只能调用 `originagent_confirmation_summary` 后回复"不能做这个"。建议用户要么放开 `can_exec=true`，要么删除该 cron job。本 spec 不改代码（配置层问题，非代码 bug）。

## Impact

- **Affected specs**:
  - `fix-cron-death-loop-and-orphan-tool`（P0-3 cron 通道静默丢弃不受影响；但本 spec 改变了 cron session key 解析，需确认 `_active_tasks` 注册与 cognitive_scheduler 扫描一致性）
  - `fix-cognition-architecture-defects`（cognitive_scheduler 扫描逻辑不受影响——它扫描 `session_keys()` 返回的所有 session，本 spec 只改 cron turn 写入哪个 session）

- **Affected code**:
  - `OriginAgent/cli/commands.py`（on_cron_job 的 session_key 解析，2 处：L974 + L1019）
  - `OriginAgent/config/schema.py`（BackgroundReviewConfig.max_tokens 默认值，L399-404）
  - `OriginAgent/agent/background_review.py`（fallback 默认值，L1543）
  - `tests/cli/test_on_cron_job_session_extraction.py`（新增 test_on_cron_job_uses_payload_session_key_when_provided）
  - `tests/agent/test_background_review.py`（更新默认 max_tokens 断言）

- **Behavioral changes**:
  - **BREAKING（对 cron job 配置者）**：cron job 现在会真正使用 `payload.sessionKey`。如果用户配置了 `sessionKey: "tenant:guest"`，cron turn 会写入 `tenant:guest` session，与用户对话共享上下文。之前这是静默失效的（payload.sessionKey 只在 deliver 逻辑读取），现在会生效——用户需要确认 payload.sessionKey 配置是否正确。
  - 已有的 `cron:{job.id}` session（如 `cron_8d88e717.jsonl`，110KB）仍存在（历史数据），但新 cron turn 不再写入它们。用户可手动清理这些孤儿 session 文件。
  - BackgroundReviewConfig 默认 max_tokens 翻倍（8192 → 16384），单次 background review 的 token 消耗上限提高。用户可通过配置文件显式覆盖回 8192。

## ADDED Requirements

### Requirement: Cron Session Key Resolution

`on_cron_job` SHALL resolve the session key for cron turn execution by preferring `job.payload.session_key` when it is non-empty, falling back to `f"cron:{job.id}"` when `payload.session_key` is empty/None. The resolved key SHALL be used consistently for:
- `_process_message(session_key=...)` call
- `_active_tasks` registration and done_callback cleanup
- `_last_assistant_content_from_session(...)` query on Path B recovery

The denied_tools scan at [commands.py:913](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/cli/commands.py#L913) SHALL remain using `f"cron:{job.id}"` because it reads cron-job-specific capability denial history, which is independent of where the turn executes.

#### Scenario: payload.session_key is set
- **WHEN** `job.payload.session_key = "tenant:guest"` is non-empty
- **THEN** `on_cron_job` uses `"tenant:guest"` as the session key for `_process_message`
- **AND** `_active_tasks["tenant:guest"]` is registered with the process task
- **AND** `_last_assistant_content_from_session("tenant:guest")` is queried on Path B recovery
- **AND** denied_tools scan still reads from `cron:{job.id}` session (cron-job-specific history)

#### Scenario: payload.session_key is empty
- **WHEN** `job.payload.session_key = None` or `""`
- **THEN** `on_cron_job` falls back to `f"cron:{job.id}"`
- **AND** all session-keyed state uses the fallback key (backward compatibility with existing cron jobs that have no sessionKey configured)

#### Scenario: cognitive_scheduler perceives cron activity correctly
- **WHEN** cron job with `payload.session_key = "tenant:guest"` is executing
- **AND** cognitive_scheduler scans `tenant:guest` session
- **THEN** `_active_tasks["tenant:guest"]` has the cron process task registered
- **AND** `active_task_count >= 1` for `tenant:guest`
- **AND** cognitive_scheduler skips `tenant:guest` with `reason="active_tasks"` (existing behavior, now correctly triggered)

### Requirement: BackgroundReviewConfig max_tokens Default

`BackgroundReviewConfig.max_tokens` SHALL default to `16384` (previously `8192`) to accommodate reasoning models that consume significant tokens in `reasoning_content` before producing final JSON in `content`. The fallback default in [background_review.py:1543](file:///d:/Demo/OpenHome/OriginAgentclient/OriginAgent/agent/background_review.py#L1543) SHALL also be `16384` to stay consistent.

#### Scenario: Default config
- **WHEN** `BackgroundReviewConfig` is instantiated without explicit `max_tokens`
- **THEN** `max_tokens == 16384`

#### Scenario: User override
- **WHEN** User explicitly sets `max_tokens = 8192` in config file
- **THEN** The user's value is respected (no override), and background review uses 8192

#### Scenario: Fallback when cfg has no max_tokens attribute
- **WHEN** `cfg` passed to background review does not expose `max_tokens` attribute
- **THEN** The fallback default `16384` is used (not the old `8192`)

## MODIFIED Requirements

(none — existing cron session key behavior was implicit/buggy, not a documented requirement)

## REMOVED Requirements

(none)
