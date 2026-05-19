import pytest

from OpenHome.agent.tools.long_task import CompleteGoalTool, LongTaskTool
from OpenHome.bus.queue import MessageBus
from OpenHome.session.goal_state import GOAL_STATE_KEY
from OpenHome.session.manager import SessionManager


def _tools(tmp_path):
    bus = MessageBus()
    sessions = SessionManager(tmp_path)
    long_task = LongTaskTool(sessions=sessions, bus=bus)
    complete = CompleteGoalTool(sessions=sessions, bus=bus)
    for tool in (long_task, complete):
        tool.set_context("websocket", "chat-1", session_key="websocket:chat-1")
    return sessions, bus, long_task, complete


@pytest.mark.asyncio
async def test_long_task_records_goal_metadata_and_publishes_ws(tmp_path):
    sessions, bus, long_task, _ = _tools(tmp_path)

    result = await long_task.execute(goal="Finish docs", ui_summary="Docs")

    assert "Goal recorded" in result
    session = sessions.get_or_create("websocket:chat-1")
    assert session.metadata[GOAL_STATE_KEY]["status"] == "active"
    assert session.metadata[GOAL_STATE_KEY]["objective"] == "Finish docs"

    outbound = await bus.consume_outbound()
    assert outbound.metadata["_goal_state_sync"] is True
    assert outbound.metadata["goal_state"]["active"] is True
    assert outbound.metadata["goal_state"]["ui_summary"] == "Docs"


@pytest.mark.asyncio
async def test_complete_goal_closes_active_goal(tmp_path):
    sessions, bus, long_task, complete = _tools(tmp_path)
    await long_task.execute(goal="Finish docs")
    await bus.consume_outbound()

    result = await complete.execute(recap="Done")

    assert "Goal marked complete" in result
    session = sessions.get_or_create("websocket:chat-1")
    assert session.metadata[GOAL_STATE_KEY]["status"] == "completed"
    assert session.metadata[GOAL_STATE_KEY]["recap"] == "Done"

    outbound = await bus.consume_outbound()
    assert outbound.metadata["goal_state"] == {"active": False}


@pytest.mark.asyncio
async def test_long_task_rejects_second_active_goal(tmp_path):
    _, _, long_task, _ = _tools(tmp_path)

    await long_task.execute(goal="First")
    result = await long_task.execute(goal="Second")

    assert "already active" in result

