"""Tests for session-level cognitive circuit breaker in AgentCognitiveRuntime.

验证连续 LLM 失败熔断机制：
- 同一 session 连续 3 次 nudge 触发的 LLM 失败后，进入 30 分钟认知冷却。
- 冷却期内 run_cognitive_pass_for_session 跳过该 session，发射
  cognitive.pass.skipped 事件且 reason="cognitive_cooldown"。
- 冷却过期后恢复，consecutive_failures 重置为 0。
- 成功响应也重置 consecutive_failures。
"""

from __future__ import annotations

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


def _make_candidate(session_key: str) -> dict:
    return {
        "event": _make_event(session_key),
        "cooldown_key": f"pending_confirmation:{session_key}:conf-1",
        "message": SimpleNamespace(content="nudge"),
    }


def _error_message() -> dict:
    return {"role": "assistant", "content": "Error: model error", "stop_reason": "error"}


def _placeholder_error_message() -> dict:
    """Simulate the placeholder written by _append_model_error_placeholder.

    This message has NO ``stop_reason='error'`` — only the content text
    identifies it as a model error. The old ``_detect_last_turn_failure``
    checked for case-sensitive ``"Error:"`` or ``content.startswith("Error")``,
    neither of which matches ``"[Assistant reply unavailable due to model
    error.]"``, so the circuit breaker never triggered.
    """
    return {"role": "assistant", "content": "[Assistant reply unavailable due to model error.]"}


def _normal_message() -> dict:
    return {"role": "assistant", "content": "Sure, here is the answer.", "stop_reason": "stop"}


def _build_runtime(session: SimpleNamespace) -> AgentCognitiveRuntime:
    cognitive_loop = SimpleNamespace(
        config=SimpleNamespace(enabled=True, interval_seconds=1),
    )
    cognitive_scheduler = SimpleNamespace(start=lambda: "disabled")
    active_intents = SimpleNamespace(
        eligibility_for_cognition=lambda _sk, **_kw: (True, None),
        passes_cooldown=lambda _sk, _intent_id: (True, None),
        config=SimpleNamespace(enabled=True, max_messages_per_session_per_pass=1),
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
        utcnow_iso=lambda: "2026-07-17T00:00:00+00:00",
    )
    return AgentCognitiveRuntime(deps)


def _find_skipped_call(mock_log_event, reason: str | None = None):
    for call in mock_log_event.call_args_list:
        if call.args and call.args[0] == "cognitive.pass.skipped":
            if reason is None or call.kwargs.get("reason") == reason:
                return call
    return None


@pytest.mark.asyncio
async def test_session_enters_cooldown_after_threshold_failures() -> None:
    """连续 3 次 nudge 后的 LLM 失败，第 4 次 pass 应被冷却跳过。"""
    session = SimpleNamespace(key="cli:test-session", messages=[])
    runtime = _build_runtime(session)
    clock = [1000.0]
    fake_time = SimpleNamespace(time=lambda: clock[0])

    with patch("OriginAgent.agent.agent_cognitive_runtime.time", fake_time), \
         patch("OriginAgent.agent.agent_cognitive_runtime.log_event") as mock_log_event:
        # Call 1: 上一轮无 nudge（pending 为空），正常发射 nudge
        session.messages = [_normal_message()]
        await runtime.run_cognitive_pass_for_session(
            "cli:test-session", active_task_count=0, running_subagents=0,
        )

        # Call 2: 上一轮 nudge 产生 error -> consecutive_failures=1
        session.messages = [_error_message()]
        await runtime.run_cognitive_pass_for_session(
            "cli:test-session", active_task_count=0, running_subagents=0,
        )

        # Call 3: -> consecutive_failures=2
        session.messages = [_error_message()]
        await runtime.run_cognitive_pass_for_session(
            "cli:test-session", active_task_count=0, running_subagents=0,
        )

        # Call 4: -> consecutive_failures=3，设置 cooldown，立即检查到冷却 -> 跳过
        session.messages = [_error_message()]
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:test-session", active_task_count=0, running_subagents=0,
        )

    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.action == "skip"
    assert decision.suppression_reason == "cognitive_cooldown"

    skipped_call = _find_skipped_call(mock_log_event, reason="cognitive_cooldown")
    assert skipped_call is not None, "cognitive.pass.skipped (cognitive_cooldown) 未被记录"
    assert skipped_call.kwargs.get("session_key") == "cli:test-session"


@pytest.mark.asyncio
async def test_cooldown_expires_after_timeout() -> None:
    """冷却过期后恢复执行，consecutive_failures 重置为 0。"""
    session = SimpleNamespace(key="cli:test-session", messages=[])
    runtime = _build_runtime(session)
    clock = [1000.0]
    fake_time = SimpleNamespace(time=lambda: clock[0])

    with patch("OriginAgent.agent.agent_cognitive_runtime.time", fake_time), \
         patch("OriginAgent.agent.agent_cognitive_runtime.log_event"):
        # Call 1: 发射 nudge（pending 为空）
        session.messages = [_normal_message()]
        await runtime.run_cognitive_pass_for_session(
            "cli:test-session", active_task_count=0, running_subagents=0,
        )
        # Call 2-4: 三次失败，第 4 次进入冷却
        for _ in range(3):
            session.messages = [_error_message()]
            await runtime.run_cognitive_pass_for_session(
                "cli:test-session", active_task_count=0, running_subagents=0,
            )

    state = runtime._session_failure_states.get("cli:test-session", {})
    assert state.get("consecutive_failures") == 3
    assert state.get("cooldown_until") is not None

    # 时间前进 31 分钟，cooldown 过期
    clock[0] = 1000.0 + 31 * 60

    with patch("OriginAgent.agent.agent_cognitive_runtime.time", fake_time), \
         patch("OriginAgent.agent.agent_cognitive_runtime.log_event"):
        session.messages = [_normal_message()]
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:test-session", active_task_count=0, running_subagents=0,
        )

    assert decisions, "冷却过期后应返回非空 decisions"
    assert all(d.action != "skip" for d in decisions), "冷却过期后不应再跳过"
    state_after = runtime._session_failure_states.get("cli:test-session", {})
    assert state_after.get("consecutive_failures", 0) == 0


@pytest.mark.asyncio
async def test_success_resets_failure_counter() -> None:
    """成功响应重置 consecutive_failures；后续失败从 1 重新计数。"""
    session = SimpleNamespace(key="cli:test-session", messages=[])
    runtime = _build_runtime(session)
    clock = [1000.0]
    fake_time = SimpleNamespace(time=lambda: clock[0])

    with patch("OriginAgent.agent.agent_cognitive_runtime.time", fake_time), \
         patch("OriginAgent.agent.agent_cognitive_runtime.log_event"):
        # Call 1: 发射 nudge
        session.messages = [_normal_message()]
        await runtime.run_cognitive_pass_for_session(
            "cli:test-session", active_task_count=0, running_subagents=0,
        )
        # Call 2: 失败 -> failures=1
        session.messages = [_error_message()]
        await runtime.run_cognitive_pass_for_session(
            "cli:test-session", active_task_count=0, running_subagents=0,
        )
        # Call 3: 失败 -> failures=2
        session.messages = [_error_message()]
        await runtime.run_cognitive_pass_for_session(
            "cli:test-session", active_task_count=0, running_subagents=0,
        )
        assert runtime._session_failure_states["cli:test-session"]["consecutive_failures"] == 2

        # Call 4: 成功 -> failures 重置为 0
        session.messages = [_normal_message()]
        await runtime.run_cognitive_pass_for_session(
            "cli:test-session", active_task_count=0, running_subagents=0,
        )
        state = runtime._session_failure_states.get("cli:test-session", {})
        assert state.get("consecutive_failures", 0) == 0

        # Call 5: 失败 -> failures=1（验证重置生效）
        session.messages = [_error_message()]
        await runtime.run_cognitive_pass_for_session(
            "cli:test-session", active_task_count=0, running_subagents=0,
        )
        state = runtime._session_failure_states.get("cli:test-session", {})
        assert state.get("consecutive_failures", 0) == 1


def test_detect_last_turn_failure_matches_placeholder_text() -> None:
    """_detect_last_turn_failure must match the placeholder text written by
    _append_model_error_placeholder: '[Assistant reply unavailable due to
    model error.]'.

    Regression for the cron death loop: the old check was case-sensitive
    ``"Error:" in content`` which didn't match the placeholder, so the
    circuit breaker never triggered and the loop spun indefinitely.
    """
    session = SimpleNamespace(messages=[])
    runtime = _build_runtime(session)

    # Placeholder without stop_reason="error" — the bug scenario
    session.messages = [_placeholder_error_message()]
    assert runtime._detect_last_turn_failure(session) is True

    # Case-insensitive "error:" match
    session.messages = [{"role": "assistant", "content": "error: something went wrong"}]
    assert runtime._detect_last_turn_failure(session) is True

    # Normal message — not a failure
    session.messages = [_normal_message()]
    assert runtime._detect_last_turn_failure(session) is False


@pytest.mark.asyncio
async def test_placeholder_error_triggers_cooldown() -> None:
    """The model-error placeholder (without stop_reason='error') must be
    detected as a failure so the circuit breaker can trigger.

    This is the exact production scenario: runner.py writes the placeholder
    via _append_model_error_placeholder, but _detect_last_turn_failure
    didn't recognize it, so consecutive_failures never reached the threshold
    and the cron session looped forever emitting nudges every ~15s.
    """
    session = SimpleNamespace(key="cli:placeholder-test", messages=[])
    runtime = _build_runtime(session)
    clock = [1000.0]
    fake_time = SimpleNamespace(time=lambda: clock[0])

    with patch("OriginAgent.agent.agent_cognitive_runtime.time", fake_time), \
         patch("OriginAgent.agent.agent_cognitive_runtime.log_event"):
        # Call 1: normal -> emit nudge
        session.messages = [_normal_message()]
        await runtime.run_cognitive_pass_for_session(
            "cli:placeholder-test", active_task_count=0, running_subagents=0,
        )
        # Call 2-4: three placeholder errors -> should enter cooldown
        for _ in range(3):
            session.messages = [_placeholder_error_message()]
            await runtime.run_cognitive_pass_for_session(
                "cli:placeholder-test", active_task_count=0, running_subagents=0,
            )

    state = runtime._session_failure_states.get("cli:placeholder-test", {})
    assert state.get("consecutive_failures") == 3
    assert state.get("cooldown_until") is not None
