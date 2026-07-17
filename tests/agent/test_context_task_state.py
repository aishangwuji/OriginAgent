"""Tests for task_state context injection into phase1 continuity blocks.

Design:
- task_state is injected as a SEPARATE block (``<task_state>``), not inside
  ``<recovered_continuity>``. This is because task_state is LIVE session
  state (intra-session), not checkpoint recovery state (cross-session).
- The block is CONDITIONALLY injected — only when
  ``session.metadata["_task_state"]`` exists. A new session that has never
  called TaskStateTool gets no task_state block (no noise).
- This makes the Agent's meta-cognitive state visible on EVERY turn,
  enabling the LLM to reason about its own progress.

Why not inject into ``build_recovered_continuity_context``?
- ``build_recovered_continuity_context`` is called only during checkpoint
  recovery (cross-session). task_state is intra-session state that should
  be visible on every turn, not just recovery.
- ``session.metadata["_task_state"]`` survives across process restarts
  (sessions are persisted), so checkpoint recovery automatically restores
  task_state — no need to duplicate it in the checkpoint.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from OriginAgent.agent.context import ContextBuilder
from OriginAgent.session.manager import SessionManager
from OriginAgent.agent.confirmation import PendingConfirmationStore


def _make_builder(tmp_path: Path) -> tuple[ContextBuilder, SessionManager, object]:
    """Create a ContextBuilder wired to a real SessionManager."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = SessionManager(workspace)
    builder = ContextBuilder(
        workspace=workspace,
        timezone="UTC",
        sessions=sessions,
        confirmation_store=PendingConfirmationStore(workspace),
    )
    return builder, sessions, workspace


# ─── TASK_STATE_CONTEXT_KIND constant ─────────────────────────────────────


def test_task_state_context_kind_constant_exists() -> None:
    """ContextBuilder exposes TASK_STATE_CONTEXT_KIND for audit consistency."""
    assert hasattr(ContextBuilder, "TASK_STATE_CONTEXT_KIND")
    assert isinstance(ContextBuilder.TASK_STATE_CONTEXT_KIND, str)
    assert ContextBuilder.TASK_STATE_CONTEXT_KIND == "task_state_context"


# ─── build_task_state_block static method ─────────────────────────────────


def test_build_task_state_block_renders_correctly() -> None:
    """build_task_state_block renders the snapshot as a <task_state> block."""
    snapshot = {
        "current_state": "BLOCKED",
        "previous_state": "EXPLORE",
        "reason": "exec denied by capability_snapshot_required",
        "history": [
            {"state": "BLOCKED", "ts": "2026-07-17T00:00:00+00:00", "reason": "exec denied"},
        ],
    }
    block = ContextBuilder.build_task_state_block(snapshot)

    assert block["type"] == "text"
    text = block["text"]
    assert "<task_state trust='internal'>" in text
    assert "</task_state>" in text
    # Current state is visible
    assert "BLOCKED" in text
    # Previous state is visible
    assert "EXPLORE" in text
    # Reason is visible
    assert "exec denied" in text
    # History entry is visible
    assert "2026-07-17T00:00:00+00:00" in text


def test_build_task_state_block_meta_kind() -> None:
    """Block _meta.kind is TASK_STATE_CONTEXT_KIND for audit tracking."""
    block = ContextBuilder.build_task_state_block({"current_state": "EXPLORE"})
    assert block["_meta"]["kind"] == ContextBuilder.TASK_STATE_CONTEXT_KIND
    assert block["_meta"]["trust"] == "internal"


# ─── build_phase1_continuity_blocks integration ──────────────────────────


def test_phase1_includes_task_state_when_present(tmp_path: Path) -> None:
    """When session.metadata has _task_state, the block is included."""
    builder, sessions, _ = _make_builder(tmp_path)
    session = sessions.get_or_create("cli:direct")
    session.metadata["_task_state"] = {
        "current_state": "BLOCKED",
        "previous_state": "EXPLORE",
        "reason": "exec denied",
        "history": [],
    }

    blocks = builder.build_phase1_continuity_blocks(
        session_key="cli:direct",
        runtime_context=None,
    )

    kinds = [b.get("_meta", {}).get("kind") for b in blocks if isinstance(b, dict)]
    assert ContextBuilder.TASK_STATE_CONTEXT_KIND in kinds

    # Verify the block content
    task_block = next(
        b for b in blocks
        if isinstance(b, dict) and b.get("_meta", {}).get("kind") == ContextBuilder.TASK_STATE_CONTEXT_KIND
    )
    assert "BLOCKED" in task_block["text"]
    assert "exec denied" in task_block["text"]


def test_phase1_excludes_task_state_when_absent(tmp_path: Path) -> None:
    """When session.metadata has no _task_state, no block is added (no noise).

    This is critical: a new session that has never called TaskStateTool
    should NOT get an empty task_state block. The block is opt-in.
    """
    builder, sessions, _ = _make_builder(tmp_path)
    session = sessions.get_or_create("cli:direct")
    assert "_task_state" not in session.metadata

    blocks = builder.build_phase1_continuity_blocks(
        session_key="cli:direct",
        runtime_context=None,
    )

    kinds = [b.get("_meta", {}).get("kind") for b in blocks if isinstance(b, dict)]
    assert ContextBuilder.TASK_STATE_CONTEXT_KIND not in kinds


def test_phase1_excludes_task_state_when_no_session(tmp_path: Path) -> None:
    """When session is None (no session_key), no task_state block is added."""
    builder, _, _ = _make_builder(tmp_path)

    blocks = builder.build_phase1_continuity_blocks(
        session_key=None,
        runtime_context=None,
    )

    kinds = [b.get("_meta", {}).get("kind") for b in blocks if isinstance(b, dict)]
    assert ContextBuilder.TASK_STATE_CONTEXT_KIND not in kinds


def test_phase1_task_state_positioned_after_world_state(tmp_path: Path) -> None:
    """task_state block comes AFTER world_state in the assembly order.

    Rationale: world_state is the Agent's belief about the external world,
    while task_state is the Agent's belief about its own progress. External
    state is more stable (changes less frequently), so it comes first;
    task_state is more volatile (changes every turn), so it comes last.
    This mirrors the BDI ordering: Beliefs (world) → Desires (goal) →
    Intentions (task state).
    """
    builder, sessions, _ = _make_builder(tmp_path)
    session = sessions.get_or_create("cli:direct")
    session.metadata["_task_state"] = {
        "current_state": "EXPLORE",
        "previous_state": None,
        "reason": "",
        "history": [],
    }

    blocks = builder.build_phase1_continuity_blocks(
        session_key="cli:direct",
        runtime_context=None,
    )

    kinds = [b.get("_meta", {}).get("kind") for b in blocks if isinstance(b, dict)]
    if ContextBuilder.WORLD_STATE_CONTEXT_KIND in kinds and ContextBuilder.TASK_STATE_CONTEXT_KIND in kinds:
        ws_idx = kinds.index(ContextBuilder.WORLD_STATE_CONTEXT_KIND)
        ts_idx = kinds.index(ContextBuilder.TASK_STATE_CONTEXT_KIND)
        assert ts_idx > ws_idx, "task_state must come after world_state"


def test_phase1_task_state_reflects_latest_state(tmp_path: Path) -> None:
    """The injected block reflects the LATEST task_state in session.metadata.

    This verifies that the context reads live state (rule 5: no stale
    snapshots). If the Agent calls TaskStateTool to transition, the next
    turn's context block shows the new state.
    """
    builder, sessions, _ = _make_builder(tmp_path)
    session = sessions.get_or_create("cli:direct")

    # First state: EXPLORE
    session.metadata["_task_state"] = {
        "current_state": "EXPLORE",
        "previous_state": None,
        "reason": "",
        "history": [],
    }
    blocks1 = builder.build_phase1_continuity_blocks(
        session_key="cli:direct",
        runtime_context=None,
    )
    task_block1 = next(
        b for b in blocks1
        if isinstance(b, dict) and b.get("_meta", {}).get("kind") == ContextBuilder.TASK_STATE_CONTEXT_KIND
    )
    assert "EXPLORE" in task_block1["text"]

    # Simulate Agent calling TaskStateTool to transition to BLOCKED
    session.metadata["_task_state"] = {
        "current_state": "BLOCKED",
        "previous_state": "EXPLORE",
        "reason": "exec denied",
        "history": [
            {"state": "BLOCKED", "ts": "2026-07-17T00:00:00+00:00", "reason": "exec denied"},
        ],
    }

    # Next turn: context should show BLOCKED (not stale EXPLORE)
    blocks2 = builder.build_phase1_continuity_blocks(
        session_key="cli:direct",
        runtime_context=None,
    )
    task_block2 = next(
        b for b in blocks2
        if isinstance(b, dict) and b.get("_meta", {}).get("kind") == ContextBuilder.TASK_STATE_CONTEXT_KIND
    )
    assert "BLOCKED" in task_block2["text"]
    assert "EXPLORE" in task_block2["text"]  # previous_state
    assert "exec denied" in task_block2["text"]  # reason


# ─── Phase E: action_trace injection in task_state block ───────────────────
#
# The task_state block now includes a summary of recent action_trace entries.
# This lets the Agent see the action_ids it needs to reference when calling
# evaluate_action or task_state(transition, action_id=...).
# Without this, the Agent would be blind to its own action history and
# unable to form the complete action→result→evaluation→state causal chain.


def test_build_task_state_block_with_action_trace() -> None:
    """build_task_state_block includes action_trace summary when provided."""
    snapshot = {
        "current_state": "EXPLORE",
        "previous_state": None,
        "reason": "",
        "history": [],
    }
    action_trace = [
        {
            "action_id": "call_abc",
            "tool_name": "echo",
            "params_summary": "text=hello",
            "result_summary": "echo: hello",
            "success": True,
            "denied": False,
            "ts": "2026-07-17T00:00:00+00:00",
            "iteration": 0,
        },
    ]
    block = ContextBuilder.build_task_state_block(snapshot, action_trace=action_trace)
    text = block["text"]
    # action_id is visible so the Agent can reference it
    assert "call_abc" in text
    # tool_name is visible
    assert "echo" in text
    # success status is visible
    assert "ok" in text.lower() or "success" in text.lower() or "true" in text.lower()


def test_build_task_state_block_without_action_trace_backward_compatible() -> None:
    """When action_trace is not passed, the block works as before (no crash)."""
    snapshot = {"current_state": "EXPLORE"}
    block = ContextBuilder.build_task_state_block(snapshot)
    text = block["text"]
    assert "<task_state" in text
    assert "EXPLORE" in text


def test_build_task_state_block_empty_action_trace_omits_section() -> None:
    """When action_trace is an empty list, no action section is rendered."""
    snapshot = {"current_state": "EXPLORE"}
    block = ContextBuilder.build_task_state_block(snapshot, action_trace=[])
    text = block["text"]
    # Should not have an "Recent actions" section
    assert "Recent actions" not in text


def test_build_task_state_block_denied_action_marked() -> None:
    """Denied actions are clearly marked in the action_trace summary."""
    snapshot = {"current_state": "BLOCKED"}
    action_trace = [
        {
            "action_id": "call_denied",
            "tool_name": "exec",
            "params_summary": "cmd=rm",
            "result_summary": "Error: denied by policy",
            "success": False,
            "denied": True,
            "ts": "2026-07-17T00:00:00+00:00",
            "iteration": 0,
        },
    ]
    block = ContextBuilder.build_task_state_block(snapshot, action_trace=action_trace)
    text = block["text"]
    assert "call_denied" in text
    assert "DENIED" in text or "denied" in text


def test_phase1_includes_action_trace_with_task_state(tmp_path: Path) -> None:
    """When both _task_state and _action_trace exist, block includes both."""
    builder, sessions, _ = _make_builder(tmp_path)
    session = sessions.get_or_create("cli:direct")
    session.metadata["_task_state"] = {
        "current_state": "EXPLORE",
        "previous_state": None,
        "reason": "",
        "history": [],
    }
    session.metadata["_action_trace"] = [
        {
            "action_id": "call_xyz",
            "tool_name": "echo",
            "params_summary": "text=hi",
            "result_summary": "echo: hi",
            "success": True,
            "denied": False,
            "ts": "2026-07-17T00:00:00+00:00",
            "iteration": 0,
        },
    ]

    blocks = builder.build_phase1_continuity_blocks(
        session_key="cli:direct",
        runtime_context=None,
    )
    task_block = next(
        b for b in blocks
        if isinstance(b, dict) and b.get("_meta", {}).get("kind") == ContextBuilder.TASK_STATE_CONTEXT_KIND
    )
    assert "call_xyz" in task_block["text"]


def test_phase1_action_trace_not_injected_when_task_state_absent(tmp_path: Path) -> None:
    """When _task_state is absent but _action_trace exists, no block is injected.

    Rationale: the block is opt-in via task_state. If the Agent hasn't
    engaged its meta-cognitive layer (no task_state call), it doesn't need
    to see action_trace in context. evaluate_action defaults to the most
    recent action_id when not specified, so the Agent can still evaluate
    without seeing the trace.
    """
    builder, sessions, _ = _make_builder(tmp_path)
    session = sessions.get_or_create("cli:direct")
    session.metadata["_action_trace"] = [
        {
            "action_id": "call_orphan",
            "tool_name": "echo",
            "params_summary": "",
            "result_summary": "",
            "success": True,
            "denied": False,
            "ts": "2026-07-17T00:00:00+00:00",
            "iteration": 0,
        },
    ]
    assert "_task_state" not in session.metadata

    blocks = builder.build_phase1_continuity_blocks(
        session_key="cli:direct",
        runtime_context=None,
    )
    kinds = [b.get("_meta", {}).get("kind") for b in blocks if isinstance(b, dict)]
    assert ContextBuilder.TASK_STATE_CONTEXT_KIND not in kinds


def test_phase1_action_trace_capped_at_5_entries(tmp_path: Path) -> None:
    """Only the most recent 5 action_trace entries are shown (token budget)."""
    builder, sessions, _ = _make_builder(tmp_path)
    session = sessions.get_or_create("cli:direct")
    session.metadata["_task_state"] = {
        "current_state": "EXPLORE",
        "previous_state": None,
        "reason": "",
        "history": [],
    }
    # 10 action_trace entries
    session.metadata["_action_trace"] = [
        {
            "action_id": f"call_{i}",
            "tool_name": "echo",
            "params_summary": f"n={i}",
            "result_summary": f"echo: {i}",
            "success": True,
            "denied": False,
            "ts": "2026-07-17T00:00:00+00:00",
            "iteration": i,
        }
        for i in range(10)
    ]

    blocks = builder.build_phase1_continuity_blocks(
        session_key="cli:direct",
        runtime_context=None,
    )
    task_block = next(
        b for b in blocks
        if isinstance(b, dict) and b.get("_meta", {}).get("kind") == ContextBuilder.TASK_STATE_CONTEXT_KIND
    )
    text = task_block["text"]
    # Last 5 should be visible (call_5 through call_9)
    assert "call_9" in text
    assert "call_5" in text
    # First 5 should NOT be visible (capped)
    assert "call_0" not in text
    assert "call_4" not in text
