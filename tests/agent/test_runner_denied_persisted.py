"""Tests for extended ``denied.persisted`` coverage (Task 9 of fix-cron-runtime-and-context-gaps).

Problem: ``_persist_session_denied_tool`` only matched ``capability_*`` policy
rules, missing ``*_grant_required`` and ``*_requires_grant`` rules. As a result,
when the ``message`` tool was denied with
``policy_rule=message_cross_target_grant_required`` (cron trying to send to a
non-delivery-target channel) or when ``cron`` was denied with
``policy_rule=cron_high_capability_requires_grant``, no
``event.tool.denied.persisted`` was emitted and the tool was NOT written into
``session.metadata["_denied_tools"]``. The next cron turn would re-attempt the
same denied tool, producing repeated ``tool.complete status=error`` log lines
without the session-permanent short-circuit kicking in.

Fix: Expand the filter in ``_persist_session_denied_tool`` so that
``capability_*``, ``*_grant_required`` and ``*_requires_grant`` rules all
trigger persistence. (Session-permanent ``*_denied`` rules such as
``capability_*_denied`` are already covered by the ``capability_*`` prefix;
per-target ``*_denied`` rules like ``ssrf_denied`` / ``symlink_path_denied``
remain correctly excluded.) Pure string errors without a ``policy_rule``
still do not trigger persistence.

These tests verify the fix by constructing ``PolicyDeniedError`` instances with
the previously-missed policy rules and asserting both the
``event.tool.denied.persisted`` log emission and the ``_denied_tools`` mutation.
A negative test confirms that a generic ``"Error: foo"`` string (no policy_rule)
still does NOT trigger persistence.

Loguru is used directly (matching the pattern in ``test_action_summary.py``)
because ``runner.py`` uses ``loguru.logger``, not the stdlib ``logging``
module, so pytest's ``caplog`` fixture cannot capture these records.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from loguru import logger as loguru_logger

from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.registry import ToolRegistry
from OriginAgent.config.schema import AgentDefaults
from OriginAgent.providers.base import ToolCallRequest
from OriginAgent.security.policy import PolicyDeniedError
from OriginAgent.session.manager import Session, SessionManager


def _make_sessions_with_session(session_key: str) -> tuple[MagicMock, Session]:
    """Return a MagicMock SessionManager wired to a real Session object."""
    session = Session(key=session_key)
    sessions = MagicMock(spec=SessionManager)
    sessions.get_or_create.return_value = session
    return sessions, session


def _make_runner():
    from OriginAgent.agent.runner import AgentRunner

    return AgentRunner(MagicMock())


def _make_spec_with_tools(tools: ToolRegistry, sessions: Any, session_key: str):
    from OriginAgent.agent.runner import AgentRunSpec

    return AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        sessions=sessions,
        session_key=session_key,
    )


def _persisted_log_messages(records: list[str]) -> list[str]:
    return [r for r in records if "event.tool.denied.persisted" in r]


# ─── Test 1: message_cross_target_grant_required ───────────────────────────


class _MessageCrossTargetGrantRequiredTool(Tool):
    """Tool whose execute() raises PolicyDeniedError with
    policy_rule=message_cross_target_grant_required.

    Name ``message_stub`` is intentionally NOT in
    ``_CAPABILITY_REQUIRED_TOOL_NAME_FALLBACK`` so that prepare_call passes and
    the denial reaches the execute() exception path.
    """

    @property
    def name(self) -> str:
        return "message_stub"

    @property
    def description(self) -> str:
        return "stub that raises message_cross_target_grant_required"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"content": {"type": "string"}}}

    async def execute(self, **kwargs: Any) -> str:
        raise PolicyDeniedError(
            "message to non-delivery-target channel requires explicit grant",
            code="cross_target_denied",
            boundary="capability",
            policy_rule="message_cross_target_grant_required",
        )


@pytest.mark.asyncio
async def test_denied_persisted_for_message_cross_target_grant_required() -> None:
    """policy_rule=message_cross_target_grant_required triggers denied.persisted."""
    sessions, session = _make_sessions_with_session("cron:job-msg-grant")
    runner = _make_runner()

    tools = ToolRegistry()
    tools.register(_MessageCrossTargetGrantRequiredTool())
    spec = _make_spec_with_tools(tools, sessions, "cron:job-msg-grant")

    records: list[str] = []
    handler_id = loguru_logger.add(lambda m: records.append(str(m)), level="INFO")
    try:
        tc = ToolCallRequest(id="c1", name="message_stub", arguments={"content": "hi"})
        result, event, _ = await runner._run_tool_core(spec, tc, {}, {}, set())
    finally:
        loguru_logger.remove(handler_id)

    assert event["status"] == "error"
    # event.tool.denied.persisted log emitted with matching policy_rule
    persisted_logs = _persisted_log_messages(records)
    assert len(persisted_logs) == 1, (
        f"expected 1 denied.persisted log, got {len(persisted_logs)}; "
        f"records={records}"
    )
    assert "message_cross_target_grant_required" in persisted_logs[0]
    assert "message_stub" in persisted_logs[0]
    # session.metadata updated so subsequent calls are short-circuited
    assert "message_stub" in session.metadata.get("_denied_tools", [])


# ─── Test 2: cron_high_capability_requires_grant ───────────────────────────


class _CronHighCapabilityRequiresGrantTool(Tool):
    """Tool whose execute() raises PolicyDeniedError with
    policy_rule=cron_high_capability_requires_grant.

    Name ``cron_stub`` is intentionally NOT in
    ``_CAPABILITY_REQUIRED_TOOL_NAME_FALLBACK`` so that prepare_call passes and
    the denial reaches the execute() exception path.
    """

    @property
    def name(self) -> str:
        return "cron_stub"

    @property
    def description(self) -> str:
        return "stub that raises cron_high_capability_requires_grant"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        raise PolicyDeniedError(
            "cron high-capability operation requires explicit user grant",
            code="capability_denied",
            boundary="capability",
            policy_rule="cron_high_capability_requires_grant",
        )


@pytest.mark.asyncio
async def test_denied_persisted_for_cron_high_capability_requires_grant() -> None:
    """policy_rule=cron_high_capability_requires_grant triggers denied.persisted."""
    sessions, session = _make_sessions_with_session("cron:job-cron-grant")
    runner = _make_runner()

    tools = ToolRegistry()
    tools.register(_CronHighCapabilityRequiresGrantTool())
    spec = _make_spec_with_tools(tools, sessions, "cron:job-cron-grant")

    records: list[str] = []
    handler_id = loguru_logger.add(lambda m: records.append(str(m)), level="INFO")
    try:
        tc = ToolCallRequest(id="c1", name="cron_stub", arguments={})
        result, event, _ = await runner._run_tool_core(spec, tc, {}, {}, set())
    finally:
        loguru_logger.remove(handler_id)

    assert event["status"] == "error"
    persisted_logs = _persisted_log_messages(records)
    assert len(persisted_logs) == 1, (
        f"expected 1 denied.persisted log, got {len(persisted_logs)}; "
        f"records={records}"
    )
    assert "cron_high_capability_requires_grant" in persisted_logs[0]
    assert "cron_stub" in persisted_logs[0]
    assert "cron_stub" in session.metadata.get("_denied_tools", [])


# ─── Test 3: generic error string (no policy_rule) ─────────────────────────


class _GenericErrorTool(Tool):
    """Tool whose execute() returns a generic 'Error: foo' string.

    The string does not match any pattern in ``policy_rule_from_error_text``,
    so ``policy_rule`` is None and ``_persist_session_denied_tool`` must
    short-circuit without emitting ``denied.persisted``.
    """

    @property
    def name(self) -> str:
        return "generic_error_stub"

    @property
    def description(self) -> str:
        return "stub that returns a generic error string"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "Error: foo"


@pytest.mark.asyncio
async def test_denied_persisted_not_emitted_for_generic_error() -> None:
    """Generic 'Error: foo' (no policy_rule) does NOT trigger denied.persisted."""
    sessions, session = _make_sessions_with_session("cron:job-generic-err")
    runner = _make_runner()

    tools = ToolRegistry()
    tools.register(_GenericErrorTool())
    spec = _make_spec_with_tools(tools, sessions, "cron:job-generic-err")

    records: list[str] = []
    handler_id = loguru_logger.add(lambda m: records.append(str(m)), level="INFO")
    try:
        tc = ToolCallRequest(id="c1", name="generic_error_stub", arguments={})
        result, event, _ = await runner._run_tool_core(spec, tc, {}, {}, set())
    finally:
        loguru_logger.remove(handler_id)

    # tool.complete status=error happens at the wrapper layer (not called here),
    # so we only assert that denied.persisted is NOT emitted by _run_tool_core.
    assert event["status"] == "error"
    persisted_logs = _persisted_log_messages(records)
    assert len(persisted_logs) == 0, (
        f"expected 0 denied.persisted logs, got {len(persisted_logs)}; "
        f"records={records}"
    )
    # session.metadata NOT updated
    assert "generic_error_stub" not in session.metadata.get("_denied_tools", [])
