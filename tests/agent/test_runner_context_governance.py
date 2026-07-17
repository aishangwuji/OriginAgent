"""Tests for context governance helpers in the shared agent runner.

Covers ``AgentRunner._drop_orphan_tool_results`` position-legality checks:
tool messages must immediately follow the assistant message that declared
the matching ``tool_call_id`` (interleaved tool messages are allowed, but a
user/system message between the assistant and its tool result makes the
tool result an orphan by position).
"""

from __future__ import annotations

from OriginAgent.agent.runner import AgentRunner


def _assistant_with_tool_calls(call_ids: list[str]) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": cid,
                "type": "function",
                "function": {"name": "f" if cid == "call_1" else "g", "arguments": "{}"},
            }
            for cid in call_ids
        ],
    }


def _tool_result(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def test_drop_orphan_tool_results_removes_tool_separated_from_assistant_by_user():
    """A tool message separated from its assistant by a user message is positionally illegal."""
    messages = [
        _assistant_with_tool_calls(["call_1"]),
        {"role": "user", "content": "hi"},
        _tool_result("call_1", "result"),
    ]

    result = AgentRunner._drop_orphan_tool_results(messages)

    assert not any(
        msg.get("role") == "tool" and msg.get("tool_call_id") == "call_1"
        for msg in result
    )


def test_drop_orphan_tool_results_keeps_tool_immediately_after_assistant():
    """A tool message immediately after its assistant (with a later user) is positionally legal."""
    messages = [
        _assistant_with_tool_calls(["call_1"]),
        _tool_result("call_1", "result"),
        {"role": "user", "content": "hi"},
    ]

    result = AgentRunner._drop_orphan_tool_results(messages)

    assert any(
        msg.get("role") == "tool" and msg.get("tool_call_id") == "call_1"
        for msg in result
    )


def test_drop_orphan_tool_results_handles_multiple_tool_results_for_same_assistant():
    """Multiple tool results for the same assistant (no user/system between) are all legal."""
    messages = [
        _assistant_with_tool_calls(["call_1", "call_2"]),
        _tool_result("call_1", "r1"),
        _tool_result("call_2", "r2"),
        {"role": "user", "content": "hi"},
    ]

    result = AgentRunner._drop_orphan_tool_results(messages)

    kept_ids = {
        msg.get("tool_call_id")
        for msg in result
        if msg.get("role") == "tool"
    }
    assert kept_ids == {"call_1", "call_2"}


def test_snip_then_drop_orphans_cleans_tools_left_by_removed_assistant():
    """Simulate post-snip state: assistant was removed but its tool result remains.

    After ``_snip_history`` removes an assistant message that declared
    ``tool_calls``, the matching ``tool`` result messages may be left behind
    (snip keeps a trailing tail of messages to preserve recent context, and
    the boundary can fall between an assistant and its tool result). The
    post-snip governance pipeline (drop -> backfill -> drop) must clean these
    orphaned tool messages so the model never sees a ``tool`` message without
    a preceding declaring assistant.

    This test simulates the post-snip state directly (rather than triggering
    snip end-to-end) and verifies the final pipeline result is clean,
    regardless of which intermediate step performs the cleanup. The test is
    regression protection: it locks in the contract that the three-step
    post-snip pipeline yields a legally-ordered message list.
    """
    # Simulate post-snip state: the assistant(tool_calls=[call_1]) that
    # originally began this segment was removed by snip, but its tool result
    # message remains -- positionally orphaned (no preceding assistant).
    messages_after_snip = [
        _tool_result("call_1", "result"),
        {"role": "user", "content": "next question"},
        {"role": "assistant", "content": "answer"},
    ]

    # Execute the post-snip cleanup pipeline (three calls per spec).
    messages = AgentRunner._drop_orphan_tool_results(messages_after_snip)
    messages = AgentRunner._backfill_missing_tool_results(messages)
    messages = AgentRunner._drop_orphan_tool_results(messages)

    # The orphaned tool result must be gone.
    assert all(
        not (m.get("role") == "tool" and m.get("tool_call_id") == "call_1")
        for m in messages
    )
    # First message must not be a tool (illegal start of conversation).
    if messages:
        first = messages[0]
        assert first.get("role") in ("user", "assistant", "system"), (
            f"first role: {first.get('role')}"
        )
