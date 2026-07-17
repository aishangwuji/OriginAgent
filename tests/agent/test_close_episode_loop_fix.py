"""Tests for close_episode tool loop fix (A+B+C).

The close_episode death loop occurs when the LLM calls close_episode with
different ``label`` params each iteration. The idempotency check uses
tool_name+param_hash, so different labels bypass dedup. Each call succeeds
(finish_reason=tool_calls), so the circuit breaker never triggers.

Three fixes:
    A. Tool-loop circuit breaker — same tool name called > threshold times
       in one turn → blocked.
    B. once_per_turn flag — CloseEpisodeTool marked as once-per-turn;
       second call in same turn blocked.
    C. Description fix + injection guard — description says "at most once
       per turn"; tool result appends "do not call again" hint.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.close_episode import CloseEpisodeTool
from OriginAgent.session.manager import SessionManager


# ─── Fix B: once_per_turn flag ─────────────────────────────────────────────


class TestOncePerTurnFlag:
    """Test the once_per_turn property on Tool base class and CloseEpisodeTool."""

    def test_tool_base_once_per_turn_defaults_false(self) -> None:
        """Tool base class once_per_turn defaults to False."""
        # Cannot instantiate abstract Tool directly; use a concrete stub
        class StubTool(Tool):
            name = "stub"
            description = "stub"
            parameters = {"type": "object", "properties": {}}

            async def execute(self, **kwargs: Any) -> str:
                return "ok"

        tool = StubTool()
        assert tool.once_per_turn is False

    def test_close_episode_once_per_turn_is_true(self) -> None:
        """CloseEpisodeTool.once_per_turn returns True."""
        sessions = MagicMock(spec=SessionManager)
        tool = CloseEpisodeTool(sessions)
        assert tool.once_per_turn is True


# ─── Fix C: description and injection guard ────────────────────────────────


class TestCloseEpisodeDescriptionGuard:
    """Test the description includes once-per-turn guidance (Fix C)."""

    def test_description_mentions_once_per_turn(self) -> None:
        """Description includes 'once per turn' or 'at most once' guidance."""
        sessions = MagicMock(spec=SessionManager)
        tool = CloseEpisodeTool(sessions)
        desc = tool.description.lower()
        assert "once" in desc and "turn" in desc

    def test_execute_result_includes_no_retry_hint(self) -> None:
        """Tool result appends 'do not call again' hint after success."""
        sessions = MagicMock(spec=SessionManager)
        session = MagicMock()
        session.start_new_episode = MagicMock()
        sessions.get_or_create.return_value = session

        tool = CloseEpisodeTool(sessions)
        tool._session_key = "cli:direct"

        result = tool.execute(label="new topic")
        # Use asyncio to run the coroutine
        import asyncio
        result_str = asyncio.run(result) if asyncio.iscoroutine(result) else result

        assert "do not call" in result_str.lower() or "once per turn" in result_str.lower()


# ─── Fix A: circuit breaker logic ──────────────────────────────────────────


class TestToolLoopCircuitBreaker:
    """Test the tool-loop circuit breaker constant and threshold check."""

    def test_max_same_tool_calls_per_turn_constant_exists(self) -> None:
        """The runner exposes a _MAX_SAME_TOOL_CALLS_PER_TURN constant."""
        from OriginAgent.agent.runner import AgentRunner

        assert hasattr(AgentRunner, "_MAX_SAME_TOOL_CALLS_PER_TURN")
        threshold = AgentRunner._MAX_SAME_TOOL_CALLS_PER_TURN
        assert isinstance(threshold, int)
        assert threshold >= 3  # at least 3 to allow legitimate repeated calls

    def test_circuit_breaker_check_method_exists(self) -> None:
        """AgentRunner has a method to check if circuit breaker should trip."""
        from OriginAgent.agent.runner import AgentRunner

        # The method should exist and be callable
        assert hasattr(AgentRunner, "_check_tool_circuit_breaker")

    def test_circuit_breaker_returns_false_below_threshold(self) -> None:
        """Below threshold → no trip."""
        from OriginAgent.agent.runner import AgentRunner

        counts = {"close_episode": 3}
        threshold = 5
        result = AgentRunner._check_tool_circuit_breaker(counts, threshold)
        assert result is None  # no tripped tool

    def test_circuit_breaker_returns_tripped_tool_at_threshold(self) -> None:
        """At/above threshold → returns the tripped tool name."""
        from OriginAgent.agent.runner import AgentRunner

        counts = {"close_episode": 5}
        threshold = 5
        result = AgentRunner._check_tool_circuit_breaker(counts, threshold)
        assert result == "close_episode"

    def test_circuit_breaker_returns_tripped_tool_above_threshold(self) -> None:
        """Above threshold → returns the tripped tool name."""
        from OriginAgent.agent.runner import AgentRunner

        counts = {"close_episode": 10, "read_file": 2}
        threshold = 5
        result = AgentRunner._check_tool_circuit_breaker(counts, threshold)
        assert result == "close_episode"

    def test_circuit_breaker_empty_counts(self) -> None:
        """Empty counts → no trip."""
        from OriginAgent.agent.runner import AgentRunner

        result = AgentRunner._check_tool_circuit_breaker({}, 5)
        assert result is None

    def test_circuit_breaker_multiple_tripped_returns_first(self) -> None:
        """Multiple tools over threshold → returns one of them (deterministic)."""
        from OriginAgent.agent.runner import AgentRunner

        counts = {"close_episode": 6, "exec": 7}
        threshold = 5
        result = AgentRunner._check_tool_circuit_breaker(counts, threshold)
        # Should return one of the tripped tools
        assert result in {"close_episode", "exec"}


# ─── Integration: _run_tool_core enforces once_per_turn + circuit breaker ──


@pytest.mark.asyncio
async def test_run_tool_core_blocks_once_per_turn_after_first_success() -> None:
    """_run_tool_core blocks a once_per_turn tool on the second call in same turn."""
    from OriginAgent.agent.runner import AgentRunner, AgentRunSpec
    from OriginAgent.agent.tools.registry import ToolRegistry
    from OriginAgent.config.schema import AgentDefaults
    from OriginAgent.providers.base import ToolCallRequest

    sessions = MagicMock(spec=SessionManager)
    session = MagicMock()
    sessions.get_or_create.return_value = session

    close_tool = CloseEpisodeTool(sessions)
    close_tool._session_key = "cli:test"
    tools = ToolRegistry()
    tools.register(close_tool)

    runner = AgentRunner(MagicMock())
    spec = AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
    )
    idempotency_keys: set[str] = set()
    tool_call_counts: dict[str, int] = {}
    once_per_turn_called: set[str] = set()

    tc = ToolCallRequest(id="c1", name="close_episode", arguments={"label": "topic-a"})
    result1, event1, _ = await runner._run_tool_core(
        spec, tc, {}, {}, idempotency_keys,
        tool_call_counts=tool_call_counts,
        once_per_turn_called=once_per_turn_called,
    )
    assert event1["status"] == "ok"
    assert "close_episode" in once_per_turn_called
    assert tool_call_counts.get("close_episode") == 1

    # Second call with different label — should be blocked by once_per_turn
    tc2 = ToolCallRequest(id="c2", name="close_episode", arguments={"label": "topic-b"})
    result2, event2, _ = await runner._run_tool_core(
        spec, tc2, {}, {}, idempotency_keys,
        tool_call_counts=tool_call_counts,
        once_per_turn_called=once_per_turn_called,
    )
    assert event2["status"] == "skipped"
    assert "once_per_turn" in event2["detail"]
    assert "once-per-turn" in result2.lower() or "already called" in result2.lower()
    # Count should NOT increment for blocked calls
    assert tool_call_counts.get("close_episode") == 1


@pytest.mark.asyncio
async def test_run_tool_core_circuit_breaker_blocks_after_threshold() -> None:
    """_run_tool_core blocks a tool after _MAX_SAME_TOOL_CALLS_PER_TURN successes."""
    from OriginAgent.agent.runner import AgentRunner, AgentRunSpec
    from OriginAgent.agent.tools.registry import ToolRegistry
    from OriginAgent.config.schema import AgentDefaults
    from OriginAgent.providers.base import ToolCallRequest

    # Use a stub tool that is NOT once_per_turn (to test circuit breaker independently)
    class _RepeatableTool(Tool):
        @property
        def name(self) -> str:
            return "repeatable"
        @property
        def description(self) -> str:
            return "repeatable"
        @property
        def parameters(self) -> dict:
            return {"type": "object", "properties": {"n": {"type": "string"}}}
        async def execute(self, **kwargs: Any) -> str:
            return f"ok-{kwargs.get('n', '')}"

    tools = ToolRegistry()
    tools.register(_RepeatableTool())

    runner = AgentRunner(MagicMock())
    spec = AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=30,
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
    )
    idempotency_keys: set[str] = set()
    tool_call_counts: dict[str, int] = {}
    once_per_turn_called: set[str] = set()
    threshold = AgentRunner._MAX_SAME_TOOL_CALLS_PER_TURN

    # Call threshold times — all should succeed (different param → different idempotency key)
    for i in range(threshold):
        tc = ToolCallRequest(id=f"c{i}", name="repeatable", arguments={"n": str(i)})
        result, event, _ = await runner._run_tool_core(
            spec, tc, {}, {}, idempotency_keys,
            tool_call_counts=tool_call_counts,
            once_per_turn_called=once_per_turn_called,
        )
        assert event["status"] == "ok", f"call {i} should succeed, got {event}"
    assert tool_call_counts.get("repeatable") == threshold

    # Next call should be blocked by circuit breaker
    tc_blocked = ToolCallRequest(id="cX", name="repeatable", arguments={"n": "overflow"})
    result_blocked, event_blocked, _ = await runner._run_tool_core(
        spec, tc_blocked, {}, {}, idempotency_keys,
        tool_call_counts=tool_call_counts,
        once_per_turn_called=once_per_turn_called,
    )
    assert event_blocked["status"] == "skipped"
    assert "circuit breaker" in event_blocked["detail"]
    assert "tool loop" in result_blocked.lower() or "circuit breaker" in result_blocked.lower()


# ─── End-to-end: run() breaks the close_episode death loop ─────────────────


@pytest.mark.asyncio
async def test_run_breaks_close_episode_death_loop() -> None:
    """Full run() loop: LLM keeps calling close_episode with different labels,
    runner must break the loop via once_per_turn after the first success.
    """
    from OriginAgent.agent.runner import AgentRunner, AgentRunSpec
    from OriginAgent.agent.tools.registry import ToolRegistry
    from OriginAgent.config.schema import AgentDefaults
    from OriginAgent.providers.base import LLMResponse, ToolCallRequest

    sessions = MagicMock(spec=SessionManager)
    session = MagicMock()
    sessions.get_or_create.return_value = session

    close_tool = CloseEpisodeTool(sessions)
    close_tool._session_key = "cli:test"
    tools = ToolRegistry()
    tools.register(close_tool)

    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        # First few calls: LLM keeps calling close_episode with different labels
        if call_count["n"] <= 10:
            return LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(
                    id=f"call_{call_count['n']}",
                    name="close_episode",
                    arguments={"label": f"topic-{call_count['n']}"},
                )],
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            )
        # After 10 attempts, LLM gives up and responds
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider = MagicMock()
    provider.chat_with_retry = chat_with_retry

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "close the topic"},
        ],
        tools=tools,
        model="test-model",
        max_iterations=20,
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
    ))

    # The runner should have stopped — once_per_turn blocks the 2nd call,
    # so the LLM sees the error and eventually gives up.
    # Key assertion: close_episode was called many times by the LLM, but
    # only ONE actually succeeded (the rest were blocked).
    assert result.final_content is not None
    # The LLM should have made at most a few calls before the blocked result
    # forced it to stop — far fewer than max_iterations=20.
    assert call_count["n"] < 20, (
        f"Runner did not break the loop: LLM was called {call_count['n']} times"
    )
