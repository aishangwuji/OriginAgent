"""Working memory 字段时间衰减（field decay）测试。

覆盖 _apply_field_decay 的行为：
- updated_at 超过 30 分钟时，易失真字段被清空
- updated_at 在 30 分钟内时，字段保留原值
- updated_at 缺失或格式错误时，容错降级不阻塞加载
- current_goal 不受时间衰减影响（由 _hydrate_goal 独立判断）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from OriginAgent.agent.working_memory import WorkingMemoryManager, WorkingMemorySnapshot
from OriginAgent.session.manager import SessionManager


# 衰减作用域内的字段：这些字段一旦写入即被每轮注入，LLM confabulate
# 的内容会形成自我强化的虚假记忆，因此超过 30 分钟需清空。
DECAYED_FIELDS = (
    "attention_items",
    "open_loops",
    "active_constraints",
    "pending_questions",
    "tool_residue",
)


def _snapshot_raw(*, updated_at: str, current_goal: str = "remember-this-goal") -> dict:
    """构造一个包含全部字段非空值的 working_memory_v1 元数据块。"""
    return {
        "session_key": "cli:test",
        "scope": "session",
        "owner_id": "user-1",
        "current_goal": current_goal,
        "current_plan": ["step one"],
        "open_loops": ["loop-a", "loop-b"],
        "active_constraints": ["constraint-a"],
        "pending_questions": ["question-a"],
        "priority_facts": ["fact-stable"],
        "attention_items": ["attention-a", "attention-b"],
        "tool_residue": ["residue-a"],
        "updated_at": updated_at,
        "expires_at": None,
    }


# ---------------------------------------------------------------------------
# 用例 1：updated_at 超过 30 分钟 → 衰减字段清空
# ---------------------------------------------------------------------------


def test_load_decays_fields_when_updated_at_older_than_30min(tmp_path: Path):
    """updated_at 距当前时间超过 30 分钟时，衰减作用域字段应清空为 []。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    stale_at = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    session.metadata["working_memory_v1"] = _snapshot_raw(updated_at=stale_at)

    manager = WorkingMemoryManager(sessions)
    snapshot = manager.load(session)

    for field in DECAYED_FIELDS:
        assert getattr(snapshot, field) == [], f"字段 {field} 应被衰减清空"


# ---------------------------------------------------------------------------
# 用例 2：updated_at 在 30 分钟内 → 字段保留原值
# ---------------------------------------------------------------------------


def test_load_preserves_fields_when_updated_at_within_30min(tmp_path: Path):
    """updated_at 距当前时间在 30 分钟内时，衰减作用域字段应保留原值。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    fresh_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    session.metadata["working_memory_v1"] = _snapshot_raw(updated_at=fresh_at)

    manager = WorkingMemoryManager(sessions)
    snapshot = manager.load(session)

    assert snapshot.attention_items == ["attention-a", "attention-b"]
    assert snapshot.open_loops == ["loop-a", "loop-b"]
    assert snapshot.active_constraints == ["constraint-a"]
    assert snapshot.pending_questions == ["question-a"]
    assert snapshot.tool_residue == ["residue-a"]


# ---------------------------------------------------------------------------
# 用例 3：updated_at 为空字符串 → 容错降级，保留所有字段
# ---------------------------------------------------------------------------


def test_load_preserves_fields_when_updated_at_empty(tmp_path: Path):
    """updated_at 为空字符串时不阻塞加载，衰减字段保留原值（容错降级）。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    session.metadata["working_memory_v1"] = _snapshot_raw(updated_at="")

    manager = WorkingMemoryManager(sessions)
    snapshot = manager.load(session)

    assert snapshot.attention_items == ["attention-a", "attention-b"]
    assert snapshot.open_loops == ["loop-a", "loop-b"]
    assert snapshot.active_constraints == ["constraint-a"]
    assert snapshot.pending_questions == ["question-a"]
    assert snapshot.tool_residue == ["residue-a"]


# ---------------------------------------------------------------------------
# 用例 4：updated_at 格式错误 → 容错降级，保留所有字段
# ---------------------------------------------------------------------------


def test_load_preserves_fields_when_updated_at_malformed(tmp_path: Path):
    """updated_at 格式错误（如 'invalid-date'）时不阻塞加载，保留原值（容错降级）。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    session.metadata["working_memory_v1"] = _snapshot_raw(updated_at="invalid-date")

    manager = WorkingMemoryManager(sessions)
    snapshot = manager.load(session)

    assert snapshot.attention_items == ["attention-a", "attention-b"]
    assert snapshot.open_loops == ["loop-a", "loop-b"]
    assert snapshot.active_constraints == ["constraint-a"]
    assert snapshot.pending_questions == ["question-a"]
    assert snapshot.tool_residue == ["residue-a"]


# ---------------------------------------------------------------------------
# 用例 5：current_goal 不受时间衰减影响
# ---------------------------------------------------------------------------


def test_load_preserves_current_goal_regardless_of_decay(tmp_path: Path):
    """即使 snapshot 超过 30 分钟，current_goal 仍保留原值（由 _hydrate_goal 独立判断）。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    stale_at = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    # 注意：不设置 goal_state，避免 _hydrate_goal 干扰断言
    session.metadata["working_memory_v1"] = _snapshot_raw(
        updated_at=stale_at,
        current_goal="persistent-goal",
    )

    manager = WorkingMemoryManager(sessions)
    snapshot = manager.load(session)

    # current_goal 不在衰减作用域内，即便其他字段被清空也应保留
    assert snapshot.current_goal == "persistent-goal"
    # 顺带验证衰减确实发生了（否则该用例无区分度）
    assert snapshot.attention_items == []


# ---------------------------------------------------------------------------
# 工作记忆加载/保存事件日志（Task 7）
# ---------------------------------------------------------------------------


def _find_log_call(mock_log_event, event_name):
    """从 mock_log_event.call_args_list 中找出指定事件名的首次调用。"""
    for call in mock_log_event.call_args_list:
        if call.args and call.args[0] == event_name:
            return call
    return None


def test_load_logs_field_counts(tmp_path: Path):
    """working_memory.loaded 事件应包含 has_goal / open_loops_count /
    attention_items_count / priority_facts_count。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    fresh_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    session.metadata["working_memory_v1"] = {
        "session_key": "cli:test",
        "scope": "session",
        "owner_id": "user-1",
        "current_goal": "active-goal",
        "current_plan": [],
        "open_loops": ["loop-1", "loop-2", "loop-3"],
        "active_constraints": [],
        "pending_questions": [],
        "priority_facts": ["fact-1", "fact-2", "fact-3", "fact-4", "fact-5"],
        "attention_items": ["att-1", "att-2"],
        "tool_residue": [],
        "updated_at": fresh_at,
        "expires_at": None,
    }

    manager = WorkingMemoryManager(sessions)

    with patch("OriginAgent.agent.working_memory.log_event") as mock_log_event:
        manager.load(session)

    loaded_call = _find_log_call(mock_log_event, "working_memory.loaded")
    assert loaded_call is not None, "working_memory.loaded 未被记录"
    kwargs = loaded_call.kwargs

    assert kwargs["has_goal"] is True
    assert kwargs["open_loops_count"] == 3
    assert kwargs["attention_items_count"] == 2
    assert kwargs["priority_facts_count"] == 5


def test_save_logs_change_summary(tmp_path: Path):
    """working_memory.saved 事件应包含 session_key / goal_changed / attention_items_count。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")

    snapshot = WorkingMemorySnapshot(
        session_key="cli:test",
        owner_id="user-1",
        current_goal="new-goal",
        attention_items=["att-1", "att-2"],
    )

    manager = WorkingMemoryManager(sessions)

    with patch("OriginAgent.agent.working_memory.log_event") as mock_log_event:
        manager.save(session, snapshot)

    saved_call = _find_log_call(mock_log_event, "working_memory.saved")
    assert saved_call is not None, "working_memory.saved 未被记录"
    kwargs = saved_call.kwargs

    assert kwargs["session_key"] == "cli:test"
    assert "goal_changed" in kwargs
    assert kwargs["attention_items_count"] == 2


# ---------------------------------------------------------------------------
# turn-scoped cache（Task 2）：连续 load 复用同一 snapshot
# ---------------------------------------------------------------------------


def test_load_caches_snapshot_per_session(tmp_path: Path):
    """同一 session 的连续 load 调用应复用同一 snapshot。

    缓存命中时应跳过反序列化后的副作用（_apply_field_decay / _hydrate_goal /
    _hydrate_due_reminders），避免每轮 8 次 load 造成的重复日志噪音。
    """
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    fresh_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    session.metadata["working_memory_v1"] = _snapshot_raw(updated_at=fresh_at)

    manager = WorkingMemoryManager(sessions)

    # _hydrate_goal 是 @staticmethod，通过 patch.object 在类上替换为 MagicMock
    # 验证缓存命中时不会再次触发 hydration 副作用。
    with patch.object(WorkingMemoryManager, "_hydrate_goal") as mock_hydrate:
        first = manager.load(session)
        second = manager.load(session)
        third = manager.load(session)

    # 仅首次 load（缓存未命中）触发 hydration，后续两次命中缓存跳过
    assert mock_hydrate.call_count == 1, (
        f"_hydrate_goal 应只被调用一次，实际 {mock_hydrate.call_count} 次"
    )
    # 三次返回的是同一对象（缓存复用，而非每次新建）
    assert second is first, "缓存命中时应返回同一对象"
    assert third is first, "缓存命中时应返回同一对象"


def test_save_invalidates_cache(tmp_path: Path):
    """save 后再次 load 应返回新的 snapshot，而非缓存的旧值。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    # 不预设 working_memory_v1：避免触发 save 的冲突保护逻辑（冲突保护是
    # 独立特性，本测试只验证缓存失效）。从空状态开始，save 不存在 existing
    # 用于比对，不会发生 current_goal / attention_items 冲突覆盖。

    manager = WorkingMemoryManager(sessions)
    # 首次 load：metadata 无 working_memory_v1 → from_json(None) 返回空 snapshot
    cached = manager.load(session)
    assert cached.current_goal == ""
    assert cached.attention_items == []

    new_snapshot = WorkingMemorySnapshot(
        session_key=session.key,
        owner_id="user-1",
        current_goal="new-goal",
        attention_items=["new-att"],
    )
    manager.save(session, new_snapshot)

    reloaded = manager.load(session)
    # 缓存已被 save 更新，不应返回旧缓存对象
    assert reloaded is not cached, "save 后 load 不应返回旧缓存对象"
    # 应返回 new_snapshot 本身（save 已将其写入缓存）
    assert reloaded is new_snapshot, "save 后 load 应返回新写入缓存的 snapshot"
    assert reloaded.current_goal == "new-goal"
    assert reloaded.attention_items == ["new-att"]


def test_clear_invalidates_cache(tmp_path: Path):
    """clear 后再次 load 应返回空 snapshot，而非缓存的旧值。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    fresh_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    session.metadata["working_memory_v1"] = _snapshot_raw(updated_at=fresh_at)

    manager = WorkingMemoryManager(sessions)
    cached = manager.load(session)
    assert cached.attention_items == ["attention-a", "attention-b"]
    assert cached.open_loops == ["loop-a", "loop-b"]

    manager.clear(session)

    reloaded = manager.load(session)
    # 缓存已被 clear 清除，不应返回旧缓存对象
    assert reloaded is not cached, "clear 后 load 不应返回旧缓存对象"
    # 元数据被 pop 后 from_json(None) 返回空 snapshot
    assert reloaded.current_goal == ""
    assert reloaded.open_loops == []
    assert reloaded.active_constraints == []
    assert reloaded.pending_questions == []
    assert reloaded.attention_items == []
    assert reloaded.tool_residue == []


# ---------------------------------------------------------------------------
# 状态签名去重（Task 3）：跨 turn 状态未变时抑制 working_memory.loaded/saved
# ---------------------------------------------------------------------------


def _attach_loguru_to_caplog(caplog, level: str = "DEBUG"):
    """将 loguru 输出桥接到 pytest caplog，返回 handler_id 供 finally 移除。

    loguru 不走 stdlib logging，caplog 默认收不到 loguru 的 debug 记录。
    复用 test_cursor_recovery.py 中验证过的桥接模式。
    """
    from loguru import logger as loguru_logger

    return loguru_logger.add(caplog.handler, format="{message}", level=level)


def test_loaded_event_deduped_on_identical_state(tmp_path: Path, caplog):
    """跨 turn 缓存失效但状态相同时，working_memory.loaded 应被抑制为 debug 日志。

    场景：turn 1 load + save（写入 session.metadata），turn 2 开始时缓存失效
    （例如新 manager 实例或缓存被清），load 重新从 session.metadata 反序列化
    得到内容相同的 snapshot。此时 log_event 不应再次输出，仅记录 debug 级别
    的 suppressed 信息。
    """
    import logging

    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    fresh_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    session.metadata["working_memory_v1"] = _snapshot_raw(updated_at=fresh_at)

    manager = WorkingMemoryManager(sessions)

    handler_id = _attach_loguru_to_caplog(caplog, level="DEBUG")
    try:
        with caplog.at_level(logging.DEBUG):
            with patch("OriginAgent.agent.working_memory.log_event") as mock_log:
                # 第一次 load：缓存未命中，触发 log_event
                manager.load(session)
                # 模拟缓存失效（如新 manager 实例 / 跨 turn），
                # 但 session.metadata 仍保留相同状态
                manager._cache.clear()
                # 第二次 load：缓存未命中，重新反序列化得到相同 snapshot
                manager.load(session)

                loaded_calls = [
                    c for c in mock_log.call_args_list
                    if c.args and c.args[0] == "working_memory.loaded"
                ]
                assert len(loaded_calls) == 1, (
                    "状态相同时 working_memory.loaded 应只触发一次，"
                    f"实际 {len(loaded_calls)} 次"
                )

            # 第二次 load 应输出 debug 级别的 suppressed 日志
            suppressed_records = [
                r for r in caplog.records
                if "suppressed" in r.getMessage().lower()
                or "deduped" in r.getMessage().lower()
            ]
            assert suppressed_records, (
                "expected a debug-level suppression log record; got: "
                f"{[r.getMessage() for r in caplog.records]}"
            )
    finally:
        from loguru import logger as loguru_logger

        loguru_logger.remove(handler_id)


def test_loaded_event_emitted_on_state_change(tmp_path: Path):
    """状态变化（current_goal 不同）时，两次 load 都应触发 working_memory.loaded。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    fresh_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    session.metadata["working_memory_v1"] = _snapshot_raw(
        updated_at=fresh_at,
        current_goal="goal-A",
    )

    manager = WorkingMemoryManager(sessions)

    with patch("OriginAgent.agent.working_memory.log_event") as mock_log:
        # 第一次 load：状态 A
        manager.load(session)

        # 修改 session.metadata 为状态 B（current_goal 不同）
        session.metadata["working_memory_v1"] = _snapshot_raw(
            updated_at=fresh_at,
            current_goal="goal-B-different",
        )
        # 清空缓存，强制下一次 load 重新反序列化
        manager._cache.clear()

        # 第二次 load：状态 B
        manager.load(session)

        loaded_calls = [
            c for c in mock_log.call_args_list
            if c.args and c.args[0] == "working_memory.loaded"
        ]
        assert len(loaded_calls) == 2, (
            "状态变化时 working_memory.loaded 应触发两次，"
            f"实际 {len(loaded_calls)} 次"
        )


def test_saved_event_deduped_on_no_change(tmp_path: Path, caplog):
    """同一 snapshot 连续 save 两次（签名相同），working_memory.saved 只触发一次。"""
    import logging

    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")

    snapshot = WorkingMemorySnapshot(
        session_key=session.key,
        owner_id="user-1",
        current_goal="stable-goal",
        attention_items=["att-1"],
        open_loops=["loop-1"],
        priority_facts=["fact-1"],
    )

    manager = WorkingMemoryManager(sessions)

    handler_id = _attach_loguru_to_caplog(caplog, level="DEBUG")
    try:
        with caplog.at_level(logging.DEBUG):
            with patch("OriginAgent.agent.working_memory.log_event") as mock_log:
                # 第一次 save：触发 log_event
                manager.save(session, snapshot)
                # 第二次 save：同一对象，签名相同，应抑制
                manager.save(session, snapshot)

                saved_calls = [
                    c for c in mock_log.call_args_list
                    if c.args and c.args[0] == "working_memory.saved"
                ]
                assert len(saved_calls) == 1, (
                    "状态相同时 working_memory.saved 应只触发一次，"
                    f"实际 {len(saved_calls)} 次"
                )

            # 第二次 save 应输出 debug 级别的 suppressed 日志
            suppressed_records = [
                r for r in caplog.records
                if "suppressed" in r.getMessage().lower()
                or "deduped" in r.getMessage().lower()
            ]
            assert suppressed_records, (
                "expected a debug-level suppression log record; got: "
                f"{[r.getMessage() for r in caplog.records]}"
            )
    finally:
        from loguru import logger as loguru_logger

        loguru_logger.remove(handler_id)
