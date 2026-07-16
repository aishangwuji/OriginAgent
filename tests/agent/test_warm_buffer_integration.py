"""Phase 2 Task 4 集成测试：turn 结束后将热区超出 50 轮的部分追加到温区。

覆盖三个场景：
- 53 轮后温区有 3 轮（边界值）；
- 100 轮后温区满 50 轮（恰好达到温区上限）；
- 不足 50 轮时温区为空（无溢出）。

测试既直接覆盖 ``_spill_overflow_into_warm_store`` 的核心逻辑，也通过
``AgentTurnPipeline.state_save`` 端到端验证 state_save 集成正确。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.agent_turn_pipeline import (
    AgentTurnPipeline,
    TurnContext,
    TurnEvent,
    TurnPipelineDeps,
    TurnState,
    _spill_overflow_into_warm_store,
    _split_messages_by_turn,
)
from OriginAgent.agent.warm_store import WARM_BUFFER_METADATA_KEY, WarmStore
from OriginAgent.bus.events import InboundMessage
from OriginAgent.session.manager import Session


def _user_msg(text: str, idx: int) -> dict:
    """构造一条 user 消息，附带稳定 timestamp 便于排序。"""
    return {
        "role": "user",
        "content": f"{text}-{idx}",
        "timestamp": f"2026-07-07T13:{idx:02d}:00Z",
    }


def _assistant_msg(text: str, idx: int) -> dict:
    """构造一条 assistant 消息。"""
    return {
        "role": "assistant",
        "content": f"{text}-a{idx}",
        "timestamp": f"2026-07-07T13:{idx:02d}:30Z",
    }


def _populate_turns(session: Session, turn_count: int) -> None:
    """向 session 直接填充指定轮数的对话（每轮 1 user + 1 assistant）。"""
    for i in range(turn_count):
        session.messages.append(_user_msg("u", i))
        session.messages.append(_assistant_msg("a", i))


def test_split_messages_by_turn_groups_user_with_following_assistants() -> None:
    """_split_messages_by_turn 按 user turn 边界拆分消息列表。"""
    messages = [
        _user_msg("u", 0),
        _assistant_msg("a", 0),
        _user_msg("u", 1),
        _assistant_msg("a", 1),
        _assistant_msg("a-extra", 1),
    ]

    turns = _split_messages_by_turn(messages)

    assert len(turns) == 2
    assert turns[0][0]["content"] == "u-0"
    assert len(turns[0][1]) == 1
    assert turns[1][0]["content"] == "u-1"
    assert len(turns[1][1]) == 2


def test_split_messages_by_turn_skips_leading_orphan_assistants() -> None:
    """开头孤儿 assistant 消息被忽略，不污染温区首轮结构。"""
    messages = [
        _assistant_msg("orphan", 0),
        _user_msg("u", 0),
        _assistant_msg("a", 0),
    ]

    turns = _split_messages_by_turn(messages)

    assert len(turns) == 1
    assert turns[0][0]["content"] == "u-0"
    assert len(turns[0][1]) == 1


def test_spill_overflow_53_turns_yields_3_warm_turns() -> None:
    """53 轮：热区保留 50 轮，温区追加 3 轮。"""
    session = Session(key="cli:warm-53")
    _populate_turns(session, turn_count=53)

    _spill_overflow_into_warm_store(session, max_turns=50)

    buffer = WarmStore().load(session)
    assert buffer["turn_count"] == 3
    # 热区保留 50 轮 = 50 user + 50 assistant = 100 条消息
    assert len(session.messages) == 100
    # 热区起点应为第 4 轮的 user 消息（索引 6），内容形如 u-3
    assert session.messages[0]["content"] == "u-3"
    # 温区首条 user 消息应为最早的 u-0
    assert buffer["messages"][0]["content"] == "u-0"


def test_spill_overflow_100_turns_fills_warm_to_50() -> None:
    """100 轮：热区保留 50 轮，温区正好满 50 轮。"""
    session = Session(key="cli:warm-100")
    _populate_turns(session, turn_count=100)

    _spill_overflow_into_warm_store(session, max_turns=50)

    warm_store = WarmStore()
    buffer = warm_store.load(session)
    assert buffer["turn_count"] == 50
    assert warm_store.is_full(session) is True
    # 热区保留 50 轮 = 100 条消息
    assert len(session.messages) == 100
    # 热区第一条 user 应为第 51 轮的 u-50
    assert session.messages[0]["content"] == "u-50"
    # 温区首条 user 应为最早的 u-0
    assert buffer["messages"][0]["content"] == "u-0"


def test_spill_overflow_below_50_turns_leaves_warm_empty() -> None:
    """不足 50 轮：温区为空，session.messages 不变。"""
    session = Session(key="cli:warm-short")
    _populate_turns(session, turn_count=30)

    _spill_overflow_into_warm_store(session, max_turns=50)

    buffer = WarmStore().load(session)
    assert buffer["turn_count"] == 0
    assert buffer["messages"] == []
    # session.messages 保持原样，未被裁剪
    assert len(session.messages) == 60
    assert WARM_BUFFER_METADATA_KEY not in session.metadata


def test_spill_overflow_preserves_tool_call_pairing() -> None:
    """含 tool_calls / tool_results 的轮次在溢出时保持完整配对。"""
    session = Session(key="cli:warm-toolcall")
    # 前 50 轮：标准 user + assistant
    _populate_turns(session, turn_count=50)
    # 第 51 轮：user + assistant(tool_calls) + tool_result + assistant
    session.messages.append(_user_msg("tool", 50))
    session.messages.append({
        "role": "assistant",
        "content": "调用工具",
        "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "search"}}],
        "timestamp": "2026-07-07T13:50:30Z",
    })
    session.messages.append({
        "role": "tool",
        "tool_call_id": "t1",
        "name": "search",
        "content": "结果",
        "timestamp": "2026-07-07T13:50:45Z",
    })
    session.messages.append(_assistant_msg("after-tool", 50))
    # 第 52 轮：user + assistant
    session.messages.append(_user_msg("u", 51))
    session.messages.append(_assistant_msg("a", 51))

    _spill_overflow_into_warm_store(session, max_turns=50)

    buffer = WarmStore().load(session)
    # 仅溢出 2 轮（第 51、52 轮）
    assert buffer["turn_count"] == 2
    # 热区不应出现孤儿 tool_result（即 tool 消息必须与其 assistant 在一起）
    tool_indices = [i for i, m in enumerate(session.messages) if m.get("role") == "tool"]
    for idx in tool_indices:
        # 找到该 tool_result 对应的 tool_call_id 必须出现在前方的 assistant 中
        tool_call_id = session.messages[idx].get("tool_call_id")
        assert any(
            tc.get("id") == tool_call_id
            for m in session.messages[:idx]
            if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or [])
        ), f"orphan tool_result at index {idx} with id={tool_call_id}"


def _build_minimal_deps(session: Session) -> TurnPipelineDeps:
    """构造最小可用的 TurnPipelineDeps，所有依赖均 mock 化。"""
    consolidator = MagicMock()
    consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)

    working_memory = MagicMock()
    working_memory.load = MagicMock(return_value=MagicMock())

    rolling_episode_compaction = MagicMock()
    rolling_episode_compaction.maybe_compact = MagicMock(return_value=None)

    sessions = MagicMock()
    sessions.save = MagicMock(return_value=None)

    context = MagicMock()
    context._context_config = MagicMock(governance_enabled=False)

    tools = {"message": MagicMock()}

    return TurnPipelineDeps(
        auto_compact=MagicMock(),
        commands=MagicMock(),
        command_loop=MagicMock(),
        get_consolidator=lambda: consolidator,
        get_tools=lambda: tools,
        get_context=lambda: context,
        sessions=sessions,
        bus=MagicMock(),
        get_working_memory=lambda: working_memory,
        get_memory_governance=lambda: MagicMock(),
        get_rolling_episode_compaction=lambda: rolling_episode_compaction,
        workspace=Path("/tmp/test-warm"),
        tools_config=MagicMock(),
        domain_runtime_contributions=[],
        domain_runtime_overrides={},
        archive_session_file_cap=lambda s: None,
        restore_runtime_checkpoint=lambda s: False,
        restore_pending_user_turn=lambda s: False,
        load_continuity_checkpoint=lambda s: None,
        record_recovered_continuity_checkpoint=lambda c: None,
        mark_webui_session=lambda s, m: None,
        persist_shortcut_command_turn=lambda m, s, r: None,
        is_webui_message=lambda m: False,
        resolve_runtime_context=lambda *a, **kw: MagicMock(),
        record_runtime_context=lambda s, r: None,
        write_continuity_runtime_identity=lambda s, r: None,
        snapshot_for_trigger=lambda t: MagicMock(),
        update_working_memory_from_turn=lambda *a, **kw: None,
        set_tool_context=lambda *a, **kw: None,
        replay_token_budget=lambda: 4096,
        build_initial_messages=lambda *a, **kw: [],
        persist_user_message_early=lambda *a, **kw: False,
        schedule_session_search_refresh=lambda *a, **kw: None,
        build_progress_callback=lambda m: MagicMock(),
        build_retry_wait_callback=lambda m: MagicMock(),
        pending_ask_user_id=lambda m: None,
        consume_tool_approval_reply=lambda *a, **kw: (None, False),
        build_recovered_continuity_context=lambda c: {},
        run_agent_loop=lambda *a, **kw: (None, [], [], "completed", False),
        clear_pending_user_turn=lambda s: None,
        clear_runtime_checkpoint=lambda s: None,
        save_turn=lambda s, m, skip: None,
        record_governance_audit=lambda a: None,
        save_continuity_checkpoint=lambda *a, **kw: {},
        schedule_background=lambda c: None,
        schedule_nearline_memory=lambda c: None,
        schedule_background_review=lambda c: None,
        schedule_curator_review=lambda c: None,
        automation_enabled=lambda: False,
        action_planner=None,
        record_action_continuity_audit=lambda a: None,
        assemble_outbound=lambda *a, **kw: None,
        get_max_messages=lambda: 120,
    )


def _build_save_ctx(session: Session) -> TurnContext:
    """构造一个直接进入 state_save 的 TurnContext。"""
    return TurnContext(
        msg=InboundMessage(
            channel="cli",
            sender_id="user-1",
            chat_id="warm",
            content="continue",
            metadata={},
        ),
        session_key=session.key,
        state=TurnState.SAVE,
        turn_id="turn:warm-save",
        session=session,
        history=[],
        all_messages=[{"role": "assistant", "content": "done"}],
        final_content="done",
    )


@pytest.mark.asyncio
async def test_state_save_spills_overflow_into_warm_store_at_53_turns() -> None:
    """端到端：state_save 在 53 轮时把超出 50 轮的部分追加到温区。"""
    session = Session(key="cli:state-save-53")
    _populate_turns(session, turn_count=53)

    pipeline = AgentTurnPipeline(_build_minimal_deps(session))
    ctx = _build_save_ctx(session)

    result = await pipeline.state_save(ctx)

    assert result == TurnEvent.OK
    buffer = WarmStore().load(session)
    assert buffer["turn_count"] == 3
    assert len(session.messages) == 100
    assert session.messages[0]["content"] == "u-3"
    assert buffer["messages"][0]["content"] == "u-0"


@pytest.mark.asyncio
async def test_state_save_keeps_warm_empty_below_50_turns() -> None:
    """端到端：state_save 在 30 轮时不触发温区填补。"""
    session = Session(key="cli:state-save-short")
    _populate_turns(session, turn_count=30)

    pipeline = AgentTurnPipeline(_build_minimal_deps(session))
    ctx = _build_save_ctx(session)

    result = await pipeline.state_save(ctx)

    assert result == TurnEvent.OK
    buffer = WarmStore().load(session)
    assert buffer["turn_count"] == 0
    assert WARM_BUFFER_METADATA_KEY not in session.metadata
    # session.messages 保持原样
    assert len(session.messages) == 60


@pytest.mark.asyncio
async def test_state_save_fills_warm_to_50_at_100_turns() -> None:
    """端到端：state_save 在 100 轮时温区正好满 50 轮。"""
    session = Session(key="cli:state-save-100")
    _populate_turns(session, turn_count=100)

    pipeline = AgentTurnPipeline(_build_minimal_deps(session))
    ctx = _build_save_ctx(session)

    result = await pipeline.state_save(ctx)

    assert result == TurnEvent.OK
    warm_store = WarmStore()
    buffer = warm_store.load(session)
    assert buffer["turn_count"] == 50
    assert warm_store.is_full(session) is True
    assert len(session.messages) == 100
    assert session.messages[0]["content"] == "u-50"
