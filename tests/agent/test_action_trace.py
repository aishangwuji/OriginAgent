"""Tests for action_trace — system-side automatic capture of action→result.

This module is the data layer of the action-result causal chain:
- runner records every tool call + result to session.metadata["_action_trace"]
- evaluate_action tool reads this trace to let the Agent assess causality
- task_state transitions reference action_id to form the full chain

Design (rule 34: verification first; rule 32: minimal; rule 5: re-read):
- record_action_trace writes to session.metadata (no stale cache)
- params are summarized with sensitive-field redaction (rule 18)
- FIFO cap prevents unbounded growth (rule 22)
- denied detection covers both keyword and event.status paths
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.action_trace import (
    _MAX_TRACE,
    _METADATA_KEY,
    _detect_denied,
    _summarize_params,
    _summarize_result,
    get_action_trace,
    get_recent_actions,
    record_action_trace,
)
from OriginAgent.session.manager import Session, SessionManager


# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_spec(session_key: str = "cli:test") -> tuple[SimpleNamespace, Session]:
    """Build a minimal spec-like object with sessions + session_key."""
    session = Session(key=session_key)
    sessions = MagicMock(spec=SessionManager)
    sessions.get_or_create.return_value = session
    spec = SimpleNamespace(sessions=sessions, session_key=session_key)
    return spec, session


def _make_tool_call(
    name: str = "read_file",
    call_id: str = "call_abc123",
    arguments: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        name=name,
        arguments=arguments or {},
    )


# ─── _summarize_params ─────────────────────────────────────────────────────


def test_summarize_params_dict() -> None:
    """Dict params are summarized as key=value pairs."""
    summary = _summarize_params({"path": "/foo/bar", "encoding": "utf-8"})
    assert "path=/foo/bar" in summary
    assert "encoding=utf-8" in summary


def test_summarize_params_redacts_sensitive_keys() -> None:
    """Fields matching sensitive patterns are redacted (rule 18).

    Non-sensitive keys are prioritized, so 'path' survives the 3-key cap.
    Among the sensitive keys, those that fit the cap appear as <redacted>.
    The critical assertion: raw secret values NEVER appear in the summary.
    """
    summary = _summarize_params({
        "api_key": "sk-1234567890",
        "token": "Bearer abc",
        "password": "secret123",
        "path": "/safe",
    })
    # Non-sensitive key is always included (prioritized)
    assert "path=/safe" in summary
    # At least one sensitive key is redacted (the first one that fits the cap)
    assert "<redacted>" in summary
    # Raw secret values must NEVER appear (rule 18 — the core invariant)
    assert "sk-1234567890" not in summary
    assert "Bearer abc" not in summary
    assert "secret123" not in summary


def test_summarize_params_truncates_long_values() -> None:
    """Long individual values are truncated."""
    long_val = "x" * 200
    summary = _summarize_params({"data": long_val})
    assert "..." in summary
    assert len(summary) <= 200 + 50  # summary cap + some overhead


def test_summarize_params_limits_to_three_keys() -> None:
    """Only first 3 keys are included to keep summary compact."""
    summary = _summarize_params({
        "a": "1", "b": "2", "c": "3", "d": "4", "e": "5",
    })
    assert "a=1" in summary
    assert "b=2" in summary
    assert "c=3" in summary
    assert "d=4" not in summary
    assert "e=5" not in summary


def test_summarize_params_non_dict() -> None:
    """Non-dict params are stringified."""
    assert _summarize_params("just a string") == "just a string"
    assert _summarize_params(None) == "None"


# ─── _summarize_result ─────────────────────────────────────────────────────


def test_summarize_result_string() -> None:
    """String results are passed through (with truncation)."""
    assert _summarize_result("hello world") == "hello world"


def test_summarize_result_truncates_long_string() -> None:
    """Long results are truncated with ellipsis."""
    long_result = "x" * 500
    summary = _summarize_result(long_result)
    assert summary.endswith("...")
    assert len(summary) <= 203  # 200 + "..."


def test_summarize_result_list_blocks() -> None:
    """Content block lists have their text extracted."""
    result = [
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
    ]
    summary = _summarize_result(result)
    assert "first" in summary
    assert "second" in summary


def test_summarize_result_none() -> None:
    """None results produce a placeholder."""
    assert _summarize_result(None) == "<none>"


def test_summarize_result_dict_with_text() -> None:
    """Dict results with a 'text' key extract that field."""
    summary = _summarize_result({"text": "extracted content", "other": "ignored"})
    assert "extracted content" in summary


# ─── _detect_denied ────────────────────────────────────────────────────────


def test_detect_denied_by_keyword() -> None:
    """Denied is detected via result keywords."""
    assert _detect_denied("Error: tool denied by policy", {"status": "ok"}) is True
    assert _detect_denied("Permission denied", {"status": "ok"}) is True
    assert _detect_denied("not allowed in this session", {"status": "ok"}) is True


def test_detect_denied_by_event_status() -> None:
    """Denied is detected via event.status field."""
    assert _detect_denied("some result", {"status": "denied"}) is True


def test_detect_denied_false_for_normal_result() -> None:
    """Normal results are not flagged as denied."""
    assert _detect_denied("success: file read", {"status": "ok"}) is False
    assert _detect_denied("", {"status": "ok"}) is False


# ─── record_action_trace ───────────────────────────────────────────────────


def test_record_persists_to_session_metadata() -> None:
    """Action trace is written to session.metadata['_action_trace']."""
    spec, session = _make_spec()
    tc = _make_tool_call(name="read_file", arguments={"path": "/foo"})
    action_id = record_action_trace(
        spec, tc, result="file contents",
        event={"status": "ok"}, error=None, iteration=1,
    )
    trace = session.metadata.get(_METADATA_KEY)
    assert trace is not None
    assert len(trace) == 1
    entry = trace[0]
    assert entry["action_id"] == action_id
    assert entry["tool_name"] == "read_file"
    assert entry["params_summary"] == "path=/foo"
    assert entry["result_summary"] == "file contents"
    assert entry["success"] is True
    assert entry["denied"] is False
    assert entry["iteration"] == 1
    assert "ts" in entry


def test_record_returns_action_id() -> None:
    """Returns the tool_call.id as action_id."""
    spec, _ = _make_spec()
    tc = _make_tool_call(call_id="call_xyz789")
    action_id = record_action_trace(
        spec, tc, result="ok",
        event={"status": "ok"}, error=None, iteration=0,
    )
    assert action_id == "call_xyz789"


def test_record_no_sessions_returns_none() -> None:
    """When spec.sessions is None, returns None without crashing."""
    spec = SimpleNamespace(sessions=None, session_key="cli:test")
    tc = _make_tool_call()
    action_id = record_action_trace(
        spec, tc, result="ok",
        event={"status": "ok"}, error=None, iteration=0,
    )
    assert action_id is None


def test_record_no_session_key_returns_none() -> None:
    """When session_key is None, returns None."""
    sessions = MagicMock(spec=SessionManager)
    spec = SimpleNamespace(sessions=sessions, session_key=None)
    tc = _make_tool_call()
    action_id = record_action_trace(
        spec, tc, result="ok",
        event={"status": "ok"}, error=None, iteration=0,
    )
    assert action_id is None


def test_record_with_error_marks_unsuccessful() -> None:
    """When error is not None, success is False."""
    spec, session = _make_spec()
    tc = _make_tool_call()
    record_action_trace(
        spec, tc, result="",
        event={"status": "error"}, error=RuntimeError("boom"),
        iteration=2,
    )
    trace = session.metadata[_METADATA_KEY]
    assert trace[0]["success"] is False


def test_record_denied_tool_detected() -> None:
    """Denied tool calls are flagged."""
    spec, session = _make_spec()
    tc = _make_tool_call(name="exec", arguments={"command": "rm -rf /"})
    record_action_trace(
        spec, tc, result="Error: exec denied by capability policy",
        event={"status": "ok"}, error=None, iteration=1,
    )
    trace = session.metadata[_METADATA_KEY]
    assert trace[0]["denied"] is True
    assert trace[0]["success"] is True  # status was ok, just denied


def test_record_generates_fallback_id_when_tool_call_id_missing() -> None:
    """When tool_call.id is None, a fallback action_id is generated."""
    spec, _ = _make_spec()
    tc = SimpleNamespace(id=None, name="read_file", arguments={})
    action_id = record_action_trace(
        spec, tc, result="ok",
        event={"status": "ok"}, error=None, iteration=0,
    )
    assert action_id is not None
    assert action_id.startswith("act_")


def test_action_trace_fifo_cap() -> None:
    """Trace is capped at _MAX_TRACE entries (FIFO)."""
    spec, session = _make_spec()
    tc = _make_tool_call()
    for i in range(_MAX_TRACE + 10):
        record_action_trace(
            spec, tc, result=f"result-{i}",
            event={"status": "ok"}, error=None, iteration=i,
        )
    trace = session.metadata[_METADATA_KEY]
    assert len(trace) == _MAX_TRACE
    # Oldest entries dropped, newest retained
    assert trace[0]["result_summary"] == f"result-{10}"
    assert trace[-1]["result_summary"] == f"result-{_MAX_TRACE + 9}"


# ─── get_action_trace / get_recent_actions ────────────────────────────────


def test_get_action_trace_returns_copy() -> None:
    """get_action_trace returns a copy (not the live list)."""
    spec, session = _make_spec()
    tc = _make_tool_call()
    record_action_trace(spec, tc, "r1", {"status": "ok"}, None, 0)
    trace = get_action_trace(session)
    trace.append({"injected": True})
    # Original session metadata should not be modified
    assert len(session.metadata[_METADATA_KEY]) == 1


def test_get_action_trace_empty() -> None:
    """Empty trace returns empty list, not None."""
    _, session = _make_spec()
    assert get_action_trace(session) == []


def test_get_recent_actions_returns_last_n() -> None:
    """get_recent_actions returns the most recent N entries."""
    spec, session = _make_spec()
    tc = _make_tool_call()
    for i in range(10):
        record_action_trace(
            spec, tc, result=f"r{i}",
            event={"status": "ok"}, error=None, iteration=i,
        )
    recent = get_recent_actions(session, limit=3)
    assert len(recent) == 3
    assert recent[0]["result_summary"] == "r7"
    assert recent[2]["result_summary"] == "r9"


def test_get_recent_actions_default_limit() -> None:
    """Default limit is 5."""
    spec, session = _make_spec()
    tc = _make_tool_call()
    for i in range(10):
        record_action_trace(
            spec, tc, result=f"r{i}",
            event={"status": "ok"}, error=None, iteration=i,
        )
    recent = get_recent_actions(session)
    assert len(recent) == 5


# ─── Integration: denied detection via event ───────────────────────────────


def test_record_with_event_status_denied() -> None:
    """When event.status='denied', the entry's denied flag is True."""
    spec, session = _make_spec()
    tc = _make_tool_call(name="exec")
    record_action_trace(
        spec, tc, result="",
        event={"status": "denied"}, error=None, iteration=0,
    )
    trace = session.metadata[_METADATA_KEY]
    assert trace[0]["denied"] is True
