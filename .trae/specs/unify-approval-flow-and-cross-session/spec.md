# 跨会话审批流与 TTL 分级 Spec

> **change-id**: `unify-approval-flow-and-cross-session`
> **创建时间**: 2026-07-20
> **关联规则**: 规则 0 红线闭集（规则 3 边界校验、规则 12 幂等、规则 18 安全边界）；规则 19 可回滚；规则 27 清单；规则 34 验证先行
> **关联前序 spec**: `fix-cron-death-loop-and-orphan-tool`、`fix-cron-runtime-and-context-gaps`、`fix-cron-session-key-routing`、`webui-tenant-admin`

## Why

D1+D2+D3+D6 已解决 cron 权限继承死锁的根因——新创建的 cron job 不再生成永远无法通过的 `tool_approval`。但系统中仍存在三类未闭环问题：

1. **历史遗留 pending 无法清理**：5 个 D1 修复前生成的死锁 pending 仍躺在 `pending_confirmations.json` 中，status=pending 但 `expires_at < now`。D6 让 `list_tool_approvals` 自动把过期 pending 标记为 expired，但用户在 Telegram 端没有任何审批入口——既看不到 pending 详情，也无法主动 approve/reject。
2. **TTL 一刀切导致 cron 场景人为紧张**：`tool_approval` 的 TTL 默认 2 分钟（high risk），但 cron job 可能每小时才触发一次。若用户暂时不在 Telegram 旁（吃饭、睡觉），2 分钟后 pending 即过期，下次 cron 触发又生成新 pending，循环堆积。
3. **审批交互入口完全缺失**：Telegram 没有 `/approval` 命令，WebUI 没有 ApprovalsPanel，REST API 没有 `/api/approvals` 端点。用户唯一的"审批"方式是在聊天里告诉 Agent confirmation_id——既不安全（任何人都能在群里发 ID），也不直观（看不到 pending 列表）。

这三件事必须一起做：TTL 延长（D7）需要 owner_id 字段（D10）做权限校验，审批 UI（方向 C）需要 list/approve/reject 三个 API 端点同时落地才好用。

## What Changes

### D10 — ConfirmationRequest.owner_id 字段与跨会话审批

- `ConfirmationRequest` dataclass 新增 `owner_id: str | None = None` 字段（向后兼容，旧文件无此字段时默认 None）
- `ConfirmationManager.create_tool_approval` 新增 `owner_id: str | None = None` 参数，写入 request
- `ConfirmationManager` 新增 `list_pending_for_owner(owner_id: str) -> list[ConfirmationRequest]` 方法，返回该 owner 名下所有非 expired 的 pending
- `ConfirmationManager.resolve_user_reply` 放宽 session 约束：当 caller 的 `actor_id == request.owner_id` 时允许跨 session 审批（即用户在交互会话中可以审批 cron 触发的 pending）；`owner_id is None` 时维持现有行为（向后兼容）

### D7 — tool_approval TTL 分级（user 2min / cron 1h）

- `ConfirmationConfig` 新增 `tool_approval_cron_ttl_seconds: int = 3600`（1 小时），现有 `confirm_ttl_by_risk` 仅用于 user trigger
- `_ttl_for` 函数签名扩展为 `_ttl_for(kind, risk, config, trigger=None)`：当 `kind=="tool_approval" and trigger=="scheduled"` 时返回 1h TTL；否则维持现有 2min
- `create_tool_approval` 调用 `_ttl_for` 时传入 `trigger` 参数
- 配置文件 schema 同步：`config/schema.py` 的 `ConfirmationConfig` 模型添加新字段（含 `ge=60, le=86400` 校验，rule 17 禁硬编码）

### 方向 C — 审批 UI 三件套

#### C-1 REST API 端点（gateway/rest_api.py）

新增 3 个端点，沿用现有 dispatch 模式（基于 `WsRequest` + `Response`）：

- `GET /api/approvals` — 列出当前 owner 的 pending（含 owner_id 过滤）
- `POST /api/approvals/{confirmation_id}/approve` — 审批通过
- `POST /api/approvals/{confirmation_id}/reject` — 审批拒绝

**鉴权**：复用现有 `_check_api_token` 机制；审批操作必须校验 caller 的 `actor_id == request.owner_id`（规则 18 安全边界），否则返回 403。

#### C-2 Telegram `/approval` 命令（channels/telegram.py）

新增 `/approval` 命令处理器（沿用现有 `/pairing` 命令的 Regex filter 模式）：

- `/approval list` — 列出当前 sender 名下 pending（调用 `list_pending_for_owner`，sender 通过 identity resolver 解析为 owner_id）
- `/approval approve <confirmation_id>` — 审批通过
- `/approval reject <confirmation_id>` — 审批拒绝

**安全约束**：sender 必须是 owner 角色才能执行审批（guest 角色拒绝）；confirmation_id 必须属于 caller 的 owner_id（防止跨用户审批）。

#### C-3 WebUI ApprovalsPanel（webui/src/components/approvals/）

新增组件：

- `ApprovalsPanel.tsx` — 列表 + approve/reject 按钮
- `ApprovalsPanel` 注入到 settings 主面板（与 `TenantsSettings` 同级）
- API client（`webui/src/lib/api.ts`）扩展 `listApprovals/approveApproval/rejectApproval` 三个方法
- i18n key：`approvals.title` / `approvals.empty` / `approvals.approve` / `approvals.reject` / `approvals.risk.high` 等

## Impact

### 受影响 specs
- `fix-cron-death-loop-and-orphan-tool` — D6 的 lazy expire_old 仍在 list_tool_approvals 入口，D10 新增的 list_pending_for_owner 应复用同样模式
- `fix-cron-runtime-and-context-gaps` — D1+D2+D3 已完成的 cron 权限继承修复为本 spec 提供基础（新 cron 不再生成死锁 pending）
- `webui-tenant-admin` — WebUI 鉴权模式与组件注入方式本 spec 沿用

### 受影响代码

**Backend (Python)**:
- `OriginAgent/agent/confirmation.py` — `ConfirmationRequest` 新增 `owner_id` 字段；`ConfirmationManager` 新增 `list_pending_for_owner`；`create_tool_approval` 新增 `owner_id` 参数；`resolve_user_reply` 放宽跨 session 约束；`_ttl_for` 签名扩展
- `OriginAgent/config/schema.py` — `ConfirmationConfig` 新增 `tool_approval_cron_ttl_seconds`
- `OriginAgent/agent/agent_runtime_context.py` — 调用 `create_tool_approval` 时透传 `actor_id` 作为 `owner_id`
- `OriginAgent/cli/commands.py` — `on_cron_job` 触发时若有 tool_approval 生成，传入 `owner_id`（来自 `job.payload.owner_id`，由 D3 修复时已写入 `actor_id`）
- `OriginAgent/gateway/rest_api.py` — 新增 3 个 `/api/approvals` 端点
- `OriginAgent/channels/telegram.py` — 新增 `/approval` 命令处理器

**Frontend (TypeScript/React)**:
- `webui/src/components/approvals/ApprovalsPanel.tsx`（新建）
- `webui/src/components/settings/SettingsPage.tsx`（或现有 settings 主入口）— 注入 ApprovalsPanel
- `webui/src/lib/api.ts` — 3 个新 API 方法
- `webui/src/i18n/locales/zh-CN/common.json` / `en/common.json` — 新 i18n key

### 兼容性
- `ConfirmationRequest` 新增 `owner_id` 字段为 optional，旧 json 文件无此字段时 `from_dict` 默认 None——向后兼容
- `ConfirmationConfig` 新增 `tool_approval_cron_ttl_seconds` 字段有默认值 3600，旧 config 文件无需修改
- `create_tool_approval` 新增 `owner_id` 参数为 optional，现有调用方无需修改
- REST API 新增端点不影响现有端点
- Telegram `/approval` 命令与现有 `/pairing` `/status` 命令互不冲突

## ADDED Requirements

### Requirement: 跨会话审批路径
系统 SHALL 允许 owner 在交互会话中审批任意 session 的 pending confirmation，前提是 caller 的 `actor_id` 与 confirmation 的 `owner_id` 匹配。

#### Scenario: owner 在交互会话中审批 cron 触发的 pending
- **GIVEN** cron 触发生成了一个 `tool_approval`，`owner_id="tenant:owner"`，`session_key="cron:abc123"`
- **WHEN** owner 在 Telegram 交互会话中发送 `/approval approve <confirmation_id>`，sender 解析为 `tenant:owner`
- **THEN** 系统校验 `caller.owner_id == request.owner_id` 通过
- **AND** confirmation 状态变为 `confirmed_once` 或 `confirmed_persistent`（取决于 risk 与 persistent flag）
- **AND** 触发对应 grant 创建（若未存在）
- **AND** 下次 cron 触发时，新 grant 被加载，tool call 正常执行

#### Scenario: 非 owner 用户尝试审批被拒绝
- **GIVEN** 上述 pending，`owner_id="tenant:owner"`
- **WHEN** tenant:guest 用户发送 `/approval approve <confirmation_id>`
- **THEN** 系统返回 403 Forbidden，confirmation 状态不变
- **AND** 审计日志记录 `approval_denied_owner_mismatch`

### Requirement: tool_approval TTL 按 trigger 分级
系统 SHALL 对 `trigger=="scheduled"` 的 `tool_approval` 应用 1 小时 TTL，对其他 trigger 应用现有 `confirm_ttl_by_risk.high=2min` TTL。

#### Scenario: cron 触发的 pending 在 1 小时内有效
- **GIVEN** cron 触发生成 `tool_approval`，`trigger="scheduled"`，`risk="high"`
- **WHEN** 30 分钟后 owner 调用 `list_pending_for_owner`
- **THEN** pending 仍在列表中，status="pending"
- **AND** `expires_at` = `created_at + 1h`

#### Scenario: 用户触发的 pending 维持 2 分钟 TTL
- **GIVEN** 用户在交互会话中触发生成 `tool_approval`，`trigger="user"`，`risk="high"`
- **WHEN** 3 分钟后查询
- **THEN** pending 已过期（D6 lazy expire_old 会把 status 更新为 "expired"）

### Requirement: REST API 审批端点
系统 SHALL 提供 `/api/approvals` 系列 REST 端点，支持列出、审批、拒绝当前 owner 的 pending。

#### Scenario: GET /api/approvals 返回当前 owner 的 pending 列表
- **GIVEN** owner_id="tenant:owner" 有 3 个 pending（其中 1 个已 expired）
- **WHEN** 调用 `GET /api/approvals` 携带 owner 的 API token
- **THEN** 返回 200，body 含 2 个未过期的 pending 详情
- **AND** 已过期的 pending 不在列表中

#### Scenario: POST /api/approvals/{id}/approve 审批通过
- **GIVEN** pending confirmation_id="confirmation_abc123", owner_id="tenant:owner"
- **WHEN** owner 调用 `POST /api/approvals/confirmation_abc123/approve`
- **THEN** 返回 200，body 含 grant_id
- **AND** confirmation 状态变为 confirmed_once 或 confirmed_persistent
- **AND** 对应 grant 创建并写入 `capability_grants.json`

### Requirement: Telegram `/approval` 命令
系统 SHALL 在 Telegram channel 提供 `/approval` 命令支持列出、审批、拒绝 pending。

#### Scenario: /approval list 显示当前 sender 的 pending
- **GIVEN** sender 解析为 tenant:owner，有 2 个 pending
- **WHEN** sender 发送 `/approval list`
- **THEN** bot 回复消息列出 2 个 pending 的 confirmation_id、tool_name、prompt、expires_at
- **AND** 每个 pending 附带 approve/reject 操作提示

#### Scenario: /approval approve <id> 成功审批
- **GIVEN** sender 为 tenant:owner，pending confirmation_id="confirmation_abc123"
- **WHEN** sender 发送 `/approval approve confirmation_abc123`
- **THEN** bot 回复"已批准 confirmation_abc123，权限已下发"
- **AND** capability_grants.json 写入新 grant

## MODIFIED Requirements

### Requirement: ConfirmationRequest 数据结构
`ConfirmationRequest` dataclass 在原有字段基础上 SHALL 新增 `owner_id: str | None = None` 字段，表示该 confirmation 所属的 owner 身份（如 `tenant:owner`）。`from_dict` / `to_dict` 必须同步处理此字段。旧 json 文件无此字段时 `from_dict` 应默认 None 保持向后兼容。

### Requirement: ConfirmationManager.create_tool_approval
`create_tool_approval` SHALL 新增 `owner_id: str | None = None` 参数，写入 `ConfirmationRequest.owner_id`。当 `owner_id is None` 时（旧调用方），维持现有行为，不阻塞创建。

### Requirement: ConfirmationManager.resolve_user_reply
`resolve_user_reply` SHALL 在 caller 的 `actor_id` 与 `request.owner_id` 匹配时允许跨 session 审批。当 `request.owner_id is None` 时（旧数据），维持现有 session 级约束。

### Requirement: _ttl_for 函数
`_ttl_for(kind, risk, config, trigger=None)` SHALL 在 `kind=="tool_approval" and trigger=="scheduled"` 时返回 `config.tool_approval_cron_ttl_seconds` 对应的 TTL；其他情况维持现有 `confirm_ttl_by_risk` 查表逻辑。

### Requirement: ConfirmationConfig
`ConfirmationConfig` SHALL 新增 `tool_approval_cron_ttl_seconds: int` 字段，默认 3600，范围 `[60, 86400]`（1 分钟到 24 小时）。

## REMOVED Requirements

无。本 spec 不移除任何既有需求。

## 边界与异常场景

### 边界 1: approval 命令注入风险
LLM 可能在 turn 中尝试调用 `/approval` 命令绕过用户审批。**防护**：`/approval` 必须是 Telegram 命令（前置 `/`），不是 Agent 可调用的 tool；channel 层在收到 `/approval` 时直接路由到命令处理器，不进入 Agent loop。

### 边界 2: 跨 owner 审批越权
tenant:guest 用户的 sender 可能在 Telegram 群里发 `/approval approve <owner_pending_id>`。**防护**：命令处理器先通过 identity resolver 解析 sender 为 tenant_id，与 confirmation.owner_id 比对，不匹配返回 403 + 审计日志。

### 边界 3: 历史 5 个死锁 pending 的清理
D1 修复前的 5 个 pending 无 `owner_id` 字段（None）。**处置**：`list_pending_for_owner(owner_id=None)` 时返回所有 owner_id=None 的 pending（向后兼容），让 owner 也能审批清理这些历史遗留；审批后状态变为 `expired` 或 `rejected`，从队列中消失。

### 边界 4: TTL 延长不能削弱 high-risk 安全窗口
用户在交互会话中触发的 `tool_approval` 仍维持 2min TTL，防止用户离开后高危操作长时间悬挂。**约束**：TTL 分级仅按 trigger 区分，不按 risk 区分；scheduled trigger 的 1h TTL 仅用于 cron 场景，用户场景不变。

### 边界 5: WebUI ApprovalsPanel 与 TenantsSettings 的权限边界
ApprovalsPanel 只对 owner 可见（与 TenantsSettings 同级权限）。**约束**：guest 用户在 WebUI 看不到 ApprovalsPanel 入口；尝试直接访问 `/api/approvals` 返回 403。

## 测试覆盖要求（规则 15/34）

- **D10 单元测试**：`tests/agent/test_confirmation_owner_id.py`
  - `test_create_tool_approval_writes_owner_id`
  - `test_list_pending_for_owner_filters_by_owner_id`
  - `test_resolve_user_reply_allows_cross_session_when_owner_matches`
  - `test_resolve_user_reply_denies_cross_owner_approval`
  - `test_old_pending_without_owner_id_loads_with_none`（向后兼容）

- **D7 单元测试**：`tests/agent/test_confirmation_ttl.py`
  - `test_ttl_for_cron_trigger_returns_1h`
  - `test_ttl_for_user_trigger_returns_2min`
  - `test_create_tool_approval_cron_trigger_uses_1h_ttl`
  - `test_create_tool_approval_user_trigger_uses_2min_ttl`

- **REST API 集成测试**：`tests/gateway/test_approvals_api.py`
  - `test_get_approvals_returns_owner_pending`
  - `test_post_approve_creates_grant_and_updates_status`
  - `test_post_reject_updates_status`
  - `test_cross_owner_approval_returns_403`

- **Telegram 命令集成测试**：`tests/channels/test_telegram_approval_command.py`
  - `test_approval_list_returns_pending_for_owner_sender`
  - `test_approval_approve_executes_and_replies`
  - `test_approval_reject_executes_and_replies`
  - `test_guest_sender_approval_command_rejected`

- **WebUI 单元测试**：`webui/src/components/approvals/__tests__/ApprovalsPanel.test.tsx`
  - 列表渲染、approve/reject 按钮回调、空状态、loading 状态

## 提交策略（规则 37）

- Task 1（D10 字段与跨会话审批）— 独立 commit
- Task 2（D7 TTL 分级）— 独立 commit
- Task 3（REST API 端点）— 独立 commit
- Task 4（Telegram /approval 命令）— 独立 commit
- Task 5（WebUI ApprovalsPanel）— 独立 commit
- Task 6（systemmap 同步 + 全量回归）— 独立 commit

每个 commit message 首行简明祈使句；正文说明"为什么这么改"；BREAKING 变更在 commit message 中显式标注（本 spec 无 BREAKING）。
