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


# ---------------------------------------------------------------------------
# P1-b: attention_items conflict must not refresh updated_at
# ---------------------------------------------------------------------------


def test_attention_items_conflict_does_not_refresh_updated_at(tmp_path: Path):
    """When attention_items conflict triggers rollback, ``updated_at`` must
    NOT be refreshed.

    Regression: cron session had 8 stale attention_items that conflicted
    with every new turn's items. The conflict rollback preserved the
    stale items, but ``updated_at`` was refreshed anyway — defeating the
    30-minute field decay. The stale items persisted forever, producing
    the "Working memory attention_items conflict: keeping existing 8
    items" warning every turn.

    Fix: skip the ``updated_at`` refresh when a rollback occurred, so
    the decay timer keeps counting from the last *successful* write.
    """
    import time
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    manager = WorkingMemoryManager(sessions)

    # First write: establish baseline attention_items + updated_at
    manager.upsert(session, attention_items=["item-a", "item-b"])
    snapshot1 = manager.load(session)
    original_updated_at = snapshot1.updated_at
    assert original_updated_at, "baseline updated_at should be set"
    assert snapshot1.attention_items == ["item-a", "item-b"]

    # Sleep to ensure a different timestamp would be produced
    time.sleep(0.01)

    # Second write: conflicting items (no intersection)
    snapshot2 = manager.load(session)
    snapshot2.attention_items = ["item-x", "item-y"]  # no intersection
    result = manager.save(session, snapshot2)

    # Conflict rollback: existing items preserved
    assert result.attention_items == ["item-a", "item-b"]
    # KEY ASSERTION: updated_at must NOT be refreshed on conflict rollback
    assert result.updated_at == original_updated_at, (
        "updated_at must not refresh on conflict rollback — "
        "otherwise the 30-minute decay never fires and stale items persist forever. "
        f"original={original_updated_at}, result={result.updated_at}"
    )


def test_attention_items_no_conflict_refreshes_updated_at(tmp_path: Path):
    """When there's no conflict (new items intersect or existing is empty),
    ``updated_at`` IS refreshed normally.

    Ensures the fix doesn't accidentally suppress updated_at on legitimate writes.
    """
    import time
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    manager = WorkingMemoryManager(sessions)

    # First write
    manager.upsert(session, attention_items=["item-a", "item-b"])
    original_updated_at = manager.load(session).updated_at

    # Sleep to ensure a different timestamp
    time.sleep(0.01)

    # Second write: intersecting items (no conflict)
    snapshot = manager.load(session)
    snapshot.attention_items = ["item-a", "item-c"]  # "item-a" intersects
    result = manager.save(session, snapshot)

    # No conflict → updated_at refreshed
    assert result.updated_at != original_updated_at, (
        f"updated_at should be refreshed on non-conflict write. "
        f"original={original_updated_at}, result={result.updated_at}"
    )
    assert result.attention_items == ["item-a", "item-c"]


def test_attention_items_conflict_then_decay_clears_stale_items(tmp_path: Path):
    """End-to-end: after a conflict rollback, waiting 30+ minutes causes
    the stale items to decay on the next load.

    This is the user-visible behavior: "keeping existing 8 items" should
    stop after 30 minutes, not persist forever.
    """
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    manager = WorkingMemoryManager(sessions)

    # Write baseline items
    manager.upsert(session, attention_items=["stale-a", "stale-b"])
    original_updated_at = manager.load(session).updated_at

    # Simulate a conflict rollback (doesn't refresh updated_at)
    snapshot = manager.load(session)
    snapshot.attention_items = ["fresh-x"]  # no intersection
    manager.save(session, snapshot)

    # Verify updated_at was NOT refreshed
    assert manager.load(session).updated_at == original_updated_at

    # Simulate 31 minutes passing by manually setting updated_at
    future_time = datetime.now(timezone.utc) - timedelta(minutes=31)
    raw = session.metadata["working_memory_v1"]
    raw["updated_at"] = future_time.isoformat()
    session.metadata["working_memory_v1"] = raw
    # Invalidate cache so load re-reads
    manager._cache.pop(session.key, None)
    # Also invalidate the signature so load emits event
    manager._last_emitted_signature.pop(session.key, None)

    # Load should trigger decay and clear the stale items
    decayed = manager.load(session)
    assert decayed.attention_items == [], (
        "stale attention_items should be cleared by 30-minute decay"
    )


def test_repeated_conflict_does_not_keep_refreshing_updated_at(tmp_path: Path):
    """Multiple consecutive conflicts must not refresh updated_at each time.

    Regression: every turn, loop.py calls upsert with new attention_items,
    conflict triggers, updated_at was refreshed — so even though the items
    never changed, the decay timer kept resetting.
    """
    import time
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:test")
    manager = WorkingMemoryManager(sessions)

    # Baseline
    manager.upsert(session, attention_items=["item-a", "item-b"])
    original_updated_at = manager.load(session).updated_at

    # Simulate 3 consecutive conflicts (3 turns)
    for i in range(3):
        time.sleep(0.01)  # ensure different timestamps would be produced
        snapshot = manager.load(session)
        snapshot.attention_items = [f"conflicting-{i}"]  # no intersection
        manager.save(session, snapshot)

    # After 3 conflicts, updated_at should still be the original
    final = manager.load(session)
    assert final.updated_at == original_updated_at, (
        "updated_at must not refresh across multiple conflict rollbacks. "
        f"original={original_updated_at}, final={final.updated_at}"
    )
    assert final.attention_items == ["item-a", "item-b"]
