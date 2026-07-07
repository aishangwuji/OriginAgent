"""Working memory 反自我强化（anti-confabulation）测试。

覆盖三类修复：
- Task 7: _hydrate_goal 的 started_at 过期检查
- Task 8: _hydrate_due_reminders 的 [reminder] 前缀标记
- Task 9: save 的 current_goal / attention_items 冲突验证
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from OriginAgent.agent.reminders import ReminderRecord, ReminderStore
from OriginAgent.agent.working_memory import WorkingMemoryManager, WorkingMemorySnapshot
from OriginAgent.session.manager import SessionManager


def _active_goal_state(*, started_at: str) -> dict:
    """构造一个 active 状态的 goal_state 元数据块。"""
    return {
        "status": "active",
        "objective": "Finish the report",
        "ui_summary": "report",
        "started_at": started_at,
    }


# ---------------------------------------------------------------------------
# Task 7: _hydrate_goal 过期检查
# ---------------------------------------------------------------------------


def test_hydrate_goal_not_expired(tmp_path: Path):
    """started_at 在 30 分钟内时，current_goal 和 priority_facts 应正常注入。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    started_at = datetime.now(timezone.utc).isoformat()
    session.metadata["goal_state"] = _active_goal_state(started_at=started_at)

    manager = WorkingMemoryManager(sessions)
    snapshot = manager.load(session)

    assert snapshot.current_goal == "Finish the report"
    assert "goal_summary: report" in snapshot.priority_facts


def test_hydrate_goal_expired(tmp_path: Path):
    """started_at 超过 30 分钟时，不应注入 current_goal 和 priority_facts。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    started_at = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    session.metadata["goal_state"] = _active_goal_state(started_at=started_at)

    manager = WorkingMemoryManager(sessions)
    snapshot = manager.load(session)

    assert snapshot.current_goal == ""
    assert "goal_summary: report" not in snapshot.priority_facts


# ---------------------------------------------------------------------------
# Task 8: _hydrate_due_reminders source 标记
# ---------------------------------------------------------------------------


def test_hydrate_due_reminders_with_prefix(tmp_path: Path):
    """reminder 注入的 attention_items 应带 [reminder] 前缀，与 LLM 内容区分。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    reminders = ReminderStore(tmp_path)
    reminders.upsert(
        ReminderRecord.create(
            session_key="cli:test",
            channel="cli",
            chat_id="test",
            content="Follow up on the current plan",
            due_at="2026-06-01T00:00:00+00:00",
        )
    )

    manager = WorkingMemoryManager(sessions, reminder_store=reminders)
    snapshot = manager.load(session)

    assert "[reminder] Follow up on the current plan" in snapshot.attention_items


# ---------------------------------------------------------------------------
# Task 9: save 冲突验证
# ---------------------------------------------------------------------------


def test_save_consistent_goal(tmp_path: Path):
    """新 current_goal 与现有值相同时，应正常保存。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    manager = WorkingMemoryManager(sessions)

    manager.upsert(session, current_goal="背期末题")
    snapshot = manager.load(session)
    snapshot.current_goal = "背期末题"

    result = manager.save(session, snapshot)

    assert result.current_goal == "背期末题"


def test_save_conflicting_goal_keeps_existing(tmp_path: Path):
    """现有 current_goal 非空且新值完全不同时，应保留现有值。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    manager = WorkingMemoryManager(sessions)

    manager.upsert(session, current_goal="背期末题")
    snapshot = manager.load(session)
    # 模拟 LLM confabulation：写入完全不同的 goal
    snapshot.current_goal = "玩电脑"

    result = manager.save(session, snapshot)

    assert result.current_goal == "背期末题"


def test_save_empty_existing_allows_new(tmp_path: Path):
    """现有 current_goal 为空时，新值应正常保存。"""
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    manager = WorkingMemoryManager(sessions)

    snapshot = WorkingMemorySnapshot(session_key="cli:test")
    snapshot.current_goal = "背期末题"

    result = manager.save(session, snapshot)

    assert result.current_goal == "背期末题"
