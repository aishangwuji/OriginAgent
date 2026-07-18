# Checklist

## P0 止血——「两个对话重复」根因

### Task 1: on_cron_job 尊重 payload.sessionKey

- [x] Task 1: `on_cron_job` 优先使用 `job.payload.session_key`，fallback 到 `f"cron:{job.id}"` — commands.py L978
- [x] Task 1: `_process_message` 的 `session_key` 参数使用 resolved key（而非硬编码 `cron:{job.id}`） — commands.py L982
- [x] Task 1: `_active_tasks` 注册与 done_callback cleanup 使用 resolved key — commands.py L993-1000
- [x] Task 1: `_last_assistant_content_from_session` 查询使用 resolved key（Path B recovery） — commands.py L1023
- [x] Task 1: denied_tools 扫描仍用 `f"cron:{job.id}"`（保持不变，cron-job-specific 历史） — commands.py L913
- [x] Task 1: 测试 `test_on_cron_job_uses_payload_session_key_when_provided` 通过
- [x] Task 1: 测试 `test_on_cron_job_falls_back_to_cron_job_id_when_session_key_empty` 通过（向后兼容）
- [x] Task 1: 测试 `test_on_cron_job_path_b_recovery_uses_resolved_session_key` 通过
- [x] Task 1: 既有 `test_on_cron_job_extracts_response_from_session_when_outbound_suppressed` 不回归
- [x] Task 1: 既有 `test_on_cron_job_registers_to_active_tasks` 不回归

## P2 Background review token budget

### Task 2: BackgroundReviewConfig.max_tokens 默认值提升

- [x] Task 2: `BackgroundReviewConfig.max_tokens` 默认值从 8192 改为 16384 — schema.py L399-410
- [x] Task 2: `background_review.py:1546` fallback 默认值同步改为 16384
- [x] Task 2: 测试 `test_review_turn_default_max_tokens_is_16384`（重命名 + 断言更新）通过
- [x] Task 2: 测试 `test_review_turn_uses_configured_max_tokens`（用户显式覆盖路径）通过
- [x] Task 2: 既有 `test_background_review.py` 其他测试不回归（21/21 PASSED）

## 规则合规性自检

- [x] 规则3（边界数据校验）：本次变更不涉及外部输入 schema 校验
- [x] 规则5（缓存值声明生命周期）：`cron_session_key` 是局部变量，生命周期与 `on_cron_job` 调用栈一致；`BackgroundReviewConfig.max_tokens` 是 frozen dataclass 字段，不可变
- [x] 规则7（状态变化审计）：`_active_tasks[resolved_key]` 的写入路径单一（on_cron_job），done_callback 清理路径单一，无并发竞争
- [x] 规则8（异步时序交叉）：cron turn 与 cognitive_scheduler 扫描异步——resolved key 让 `_active_tasks` 注册到正确 session，cognitive_scheduler 能正确感知。若 cron turn 未完成时 cognitive_scheduler 扫描，`active_task_count >= 1` 触发 skip（已有行为，现在 key 对齐）
- [x] 规则9（session token 隔离）：不涉及异步资源开关对
- [x] 规则12（幂等）：不涉及可重试写操作
- [x] 规则14（关键假设断言化）：不涉及关键假设
- [x] 规则18（安全边界）：不涉及身份操作/凭证/SQL
- [x] 规则27（变更交付清单）：本 spec 文档即清单，每 Task 对应独立 commit
- [x] 规则32（最小化实现）：本次变更无投机性抽象——resolved key 是直接修复，max_tokens 调优是配置变更，均无新增接口/配置项/功能开关
- [x] 规则33（手术式变更）：仅修改与本次任务直接相关的代码（commands.py 2 行 + schema.py 1 行 + background_review.py 1 行 + 测试）
- [x] 规则34（验证先行）：每个 Task 的 SubTask.1 都是先写测试，并确认 FAIL 后再实现
- [x] 规则37（原子提交）：每个 Task 独立 commit

## 系统认知同步

- [x] 本次变更已更新 `systemmap/domain-overview.md` L209（Path B 描述中标注 resolved session key 来源 `job.payload.session_key or f"cron:{job.id}"`，并标注 2026-07-18 修复前硬编码 `cron:{job.id}`）。cron session key 解析虽是路由层细节、不影响业务状态机，但 systemmap 中已记录 cron Path B 投递流程，需同步反映修复后的 resolved key 行为。

## 文档说明（用户手动操作，非代码验证）

- [x] 用户已知晓：可手动删除 `C:\Users\15216\.originagent\workspace\sessions\unified_default.jsonl`（7/3 旧残留）
- [x] 用户已知晓：可手动审查/修改/删除 cron job `8d88e717`（can_exec=false 与 message 要求不匹配）
- [x] 用户已知晓：已有的 `cron_8d88e717.jsonl` 等 cron session 文件在新代码上线后不再被写入，可手动清理
