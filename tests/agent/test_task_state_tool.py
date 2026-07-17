"""Tests for TaskStateTool — Agent-managed task state machine.

The state machine tracks the action-result causal chain:
    EXPLORE → BLOCKED → WAITING → {RECOVERED|ABANDONED} → SATISFIED

Design:
- State is persisted to ``session.metadata["_task_state"]`` (not priority_facts)
  to avoid polluting MemoryGovernance promotion and ActionAutomation evidence_refs.
- Tool only needs SessionManager (same pattern as CloseEpisodeTool).
- Context rendering is handled separately (Phase 3 injects into recovered_continuity).

This is the BDI/ACT-R/Soar/EPIC concrete embodiment:
- Belief: current task state
- Desire: SATISFIED (goal)
- Intention: next action chosen based on current state
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.task_state import TaskStateTool
from OriginAgent.session.manager import Session, SessionManager


def _make_sessions_with_session(session_key: str) -> tuple[MagicMock, Session]:
    """Return a MagicMock SessionManager wired to a real Session."""
    session = Session(key=session_key)
    sessions = MagicMock(spec=SessionManager)
    sessions.get_or_create.return_value = session
    return sessions, session


def _make_tool(session_key: str = "cron:job-1") -> tuple[TaskStateTool, Session]:
    sessions, session = _make_sessions_with_session(session_key)
    tool = TaskStateTool(sessions)
    tool._session_key = session_key
    return tool, session


# ─── State machine: initial state ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_initial_state_is_explore() -> None:
    """New session starts in EXPLORE state."""
    tool, session = _make_tool()
    result = await tool.execute(action="get")
    data = json.loads(result)
    assert data["current_state"] == "EXPLORE"
    assert data["previous_state"] is None
    assert data["history"] == []


@pytest.mark.asyncio
async def test_get_creates_task_state_if_absent() -> None:
    """Getting state when _task_state is absent initializes it to EXPLORE."""
    tool, session = _make_tool()
    assert "_task_state" not in session.metadata
    await tool.execute(action="get")
    ts = session.metadata.get("_task_state")
    assert ts is not None
    assert ts["current_state"] == "EXPLORE"


# ─── State machine: valid transitions ──────────────────────────────────────


@pytest.mark.asyncio
async def test_transition_explore_to_blocked() -> None:
    """EXPLORE → BLOCKED is valid."""
    tool, _ = _make_tool()
    result = await tool.execute(
        action="transition", new_state="BLOCKED",
        reason="exec denied by capability_snapshot_required",
    )
    data = json.loads(result)
    assert data["current_state"] == "BLOCKED"
    assert data["previous_state"] == "EXPLORE"
    assert "exec denied" in data["reason"]


@pytest.mark.asyncio
async def test_transition_blocked_to_recovered() -> None:
    """BLOCKED → RECOVERED is valid."""
    tool, _ = _make_tool()
    await tool.execute(action="transition", new_state="BLOCKED", reason="hit barrier")
    result = await tool.execute(
        action="transition", new_state="RECOVERED", reason="found workaround",
    )
    data = json.loads(result)
    assert data["current_state"] == "RECOVERED"
    assert data["previous_state"] == "BLOCKED"


@pytest.mark.asyncio
async def test_transition_explore_to_satisfied() -> None:
    """EXPLORE → SATISFIED is valid (direct completion)."""
    tool, _ = _make_tool()
    result = await tool.execute(action="transition", new_state="SATISFIED", reason="done")
    data = json.loads(result)
    assert data["current_state"] == "SATISFIED"


@pytest.mark.asyncio
async def test_transition_terminal_to_explore_starts_new_task() -> None:
    """SATISFIED → EXPLORE starts a new task (terminal states allow EXPLORE)."""
    tool, _ = _make_tool()
    await tool.execute(action="transition", new_state="SATISFIED", reason="done")
    result = await tool.execute(action="transition", new_state="EXPLORE", reason="new task")
    data = json.loads(result)
    assert data["current_state"] == "EXPLORE"
    assert data["previous_state"] == "SATISFIED"


@pytest.mark.asyncio
async def test_transition_abandoned_to_explore_starts_new_task() -> None:
    """ABANDONED → EXPLORE starts a new task."""
    tool, _ = _make_tool()
    await tool.execute(action="transition", new_state="ABANDONED", reason="gave up")
    result = await tool.execute(action="transition", new_state="EXPLORE", reason="trying again")
    data = json.loads(result)
    assert data["current_state"] == "EXPLORE"


# ─── State machine: invalid transitions ────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_transition_returns_error() -> None:
    """EXPLORE → RECOVERED is invalid (must go through BLOCKED first)."""
    tool, _ = _make_tool()
    result = await tool.execute(
        action="transition", new_state="RECOVERED", reason="shortcut",
    )
    assert "invalid" in result.lower() or "error" in result.lower()
    assert "EXPLORE" in result
    assert "RECOVERED" in result
    # State should NOT change
    ts = tool._get_task_state()
    assert ts["current_state"] == "EXPLORE"


@pytest.mark.asyncio
async def test_invalid_transition_blocked_to_satisfied() -> None:
    """BLOCKED → SATISFIED is invalid (must recover first)."""
    tool, _ = _make_tool()
    await tool.execute(action="transition", new_state="BLOCKED", reason="stuck")
    result = await tool.execute(action="transition", new_state="SATISFIED", reason="shortcut")
    assert "invalid" in result.lower() or "error" in result.lower()
    ts = tool._get_task_state()
    assert ts["current_state"] == "BLOCKED"


# ─── State machine: history tracking ───────────────────────────────────────


@pytest.mark.asyncio
async def test_history_accumulates_transitions() -> None:
    """History records each transition with timestamp and reason."""
    tool, _ = _make_tool()
    await tool.execute(action="transition", new_state="BLOCKED", reason="barrier")
    await tool.execute(action="transition", new_state="RECOVERED", reason="workaround")
    result = await tool.execute(action="transition", new_state="SATISFIED", reason="done")

    data = json.loads(result)
    history = data["history"]
    assert len(history) == 3
    assert history[0]["state"] == "BLOCKED"
    assert history[1]["state"] == "RECOVERED"
    assert history[2]["state"] == "SATISFIED"
    # Each entry has a timestamp
    for entry in history:
        assert "ts" in entry
        assert "reason" in entry


@pytest.mark.asyncio
async def test_history_capped_at_max_entries() -> None:
    """History doesn't grow unbounded — capped at _MAX_HISTORY (20)."""
    tool, _ = _make_tool()
    # Do many transitions: EXPLORE → BLOCKED → RECOVERED → EXPLORE → ...
    for i in range(25):
        await tool.execute(action="transition", new_state="BLOCKED", reason=f"block {i}")
        await tool.execute(action="transition", new_state="RECOVERED", reason=f"recover {i}")
        await tool.execute(action="transition", new_state="EXPLORE", reason=f"resume {i}")

    ts = tool._get_task_state()
    assert len(ts["history"]) <= 20


# ─── State machine: idempotency ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transition_to_same_state_is_idempotent() -> None:
    """Transitioning to the same state is a no-op (returns current state)."""
    tool, _ = _make_tool()
    await tool.execute(action="transition", new_state="BLOCKED", reason="first")
    result = await tool.execute(action="transition", new_state="BLOCKED", reason="again")

    data = json.loads(result)
    assert data["current_state"] == "BLOCKED"
    # History should NOT have a duplicate entry
    history = data["history"]
    block_entries = [h for h in history if h["state"] == "BLOCKED"]
    assert len(block_entries) == 1


# ─── State machine: persistence ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_state_persists_in_session_metadata() -> None:
    """Task state is persisted to session.metadata['_task_state']."""
    tool, session = _make_tool()
    await tool.execute(action="transition", new_state="BLOCKED", reason="test")
    ts = session.metadata.get("_task_state")
    assert ts is not None
    assert ts["current_state"] == "BLOCKED"


@pytest.mark.asyncio
async def test_state_survives_across_tool_instances() -> None:
    """State persists because it lives in session.metadata, not the tool."""
    sessions, session = _make_sessions_with_session("cron:job-1")
    tool1 = TaskStateTool(sessions)
    tool1._session_key = "cron:job-1"
    await tool1.execute(action="transition", new_state="BLOCKED", reason="first instance")

    # New tool instance, same session
    tool2 = TaskStateTool(sessions)
    tool2._session_key = "cron:job-1"
    result = await tool2.execute(action="get")
    data = json.loads(result)
    assert data["current_state"] == "BLOCKED"


# ─── State machine: reset ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_clears_state() -> None:
    """Reset clears task state back to initial EXPLORE."""
    tool, session = _make_tool()
    await tool.execute(action="transition", new_state="BLOCKED", reason="test")
    result = await tool.execute(action="reset")
    assert "reset" in result.lower() or "cleared" in result.lower()
    ts = session.metadata.get("_task_state")
    assert ts["current_state"] == "EXPLORE"
    assert ts["history"] == []


# ─── State machine: no sessions ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_sessions_no_crash() -> None:
    """When sessions is None, tool returns error without crashing."""
    tool = TaskStateTool(None)
    tool._session_key = None
    result = await tool.execute(action="get")
    assert "error" in result.lower() or "unavailable" in result.lower()


# ─── Tool properties ───────────────────────────────────────────────────────


def test_tool_name() -> None:
    tool, _ = _make_tool()
    assert tool.name == "task_state"


def test_tool_once_per_turn_is_false() -> None:
    """task_state can be called multiple times per turn (state may evolve)."""
    tool, _ = _make_tool()
    assert tool.once_per_turn is False


def test_tool_description_mentions_states() -> None:
    """Description lists all valid states so the LLM knows the vocabulary."""
    tool, _ = _make_tool()
    desc = tool.description
    for state in ("EXPLORE", "BLOCKED", "WAITING", "RECOVERED", "ABANDONED", "SATISFIED"):
        assert state in desc


def test_tool_parameters_have_required_fields() -> None:
    """Parameters include action, new_state, reason."""
    tool, _ = _make_tool()
    params = tool.parameters
    props = params.get("properties", {})
    assert "action" in props
    assert "new_state" in props
    assert "reason" in props
    assert "action" in params.get("required", [])


# ─── State machine: WAITING transitions ────────────────────────────────────


@pytest.mark.asyncio
async def test_transition_explore_to_waiting() -> None:
    """EXPLORE → WAITING is valid (Agent needs external input)."""
    tool, _ = _make_tool()
    result = await tool.execute(action="transition", new_state="WAITING", reason="need user input")
    data = json.loads(result)
    assert data["current_state"] == "WAITING"


@pytest.mark.asyncio
async def test_transition_waiting_to_explore() -> None:
    """WAITING → EXPLORE is valid (received input, resume)."""
    tool, _ = _make_tool()
    await tool.execute(action="transition", new_state="WAITING", reason="waiting")
    result = await tool.execute(action="transition", new_state="EXPLORE", reason="got input")
    data = json.loads(result)
    assert data["current_state"] == "EXPLORE"


@pytest.mark.asyncio
async def test_transition_waiting_to_abandoned() -> None:
    """WAITING → ABANDONED is valid (timed out)."""
    tool, _ = _make_tool()
    await tool.execute(action="transition", new_state="WAITING", reason="waiting")
    result = await tool.execute(action="transition", new_state="ABANDONED", reason="timed out")
    data = json.loads(result)
    assert data["current_state"] == "ABANDONED"


# ─── Causal chain: action_id / evaluation_id association ───────────────────
#
# These tests verify that task_state transitions can reference the action
# (from action_trace) and evaluation (from evaluate_action) that triggered
# them, forming the complete causal chain:
#   action → result → evaluation → state transition
# Each link has a unique ID, making the full chain traceable.


@pytest.mark.asyncio
async def test_transition_with_action_id() -> None:
    """Transition can reference the action_id that triggered it."""
    tool, _ = _make_tool()
    result = await tool.execute(
        action="transition", new_state="BLOCKED", reason="exec denied",
        action_id="call_abc123",
    )
    data = json.loads(result)
    assert data["current_state"] == "BLOCKED"
    # The latest history entry should include the action_id
    history = data["history"]
    assert history[-1]["action_id"] == "call_abc123"


@pytest.mark.asyncio
async def test_transition_with_evaluation_id() -> None:
    """Transition can reference the evaluation_id that justified it."""
    tool, _ = _make_tool()
    result = await tool.execute(
        action="transition", new_state="SATISFIED", reason="goal achieved",
        evaluation_id="eval_def67890",
    )
    data = json.loads(result)
    history = data["history"]
    assert history[-1]["evaluation_id"] == "eval_def67890"


@pytest.mark.asyncio
async def test_transition_with_both_ids() -> None:
    """Transition can reference both action_id and evaluation_id."""
    tool, _ = _make_tool()
    result = await tool.execute(
        action="transition", new_state="BLOCKED", reason="tool denied",
        action_id="call_xyz",
        evaluation_id="eval_abc",
    )
    data = json.loads(result)
    entry = data["history"][-1]
    assert entry["action_id"] == "call_xyz"
    assert entry["evaluation_id"] == "eval_abc"


@pytest.mark.asyncio
async def test_transition_without_ids_backward_compatible() -> None:
    """Transitions without action_id/evaluation_id still work (backward compat)."""
    tool, _ = _make_tool()
    result = await tool.execute(
        action="transition", new_state="BLOCKED", reason="no ids",
    )
    data = json.loads(result)
    entry = data["history"][-1]
    # Fields should be absent or None when not provided (backward compat)
    assert entry.get("action_id") is None or "action_id" not in entry
    assert entry.get("evaluation_id") is None or "evaluation_id" not in entry


@pytest.mark.asyncio
async def test_history_accumulates_ids_across_transitions() -> None:
    """Multiple transitions each carry their own action_id/evaluation_id."""
    tool, _ = _make_tool()
    await tool.execute(
        action="transition", new_state="BLOCKED", reason="first",
        action_id="call_1", evaluation_id="eval_1",
    )
    await tool.execute(
        action="transition", new_state="RECOVERED", reason="second",
        action_id="call_2", evaluation_id="eval_2",
    )
    result = await tool.execute(
        action="transition", new_state="SATISFIED", reason="done",
        action_id="call_3", evaluation_id="eval_3",
    )
    data = json.loads(result)
    history = data["history"]
    assert len(history) == 3
    assert history[0]["action_id"] == "call_1"
    assert history[1]["action_id"] == "call_2"
    assert history[2]["action_id"] == "call_3"
    assert history[0]["evaluation_id"] == "eval_1"
    assert history[2]["evaluation_id"] == "eval_3"


@pytest.mark.asyncio
async def test_same_state_transition_with_id_is_idempotent() -> None:
    """Same-state transition with action_id is still a no-op (no history entry)."""
    tool, _ = _make_tool()
    await tool.execute(
        action="transition", new_state="BLOCKED", reason="first",
        action_id="call_1",
    )
    # Second call to same state — should be idempotent
    result = await tool.execute(
        action="transition", new_state="BLOCKED", reason="again",
        action_id="call_2",
    )
    data = json.loads(result)
    block_entries = [h for h in data["history"] if h["state"] == "BLOCKED"]
    assert len(block_entries) == 1  # No duplicate entry
    assert block_entries[0]["action_id"] == "call_1"  # Original retained


def test_parameters_include_action_id_and_evaluation_id() -> None:
    """Tool parameters declare action_id and evaluation_id fields."""
    tool, _ = _make_tool()
    params = tool.parameters
    props = params.get("properties", {})
    assert "action_id" in props
    assert "evaluation_id" in props
    # Neither should be in required (they're optional)
    required = params.get("required", [])
    assert "action_id" not in required
    assert "evaluation_id" not in required
