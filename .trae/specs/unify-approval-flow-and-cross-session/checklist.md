# Checklist

> 用于 Spec 完成后的系统性验证（规则 31）。每个 checkpoint 必须通过实际代码审查或测试运行验证，禁止仅凭清单勾选免责。

## Task 1: D10 — ConfirmationRequest.owner_id 字段与跨会话审批

- [x] `ConfirmationRequest` dataclass 新增 `owner_id: str | None = None` 字段，位于 `metadata` 字段之后
- [x] `__post_init__` 中调用 `self.owner_id = _optional_str(self.owner_id)`
- [x] `from_dict` 中读取 `owner_id=_optional_str(raw.get("owner_id"))`
- [x] `to_dict` 通过 `asdict` 自动包含 `owner_id` 字段
- [x] `create_tool_approval` 签名新增 `owner_id: str | None = None` 参数，写入 ConfirmationRequest
- [x] `list_pending_for_owner(owner_id: str | None) -> list[ConfirmationRequest]` 方法实现，正确过滤
- [x] `resolve_user_reply` 新增 `caller_actor_id: str | None = None` 参数，owner 不匹配时返回 denied + 审计事件 `approval_denied_owner_mismatch`
- [x] 旧 json 文件（无 owner_id 字段）`from_dict` 加载时 `owner_id is None`（向后兼容）
- [x] 所有 `create_tool_approval` 的 caller 透传 `actor_id` 作为 `owner_id`（搜索确认）
- [x] `tests/agent/test_confirmation_owner_id.py` 5 个测试全部 PASSED
- [x] 红线闭集（规则 18 安全边界）100% 独立核验通过

## Task 2: D7 — tool_approval TTL 按 trigger 分级

- [x] `ConfirmationConfig` 新增 `tool_approval_cron_ttl_seconds: int = Field(default=3600, ge=60, le=86400)` 字段
- [x] `_ttl_for` 签名扩展为 `(kind, risk, config=None, trigger=None)`
- [x] `_ttl_for` 在 `kind=="tool_approval" and trigger=="scheduled"` 时返回 1h TTL
- [x] `_ttl_for` 在其他情况维持现有 `confirm_ttl_by_risk` 查表逻辑（向后兼容）
- [x] `create_tool_approval` 调用 `_ttl_for` 时传入 `trigger` 参数
- [x] `tests/agent/test_confirmation_ttl.py` 4 个测试全部 PASSED
- [x] 既有 confirmation 测试无回归

## Task 3: 方向 C-1 — REST API `/api/approvals` 端点

- [x] `GET /api/approvals` 端点实现，返回当前 owner 的 pending 列表
- [x] `POST /api/approvals/{confirmation_id}/approve` 端点实现，审批通过并返回 grant_id
- [x] `POST /api/approvals/{confirmation_id}/reject` 端点实现，审批拒绝
- [x] caller_actor_id 从 `?owner_id=` query 参数显式传入（选项 A，规则 26 假设显式化）
- [x] 跨 owner 审批返回 403 + 审计事件
- [x] `tests/gateway/test_approvals_api.py` 5 个测试全部 PASSED（含 1 个 401 鉴权测试）
- [x] 红线闭集（规则 18 鉴权边界）100% 独立核验通过

## Task 4: 方向 C-2 — Telegram `/approval` 命令

- [x] `/approval` Regex filter 在 telegram.py 中新增（与 `/pairing` 同级）— `OriginAgent/channels/telegram.py:252` BotCommand + L356-365 Regex filter
- [x] `/approval list` 子命令：调用 `list_pending_for_owner` 并格式化回复 — `OriginAgent/command/builtin.py:490-563`（cmd_approval 中 subcommand == "list" 分支）
- [x] `/approval approve <id>` 子命令：调用 `resolve_user_reply` 并回复确认 — builtin.py 中 reply="yes" 分支
- [x] `/approval reject <id>` 子命令：调用 `resolve_user_reply` 并回复确认 — builtin.py 中 reply="no" 分支
- [x] sender 通过 identity resolver 解析为 tenant_id（owner_id）— `resolver.resolve(channel=ctx.msg.channel, sender_id=str(ctx.msg.sender_id))` → `tenant.unified_session_key`
- [x] guest 角色直接拒绝（不调用 ConfirmationManager）— builtin.py 中 `tenant_id in {"guest", "__pairing_pending__"}` 分支直接返回拒绝消息，`test_guest_sender_approval_command_rejected` 断言 `manager.resolve_user_reply.assert_not_called()` + `manager.list_pending_for_owner.assert_not_called()`
- [x] confirmation_id 必须属于 caller 的 owner_id（跨 owner 审批被拒绝）— 通过 `caller_actor_id=owner_id` 透传给 `manager.resolve_user_reply`，由 Task 1 的 D10 owner_id 校验逻辑拦截
- [x] `tests/channels/test_telegram_approval_command.py` 4 个测试全部 PASSED — 2026-07-20 实测 4/4 PASSED
- [x] 红线闭集（规则 18 鉴权边界）100% 独立核验通过 — guest 路径不调用 manager；owner_id 通过 resolver 解析而非用户传入

## Task 5: 方向 C-3 — WebUI ApprovalsPanel

- [x] `webui/src/components/approvals/ApprovalsPanel.tsx` 新建，渲染列表 + approve/reject 按钮
- [x] `webui/src/lib/api.ts` 新增 `listApprovals` / `approveApproval` / `rejectApproval` 三个方法
- [x] `ApprovalsPanel` 注入到 settings 主入口（与 `TenantsSettings` 同级）
- [x] ApprovalsPanel 仅对 owner 可见（guest 隐藏，通过后端 403 + UI permission_denied 实现）
- [x] i18n key 在 9 个 locale 文件（en/zh-CN/zh-TW/vi/ko/ja/id/fr/es）中新增
- [x] `cd webui && npm run build` 构建成功（3161 modules, 9.97s）
- [x] 浏览器验证：跳过（无运行中后端，单测已覆盖核心交互路径 — spec 标注可选）

## Task 6: systemmap 同步 + 全量回归

- [x] `systemmap/domain-overview.md` cron 章节添加 D10+D7+方向 C 闭环引用 + 移除过时未完成项
- [x] 新增"审批流（D10+D7+方向 C 修复,2026-07-20）"章节
- [x] `last_verified` 更新到 2026-07-20T02:00:00+08:00
- [x] 全量回归测试通过：95/95 PASSED in 2.31s
- [x] 前端构建通过：`cd webui && npm run build` 成功
- [x] 历史 pending 可被 owner 通过新审批 UI 清理：D10+NONE filter 允许旧 pending(owner_id=None) 被任意 caller 审批，D7 cron TTL=1h 防止过期，方向 C 三件套提供审批入口 — 已具备清理能力

## 跨 Task 合规性检查（规则 27 清单）

- [x] 红线规则确认：规则 3 边界校验、规则 12 幂等、规则 18 安全边界 全部 ✅
- [x] 高风险规则推演：跨 session 审批的并发安全（多个 caller 同时 approve 同一 confirmation 的去重）已通过 `claim_consumption` 幂等覆盖
- [x] 改动范围合规性：所有改动可追溯到 spec 中具体 Requirement
- [x] 系统认知同步：systemmap 已更新
- [x] 技术债声明：无新增技术债（本 spec 是闭环修复）
- [x] 提交策略：6 个独立 commit，每个可单独 revert
