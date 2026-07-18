"""Tests for session activity awareness in AgentCognitiveRuntime.

P6 (方案 C): 验证"用户活跃时不打扰"——
- session.updated_at 在最近 5 分钟内 → cognitive pass 被跳过，
  reason="user_active"
- session.updated_at 超过 5 分钟 → pass 正常执行
- session 没有 updated_at 字段 → 优雅降级，pass 正常执行（向后兼容
  旧测试 fake）
- user-active gate 在 _update_failure_state 之前执行——不消费
  _pending_nudges flag，下次用户空闲时仍能正确检测 LLM 失败

设计依据：cognitive_scheduler 每 15s 扫描所有 session，如果不区分
"用户正在打字"和"用户离开了"，会在用户对话过程中插入 nudge，打断
思路并抢 LLM 注意力。这是"抢资源"问题在用户体验层的延伸。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from OriginAgent.agent.agent_cognitive_runtime import AgentCognitiveRuntime, CognitiveRuntimeDeps
from OriginAgent.agent.cognitive_events import CognitiveEvent


def _make_event(session_key: str) -> CognitiveEvent:
    return CognitiveEvent(
        event_id=f"pending_confirmation_nudge:{session_key}",
        session_key=session_key,
        event_type="pending_confirmation_nudge",
        source_type="pending_confirmation",
        source_reference="conf-1",
        summary="pending confirmation nudge",
    )


def _make_candidate(session_key: str, content: str = "nudge") -> dict:
    return {
        "event": _make_event(session_key),
        "cooldown_key": f"pending_confirmation:{session_key}:conf-1",
        "message": SimpleNamespace(content=content),
    }


def _normal_message() -> dict:
    return {"role": "assistant", "content": "Sure.", "stop_reason": "stop"}


def _build_runtime(session: SimpleNamespace) -> AgentCognitiveRuntime:
    cognitive_loop = SimpleNamespace(
        config=SimpleNamespace(enabled=True, interval_seconds=1),
    )
    cognitive_scheduler = SimpleNamespace(start=lambda: "disabled")
    active_intents = SimpleNamespace(
        eligibility_for_cognition=lambda _sk, **_kw: (True, None),
        passes_cooldown=lambda _sk, _intent_id: (True, None),
        config=SimpleNamespace(enabled=True, max_messages_per_session_per_pass=5),
    )
    sessions = SimpleNamespace(get_or_create=lambda _sk: session)
    deps = CognitiveRuntimeDeps(
        cognitive_loop=cognitive_loop,
        cognitive_scheduler=cognitive_scheduler,
        bus=SimpleNamespace(publish_inbound=AsyncMock()),
        sessions=sessions,
        active_intents=active_intents,
        reminder_store=SimpleNamespace(mark_fired=lambda _x: None),
        working_memory=SimpleNamespace(),
        cognitive_audit=SimpleNamespace(
            append_event=lambda _e: None,
            append_decision=lambda _d: None,
        ),
        running_flag=lambda: False,
        build_runtime_context=lambda _sk: None,
        collect_candidates=lambda _sk: [_make_candidate(_sk)],
        write_cognitive_event_to_working_memory=lambda *a, **kw: False,
        record_last_scan=lambda _p: None,
        utcnow_iso=lambda: "2026-07-18T00:00:00+00:00",
    )
    return AgentCognitiveRuntime(deps)


def _find_skipped_call(mock_log_event, reason: str | None = None):
    for call in mock_log_event.call_args_list:
        if call.args and call.args[0] == "cognitive.pass.skipped":
            if reason is None or call.kwargs.get("reason") == reason:
                return call
    return None


@pytest.mark.asyncio
async def test_recent_session_activity_skips_pass() -> None:
    """session.updated_at 在最近 5 分钟内 → pass 被跳过，reason="user_active"。"""
    # Session updated 1 minute ago — within the 5-minute window.
    recent_time = datetime.now() - timedelta(minutes=1)
    session = SimpleNamespace(
        key="cli:active-user",
        messages=[_normal_message()],
        updated_at=recent_time,
    )
    runtime = _build_runtime(session)

    with patch("OriginAgent.agent.agent_cognitive_runtime.log_event") as mock_log_event:
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:active-user", active_task_count=0, running_subagents=0,
        )

    # Pass skipped — empty return.
    assert decisions == []
    # Skip logged with the right reason.
    skipped_call = _find_skipped_call(mock_log_event, reason="user_active")
    assert skipped_call is not None, "Expected cognitive.pass.skipped with reason=user_active"


@pytest.mark.asyncio
async def test_stale_session_proceeds_normally() -> None:
    """session.updated_at 超过 5 分钟 → pass 正常执行，发射 nudge。"""
    # Session updated 10 minutes ago — outside the 5-minute window.
    stale_time = datetime.now() - timedelta(minutes=10)
    session = SimpleNamespace(
        key="cli:idle-user",
        messages=[_normal_message()],
        updated_at=stale_time,
    )
    runtime = _build_runtime(session)

    with patch("OriginAgent.agent.agent_cognitive_runtime.log_event"):
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:idle-user", active_task_count=0, running_subagents=0,
        )

    # Pass proceeded — nudge emitted.
    assert len(decisions) == 1
    assert decisions[0].outcome == "emitted"


@pytest.mark.asyncio
async def test_missing_updated_at_falls_through() -> None:
    """session 没有 updated_at 字段 → 优雅降级，pass 正常执行。

    向后兼容旧测试 fake（SimpleNamespace 不带 updated_at）——不应
    因为字段缺失而跳过 cognitive pass。
    """
    session = SimpleNamespace(
        key="cli:no-timestamp",
        messages=[_normal_message()],
        # No updated_at field.
    )
    runtime = _build_runtime(session)

    with patch("OriginAgent.agent.agent_cognitive_runtime.log_event") as mock_log_event:
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:no-timestamp", active_task_count=0, running_subagents=0,
        )

    # Pass proceeded — no skip.
    assert len(decisions) == 1
    assert decisions[0].outcome == "emitted"
    # No user_active skip logged.
    assert _find_skipped_call(mock_log_event, reason="user_active") is None


@pytest.mark.asyncio
async def test_user_active_gate_runs_before_failure_state_update() -> None:
    """user-active gate 在 _update_failure_state 之前执行——不消费
    _pending_nudges flag。

    场景：上一轮发射了 nudge，本轮用户活跃 → pass 被跳过，但
    _pending_nudges 仍保留。下一轮用户空闲时，_update_failure_state
    仍能正确检测上一轮的 LLM 失败。
    """
    from datetime import datetime, timedelta

    # First pass: stale session, emit nudge, set _pending_nudges.
    stale_time = datetime.now() - timedelta(minutes=10)
    session = SimpleNamespace(
        key="cli:gate-order",
        messages=[_normal_message()],
        updated_at=stale_time,
    )
    runtime = _build_runtime(session)

    with patch("OriginAgent.agent.agent_cognitive_runtime.log_event"):
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:gate-order", active_task_count=0, running_subagents=0,
        )
    assert decisions[0].outcome == "emitted"
    # _pending_nudges should be set after emission.
    assert "cli:gate-order" in runtime._pending_nudges

    # Second pass: user now active (session updated 1 min ago).
    session.updated_at = datetime.now() - timedelta(minutes=1)
    session.messages = [{"role": "assistant", "content": "Error: model error", "stop_reason": "error"}]

    with patch("OriginAgent.agent.agent_cognitive_runtime.log_event"):
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:gate-order", active_task_count=0, running_subagents=0,
        )
    # Pass skipped due to user_active.
    assert decisions == []
    # CRITICAL: _pending_nudges should still be set (not consumed).
    assert "cli:gate-order" in runtime._pending_nudges, (
        "_pending_nudges must not be consumed when pass is skipped by user_active"
    )

    # Third pass: user idle again, _update_failure_state should now run
    # and detect the LLM error from the second pass's messages.
    session.updated_at = datetime.now() - timedelta(minutes=10)
    # session.messages still has the error from second pass setup.

    with patch("OriginAgent.agent.agent_cognitive_runtime.log_event"):
        await runtime.run_cognitive_pass_for_session(
            "cli:gate-order", active_task_count=0, running_subagents=0,
        )

    # _update_failure_state ran on this pass (consumed the old _pending_nudges
    # entry and detected the error). The candidate loop then emitted a NEW
    # nudge, which sets _pending_nudges again — that's expected. The key
    # signal is that consecutive_failures was incremented to 1, proving
    # _update_failure_state actually ran (and wasn't skipped by user_active).
    failure_state = runtime._session_failure_states.get("cli:gate-order", {})
    assert failure_state.get("consecutive_failures", 0) == 1, (
        "Failure from the user-active-skipped pass must be detected on the next idle pass"
    )
