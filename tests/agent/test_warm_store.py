"""WarmStore 温区缓冲区管理器的单元测试。"""

from __future__ import annotations

import copy

from OriginAgent.agent.warm_store import (
    WARM_BUFFER_METADATA_KEY,
    WarmStore,
)
from OriginAgent.session.manager import Session


def _make_session(key: str = "cli:test") -> Session:
    """构造一个空的测试用 Session。"""
    return Session(key=key)


def _user_msg(text: str) -> dict:
    """构造一条 user 消息，结构与 session.messages 中的格式一致。"""
    return {"role": "user", "content": text, "timestamp": "2026-07-07T13:00:00Z"}


def _assistant_msg(text: str) -> dict:
    """构造一条 assistant 消息。"""
    return {"role": "assistant", "content": text}


def test_append_one_turn_yields_turn_count_one():
    """追加一轮对话后温区有 1 轮。"""
    session = _make_session()
    store = WarmStore()

    turn = store.append(
        session,
        _user_msg("你好"),
        [_assistant_msg("你好，有什么可以帮你？")],
    )

    assert turn == 1
    buffer = store.load(session)
    assert buffer["turn_count"] == 1
    # 一轮 = 1 user + 1 assistant = 2 条消息
    assert len(buffer["messages"]) == 2
    # 状态已持久化到 session.metadata
    assert WARM_BUFFER_METADATA_KEY in session.metadata


def test_is_full_true_after_fifty_turns():
    """追加 50 轮后 is_full 返回 True。"""
    session = _make_session()
    store = WarmStore()

    for i in range(50):
        store.append(session, _user_msg(f"第{i + 1}轮"), [_assistant_msg(f"回复{i + 1}")])

    assert store.is_full(session) is True
    assert store.is_full(session, max_turns=50) is True


def test_is_full_false_below_threshold():
    """49 轮时 is_full 返回 False（边界值校验）。"""
    session = _make_session()
    store = WarmStore()

    for i in range(49):
        store.append(session, _user_msg(f"第{i + 1}轮"), [_assistant_msg(f"回复{i + 1}")])

    assert store.is_full(session) is False


def test_drain_returns_messages_and_clears_buffer():
    """drain 清空缓冲区并返回消息。"""
    session = _make_session()
    store = WarmStore()
    # 第一轮：1 user + 2 assistant = 3 条
    store.append(
        session,
        _user_msg("问题1"),
        [_assistant_msg("答案1"), _assistant_msg("补充")],
    )
    # 第二轮：1 user + 1 assistant = 2 条
    store.append(session, _user_msg("问题2"), [_assistant_msg("答案2")])

    drained = store.drain(session)

    # 返回的消息按追加顺序排列，共 5 条
    assert len(drained) == 5
    assert drained[0]["content"] == "问题1"
    assert drained[-1]["content"] == "答案2"
    # 缓冲区已清空
    buffer = store.load(session)
    assert buffer["messages"] == []
    assert buffer["turn_count"] == 0
    # 再次 drain 返回空列表
    assert store.drain(session) == []


def test_save_then_load_restores_state_across_sessions():
    """跨会话持久化：save 后 load 能恢复。"""
    session_a = _make_session("cli:a")
    store = WarmStore()
    store.append(session_a, _user_msg("持久化测试"), [_assistant_msg("已记录")])

    # 模拟跨会话 / 重启：用同一份 metadata 深拷贝构造新 session
    persisted_metadata = copy.deepcopy(session_a.metadata)
    session_b = Session(key="cli:a", metadata=persisted_metadata)

    buffer_b = store.load(session_b)
    assert buffer_b["turn_count"] == 1
    assert len(buffer_b["messages"]) == 2
    assert buffer_b["messages"][0]["content"] == "持久化测试"
    assert buffer_b["messages"][1]["content"] == "已记录"


def test_load_on_empty_session_returns_empty_buffer():
    """空 session 的 load 返回空缓冲区。"""
    session = _make_session()
    store = WarmStore()

    buffer = store.load(session)

    assert buffer["messages"] == []
    assert buffer["turn_count"] == 0
    assert "updated_at" in buffer
    # load 不应有副作用：不应写入 session.metadata
    assert WARM_BUFFER_METADATA_KEY not in session.metadata


def test_append_handles_multiple_assistant_messages_with_tool_calls():
    """append 支持含 tool_calls / tool_results 的多条 assistant 消息。"""
    session = _make_session()
    store = WarmStore()

    assistant_msgs = [
        {
            "role": "assistant",
            "content": "调用工具",
            "tool_calls": [{"id": "t1", "function": {"name": "search"}}],
        },
        {"role": "tool", "tool_call_id": "t1", "content": "结果"},
        {"role": "assistant", "content": "基于结果回答"},
    ]
    turn = store.append(session, _user_msg("搜索一下"), assistant_msgs)

    assert turn == 1
    buffer = store.load(session)
    # 1 user + 3 assistant/tool = 4 条
    assert len(buffer["messages"]) == 4
    # 原始字段（tool_calls 等）被原样保留
    assert "tool_calls" in buffer["messages"][1]


def test_warm_store_is_stateless_and_session_isolated():
    """WarmStore 无状态：同一实例操作多个 session 互不干扰。"""
    store = WarmStore()
    session_a = _make_session("cli:a")
    session_b = _make_session("cli:b")

    store.append(session_a, _user_msg("A 的问题"), [_assistant_msg("A 的回复")])
    store.append(session_b, _user_msg("B 的问题"), [_assistant_msg("B 的回复")])
    store.append(session_b, _user_msg("B 再问"), [_assistant_msg("B 再答")])

    assert store.load(session_a)["turn_count"] == 1
    assert store.load(session_b)["turn_count"] == 2
