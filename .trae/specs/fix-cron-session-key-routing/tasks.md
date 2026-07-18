# Tasks

> 遵循规则34（验证先行）：先写测试，再实现。
> 遵循规则37（原子提交）：每个 Task 对应独立 commit。
> 遵循规则33（手术式变更）：仅修改与本次任务直接相关的代码，不顺手重构。

## P0 级（止血——「两个对话重复」根因）

### Task 1: on_cron_job 尊重 payload.sessionKey

- [x] Task 1: 让 `on_cron_job` 优先使用 `job.payload.session_key`，fallback 到 `f"cron:{job.id}"`
  - [x] SubTask 1.1（验证先行）: 在 `tests/cli/test_on_cron_job_session_extraction.py` 新增 `test_on_cron_job_uses_payload_session_key_when_provided`
    - 构造 `CronJob`，`payload.session_key = "tenant:guest"`
    - 调用 `cron.on_job(job)`
    - 断言 `agent._process_message` 被调用时 `session_key="tenant:guest"`（而非 `"cron:{job.id}"`）
    - 断言 `_active_tasks` 注册到 `"tenant:guest"` key（而非 `"cron:{job.id}"`）
  - [x] SubTask 1.2（验证先行）: 新增 `test_on_cron_job_falls_back_to_cron_job_id_when_session_key_empty`
    - 构造 `CronJob`，`payload.session_key = None`
    - 调用 `cron.on_job(job)`
    - 断言 `agent._process_message` 被调用时 `session_key=f"cron:{job.id}"`（向后兼容）
  - [x] SubTask 1.3（验证先行）: 新增 `test_on_cron_job_path_b_recovery_uses_resolved_session_key`
    - 构造 `CronJob`，`payload.session_key = "tenant:guest"`，`deliver=True`
    - mock `_process_message` 返回 `None`（模拟 Path A 抑制）
    - 在 `tenant:guest` session 里放置预期 response
    - 调用 `cron.on_job(job)`
    - 断言 Path B 从 `tenant:guest` session 提取 response（而非从 `cron:{job.id}`）
  - [x] SubTask 1.4（实现）: 修改 `OriginAgent/cli/commands.py`：
    - L974: `cron_session_key = job.payload.session_key or f"cron:{job.id}"`
    - L1019: `response = _last_assistant_content_from_session(cron_session_key)`
    - 保持 L913 不变：denied_tools 扫描仍用 `f"cron:{job.id}"`（cron-job-specific 历史）
    - 在 `cron_session_key` 赋值处添加注释说明 resolved key 的用途与 denied_tools 的独立性
  - [x] SubTask 1.5（验证）: `.\.venv\Scripts\python.exe -m pytest tests/cli/test_on_cron_job_session_extraction.py -v --basetmp=.pytest_basetmp` → 5 个测试全部 PASSED（含 2 个既有测试不回归）

## P2 级（Background review token budget）

### Task 2: BackgroundReviewConfig.max_tokens 默认值 8192 → 16384

- [x] Task 2: 将 `BackgroundReviewConfig.max_tokens` 默认值从 8192 提高到 16384，同步更新 fallback 与测试
  - [x] SubTask 2.1（验证先行）: 修改 `tests/agent/test_background_review.py`：
    - 重命名 `test_review_turn_default_max_tokens_is_8192` → `test_review_turn_default_max_tokens_is_16384`
    - 断言 `provider.calls[0]["max_tokens"] == 16384`
    - 更新 docstring 说明默认值已从 8192 提升到 16384
  - [x] SubTask 2.2（验证先行）: 修改 `test_review_turn_max_tokens_from_config`（如存在），确保显式传入 `max_tokens=8192` 时仍被尊重（用户覆盖路径）
  - [x] SubTask 2.3（实现）: 修改 `OriginAgent/config/schema.py:399-404`：
    - `max_tokens: int = Field(default=16384, ge=256, le=65536, ...)`
    - 更新 docstring 说明"raised from 8192 to 16384 on 2026-07-18 to accommodate reasoning models"
  - [x] SubTask 2.4（实现）: 修改 `OriginAgent/agent/background_review.py:1543`：
    - `max_tokens=int(getattr(cfg, "max_tokens", 16384) or 16384)`
    - 更新行内注释说明默认值与 schema 一致
  - [x] SubTask 2.5（验证）: `.\.venv\Scripts\python.exe -m pytest tests/agent/test_background_review.py -v` → 21 个测试全部 PASSED

## 文档说明（无代码变更，无 Task）

以下两项为用户手动操作，本 spec 不生成代码：

1. **清理 `unified_default.jsonl`**：用户删除 `C:\Users\15216\.originagent\workspace\sessions\unified_default.jsonl`（7/3 旧文件，`unified_session=True` 残留，cognitive_scheduler 仍扫描浪费周期）
2. **审查 cron job `8d88e717` 配置**：用户选择放开 `can_exec=true` 或删除该 cron job（当前 message 要求 `agently-cli` 但 `can_exec=false`，每分钟空转）

# Task Dependencies

## P0 内部依赖
- Task 1 的 3 个 SubTask（1.1/1.2/1.3）相互独立，可并行编写测试，但实现（1.4）需在 3 个测试都写完后进行（规则34：验证先行）

## P2 内部依赖
- Task 2 的 SubTask 2.1/2.2（测试）独立于 SubTask 2.3/2.4（实现），但实现需在测试写完后进行

## P0 与 P2 依赖
- Task 1 与 Task 2 完全独立，可并行

## 建议执行顺序
1. **第一批（并行）**：Task 1（SubTask 1.1-1.3 测试）+ Task 2（SubTask 2.1-2.2 测试）
2. **第二批（并行）**：Task 1（SubTask 1.4 实现）+ Task 2（SubTask 2.3-2.4 实现）
3. **第三批（并行）**：Task 1（SubTask 1.5 验证）+ Task 2（SubTask 2.5 验证）
4. **第四批**：原子提交（Task 1 一个 commit，Task 2 一个 commit）
