# Tasks

> 遵循规则34（验证先行）：先写测试/断言，再实现。
> 遵循规则37（原子提交）：每个 Task 对应独立 commit，commit message 说明"为什么这么改"。
> 遵循规则33（手术式变更）：仅修改与本次任务直接相关的代码，不顺手重构。
> 遵循规则32-B：触及规则 0 红线闭集（规则 12 幂等、规则 18 安全边界）的 Task 为 L1，需完整清单 + 100% 独立核验。

## Task 1: D10 — ConfirmationRequest.owner_id 字段与跨会话审批

- [x] Task 1: 在 `ConfirmationRequest` 新增 `owner_id` 字段，`ConfirmationManager` 支持跨 session 审批 ✅ 完成
  - [x] SubTask 1.1（验证先行）: 在 `tests/agent/test_confirmation_owner_id.py` 新增 5 个测试 ✅ 5 passed
    - `test_create_tool_approval_writes_owner_id`：调 `create_tool_approval(owner_id="tenant:owner", ...)`，断言返回的 request.owner_id == "tenant:owner"
    - `test_list_pending_for_owner_filters_by_owner_id`：创建 3 个 pending（2 个 owner=A、1 个 owner=B），调 `list_pending_for_owner("A")`，断言返回 2 个
    - `test_resolve_user_reply_allows_cross_session_when_owner_matches`：pending owner_id="tenant:owner" 且 session_key="cron:abc"；caller actor_id="tenant:owner" 且 session_key="telegram:owner"；调 `resolve_user_reply`，断言成功
    - `test_resolve_user_reply_denies_cross_owner_approval`：pending owner_id="tenant:owner"；caller actor_id="tenant:guest"；调 `resolve_user_reply`，断言返回 denied + 审计事件 `approval_denied_owner_mismatch`
    - `test_old_pending_without_owner_id_loads_with_none`：构造旧 json（无 owner_id 字段），`ConfirmationRequest.from_dict` 加载，断言 `owner_id is None`
  - [x] SubTask 1.2（实现）: 修改 `OriginAgent/agent/confirmation.py` ✅
    - `ConfirmationRequest` dataclass 在 `metadata` 字段后新增 `owner_id: str | None = None`
    - `__post_init__` 中加 `self.owner_id = _optional_str(self.owner_id)`
    - `from_dict` 中加 `owner_id=_optional_str(raw.get("owner_id"))`
    - `to_dict` 自动包含（asdict）
    - `create_tool_approval` 签名新增 `owner_id: str | None = None`，写入 ConfirmationRequest
    - 新增 `list_pending_for_owner(owner_id: str | None) -> list[ConfirmationRequest]`：返回所有 status=pending 且未过期 且 owner_id 匹配的 confirmation（owner_id=None 时返回所有 owner_id=None 的，向后兼容历史遗留）
    - `resolve_user_reply` 增加可选参数 `caller_actor_id: str | None = None`；若 `request.owner_id is not None and caller_actor_id != request.owner_id`，直接返回 denied + 审计事件；否则维持现有逻辑
  - [x] SubTask 1.3（实现）: 实际调用点为 `tools/registry.py:519` + `evolution_control_plane.py:996` 透传 owner_id；规则26偏离声明：spec 文本提到的 agent_runtime_context.py / runner.py / cli/commands.py 实际无 create_tool_approval 调用点
  - [x] SubTask 1.4（验证）: 5 个测试 + 86 回归全部 PASSED
  - [x] SubTask 1.5（回归）: 无回归

## Task 2: D7 — tool_approval TTL 按 trigger 分级（user 2min / cron 1h）

- [x] Task 2: 让 `_ttl_for` 按 trigger 区分 TTL，cron 1h、user 2min ✅ 完成
  - [x] SubTask 2.1（验证先行）: 在 `tests/agent/test_confirmation_ttl.py` 新增 4 个测试 ✅ 4 passed
    - `test_ttl_for_cron_trigger_returns_1h`：`_ttl_for("tool_approval", "high", config, trigger="scheduled")` 返回 timedelta(hours=1)
    - `test_ttl_for_user_trigger_returns_2min`：`_ttl_for("tool_approval", "high", config, trigger="user")` 返回 timedelta(minutes=2)
    - `test_create_tool_approval_cron_trigger_uses_1h_ttl`：创建 trigger="scheduled" 的 tool_approval，断言 `expires_at - created_at == 1h`
    - `test_create_tool_approval_user_trigger_uses_2min_ttl`：创建 trigger="user" 的 tool_approval，断言 `expires_at - created_at == 2min`
  - [x] SubTask 2.2（实现）: `OriginAgent/config/schema.py:719-728` 新增 `tool_approval_cron_ttl_seconds` ✅
    - `ConfirmationConfig` 新增 `tool_approval_cron_ttl_seconds: int = Field(default=3600, ge=60, le=86400, description="...")`
  - [x] SubTask 2.3（实现）: `OriginAgent/agent/confirmation.py:1095-1116` `_ttl_for` 扩展 + L561 调用透传 trigger ✅
    - `_ttl_for` 签名扩展：`def _ttl_for(kind, risk, config=None, trigger=None) -> timedelta:`
    - 函数体开头加：`if kind == "tool_approval" and trigger == "scheduled": cfg = config or _DEFAULT_CONFIRMATION_CONFIG; return timedelta(seconds=max(60, int(cfg.tool_approval_cron_ttl_seconds)))`
    - 其他分支不变
    - `create_tool_approval` 内 `_ttl_for("tool_approval", risk, self.config)` 改为 `_ttl_for("tool_approval", risk, self.config, trigger=trigger)`
  - [x] SubTask 2.4（验证）: 4 个测试 + 64 回归全部 PASSED
  - [x] SubTask 2.5（回归）: 修复 `test_cron_approval_deferred.py:test_list_tool_approvals_lazy_expires_old_pending` 时间步从 5min 改为 2h（D7 副作用）

## Task 3: 方向 C-1 — REST API `/api/approvals` 端点

- [x] Task 3: 在 `gateway/rest_api.py` 新增 3 个审批端点
  - [x] SubTask 3.1（验证先行）: 在 `tests/gateway/test_approvals_api.py` 新增 4 个测试
    - `test_get_approvals_returns_owner_pending`：mock ConfirmationManager，构造 owner=A 的 2 个 pending（含 1 个 expired 不应返回）；调 `GET /api/approvals` 携带 owner A 的 API token；断言返回 200 + 1 个 pending
    - `test_post_approve_creates_grant_and_updates_status`：mock ConfirmationManager，构造 pending confirmation_id=X owner=A；调 `POST /api/approvals/X/approve`；断言返回 200 + grant_id；断言 confirmation 状态变为 confirmed_once
    - `test_post_reject_updates_status`：类似上，调 `/reject`；断言状态变为 rejected
    - `test_cross_owner_approval_returns_403`：pending owner=A，caller 是 owner B；断言返回 403 + 审计事件
  - [x] SubTask 3.2（实现）: 修改 `OriginAgent/gateway/rest_api.py`
    - 在 dispatch 路由中新增 `/api/approvals` GET 处理：调用 `confirmation_manager.list_pending_for_owner(caller_actor_id)` 返回 json 列表
    - 新增 `/api/approvals/{confirmation_id}/approve` POST 处理：调用 `confirmation_manager.resolve_user_reply(confirmation_id, "yes", caller_actor_id=caller_actor_id)`；成功返回 grant_id；失败返回 403
    - 新增 `/api/approvals/{confirmation_id}/reject` POST 处理：调用 `resolve_user_reply(confirmation_id, "no", caller_actor_id=caller_actor_id)`
    - caller_actor_id 解析路径（规则 26 假设显式化，选项 A）：通过 `?owner_id=` query 参数显式传入，不复用 `_check_api_token`（其只返回 bool，不解析 owner_id）
  - [x] SubTask 3.3（验证）: `.\.venv\Scripts\python.exe -m pytest tests/gateway/test_approvals_api.py -v --basetemp=.pytest_basetmp_unify_approval` → 5 个测试全部 PASSED（4 个核心 + 1 个 401 鉴权）

## Task 4: 方向 C-2 — Telegram `/approval` 命令

- [x] Task 4: 在 `channels/telegram.py` 新增 `/approval` 命令处理器 ✅ 完成
  - [x] SubTask 4.1（验证先行）: 在 `tests/channels/test_telegram_approval_command.py` 新增 4 个测试 ✅ 4 passed
    - `test_approval_list_returns_pending_for_owner_sender`：mock identity resolver 解析 sender 为 tenant:owner；mock ConfirmationManager 返回 2 个 pending；发送 `/approval list`；断言 bot 回复包含 2 条 pending 信息
    - `test_approval_approve_executes_and_replies`：mock ConfirmationManager；发送 `/approval approve confirmation_abc`；断言 bot 回复"已批准"
    - `test_approval_reject_executes_and_replies`：类似上，调 `/reject`；断言回复"已拒绝"
    - `test_guest_sender_approval_command_rejected`：mock identity resolver 解析 sender 为 tenant:guest；发送 `/approval approve confirmation_abc`；断言 bot 回复拒绝消息（权限不足）
  - [x] SubTask 4.2（实现）: 修改 `OriginAgent/channels/telegram.py` + `OriginAgent/command/builtin.py` ✅
    - telegram.py 新增 `BotCommand("approval", ...)` 与 `/pairing` 同级的 Regex filter（L252, L356-365）
    - builtin.py 新增 `BuiltinCommandSpec("/approval", ...)` + `cmd_approval(ctx)` handler + router 注册（L102-108, L490-613, L1717-1718）
    - 命令解析：`/approval list` / `/approval approve <id>` / `/approval reject <id>` / 无参默认 list
    - 通过 identity resolver 解析 sender 为 tenant 的 unified_session_key（owner_id）
    - 调用 ConfirmationManager 对应方法：list_pending_for_owner / resolve_user_reply（"yes"/"no"）
    - bot 回复格式化：list 时多行展示 confirmation_id/tool_name/prompt，approve/reject 时简洁确认
    - guest 角色直接拒绝（不调用 ConfirmationManager，断言 `assert_not_called()`）
    - 规则 26 偏离声明：TelegramChannel 本身无 confirmation_manager / identity_resolver 注入，沿用 /pairing 模式（channel 转发到 bus → cmd_approval 通过 ctx.loop._identity_resolver 和 ctx.loop._confirmation_manager 访问）
  - [x] SubTask 4.3（验证）: `.\.venv\Scripts\python.exe -m pytest tests/channels/test_telegram_approval_command.py -v --basetemp=.pytest_basetmp_unify_approval` → 4 个测试全部 PASSED ✅
  - [x] SubTask 4.4（回归）: `tests/agent/test_confirmation.py` 56/56 PASSED 无回归 ✅

## Task 5: 方向 C-3 — WebUI ApprovalsPanel

- [x] Task 5: 在 webui 新增 ApprovalsPanel 组件并注入 settings ✅ 完成
  - [x] SubTask 5.1（实现）: 新建 `webui/src/components/approvals/ApprovalsPanel.tsx` ✅
    - 列表渲染：confirmation_id, tool_name, prompt, created_at, expires_at, risk badge
    - 每行带 Approve / Reject 按钮
    - 空状态显示"暂无待审批项"
    - Loading 状态显示 spinner
    - 调用 `webui/src/lib/api.ts` 的 listApprovals / approveApproval / rejectApproval
  - [x] SubTask 5.2（实现）: 修改 `webui/src/lib/api.ts` ✅
    - 新增 `Approval` interface + `listApprovals(token, ownerId, base)` / `approveApproval(token, confirmationId, ownerId, base)` / `rejectApproval(token, confirmationId, ownerId, base)`
    - 复用既有 `request<T>` / `buildUrl` / `apiUrl` helpers，与 fetchTenants 等同源
  - [x] SubTask 5.3（实现）: 修改 `webui/src/components/settings/SettingsView.tsx` ✅
    - 注入 ApprovalsPanel 作为新 tab（与 TenantsSettings 同级，nav key "approvals"）
    - 规则 26 偏离声明：WebUI 无 useAuth/useUser role context（单 token 模型），改用 `fetchTenants().defaultTenantId` 作为 owner_id 单一数据源（与 TenantsSettings 同源）；guest 隐藏通过后端 403 + UI permission_denied 状态实现
  - [x] SubTask 5.4（实现）: 修改 9 个 i18n locale 文件（en, zh-CN, zh-TW, vi, ko, ja, id, fr, es） ✅
    - 新增 `approvals.*` 全套 key（title/empty/approve/reject/confirm_approve/confirm_reject/risk.high/medium/low/tool/expires_at/created_at/permission_denied/refresh/success.*/error.*）
    - 新增 `settings.nav.approvals` key
  - [x] SubTask 5.5（验证）: `cd webui && npm run build` 构建成功（vite build 3161 modules, 6.29s）；`npm test -- approvals-panel` 6/6 PASSED ✅
  - [x] SubTask 5.6（验证，可选）: 浏览器手动测试跳过（无运行中的后端，单测已覆盖核心交互路径）

## Task 6: systemmap 同步 + 全量回归

- [x] Task 6: 更新 systemmap 并跑全量回归 ✅ 完成
  - [x] SubTask 6.1: 更新 `systemmap/domain-overview.md` ✅
    - 移除 cron 章节过时的 D10/D7 未完成项引用
    - 新增"审批流（D10+D7+方向 C 修复,2026-07-20）"章节，描述跨会话审批路径、TTL 分级、UI 三件套
    - 更新 `last_verified` 到 2026-07-20T02:00:00+08:00
  - [x] SubTask 6.2: 全量回归 ✅ 95/95 PASSED in 2.31s
    - `tests/agent/test_confirmation*.py` 64 passed
    - `tests/agent/test_cron_approval_deferred.py` 4 passed
    - `tests/tools/test_cron_capability_grants.py` 17 passed
    - `tests/gateway/test_approvals_api.py` 5 passed
    - `tests/channels/test_telegram_approval_command.py` 4 passed
    - (tests/cron/ 目录列为空，跳过)
  - [x] SubTask 6.3: 前端构建 ✅ `npm run build` 成功（vite build 3161 modules, 9.97s）

# Task Dependencies

## 内部依赖
- Task 1（D10 owner_id）独立，可优先开始
- Task 2（D7 TTL）独立，可与 Task 1 并行
- Task 3（REST API）依赖 Task 1（需要 owner_id 字段）+ Task 2（需要 TTL 分级，否则 list 出来的 pending 已过期）
- Task 4（Telegram /approval）依赖 Task 1 + Task 3（命令内部调用同一 ConfirmationManager API）
- Task 5（WebUI）依赖 Task 3（前端调 REST API）
- Task 6（systemmap + 回归）依赖所有 Task 完成

## 建议执行顺序
1. **第一批（并行）**：Task 1（D10）+ Task 2（D7）
2. **第二批（串行）**：Task 3（REST API）— 待 Task 1+2 完成
3. **第三批（并行）**：Task 4（Telegram）+ Task 5（WebUI）— 依赖 Task 3
4. **第四批**：Task 6（systemmap + 全量回归）— 全部完成后

## 提交策略（规则 37）
- 每个 Task 对应一个独立 commit，可被单独 `git revert`
- commit message 首行简明祈使句；正文说明"为什么这么改"
- Task 1 commit message 引用规则 18 安全边界
- Task 2 commit message 引用规则 19 可回滚（TTL 配置项可动态调整）
- 无 BREAKING 变更

## 验证策略（规则 31）
- Task 1 触及规则 18（安全边界）→ L1 红线闭集，100% 独立核验
- Task 2 触及规则 19（可回滚）→ L2 完整清单 + 抽样核验
- Task 3-5 触及规则 18（鉴权）→ L1 红线闭集，100% 独立核验
- Task 6 不涉及代码 → L3 简化声明
