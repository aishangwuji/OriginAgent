"""Tests for EvaluateActionTool — LLM meta-cognitive assessment layer.

This is the **evaluation layer** of the action-result causal chain:
- Agent calls evaluate_action after observing a tool's result
- The tool records a structured assessment (outcome + goal_alignment)
- task_state transitions can reference the returned evaluation_id

Design (rule 34: verification first; rule 32: minimal):
- action_id defaults to the most recent action in the trace (convenience)
- evaluation_id is generated as eval_<8hex> (unique, traceable)
- Persisted to session.metadata["_action_evaluations"] (FIFO cap 50)
- Re-reads session on every call (rule 5: no stale snapshots)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.action_trace import _METADATA_KEY as _TRACE_KEY, record_action_trace
from OriginAgent.agent.tools.evaluate_action import (
    _EVAL_METADATA_KEY,
    _MAX_EVALUATIONS,
    _VALID_OUTCOMES,
    _VALID_GOAL_ALIGNMENTS,
    EvaluateActionTool,
)
from OriginAgent.session.manager import Session, SessionManager


# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_sessions_with_session(session_key: str = "cli:test") -> tuple[MagicMock, Session]:
    session = Session(key=session_key)
    sessions = MagicMock(spec=SessionManager)
    sessions.get_or_create.return_value = session
    return sessions, session


def _make_tool(session_key: str = "cli:test") -> tuple[EvaluateActionTool, Session]:
    sessions, session = _make_sessions_with_session(session_key)
    tool = EvaluateActionTool(sessions)
    tool._session_key = session_key
    return tool, session


def _seed_action_trace(session: Session, action_id: str = "call_001") -> None:
    """Seed a minimal action_trace entry so evaluate_action can reference it."""
    from types import SimpleNamespace
    sessions = MagicMock(spec=SessionManager)
    sessions.get_or_create.return_value = session
    spec = SimpleNamespace(sessions=sessions, session_key="cli:test")
    tc = SimpleNamespace(id=action_id, name="read_file", arguments={"path": "/foo"})
    record_action_trace(spec, tc, "file contents", {"status": "ok"}, None, 0)


# ─── Tool properties ────────────────────────────────────────────────────────


def test_tool_name() -> None:
    tool, _ = _make_tool()
    assert tool.name == "evaluate_action"


def test_tool_description_mentions_outcomes() -> None:
    tool, _ = _make_tool()
    desc = tool.description
    for outcome in _VALID_OUTCOMES:
        assert outcome in desc


def test_tool_parameters_have_required_fields() -> None:
    tool, _ = _make_tool()
    params = tool.parameters
    props = params.get("properties", {})
    assert "action_id" in props
    assert "outcome" in props
    assert "assessment" in props
    assert "goal_alignment" in props
    assert "outcome" in params.get("required", [])


# ─── Evaluate: basic creation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_creates_evaluation() -> None:
    """A valid evaluate call creates an evaluation entry."""
    tool, session = _make_tool()
    _seed_action_trace(session, "call_001")
    result = await tool.execute(
        action_id="call_001",
        outcome="progressed",
        assessment="read_file returned expected content, goal advanced",
        goal_alignment="aligned",
    )
    data = json.loads(result)
    assert data["action_id"] == "call_001"
    assert data["outcome"] == "progressed"
    assert "evaluation_id" in data
    assert data["evaluation_id"].startswith("eval_")


@pytest.mark.asyncio
async def test_evaluate_persists_to_session() -> None:
    """Evaluation is persisted to session.metadata['_action_evaluations']."""
    tool, session = _make_tool()
    _seed_action_trace(session, "call_001")
    await tool.execute(
        action_id="call_001",
        outcome="progressed",
        assessment="good",
        goal_alignment="aligned",
    )
    evals = session.metadata.get(_EVAL_METADATA_KEY)
    assert evals is not None
    assert len(evals) == 1
    assert evals[0]["outcome"] == "progressed"


@pytest.mark.asyncio
async def test_evaluate_returns_evaluation_id() -> None:
    """The returned JSON includes a unique evaluation_id."""
    tool, session = _make_tool()
    _seed_action_trace(session, "call_001")
    result = await tool.execute(
        action_id="call_001",
        outcome="progressed",
        assessment="good",
    )
    data = json.loads(result)
    eid = data["evaluation_id"]
    assert eid.startswith("eval_")
    assert len(eid) == 13  # "eval_" + 8 hex chars


# ─── Evaluate: default action_id (most recent) ────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_default_action_id_is_most_recent() -> None:
    """When action_id is empty, evaluate the most recent action in trace."""
    tool, session = _make_tool()
    _seed_action_trace(session, "call_001")
    # Seed a second, more recent action
    _seed_action_trace(session, "call_002")
    result = await tool.execute(
        action_id="",  # empty → use most recent
        outcome="progressed",
        assessment="good",
    )
    data = json.loads(result)
    assert data["action_id"] == "call_002"


@pytest.mark.asyncio
async def test_evaluate_no_trace_returns_error() -> None:
    """When no action_trace exists and action_id is empty, return helpful error."""
    tool, _ = _make_tool()
    result = await tool.execute(
        action_id="",
        outcome="progressed",
        assessment="good",
    )
    assert "error" in result.lower() or "no action" in result.lower()


# ─── Evaluate: validation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_invalid_outcome_returns_error() -> None:
    """Invalid outcome values are rejected."""
    tool, session = _make_tool()
    _seed_action_trace(session, "call_001")
    result = await tool.execute(
        action_id="call_001",
        outcome="invalid_outcome",
        assessment="test",
    )
    assert "error" in result.lower() or "invalid" in result.lower()
    # No evaluation should be persisted
    evals = session.metadata.get(_EVAL_METADATA_KEY, [])
    assert len(evals) == 0


@pytest.mark.asyncio
async def test_evaluate_invalid_goal_alignment_returns_error() -> None:
    """Invalid goal_alignment values are rejected."""
    tool, session = _make_tool()
    _seed_action_trace(session, "call_001")
    result = await tool.execute(
        action_id="call_001",
        outcome="progressed",
        assessment="test",
        goal_alignment="invalid",
    )
    assert "error" in result.lower() or "invalid" in result.lower()


@pytest.mark.asyncio
async def test_evaluate_empty_outcome_returns_error() -> None:
    """Empty outcome is rejected (it's a required field)."""
    tool, session = _make_tool()
    _seed_action_trace(session, "call_001")
    result = await tool.execute(
        action_id="call_001",
        outcome="",
        assessment="test",
    )
    assert "error" in result.lower() or "required" in result.lower()


# ─── Evaluate: no sessions ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_no_sessions_returns_error() -> None:
    """When sessions is None, return error without crashing."""
    tool = EvaluateActionTool(None)
    tool._session_key = None
    result = await tool.execute(
        action_id="call_001",
        outcome="progressed",
        assessment="test",
    )
    assert "error" in result.lower() or "unavailable" in result.lower()


# ─── Evaluate: FIFO cap ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluations_fifo_cap() -> None:
    """Evaluations are capped at _MAX_EVALUATIONS (FIFO)."""
    tool, session = _make_tool()
    _seed_action_trace(session, "call_001")
    for i in range(_MAX_EVALUATIONS + 10):
        await tool.execute(
            action_id="call_001",
            outcome="progressed",
            assessment=f"eval-{i}",
        )
    evals = session.metadata[_EVAL_METADATA_KEY]
    assert len(evals) == _MAX_EVALUATIONS
    # Oldest dropped, newest retained
    assert evals[0]["assessment"] == f"eval-{10}"
    assert evals[-1]["assessment"] == f"eval-{_MAX_EVALUATIONS + 9}"


# ─── Evaluate: unknown action_id ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_unknown_action_id_warns_but_records() -> None:
    """Unknown action_id is recorded with a warning (Agent may reference stale id).

    We don't reject unknown action_ids because:
    1. The action_trace may have been FIFO-capped out.
    2. The Agent may reference an action from a previous turn whose trace
       is still in session.metadata but not in the recent slice.
    Instead, we record the evaluation and let the Agent decide if the
    reference is valid. This follows rule 32 (minimal) — no speculative
    validation for a scenario that may not need blocking.
    """
    tool, session = _make_tool()
    _seed_action_trace(session, "call_001")
    result = await tool.execute(
        action_id="call_nonexistent",
        outcome="uncertain",
        assessment="referenced action not found in trace",
    )
    data = json.loads(result)
    assert data["action_id"] == "call_nonexistent"
    assert data["outcome"] == "uncertain"


# ─── Evaluate: goal_alignment optional ────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_goal_alignment_defaults_to_unclear() -> None:
    """When goal_alignment is not provided, it defaults to 'unclear'."""
    tool, session = _make_tool()
    _seed_action_trace(session, "call_001")
    result = await tool.execute(
        action_id="call_001",
        outcome="progressed",
        assessment="good",
        # goal_alignment not provided
    )
    data = json.loads(result)
    assert data["goal_alignment"] == "unclear"


# ─── Evaluate: multiple evaluations accumulate ────────────────────────────


@pytest.mark.asyncio
async def test_multiple_evaluations_accumulate() -> None:
    """Multiple evaluations are accumulated in order."""
    tool, session = _make_tool()
    _seed_action_trace(session, "call_001")
    _seed_action_trace(session, "call_002")
    await tool.execute(action_id="call_001", outcome="progressed", assessment="first")
    await tool.execute(action_id="call_002", outcome="blocked", assessment="second")

    evals = session.metadata[_EVAL_METADATA_KEY]
    assert len(evals) == 2
    assert evals[0]["action_id"] == "call_001"
    assert evals[1]["action_id"] == "call_002"


# ─── once_per_turn ─────────────────────────────────────────────────────────


def test_once_per_turn_is_false() -> None:
    """evaluate_action can be called multiple times per turn (one per action)."""
    tool, _ = _make_tool()
    assert tool.once_per_turn is False
