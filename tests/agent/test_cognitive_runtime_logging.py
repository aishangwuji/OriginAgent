"""Tests for log_event attrs enrichment in AgentCognitiveRuntime.

验证 `cognitive.pass.skipped` 事件包含以下 attrs：
- session_key
- reason
- active_task_count
- running_subagents
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from OriginAgent.agent.agent_cognitive_runtime import AgentCognitiveRuntime, CognitiveRuntimeDeps


def _make_runtime_with_active_tasks_skip() -> AgentCognitiveRuntime:
    """构造一个 AgentCognitiveRuntime，其 eligibility_for_cognition 会因
    active_task_count > 0 返回 (False, "active_tasks")，从而触发
    cognitive.pass.skipped 分支。"""
    cognitive_loop = SimpleNamespace(
        config=SimpleNamespace(enabled=True, interval_seconds=1),
    )
    cognitive_scheduler = SimpleNamespace(start=lambda: "disabled")
    # active_intents.eligibility_for_cognition 在 active_task_count>0 时
    # 返回 (False, "active_tasks")，复用真实业务规则语义
    active_intents = SimpleNamespace(
        eligibility_for_cognition=lambda _sk, **_kw: (False, "active_tasks"),
    )
    deps = CognitiveRuntimeDeps(
        cognitive_loop=cognitive_loop,
        cognitive_scheduler=cognitive_scheduler,
        bus=SimpleNamespace(),
        sessions=SimpleNamespace(),
        active_intents=active_intents,
        reminder_store=SimpleNamespace(),
        working_memory=SimpleNamespace(),
        cognitive_audit=SimpleNamespace(
            append_event=lambda _e: None,
            append_decision=lambda _d: None,
        ),
        running_flag=lambda: False,
        build_runtime_context=lambda _session_key: None,
        collect_candidates=lambda _session_key: [],
        write_cognitive_event_to_working_memory=lambda *a, **kw: False,
        record_last_scan=lambda _payload: None,
        utcnow_iso=lambda: "2026-07-17T00:00:00+00:00",
    )
    return AgentCognitiveRuntime(deps)


def _find_log_call(mock_log_event, event_name):
    """从 mock_log_event.call_args_list 中找出指定事件名的首次调用。"""
    for call in mock_log_event.call_args_list:
        if call.args and call.args[0] == event_name:
            return call
    return None


@pytest.mark.asyncio
async def test_pass_skipped_includes_task_counts() -> None:
    """cognitive.pass.skipped 事件应包含 session_key / reason /
    active_task_count / running_subagents 四个 attrs。"""
    runtime = _make_runtime_with_active_tasks_skip()

    with patch("OriginAgent.agent.agent_cognitive_runtime.log_event") as mock_log_event:
        await runtime.run_cognitive_pass_for_session(
            "cli:test-session",
            active_task_count=2,
            running_subagents=1,
        )

    skipped_call = _find_log_call(mock_log_event, "cognitive.pass.skipped")
    assert skipped_call is not None, "cognitive.pass.skipped 未被记录"
    kwargs = skipped_call.kwargs

    # 必需 attrs 存在性断言
    assert "session_key" in kwargs, "cognitive.pass.skipped 缺少 session_key"
    assert "reason" in kwargs, "cognitive.pass.skipped 缺少 reason"
    assert "active_task_count" in kwargs, "cognitive.pass.skipped 缺少 active_task_count"
    assert "running_subagents" in kwargs, "cognitive.pass.skipped 缺少 running_subagents"

    # 值的正确性断言
    assert kwargs["session_key"] == "cli:test-session"
    assert kwargs["reason"] == "active_tasks"
    assert kwargs["active_task_count"] == 2
    assert kwargs["running_subagents"] == 1
