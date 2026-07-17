"""Tests for runner integration with action_trace (Phase D of causal chain).

Verifies that ``AgentRunner.run()`` automatically captures each tool call's
action→result tuple into ``session.metadata["_action_trace"]`` after the
tool batch completes. This is the **data layer** of the causal chain — the
Agent does NOT need to call any tool for this data to be collected.

Test strategy:
- Use a real ``Session`` + mock ``SessionManager`` (same pattern as
  ``test_runner_session_denied.py``).
- Use stub ``Tool`` classes with names NOT in
  ``_CAPABILITY_REQUIRED_TOOL_NAME_FALLBACK`` so they bypass capability
  snapshot checks and actually execute their ``execute()`` methods.
- Mock the provider to emit one tool call, then a final response.
- After ``runner.run()`` completes, inspect ``session.metadata["_action_trace"]``.

This is the BDI/ACT-R/Soar/EPIC percept-capture verification: the
architecture must automatically record what the Agent perceived (tool
result) without relying on the Agent's voluntary reporting.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.registry import ToolRegistry
from OriginAgent.config.schema import AgentDefaults
from OriginAgent.providers.base import LLMResponse, ToolCallRequest
from OriginAgent.security.policy import PolicyDeniedError
from OriginAgent.session.manager import Session, SessionManager

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


def _make_sessions_with_session(session_key: str) -> tuple[MagicMock, Session]:
    """Return a MagicMock SessionManager wired to a real Session."""
    session = Session(key=session_key)
    sessions = MagicMock(spec=SessionManager)
    sessions.get_or_create.return_value = session
    return sessions, session


# ─── Stub tools ────────────────────────────────────────────────────────────
# Names deliberately chosen to NOT be in _CAPABILITY_REQUIRED_TOOL_NAME_FALLBACK
# so they bypass capability snapshot checks and execute their execute() methods.


class _SuccessTool(Tool):
    """Stub tool that returns a known success result."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "echo back the input"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, **kwargs: Any) -> str:
        return f"echo: {kwargs.get('text', '')}"


class _ErrorTool(Tool):
    """Stub tool that raises a runtime error."""

    @property
    def name(self) -> str:
        return "fail_test"

    @property
    def description(self) -> str:
        return "always fails"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, **kwargs: Any) -> str:
        raise RuntimeError("simulated failure")


class _PolicyDeniedStubTool(Tool):
    """Stub tool that raises PolicyDeniedError in execute().

    Uses a non-capability-required name so it reaches execute() before
    the denial is raised — this tests the PolicyDeniedError catch path
    in _run_tool_core, not the capability-snapshot pre-check path.
    """

    @property
    def name(self) -> str:
        return "deny_test"

    @property
    def description(self) -> str:
        return "always denied"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"cmd": {"type": "string"}}}

    async def execute(self, **kwargs: Any) -> str:
        raise PolicyDeniedError(
            "Tool 'deny_test' is not allowed",
            code="capability_denied",
            boundary="capability",
            policy_rule="test_denied",
        )


def _make_tools(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _make_provider_with_tool_call(
    tool_call: ToolCallRequest,
    final_content: str = "done",
):
    """Return a MagicMock provider that emits one tool call, then a final response."""
    provider = MagicMock()
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="thinking",
                tool_calls=[tool_call],
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            )
        return LLMResponse(content=final_content, tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    return provider


def _make_spec(
    tools: ToolRegistry,
    sessions: Any | None = None,
    session_key: str | None = None,
):
    from OriginAgent.agent.runner import AgentRunSpec

    return AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "do task"},
        ],
        tools=tools,
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        sessions=sessions,
        session_key=session_key,
    )


def _make_runner(provider: Any):
    from OriginAgent.agent.runner import AgentRunner

    return AgentRunner(provider)


# ─── Phase D2: action_trace automatic capture ──────────────────────────────


@pytest.mark.asyncio
async def test_action_trace_recorded_on_tool_success() -> None:
    """After a successful tool call, action_trace entry is persisted to session.metadata."""
    sessions, session = _make_sessions_with_session("cron:job-1")
    provider = _make_provider_with_tool_call(
        ToolCallRequest(id="call_abc", name="echo", arguments={"text": "hello"})
    )
    tools = _make_tools(_SuccessTool())
    runner = _make_runner(provider)
    spec = _make_spec(tools=tools, sessions=sessions, session_key="cron:job-1")

    await runner.run(spec)

    trace = session.metadata.get("_action_trace")
    assert trace is not None, "action_trace should be recorded after tool execution"
    assert len(trace) == 1
    entry = trace[0]
    assert entry["action_id"] == "call_abc"
    assert entry["tool_name"] == "echo"
    assert entry["success"] is True
    assert entry["denied"] is False
    assert "text" in entry["params_summary"]
    assert "echo: hello" in entry["result_summary"]
    assert "ts" in entry
    assert "iteration" in entry


@pytest.mark.asyncio
async def test_action_trace_records_failure_on_tool_error() -> None:
    """When a tool raises an exception, action_trace records success=False."""
    sessions, session = _make_sessions_with_session("cron:job-2")
    provider = _make_provider_with_tool_call(
        ToolCallRequest(id="call_err", name="fail_test", arguments={"path": "missing"})
    )
    tools = _make_tools(_ErrorTool())
    runner = _make_runner(provider)
    spec = _make_spec(tools=tools, sessions=sessions, session_key="cron:job-2")

    await runner.run(spec)

    trace = session.metadata.get("_action_trace")
    assert trace is not None
    assert len(trace) == 1
    entry = trace[0]
    assert entry["tool_name"] == "fail_test"
    assert entry["success"] is False
    assert entry["denied"] is False  # Error, not denial


@pytest.mark.asyncio
async def test_action_trace_records_denied_on_policy_denial() -> None:
    """When a tool raises PolicyDeniedError, action_trace records denied=True."""
    sessions, session = _make_sessions_with_session("cron:job-3")
    provider = _make_provider_with_tool_call(
        ToolCallRequest(id="call_deny", name="deny_test", arguments={"cmd": "rm"})
    )
    tools = _make_tools(_PolicyDeniedStubTool())
    runner = _make_runner(provider)
    spec = _make_spec(tools=tools, sessions=sessions, session_key="cron:job-3")

    await runner.run(spec)

    trace = session.metadata.get("_action_trace")
    assert trace is not None
    assert len(trace) == 1
    entry = trace[0]
    assert entry["tool_name"] == "deny_test"
    assert entry["success"] is False
    assert entry["denied"] is True


@pytest.mark.asyncio
async def test_no_action_trace_when_no_session() -> None:
    """When sessions/session_key are None, no trace is recorded (no crash)."""
    provider = _make_provider_with_tool_call(
        ToolCallRequest(id="call_no_session", name="echo", arguments={"text": "hi"})
    )
    tools = _make_tools(_SuccessTool())
    runner = _make_runner(provider)
    # No sessions, no session_key — dream/subagent path
    spec = _make_spec(tools=tools, sessions=None, session_key=None)

    # Should not crash
    await runner.run(spec)

    # Nothing to assert about session.metadata since there's no session.
    # The test passes if run() completes without raising.


@pytest.mark.asyncio
async def test_action_trace_accumulates_across_iterations() -> None:
    """Multiple tool calls in sequence each produce a trace entry."""
    sessions, session = _make_sessions_with_session("cron:job-4")
    provider = MagicMock()
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="first",
                tool_calls=[
                    ToolCallRequest(id="call_1", name="echo", arguments={"text": "a"})
                ],
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            )
        if call_count["n"] == 2:
            return LLMResponse(
                content="second",
                tool_calls=[
                    ToolCallRequest(id="call_2", name="echo", arguments={"text": "b"})
                ],
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            )
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    tools = _make_tools(_SuccessTool())
    runner = _make_runner(provider)
    spec = _make_spec(tools=tools, sessions=sessions, session_key="cron:job-4")

    await runner.run(spec)

    trace = session.metadata.get("_action_trace")
    assert trace is not None
    assert len(trace) == 2
    assert trace[0]["action_id"] == "call_1"
    assert trace[1]["action_id"] == "call_2"
    # Iteration should be recorded (0-indexed)
    assert trace[0]["iteration"] == 0
    assert trace[1]["iteration"] == 1


@pytest.mark.asyncio
async def test_action_trace_does_not_crash_runner_on_failure() -> None:
    """If action_trace recording raises, the runner must not crash."""
    sessions, session = _make_sessions_with_session("cron:job-5")
    provider = _make_provider_with_tool_call(
        ToolCallRequest(id="call_crash", name="echo", arguments={"text": "x"})
    )
    tools = _make_tools(_SuccessTool())
    runner = _make_runner(provider)
    spec = _make_spec(tools=tools, sessions=sessions, session_key="cron:job-5")

    # Patch record_action_trace to raise — runner must catch and continue
    with patch(
        "OriginAgent.agent.runner.record_action_trace",
        side_effect=RuntimeError("simulated trace failure"),
    ):
        result = await runner.run(spec)

    assert result.final_content == "done"
    # The trace may or may not be recorded depending on where the exception
    # was caught — the key assertion is that run() completed successfully.
