"""Tests for CronTool turn-scoped idempotency."""

from OriginAgent.agent.tools.cron import CronTool
from OriginAgent.cron.service import CronService
from OriginAgent.security.capabilities import CapabilitySnapshot


def _make_tool(tmp_path) -> CronTool:
    """构造一个带 user_turn capability 的 CronTool。"""
    service = CronService(tmp_path / "cron" / "jobs.json")
    tool = CronTool(service)
    tool.set_capability_snapshot(CapabilitySnapshot.user_turn())
    return tool


def test_same_turn_duplicate_add_returns_existing(tmp_path) -> None:
    """同一 turn 内两次调用 _add_job 且参数相同，第二次返回已存在提示且不创建新 job。"""
    tool = _make_tool(tmp_path)
    tool.set_context("telegram", "chat-1")

    first = tool._add_job(None, "say hi", 60, None, None, None)
    second = tool._add_job(None, "say hi", 60, None, None, None)

    assert first.startswith("Created job")
    assert "already created" in second
    assert "勿重复" in second
    assert len(tool._cron.list_jobs()) == 1


def test_different_params_creates_separate_jobs(tmp_path) -> None:
    """同一 turn 内两次调用 _add_job 但 message 不同，两次都创建新 job。"""
    tool = _make_tool(tmp_path)
    tool.set_context("telegram", "chat-1")

    first = tool._add_job(None, "say hi", 60, None, None, None)
    second = tool._add_job(None, "say bye", 60, None, None, None)

    assert first.startswith("Created job")
    assert second.startswith("Created job")
    assert len(tool._cron.list_jobs()) == 2


def test_different_turns_allow_duplicate(tmp_path) -> None:
    """重置 turn 幂等键后，相同参数的 _add_job 能创建新 job。"""
    tool = _make_tool(tmp_path)
    tool.set_context("telegram", "chat-1")

    first = tool._add_job(None, "say hi", 60, None, None, None)
    second = tool._add_job(None, "say hi", 60, None, None, None)

    assert first.startswith("Created job")
    assert "already created" in second
    assert len(tool._cron.list_jobs()) == 1

    # 模拟新 turn 开始：重置幂等键
    CronTool.reset_turn_idempotency()

    third = tool._add_job(None, "say hi", 60, None, None, None)
    assert third.startswith("Created job")
    assert len(tool._cron.list_jobs()) == 2
