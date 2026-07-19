"""Telegram ``/approval`` 命令集成测试 — Task 4（方向 C-2）。

spec: ``unify-approval-flow-and-cross-session``

覆盖 4 个场景：
1. ``/approval list`` —— owner sender，返回 2 条 pending 信息
2. ``/approval approve <id>`` —— owner sender，调 ``resolve_user_reply`` 成功后回复"已批准"
3. ``/approval reject <id>`` —— owner sender，回复"已拒绝"
4. guest sender 调 ``/approval approve <id>`` —— 直接拒绝（规则 18 安全边界，
   不调用 ``ConfirmationManager``）

规则 26 假设显式化：TelegramChannel 本身没有 ``confirmation_manager`` /
``identity_resolver`` 注入（spec 与实际代码不符）。沿用 ``/pairing`` 模式：
- ``telegram.py`` 用 Regex filter 把 ``/approval`` 转发到 bus
- ``command/builtin.py`` 的 ``cmd_approval`` handler 通过 ``ctx.loop`` 访问
  ``_identity_resolver`` 和 ``_confirmation_manager``（与 ``cmd_pairing`` 同源）

测试直接调用 ``cmd_approval(ctx)``，构造 fake loop 持有 mock 的 resolver /
manager，聚焦验证命令处理器的鉴权与回包契约（规则 34 验证先行）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.confirmation import ConfirmationResult
from OriginAgent.bus.events import InboundMessage
from OriginAgent.command.builtin import cmd_approval
from OriginAgent.command.router import CommandContext

# Telegram 依赖可选；本测试只验证 cmd_approval handler，不实例化 TelegramChannel，
# 但仍尊重 telegram 可缺失的既有 skip 约定（与 tests/channels/test_telegram_channel.py 同模式）。
try:
    import telegram  # noqa: F401
except ImportError:
    pytest.skip("Telegram dependencies not installed (python-telegram-bot)", allow_module_level=True)


# 固定时间锚点（与 tests/gateway/test_approvals_api.py 对齐）
NOW_ISO = "2026-07-20T12:00:00+00:00"
EXPIRES_ISO = "2026-07-20T13:00:00+00:00"  # TTL 1h（D7 cron trigger）


def _make_pending(confirmation_id: str, *, tool_name: str, prompt: str,
                  owner_id: str = "tenant:owner") -> SimpleNamespace:
    """构造一个最小化的 pending confirmation mock。

    用 SimpleNamespace 而非真实 ConfirmationRequest，避免测试耦合
    dataclass 字段校验逻辑（那些在 test_confirmation_*.py 中已覆盖）。
    cmd_approval handler 只读 confirmation_id / metadata.tool_name / prompt /
    risk / expires_at / owner_id，所以只暴露这些字段。
    """
    return SimpleNamespace(
        confirmation_id=confirmation_id,
        kind="tool_approval",
        status="pending",
        prompt=prompt,
        action=f"tool:{tool_name}",
        risk="high",
        owner_id=owner_id,
        created_at=NOW_ISO,
        expires_at=EXPIRES_ISO,
        metadata={"tool_name": tool_name},
    )


def _make_tenant(tenant_id: str) -> SimpleNamespace:
    """构造一个最小化的 Tenant mock。"""
    return SimpleNamespace(
        tenant_id=tenant_id,
        unified_session_key=f"tenant:{tenant_id}",
        display_name=tenant_id.capitalize(),
    )


def _make_ctx(
    *,
    args: str,
    sender_id: str = "123|owner",
    resolver_return: SimpleNamespace | None = None,
    manager: MagicMock | None = None,
) -> tuple[CommandContext, MagicMock, MagicMock]:
    """构造一个携带 fake loop 的 CommandContext。

    返回 (ctx, resolver, manager) 以便测试中独立断言 mock 的调用契约。
    """
    if resolver_return is None:
        resolver_return = _make_tenant("owner")
    if manager is None:
        manager = MagicMock()

    resolver = MagicMock()
    resolver.resolve.return_value = resolver_return

    fake_loop = SimpleNamespace(
        _identity_resolver=resolver,
        _confirmation_manager=manager,
    )

    msg = InboundMessage(
        channel="telegram",
        sender_id=sender_id,
        chat_id="1",
        content=f"/approval {args}".rstrip(),
    )
    ctx = CommandContext(
        msg=msg,
        session=None,
        key="telegram:1",
        raw=f"/approval {args}".rstrip(),
        args=args,
        loop=fake_loop,
    )
    return ctx, resolver, manager


async def test_approval_list_returns_pending_for_owner_sender() -> None:
    """``/approval list`` —— owner sender，bot 回复包含 2 条 pending 信息。

    断言：
    - resolver.resolve 被调用（channel=telegram, sender_id=sender_id）
    - manager.list_pending_for_owner 被调用，owner_id="tenant:owner"
    - 回复内容包含 2 个 confirmation_id 和 tool_name
    """
    ctx, resolver, manager = _make_ctx(args="list")
    manager.list_pending_for_owner.return_value = [
        _make_pending("confirmation_abc", tool_name="exec", prompt="A wants exec"),
        _make_pending("confirmation_def", tool_name="read_file", prompt="A wants read"),
    ]

    result = await cmd_approval(ctx)

    # resolver 契约：必须用 channel + sender_id 解析身份（规则 18 安全边界）
    resolver.resolve.assert_called_once_with(channel="telegram", sender_id="123|owner")
    # manager 契约：必须用 tenant 的 unified_session_key 作为 owner_id 过滤
    manager.list_pending_for_owner.assert_called_once_with("tenant:owner")

    content = result.content
    assert "confirmation_abc" in content, f"回复应包含 confirmation_abc，实际: {content!r}"
    assert "confirmation_def" in content, f"回复应包含 confirmation_def，实际: {content!r}"
    assert "exec" in content, f"回复应包含 tool_name=exec，实际: {content!r}"
    assert "read_file" in content, f"回复应包含 tool_name=read_file，实际: {content!r}"
    # prompt 摘要应展示（让 owner 能识别 pending）
    assert "A wants exec" in content or "A wants" in content, (
        f"回复应包含 prompt 摘要，实际: {content!r}"
    )


async def test_approval_approve_executes_and_replies() -> None:
    """``/approval approve <id>`` —— owner sender，调 ``resolve_user_reply`` 成功后回复"已批准"。

    断言：
    - resolver.resolve 被调用
    - manager.resolve_user_reply 被调用，confirmation_id 与 reply="yes" / caller_actor_id="tenant:owner"
    - 回复包含"已批准"或 confirmation_id
    """
    ctx, resolver, manager = _make_ctx(args="approve confirmation_abc")
    manager.resolve_user_reply.return_value = ConfirmationResult(
        confirmation_id="confirmation_abc",
        decision="confirmed",
        reason="confirmed once",
    )

    result = await cmd_approval(ctx)

    resolver.resolve.assert_called_once_with(channel="telegram", sender_id="123|owner")
    manager.resolve_user_reply.assert_called_once_with(
        "confirmation_abc", "yes", caller_actor_id="tenant:owner"
    )

    content = result.content
    assert "已批准" in content or "批准" in content, (
        f"回复应包含'已批准'字样，实际: {content!r}"
    )
    assert "confirmation_abc" in content, (
        f"回复应包含 confirmation_id，实际: {content!r}"
    )


async def test_approval_reject_executes_and_replies() -> None:
    """``/approval reject <id>`` —— owner sender，回复"已拒绝"。

    断言：
    - manager.resolve_user_reply 被调用，reply="no"
    - 回复包含"已拒绝"
    """
    ctx, resolver, manager = _make_ctx(args="reject confirmation_abc")
    manager.resolve_user_reply.return_value = ConfirmationResult(
        confirmation_id="confirmation_abc",
        decision="rejected",
        reason="user rejected",
    )

    result = await cmd_approval(ctx)

    resolver.resolve.assert_called_once_with(channel="telegram", sender_id="123|owner")
    manager.resolve_user_reply.assert_called_once_with(
        "confirmation_abc", "no", caller_actor_id="tenant:owner"
    )

    content = result.content
    assert "已拒绝" in content or "拒绝" in content, (
        f"回复应包含'已拒绝'字样，实际: {content!r}"
    )
    assert "confirmation_abc" in content


async def test_guest_sender_approval_command_rejected() -> None:
    """guest sender 调 ``/approval approve`` —— 直接拒绝，不调用 ConfirmationManager。

    规则 18 安全边界：guest 角色不能审批，必须直接拒绝，且不得调用
    ConfirmationManager（避免 guest 通过命令路径触发任何状态变更）。

    断言：
    - resolver.resolve 返回 tenant:guest
    - manager.resolve_user_reply **未被调用**
    - manager.list_pending_for_owner **未被调用**
    - 回复包含"权限不足"或类似拒绝消息
    """
    ctx, resolver, manager = _make_ctx(
        args="approve confirmation_abc",
        resolver_return=_make_tenant("guest"),
    )

    result = await cmd_approval(ctx)

    resolver.resolve.assert_called_once_with(channel="telegram", sender_id="123|owner")
    # 关键断言：guest 不能触发任何 ConfirmationManager 写操作
    manager.resolve_user_reply.assert_not_called()
    manager.list_pending_for_owner.assert_not_called()

    content = result.content
    assert "权限" in content or "不足" in content or "拒绝" in content, (
        f"guest sender 应收到'权限不足'类拒绝消息，实际: {content!r}"
    )
