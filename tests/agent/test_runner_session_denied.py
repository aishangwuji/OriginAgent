"""Tests for session-scoped denied_tools persistence (Phase 1 of action-result causal chain).

Problem: When a tool is denied by PolicyDeniedError in a cron session, the next
cron turn (same session_key) re-attempts the same tool because:
  - turn-scoped state (once_per_turn, circuit breaker, idempotency keys) resets
  - session.messages window is limited (20 messages scanned by
    _scan_recent_policy_denials)
  - no structured "session_permanent=true" signal reaches the LLM

Fix: ``_run_tool_core`` now:
  1. Checks ``session.metadata["_denied_tools"]`` at entry — short-circuits
     with ``[SESSION_PERMANENTLY_DENIED]`` if the tool was previously denied.
  2. On PolicyDeniedError, writes the tool name into
     ``session.metadata["_denied_tools"]`` and prepends a structured
     ``[POLICY_DENIED retryable=false session_permanent=true]`` prefix to the
     LLM-visible payload.

This is the bottom-line defense (runner-level short-circuit) that works even
if the LLM ignores hints. Phase 2 (task_state tool) adds Agent-driven state
machine on top.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.registry import ToolRegistry
from OriginAgent.config.schema import AgentDefaults
from OriginAgent.providers.base import ToolCallRequest
from OriginAgent.security.policy import PolicyDeniedError
from OriginAgent.session.manager import Session, SessionManager


def _make_sessions_with_session(session_key: str) -> tuple[MagicMock, Session]:
    """Return a MagicMock SessionManager wired to a real Session object.

    The Session is a real dataclass instance so ``metadata`` is a real dict
    that survives across ``get_or_create`` calls.
    """
    session = Session(key=session_key)
    sessions = MagicMock(spec=SessionManager)
    sessions.get_or_create.return_value = session
    return sessions, session


class _PolicyDeniedTool(Tool):
    """Stub tool whose execute() always raises PolicyDeniedError."""

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "exec stub for testing"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"cmd": {"type": "string"}}}

    async def execute(self, **kwargs: Any) -> str:
        raise PolicyDeniedError(
            "Tool 'exec' is not allowed by the current capability snapshot",
            code="capability_denied",
            boundary="capability",
            policy_rule="capability_exec_denied",
        )


def _make_spec(sessions: Any | None = None, session_key: str | None = None):
    from OriginAgent.agent.runner import AgentRunSpec

    return AgentRunSpec(
        initial_messages=[],
        tools=_make_tools(),
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        sessions=sessions,
        session_key=session_key,
    )


def _make_tools() -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(_PolicyDeniedTool())
    return tools


def _make_runner():
    from OriginAgent.agent.runner import AgentRunner

    return AgentRunner(MagicMock())


# ─── Phase 1: session-scoped denied_tools persistence ─────────────────────


@pytest.mark.asyncio
async def test_policy_denied_writes_session_metadata() -> None:
    """First PolicyDeniedError writes tool name into session.metadata['_denied_tools']."""
    sessions, session = _make_sessions_with_session("cron:job-1")
    runner = _make_runner()
    spec = _make_spec(sessions=sessions, session_key="cron:job-1")

    tc = ToolCallRequest(id="c1", name="exec", arguments={"cmd": "ls"})
    result, event, _ = await runner._run_tool_core(spec, tc, {}, {}, set())

    assert event["status"] == "error"
    denied = session.metadata.get("_denied_tools")
    assert denied is not None
    assert "exec" in denied


@pytest.mark.asyncio
async def test_session_denied_short_circuits_subsequent_calls() -> None:
    """Second call to the same tool in the same session is short-circuited."""
    sessions, session = _make_sessions_with_session("cron:job-1")
    runner = _make_runner()
    spec = _make_spec(sessions=sessions, session_key="cron:job-1")

    # First call: PolicyDeniedError, writes to session.metadata
    tc1 = ToolCallRequest(id="c1", name="exec", arguments={"cmd": "ls"})
    await runner._run_tool_core(spec, tc1, {}, {}, set())

    # Second call: should be short-circuited without executing the tool
    tc2 = ToolCallRequest(id="c2", name="exec", arguments={"cmd": "rm -rf /"})
    result2, event2, _ = await runner._run_tool_core(spec, tc2, {}, {}, set())

    assert event2["status"] == "skipped"
    assert "session_denied" in event2.get("detail", "") or "SESSION_PERMANENTLY_DENIED" in result2


@pytest.mark.asyncio
async def test_policy_denied_payload_has_structured_prefix() -> None:
    """The LLM-visible payload includes a structured [POLICY_DENIED ...] prefix.

    The 'exec' tool triggers the prep_error path (capability_snapshot_required)
    because it's in _CAPABILITY_REQUIRED_TOOL_NAME_FALLBACK and no capability
    snapshot is set on the ToolRegistry. The stub's PolicyDeniedError from
    execute() is never reached — the denial happens at prepare_call stage.
    """
    sessions, _ = _make_sessions_with_session("cron:job-1")
    runner = _make_runner()
    spec = _make_spec(sessions=sessions, session_key="cron:job-1")

    tc = ToolCallRequest(id="c1", name="exec", arguments={"cmd": "ls"})
    result, _, _ = await runner._run_tool_core(spec, tc, {}, {}, set())

    assert "[POLICY_DENIED" in result
    assert "retryable=false" in result
    assert "session_permanent=true" in result
    # The actual policy_rule from the prep_error path is capability_snapshot_required
    # (not capability_exec_denied, which would come from execute() if reached).
    assert "capability_snapshot_required" in result


@pytest.mark.asyncio
async def test_session_denied_persists_across_runs() -> None:
    """denied_tools survives across run() calls because it lives in session.metadata."""
    sessions, session = _make_sessions_with_session("cron:job-1")
    runner = _make_runner()

    # First run: tool denied, writes to session.metadata
    spec1 = _make_spec(sessions=sessions, session_key="cron:job-1")
    tc1 = ToolCallRequest(id="c1", name="exec", arguments={"cmd": "ls"})
    await runner._run_tool_core(spec1, tc1, {}, {}, set())

    # Simulate a new cron turn: new run() call, same session_key
    # SessionManager.get_or_create returns the same Session object (same metadata)
    spec2 = _make_spec(sessions=sessions, session_key="cron:job-1")
    tc2 = ToolCallRequest(id="c2", name="exec", arguments={"cmd": "whoami"})
    result2, event2, _ = await runner._run_tool_core(spec2, tc2, {}, {}, set())

    # Should be short-circuited — denied_tools persisted across runs
    assert event2["status"] == "skipped"
    assert "exec" in session.metadata.get("_denied_tools", [])


@pytest.mark.asyncio
async def test_session_denied_not_shared_across_sessions() -> None:
    """denied_tools is scoped to session_key — different sessions don't interfere."""
    sessions1, session1 = _make_sessions_with_session("cron:job-A")
    sessions2, session2 = _make_sessions_with_session("cron:job-B")
    runner = _make_runner()

    # Deny exec in session A
    spec_a = _make_spec(sessions=sessions1, session_key="cron:job-A")
    tc_a = ToolCallRequest(id="c1", name="exec", arguments={"cmd": "ls"})
    await runner._run_tool_core(spec_a, tc_a, {}, {}, set())

    # Call exec in session B — should NOT be short-circuited
    spec_b = _make_spec(sessions=sessions2, session_key="cron:job-B")
    tc_b = ToolCallRequest(id="c2", name="exec", arguments={"cmd": "ls"})
    result_b, event_b, _ = await runner._run_tool_core(spec_b, tc_b, {}, {}, set())

    # session B's first call should NOT be short-circuited by session A's
    # denial — it gets a real PolicyDeniedError (status=error, not skipped).
    # Note: session B will ALSO have 'exec' in its _denied_tools after this
    # call (because exec is denied there too by the same capability rule),
    # but the key isolation property is that the FIRST call was not blocked
    # by session A's denial.
    assert event_b["status"] == "error"
    assert "exec" in session1.metadata.get("_denied_tools", [])


@pytest.mark.asyncio
async def test_no_sessions_no_crash() -> None:
    """When spec.sessions is None (dream/subagent paths), no crash, no short-circuit."""
    runner = _make_runner()
    spec = _make_spec(sessions=None, session_key=None)

    tc = ToolCallRequest(id="c1", name="exec", arguments={"cmd": "ls"})
    result, event, _ = await runner._run_tool_core(spec, tc, {}, {}, set())

    # Should still get the PolicyDeniedError (no short-circuit, no crash)
    assert event["status"] == "error"
    assert "[POLICY_DENIED" in result


@pytest.mark.asyncio
async def test_short_circuit_payload_includes_structured_prefix() -> None:
    """The short-circuit return value includes the structured prefix."""
    sessions, session = _make_sessions_with_session("cron:job-1")
    session.metadata["_denied_tools"] = ["exec"]  # pre-populate

    runner = _make_runner()
    spec = _make_spec(sessions=sessions, session_key="cron:job-1")

    tc = ToolCallRequest(id="c1", name="exec", arguments={"cmd": "ls"})
    result, event, _ = await runner._run_tool_core(spec, tc, {}, {}, set())

    assert event["status"] == "skipped"
    assert "[SESSION_PERMANENTLY_DENIED]" in result
    assert "exec" in result


@pytest.mark.asyncio
async def test_denied_tools_are_deduplicated() -> None:
    """Multiple denials of the same tool don't duplicate entries."""
    sessions, session = _make_sessions_with_session("cron:job-1")
    # Pre-populate with exec already denied
    session.metadata["_denied_tools"] = ["exec"]

    runner = _make_runner()
    # Need a spec where exec is NOT yet in denied_tools to trigger the
    # write path. Use a different tool that raises PolicyDeniedError.
    from OriginAgent.agent.tools.base import Tool as _T

    class _OtherDeniedTool(_T):
        @property
        def name(self) -> str:
            return "spawn"

        @property
        def description(self) -> str:
            return "spawn stub"

        @property
        def parameters(self) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs: Any) -> str:
            raise PolicyDeniedError(
                "spawn not allowed",
                code="capability_denied",
                boundary="capability",
                policy_rule="capability_spawn_denied",
            )

    tools = ToolRegistry()
    tools.register(_OtherDeniedTool())

    from OriginAgent.agent.runner import AgentRunSpec

    spec = AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        sessions=sessions,
        session_key="cron:job-1",
    )

    tc = ToolCallRequest(id="c1", name="spawn", arguments={})
    await runner._run_tool_core(spec, tc, {}, {}, set())

    denied = session.metadata.get("_denied_tools", [])
    assert "spawn" in denied
    assert denied.count("spawn") == 1  # no duplicate
    assert "exec" in denied  # pre-existing entry preserved


# ─── Path 2: PolicyDeniedError raised from tool.execute() ─────────────────
# This path is reached when the tool name is NOT in the capability-snapshot
# fallback list, so prepare_call passes, and the tool itself raises
# PolicyDeniedError from execute().


class _ExecuteDeniedTool(Tool):
    """Tool whose execute() raises PolicyDeniedError (not prep_error path).

    Name 'custom_exec_denied' is NOT in _CAPABILITY_REQUIRED_TOOL_NAME_FALLBACK,
    so prepare_call passes without a capability snapshot, and the denial
    happens inside execute().
    """

    @property
    def name(self) -> str:
        return "custom_exec_denied"

    @property
    def description(self) -> str:
        return "stub that raises PolicyDeniedError from execute()"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        raise PolicyDeniedError(
            "custom tool denied from execute path",
            code="capability_denied",
            boundary="capability",
            policy_rule="capability_custom_denied",
        )


def _make_execute_denied_tools() -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(_ExecuteDeniedTool())
    return tools


@pytest.mark.asyncio
async def test_execute_path_denied_writes_session_metadata() -> None:
    """PolicyDeniedError from execute() persists tool to session.metadata."""
    sessions, session = _make_sessions_with_session("cron:job-2")
    runner = _make_runner()
    from OriginAgent.agent.runner import AgentRunSpec

    spec = AgentRunSpec(
        initial_messages=[],
        tools=_make_execute_denied_tools(),
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        sessions=sessions,
        session_key="cron:job-2",
    )

    tc = ToolCallRequest(id="c1", name="custom_exec_denied", arguments={})
    result, event, _ = await runner._run_tool_core(spec, tc, {}, {}, set())

    assert event["status"] == "error"
    assert "custom_exec_denied" in session.metadata.get("_denied_tools", [])
    assert "[POLICY_DENIED" in result
    assert "capability_custom_denied" in result
    assert "code=capability_denied" in result  # exc.code propagated to prefix


@pytest.mark.asyncio
async def test_execute_path_short_circuits_subsequent_calls() -> None:
    """Second call to execute-path-denied tool is short-circuited."""
    sessions, _ = _make_sessions_with_session("cron:job-2")
    runner = _make_runner()
    from OriginAgent.agent.runner import AgentRunSpec

    spec = AgentRunSpec(
        initial_messages=[],
        tools=_make_execute_denied_tools(),
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        sessions=sessions,
        session_key="cron:job-2",
    )

    tc1 = ToolCallRequest(id="c1", name="custom_exec_denied", arguments={})
    await runner._run_tool_core(spec, tc1, {}, {}, set())

    tc2 = ToolCallRequest(id="c2", name="custom_exec_denied", arguments={})
    result2, event2, _ = await runner._run_tool_core(spec, tc2, {}, {}, set())

    assert event2["status"] == "skipped"
    assert "[SESSION_PERMANENTLY_DENIED]" in result2


# ─── Path 3: String "Error:" result from tool.execute() ───────────────────
# This is the legacy path where tools return error strings instead of raising.


class _StringDeniedTool(Tool):
    """Tool whose execute() returns a policy-denial string."""

    @property
    def name(self) -> str:
        return "custom_string_denied"

    @property
    def description(self) -> str:
        return "stub that returns a policy-denial string"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        # Text must match a capability_* rule in policy_rule_from_error_text
        # so that _persist_session_denied_tool persists it. "requires an
        # explicit capability snapshot" maps to "capability_snapshot_required".
        return (
            "Error: Tool 'custom_string_denied' requires an explicit "
            "capability snapshot"
        )


def _make_string_denied_tools() -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(_StringDeniedTool())
    return tools


@pytest.mark.asyncio
async def test_string_result_path_denied_writes_session_metadata() -> None:
    """String 'Error:' result with policy-denial text persists to session.metadata."""
    sessions, session = _make_sessions_with_session("cron:job-3")
    runner = _make_runner()
    from OriginAgent.agent.runner import AgentRunSpec

    spec = AgentRunSpec(
        initial_messages=[],
        tools=_make_string_denied_tools(),
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        sessions=sessions,
        session_key="cron:job-3",
    )

    tc = ToolCallRequest(id="c1", name="custom_string_denied", arguments={})
    result, event, _ = await runner._run_tool_core(spec, tc, {}, {}, set())

    assert event["status"] == "error"
    assert "custom_string_denied" in session.metadata.get("_denied_tools", [])
    assert "[POLICY_DENIED" in result
    # policy_rule_from_error_text maps "requires an explicit capability snapshot"
    # to "capability_snapshot_required" — verify the prefix carries the inferred rule.
    assert "capability_snapshot_required" in result


@pytest.mark.asyncio
async def test_string_result_path_short_circuits_subsequent_calls() -> None:
    """Second call to string-path-denied tool is short-circuited."""
    sessions, _ = _make_sessions_with_session("cron:job-3")
    runner = _make_runner()
    from OriginAgent.agent.runner import AgentRunSpec

    spec = AgentRunSpec(
        initial_messages=[],
        tools=_make_string_denied_tools(),
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        sessions=sessions,
        session_key="cron:job-3",
    )

    tc1 = ToolCallRequest(id="c1", name="custom_string_denied", arguments={})
    await runner._run_tool_core(spec, tc1, {}, {}, set())

    tc2 = ToolCallRequest(id="c2", name="custom_string_denied", arguments={})
    result2, event2, _ = await runner._run_tool_core(spec, tc2, {}, {}, set())

    assert event2["status"] == "skipped"
    assert "[SESSION_PERMANENTLY_DENIED]" in result2


# ─── Non-capability denials must NOT be persisted ─────────────────────────


class _SsrfDeniedTool(Tool):
    """Tool whose execute() returns an SSRF denial string.

    SSRF denials are per-URL, not session-permanent. They must NOT be
    persisted to _denied_tools (otherwise all future web_fetch calls
    would be blocked, even for safe URLs).
    """

    @property
    def name(self) -> str:
        return "custom_ssrf_denied"

    @property
    def description(self) -> str:
        return "stub that returns an SSRF denial"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return (
            "Error: internal/private url detected. This is a non-bypassable "
            "security boundary."
        )


@pytest.mark.asyncio
async def test_ssrf_denial_not_persisted_to_session() -> None:
    """SSRF denials are per-URL and must NOT be persisted to _denied_tools."""
    sessions, session = _make_sessions_with_session("cron:job-4")
    runner = _make_runner()
    from OriginAgent.agent.runner import AgentRunSpec

    tools = ToolRegistry()
    tools.register(_SsrfDeniedTool())
    spec = AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        sessions=sessions,
        session_key="cron:job-4",
    )

    tc = ToolCallRequest(id="c1", name="custom_ssrf_denied", arguments={})
    result, event, _ = await runner._run_tool_core(spec, tc, {}, {}, set())

    # The tool should still be denied (error status)...
    assert event["status"] == "error"
    # ...but NOT persisted to _denied_tools (SSRF is per-URL, not session-permanent)
    assert "custom_ssrf_denied" not in session.metadata.get("_denied_tools", [])
    # SSRF goes through _classify_violation which returns its own specialized
    # soft payload (with detailed SSRF guidance) WITHOUT the [POLICY_DENIED]
    # prefix — this is by design. The structured prefix is only added when
    # _classify_violation returns None (passes through).
    assert "non-bypassable security boundary" in result
