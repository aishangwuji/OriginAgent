"""Tests for ``AgentRuntime._build_initial_messages`` branch unification (P0-2).

背景（Issue 2）：
    cron 触发的 turn 在进入分支 B（``pending_ask_id`` 存在 +
    ``enable_phase1_continuity=False``）时，原先通过内联构造拼装 user 内容，
    既不经过 ``ContextAssemblerV2.assemble``（导致 ``context.assembled``
    事件缺失），又重复调用 ``build_reference_context_blocks``（一次用于
    内容、一次用于审计）。

    本测试验证修复后所有分支均通过 ``ContextAssemblerV2.assemble`` 拼装，
    确保：
    1. cron 触发的 turn 也发射 ``context.assembled`` 事件；
    2. ``build_reference_context_blocks`` 每 turn 仅调用一次；
    3. cron 写入用户 session 时历史消息被完整拼入。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from OriginAgent.agent.agent_runtime import AgentRuntime, RuntimeDependencies
from OriginAgent.agent.context_assembler import ContextAssemblerV2
from OriginAgent.bus.events import InboundMessage
from OriginAgent.session.manager import Session
from OriginAgent.utils.constants import RoleConstants


# ── 公共 mock 工具 ──────────────────────────────────────────────────


def _build_mock_context(*, enable_phase1_continuity: bool = False) -> MagicMock:
    """构造一个最小可用的 ContextBuilder mock。

    ``assemble_user_content`` 被接线到真实的 ``ContextAssemblerV2.assemble``，
    这样 ``context.assembled`` 事件和 ``build_reference_context_blocks``
    调用计数都可以被真实地观测到。
    """
    ctx = MagicMock()
    ctx.timezone = "UTC"
    ctx.RUNTIME_CONTEXT_KIND = "runtime_context"
    ctx.WORKING_MEMORY_CONTEXT_KIND = "working_memory_context"
    ctx.WORLD_STATE_CONTEXT_KIND = "world_state_context"
    ctx.TASK_STATE_CONTEXT_KIND = "task_state_context"
    ctx.world_state = None
    ctx.session_store = None

    ctx._context_config = MagicMock()
    ctx._context_config.enable_phase1_continuity = enable_phase1_continuity

    ctx.build_user_content.return_value = []
    ctx.build_runtime_context_block.return_value = {
        "type": "text",
        "text": "runtime",
        "_meta": {"kind": "runtime_context"},
    }
    ctx.build_internal_event_block.return_value = {
        "type": "text",
        "text": "internal event",
        "_meta": {"kind": "internal_event"},
    }
    ctx.prepare_prewarm_bundle.return_value = None
    ctx.build_phase1_continuity_blocks.return_value = []
    ctx.build_reference_context_blocks.return_value = [
        {
            "type": "text",
            "text": "reference",
            "_meta": {"kind": "reference_context", "source": "warm"},
        },
    ]
    ctx.collect_assembly_audit.return_value = {
        "media": {},
        "retrieval_fusion": {
            "sources_used": [],
            "source_counts": {},
            "deduped_count": 0,
            "trimmed_count": 0,
            "hits": {},
        },
        "governance": {},
        "prewarm": {"prewarm_empty": True},
        "governance_enabled": False,
        "prewarm_enabled": False,
    }
    ctx.build_system_prompt.return_value = "system prompt"
    # _apply_prompt_budget 透传 messages，便于断言返回内容
    ctx._apply_prompt_budget.side_effect = lambda messages, **kw: messages

    # 将 assemble_user_content 接线到真实 ContextAssemblerV2
    assembler = ContextAssemblerV2(ctx)

    def _assemble_user_content(**kwargs):
        result = assembler.assemble(**kwargs)
        ctx._last_context_assembly_audit = dict(result.audit)
        return result

    ctx.assemble_user_content.side_effect = _assemble_user_content
    return ctx


def _build_runtime_with_mock_context(
    ctx: MagicMock, *, context_window_tokens: int = 32_000
) -> AgentRuntime:
    """构造一个 AgentRuntime，其 deps 使用 mock context。"""
    state_holder = MagicMock()
    state = MagicMock()
    state.last_runtime_context = None
    state.last_context_assembly = {}
    state_holder.get.return_value = state

    tools = MagicMock()
    tools._capability_snapshot = None

    provider = MagicMock()
    provider.generation.max_tokens = 4096

    deps = RuntimeDependencies(
        context=ctx,
        state_holder=state_holder,
        tools=tools,
        provider=provider,
        context_window_tokens=context_window_tokens,
    )
    runtime = AgentRuntime(deps)
    # _build_prompt_self_model 依赖 workspace/introspection 等重基础设施，
    # 本测试仅关注 _build_initial_messages 的分支路由，直接打桩返回空 payload。
    runtime._build_prompt_self_model = lambda: {}
    return runtime


def _mk_cron_message(*, content: str = "cron tick", session_key: str = "cron:job-1") -> InboundMessage:
    """构造一个 cron 触发的 InboundMessage。"""
    return InboundMessage(
        channel="cli",
        sender_id="cron",
        chat_id="job-1",
        content=content,
        is_internal=True,
        metadata={"trigger": "scheduled"},
        session_key_override=session_key,
    )


def _mk_session(*, key: str = "cron:job-1", message_count: int = 0) -> Session:
    session = Session(key=key)
    for i in range(message_count):
        session.messages.append({
            "role": RoleConstants.USER if i % 2 == 0 else RoleConstants.ASSISTANT,
            "content": f"msg-{i}",
        })
    return session


def _find_log_call(mock_log_event, event_name):
    """从 mock_log_event.call_args_list 中找出指定事件名的调用。"""
    for call in mock_log_event.call_args_list:
        if call.args and call.args[0] == event_name:
            return call
    return None


# ── 测试 1：cron 触发的 turn 发射 context.assembled 事件 ────────────


def test_cron_trigger_emits_context_assembled_event():
    """分支 B（pending_ask_id + enable_phase1_continuity=False）修复后
    应通过 ``ContextAssemblerV2.assemble`` 拼装，从而发射 ``context.assembled``。
    """
    ctx = _build_mock_context(enable_phase1_continuity=False)
    runtime = _build_runtime_with_mock_context(ctx)

    msg = _mk_cron_message()
    session = _mk_session(key="cron:job-1")
    history: list[dict] = []

    with patch("OriginAgent.agent.context_assembler.log_event") as mock_log_event:
        runtime._build_initial_messages(
            msg,
            session,
            history,
            pending_ask_id="ask-1",
            pending_summary=None,
        )

    assembled_call = _find_log_call(mock_log_event, "context.assembled")
    assert assembled_call is not None, (
        "cron 触发的 turn 进入分支 B 时必须发射 context.assembled 事件"
    )
    kwargs = assembled_call.kwargs
    assert "block_count" in kwargs
    assert "block_kinds" in kwargs
    assert kwargs["block_count"] >= 1


# ── 测试 2：build_reference_context_blocks 每 turn 仅调用一次 ───────


def test_build_reference_context_blocks_called_once_per_turn():
    """修复后分支 B 不再重复调用 ``build_reference_context_blocks``。

    修复前：分支 B 内联构造调用一次 + 审计字典推导再调用一次 = 2 次。
    修复后：通过 ``ContextAssemblerV2.assemble`` 统一拼装 = 1 次。
    """
    ctx = _build_mock_context(enable_phase1_continuity=False)
    runtime = _build_runtime_with_mock_context(ctx)

    msg = _mk_cron_message()
    session = _mk_session(key="cron:job-1")
    history: list[dict] = []

    with patch("OriginAgent.agent.context_assembler.log_event"):
        runtime._build_initial_messages(
            msg,
            session,
            history,
            pending_ask_id="ask-1",
            pending_summary=None,
        )

    assert ctx.build_reference_context_blocks.call_count == 1, (
        f"build_reference_context_blocks 应每 turn 仅调用一次，"
        f"实际调用 {ctx.build_reference_context_blocks.call_count} 次"
    )


# ── 测试 3：cron 写入用户 session 时历史被完整拼入 ─────────────────


def test_cron_on_user_session_includes_full_history():
    """cron 写入 ``tenant:guest`` session（已有 50 条历史）时，
    返回的 messages 应包含完整历史（> 30 条）。
    """
    ctx = _build_mock_context(enable_phase1_continuity=False)
    runtime = _build_runtime_with_mock_context(ctx)

    # cron 写入用户 session
    msg = _mk_cron_message(session_key="tenant:guest")
    session = _mk_session(key="tenant:guest", message_count=50)

    # 构造 50 条历史
    history = [
        {
            "role": RoleConstants.USER if i % 2 == 0 else RoleConstants.ASSISTANT,
            "content": f"history-{i}",
        }
        for i in range(50)
    ]

    with patch("OriginAgent.agent.context_assembler.log_event"):
        messages = runtime._build_initial_messages(
            msg,
            session,
            history,
            pending_ask_id="ask-1",
            pending_summary=None,
        )

    # history 应被完整拼入：system + 50 history + tool_result + user_content
    # 至少 > 30
    assert len(messages) > 30, (
        f"cron 写入用户 session 时应包含完整历史，"
        f"messages 长度 {len(messages)} <= 30"
    )

    # 验证历史消息确实存在于返回中
    history_contents = [
        m.get("content") for m in messages
        if m.get("role") in (RoleConstants.USER, RoleConstants.ASSISTANT)
        and isinstance(m.get("content"), str)
    ]
    assert any("history-0" in c for c in history_contents), (
        "历史首条消息未出现在返回的 messages 中"
    )
    assert any("history-49" in c for c in history_contents), (
        "历史末条消息未出现在返回的 messages 中"
    )
