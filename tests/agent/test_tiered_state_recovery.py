"""Phase 6 Task 9 集成测试：session 恢复时的三级重建。

覆盖 SubTask 9.4 要求的四个场景：
- 进程重启后热区从 session.messages 尾部恢复（get_hot_history）
- 温区从 session.metadata["warm_buffer"] 恢复
- 冷区索引从 warm_summaries.jsonl 恢复
- 三级状态完整重建

设计要点：
- 使用真实 workspace（tmp_path）验证 warm_summaries.jsonl 文件读取；
- 使用真实 SessionManager / Session 验证热区 get_hot_history 行为；
- 使用 WarmStore 验证温区状态持久化到 session.metadata；
- 通过 AgentRuntime._load_continuity_checkpoint 验证三级重建的完整输出。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from OriginAgent.agent.agent_runtime import AgentRuntime
from OriginAgent.agent.warm_store import WarmStore
from OriginAgent.session.manager import Session, SessionManager


# ── 辅助构造 ──────────────────────────────────────────────────────────

def _user_msg(idx: int) -> dict:
    """构造一条 user 消息，附带稳定 timestamp 便于排序。"""
    return {
        "role": "user",
        "content": f"user message {idx}",
        "timestamp": f"2026-07-07T13:{idx:02d}:00Z",
    }


def _assistant_msg(idx: int) -> dict:
    """构造一条 assistant 消息。"""
    return {
        "role": "assistant",
        "content": f"assistant response {idx}",
        "timestamp": f"2026-07-07T13:{idx:02d}:30Z",
    }


def _populate_turns(session: Session, turn_count: int) -> None:
    """向 session.messages 直接追加指定轮次的 user+assistant 消息。"""
    for i in range(turn_count):
        session.messages.append(_user_msg(i))
        session.messages.append(_assistant_msg(i))


def _make_checkpoint_raw(session_key: str, **overrides) -> dict:
    """构造一个最小可用的 continuity_checkpoint_v1 原始字典。"""
    base = {
        "session_key": session_key,
        "current_goal": "test goal",
        "current_plan": [],
        "open_loops": [],
        "active_constraints": [],
        "pending_confirmation_refs": [],
        "recent_turns_summary": [],
        "cold_indices": [],
        "updated_at": "2026-07-07T13:00:00Z",
    }
    base.update(overrides)
    return base


def _write_warm_summaries(
    workspace: Path,
    session_key: str,
    entries: list[dict],
) -> None:
    """向 workspace/warm_summaries.jsonl 追加写入冷区索引条目。"""
    summaries_path = workspace / "warm_summaries.jsonl"
    with summaries_path.open("a", encoding="utf-8") as f:
        for entry in entries:
            record = {"session_key": session_key, **entry}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── SubTask 9.3：热区恢复 ─────────────────────────────────────────────


def test_hot_zone_recovers_from_messages_tail(tmp_path: Path):
    """进程重启后热区从 session.messages 尾部取最近 50 轮。

    场景：session 有 53 轮对话，get_hot_history(max_turns=50) 应返回
    最后 50 轮（即第 4~53 轮），且边界对齐到 user 消息。
    """
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:hot-recovery")
    _populate_turns(session, turn_count=53)

    hot = session.get_hot_history(max_turns=50)

    # 50 轮 = 50 user + 50 assistant = 100 条消息
    assert len(hot) == 100
    # 第一条应是第 4 轮的 user 消息（索引从 0 开始，第 4 轮 = idx 3）
    assert hot[0]["role"] == "user"
    assert "3" in hot[0]["content"]
    # 最后一条应是第 53 轮的 assistant 消息
    assert hot[-1]["role"] == "assistant"
    assert "52" in hot[-1]["content"]


def test_hot_zone_returns_all_when_under_50_turns(tmp_path: Path):
    """不足 50 轮时热区返回全部消息。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:hot-short")
    _populate_turns(session, turn_count=10)

    hot = session.get_hot_history(max_turns=50)

    # 10 轮 = 20 条消息，全部返回
    assert len(hot) == 20


# ── SubTask 9.1：温区恢复 ─────────────────────────────────────────────


def test_warm_zone_recovers_from_session_metadata(tmp_path: Path):
    """温区从 session.metadata["warm_buffer"] 恢复。

    场景：WarmStore 追加 5 轮到温区后，_load_continuity_checkpoint 返回的
    warm_buffer 应包含正确的 turn_count 和 message_count。
    """
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:warm-recovery")
    session.metadata["continuity_checkpoint_v1"] = _make_checkpoint_raw(session.key)

    warm_store = WarmStore()
    for i in range(5):
        warm_store.append(session, _user_msg(i), [_assistant_msg(i)])

    checkpoint = AgentRuntime._load_continuity_checkpoint(session, workspace=tmp_path)

    assert checkpoint is not None
    warm_buffer = checkpoint["warm_buffer"]
    assert warm_buffer["turn_count"] == 5
    # 5 轮 × (1 user + 1 assistant) = 10 条消息
    assert warm_buffer["message_count"] == 10
    assert warm_buffer["updated_at"]  # 非空时间戳


def test_warm_zone_empty_when_no_warm_buffer(tmp_path: Path):
    """session 无 warm_buffer 时返回空温区状态。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:warm-empty")
    session.metadata["continuity_checkpoint_v1"] = _make_checkpoint_raw(session.key)

    checkpoint = AgentRuntime._load_continuity_checkpoint(session, workspace=tmp_path)

    assert checkpoint is not None
    assert checkpoint["warm_buffer"]["turn_count"] == 0
    assert checkpoint["warm_buffer"]["message_count"] == 0


# ── SubTask 9.2：冷区索引恢复 ─────────────────────────────────────────


def test_cold_indices_recover_from_warm_summaries_file(tmp_path: Path):
    """冷区索引从 warm_summaries.jsonl 读取最近 5 条。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:cold-recovery")
    session.metadata["continuity_checkpoint_v1"] = _make_checkpoint_raw(session.key)

    _write_warm_summaries(tmp_path, session.key, [
        {"turn_range": "1-50", "summary": "第一批总结", "key_entities": ["实体A"]},
        {"turn_range": "51-100", "summary": "第二批总结", "key_entities": ["实体B"]},
        {"turn_range": "101-150", "summary": "第三批总结", "key_entities": ["实体C"]},
    ])

    checkpoint = AgentRuntime._load_continuity_checkpoint(session, workspace=tmp_path)

    assert checkpoint is not None
    cold_indices = checkpoint["cold_indices"]
    assert len(cold_indices) == 3
    assert cold_indices[0]["turn_range"] == "1-50"
    assert cold_indices[2]["turn_range"] == "101-150"
    assert cold_indices[2]["summary"] == "第三批总结"
    assert "实体C" in cold_indices[2]["key_entities"]


def test_cold_indices_filtered_by_session_key(tmp_path: Path):
    """冷区索引按 session_key 过滤，不返回其他 session 的条目。"""
    sessions = SessionManager(tmp_path)
    session_a = sessions.get_or_create("cli:session-a")
    session_a.metadata["continuity_checkpoint_v1"] = _make_checkpoint_raw(session_a.key)

    # 写入两个不同 session 的索引
    _write_warm_summaries(tmp_path, "cli:session-a", [
        {"turn_range": "1-50", "summary": "A 的总结", "key_entities": []},
    ])
    _write_warm_summaries(tmp_path, "cli:session-b", [
        {"turn_range": "1-50", "summary": "B 的总结", "key_entities": []},
    ])

    checkpoint = AgentRuntime._load_continuity_checkpoint(session_a, workspace=tmp_path)

    assert checkpoint is not None
    cold_indices = checkpoint["cold_indices"]
    assert len(cold_indices) == 1
    assert cold_indices[0]["summary"] == "A 的总结"


def test_cold_indices_capped_at_five(tmp_path: Path):
    """冷区索引最多返回 5 条（最近 5 批温区总结）。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:cold-cap")
    session.metadata["continuity_checkpoint_v1"] = _make_checkpoint_raw(session.key)

    entries = [
        {"turn_range": f"{i*50+1}-{(i+1)*50}", "summary": f"总结{i}", "key_entities": []}
        for i in range(7)
    ]
    _write_warm_summaries(tmp_path, session.key, entries)

    checkpoint = AgentRuntime._load_continuity_checkpoint(session, workspace=tmp_path)

    assert checkpoint is not None
    cold_indices = checkpoint["cold_indices"]
    assert len(cold_indices) == 5
    # 应返回最后 5 条（索引 2~6）
    assert cold_indices[0]["turn_range"] == "101-150"
    assert cold_indices[-1]["turn_range"] == "301-350"


def test_cold_indices_fallback_to_saved_checkpoint(tmp_path: Path):
    """workspace 不可用时回退到 checkpoint 中已保存的冷区索引。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:cold-fallback")
    saved_indices = [
        {"turn_range": "1-50", "summary": "已保存的总结", "key_entities": ["旧实体"]},
    ]
    session.metadata["continuity_checkpoint_v1"] = _make_checkpoint_raw(
        session.key, cold_indices=saved_indices
    )

    # workspace=None 模拟不可用场景
    checkpoint = AgentRuntime._load_continuity_checkpoint(session, workspace=None)

    assert checkpoint is not None
    cold_indices = checkpoint["cold_indices"]
    assert len(cold_indices) == 1
    assert cold_indices[0]["summary"] == "已保存的总结"
    assert "旧实体" in cold_indices[0]["key_entities"]


# ── SubTask 9.4：三级状态完整重建 ─────────────────────────────────────


def test_full_tiered_state_rebuild(tmp_path: Path):
    """三级状态完整重建：热区 + 温区 + 冷区同时恢复。

    场景：session 有 53 轮热区消息、3 轮温区消息、2 条冷区索引，
    _load_continuity_checkpoint 应一次性返回全部三级状态。
    """
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:full-rebuild")
    session.metadata["continuity_checkpoint_v1"] = _make_checkpoint_raw(session.key)

    # 热区：53 轮直接写入 session.messages
    _populate_turns(session, turn_count=53)

    # 温区：3 轮通过 WarmStore 追加
    warm_store = WarmStore()
    for i in range(3):
        warm_store.append(session, _user_msg(100 + i), [_assistant_msg(100 + i)])

    # 冷区：2 条索引写入 warm_summaries.jsonl
    _write_warm_summaries(tmp_path, session.key, [
        {"turn_range": "1-50", "summary": "早期对话总结", "key_entities": ["项目A"]},
        {"turn_range": "51-100", "summary": "中期对话总结", "key_entities": ["项目B"]},
    ])

    # 执行三级重建
    hot_history = session.get_hot_history(max_turns=50)
    checkpoint = AgentRuntime._load_continuity_checkpoint(session, workspace=tmp_path)

    # ── 验证热区：最后 50 轮 ──
    assert len(hot_history) == 100  # 50 user + 50 assistant
    assert hot_history[0]["role"] == "user"

    # ── 验证温区：3 轮 ──
    assert checkpoint is not None
    assert checkpoint["warm_buffer"]["turn_count"] == 3
    assert checkpoint["warm_buffer"]["message_count"] == 6  # 3 × 2

    # ── 验证冷区：2 条索引 ──
    cold_indices = checkpoint["cold_indices"]
    assert len(cold_indices) == 2
    assert cold_indices[0]["turn_range"] == "1-50"
    assert cold_indices[1]["turn_range"] == "51-100"

    # ── 验证 checkpoint 基础字段也完整 ──
    assert checkpoint["session_key"] == "cli:full-rebuild"
    assert checkpoint["current_goal"] == "test goal"
    assert "updated_at" in checkpoint


def test_recovered_checkpoint_renders_in_context_block(tmp_path: Path):
    """三级重建的 checkpoint 能被 build_recovered_continuity_context 正确渲染。"""
    from OriginAgent.agent.context import ContextBuilder

    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:render-test")
    session.metadata["continuity_checkpoint_v1"] = _make_checkpoint_raw(session.key)

    warm_store = WarmStore()
    warm_store.append(session, _user_msg(0), [_assistant_msg(0)])

    _write_warm_summaries(tmp_path, session.key, [
        {"turn_range": "1-50", "summary": "渲染测试总结", "key_entities": ["渲染实体"]},
    ])

    checkpoint = AgentRuntime._load_continuity_checkpoint(session, workspace=tmp_path)
    assert checkpoint is not None

    block = ContextBuilder.build_recovered_continuity_context(checkpoint)

    # 温区状态在 JSON dump 中可见
    assert "warm_buffer" in block["text"]
    assert '"turn_count": 1' in block["text"]
    # 冷区索引在可读渲染段中可见
    assert "Cold Indices" in block["text"]
    assert "渲染测试总结" in block["text"]
    assert "渲染实体" in block["text"]
