"""Tests for content-fingerprint self-loop detection in AgentCognitiveRuntime.

P5 (方案 C): 验证"自产自消"循环检测——
- 同一 session 连续 3 次发射**相同内容**的 nudge 后，进入 1 小时静默期。
- 静默期内 run_cognitive_pass_for_session 跳过该 session，发射
  cognitive.pass.skipped 事件且 reason="loop_silent_period"。
- 静默期过期后恢复，循环计数器重置。
- 不同内容重置计数器——只有 *连续相同* nudge 才算循环信号。
- 与 LLM-failure 熔断器独立——互不干扰。

设计依据：自产自消循环的特征是 cognitive_scheduler 每 15s 扫描发射
相同 nudge，LLM "成功"响应但 pending item 仍未解决。LLM-failure
熔断器无法捕获（因为 LLM 没报错），需要内容指纹检测兜底。
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from OriginAgent.agent.agent_cognitive_runtime import AgentCognitiveRuntime, CognitiveRuntimeDeps
from OriginAgent.agent.cognitive_events import CognitiveEvent


def _make_event(session_key: str, source_ref: str = "conf-1") -> CognitiveEvent:
    return CognitiveEvent(
        event_id=f"pending_confirmation_nudge:{session_key}:{source_ref}",
        session_key=session_key,
        event_type="pending_confirmation_nudge",
        source_type="pending_confirmation",
        source_reference=source_ref,
        summary="pending confirmation nudge",
    )


def _make_candidate(session_key: str, content: str, source_ref: str = "conf-1") -> dict:
    """Build a candidate with explicit content (controls the fingerprint)."""
    return {
        "event": _make_event(session_key, source_ref),
        "cooldown_key": f"pending_confirmation:{session_key}:{source_ref}",
        "message": SimpleNamespace(content=content),
    }


def _normal_message() -> dict:
    """A successful (non-error) assistant response — loop detection must
    still fire because the *nudge content* is identical, not the LLM outcome."""
    return {"role": "assistant", "content": "Sure, asking the user.", "stop_reason": "stop"}


def _build_runtime(session: SimpleNamespace, candidates: list[dict]) -> AgentCognitiveRuntime:
    """Build a runtime with a fixed candidate list (controlled by caller)."""
    cognitive_loop = SimpleNamespace(
        config=SimpleNamespace(enabled=True, interval_seconds=1),
    )
    cognitive_scheduler = SimpleNamespace(start=lambda: "disabled")
    active_intents = SimpleNamespace(
        eligibility_for_cognition=lambda _sk, **_kw: (True, None),
        # Always allow — loop detection is the gate under test, not intent cooldown.
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
        collect_candidates=lambda _sk: list(candidates),
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


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


@pytest.mark.asyncio
async def test_same_content_three_emits_triggers_silent_period() -> None:
    """3 次连续发射相同内容 nudge → 第 4 次 pass 被静默期跳过。"""
    session = SimpleNamespace(key="cli:loop-test", messages=[_normal_message()])
    # Same content every pass — the "自产自消" signature.
    runtime = _build_runtime(session, [_make_candidate("cli:loop-test", "please confirm X")])

    clock = [1000.0]
    fake_time = SimpleNamespace(time=lambda: clock[0])

    with patch("OriginAgent.agent.agent_cognitive_runtime.time", fake_time), \
         patch("OriginAgent.agent.agent_cognitive_runtime.log_event") as mock_log_event:
        # Passes 1-3: each emits the same nudge, consecutive_repeats climbs 1→2→3.
        for _ in range(3):
            session.messages = [_normal_message()]
            decisions = await runtime.run_cognitive_pass_for_session(
                "cli:loop-test", active_task_count=0, running_subagents=0,
            )
            # Passes 1-3 are not skipped — they emit normally.
            assert len(decisions) == 1
            assert decisions[0].outcome == "emitted"

        # After pass 3, consecutive_repeats=3 >= threshold → silent_until set.
        state = runtime._loop_detection["cli:loop-test"]
        assert state["consecutive_repeats"] == 3
        assert state["silent_until"] == 1000.0 + 3600.0  # 1h silent period

        # Pass 4: silent period active → skipped.
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:loop-test", active_task_count=0, running_subagents=0,
        )
        assert decisions == []  # empty return for silent-period skip

    # Verify the skip was logged with the right reason.
    skipped_call = _find_skipped_call(mock_log_event, reason="loop_silent_period")
    assert skipped_call is not None, "Expected cognitive.pass.skipped with reason=loop_silent_period"


@pytest.mark.asyncio
async def test_different_content_resets_counter() -> None:
    """每次发射不同内容的 nudge → consecutive_repeats 永远是 1，不触发静默期。"""
    session = SimpleNamespace(key="cli:no-loop", messages=[_normal_message()])
    # Will rotate through these distinct contents each pass.
    contents = ["nudge A", "nudge B", "nudge C", "nudge D", "nudge E"]
    runtime = _build_runtime(session, [_make_candidate("cli:no-loop", "placeholder")])

    clock = [1000.0]
    fake_time = SimpleNamespace(time=lambda: clock[0])

    with patch("OriginAgent.agent.agent_cognitive_runtime.time", fake_time), \
         patch("OriginAgent.agent.agent_cognitive_runtime.log_event") as mock_log_event:
        for i, content in enumerate(contents):
            # Swap the candidate's content for this pass.
            runtime._deps.collect_candidates = lambda _sk, c=content: [
                _make_candidate("cli:no-loop", c)
            ]
            session.messages = [_normal_message()]
            decisions = await runtime.run_cognitive_pass_for_session(
                "cli:no-loop", active_task_count=0, running_subagents=0,
            )
            # Every pass emits normally — no silent period.
            assert len(decisions) == 1
            assert decisions[0].outcome == "emitted"

        # Counter should be 1 (last content was new), no silent_until set.
        state = runtime._loop_detection["cli:no-loop"]
        assert state["consecutive_repeats"] == 1
        assert state.get("silent_until") is None

    # No silent-period skip should have been logged.
    assert _find_skipped_call(mock_log_event, reason="loop_silent_period") is None


@pytest.mark.asyncio
async def test_silent_period_expires_and_resets() -> None:
    """静默期过期后，loop_detection 状态被清空，下一次发射重新开始计数。"""
    session = SimpleNamespace(key="cli:expire-test", messages=[_normal_message()])
    runtime = _build_runtime(session, [_make_candidate("cli:expire-test", "same nudge")])

    clock = [1000.0]
    fake_time = SimpleNamespace(time=lambda: clock[0])

    with patch("OriginAgent.agent.agent_cognitive_runtime.time", fake_time), \
         patch("OriginAgent.agent.agent_cognitive_runtime.log_event"):
        # Passes 1-3: trigger silent period (same content, 3 repeats).
        for _ in range(3):
            session.messages = [_normal_message()]
            await runtime.run_cognitive_pass_for_session(
                "cli:expire-test", active_task_count=0, running_subagents=0,
            )
        assert runtime._loop_detection["cli:expire-test"]["silent_until"] == 4600.0

        # Pass 4: silent period active → skipped, returns [].
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:expire-test", active_task_count=0, running_subagents=0,
        )
        assert decisions == []

        # Advance clock past silent period (1h + epsilon).
        clock[0] = 4601.0

        # Pass 5: silent period expired → _check_loop_silent_period resets state,
        # pass proceeds, emits nudge, consecutive_repeats=1 (fresh start).
        session.messages = [_normal_message()]
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:expire-test", active_task_count=0, running_subagents=0,
        )
        assert len(decisions) == 1
        assert decisions[0].outcome == "emitted"

    # After the post-expire emit, counter is 1 (not 4) — silent period reset it.
    state = runtime._loop_detection["cli:expire-test"]
    assert state["consecutive_repeats"] == 1
    assert state.get("silent_until") is None


@pytest.mark.asyncio
async def test_loop_detection_independent_of_failure_cooldown() -> None:
    """LLM-failure 熔断器与内容指纹循环检测互不干扰——
    - failure cooldown 由 LLM error 触发（consecutive_failures >= 3）
    - loop silent period 由相同内容重复触发（consecutive_repeats >= 3）
    - 两者状态独立，reason 不同。
    """
    session = SimpleNamespace(key="cli:independent", messages=[_normal_message()])
    runtime = _build_runtime(session, [_make_candidate("cli:independent", "same content")])

    clock = [1000.0]
    fake_time = SimpleNamespace(time=lambda: clock[0])

    with patch("OriginAgent.agent.agent_cognitive_runtime.time", fake_time), \
         patch("OriginAgent.agent.agent_cognitive_runtime.log_event") as mock_log_event:
        # 3 passes with normal (non-error) LLM response + same nudge content.
        # → loop detection triggers, but failure cooldown does NOT (no errors).
        for _ in range(3):
            session.messages = [_normal_message()]
            await runtime.run_cognitive_pass_for_session(
                "cli:independent", active_task_count=0, running_subagents=0,
            )

        # Loop silent period should be set; failure state should NOT have cooldown.
        loop_state = runtime._loop_detection["cli:independent"]
        assert loop_state["silent_until"] is not None
        failure_state = runtime._session_failure_states.get("cli:independent", {})
        assert failure_state.get("cooldown_until") is None
        assert failure_state.get("consecutive_failures", 0) == 0

        # Pass 4: skipped by loop silent period, NOT by cognitive_cooldown.
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:independent", active_task_count=0, running_subagents=0,
        )
        assert decisions == []
        # The skip reason must be loop_silent_period, not cognitive_cooldown.
        skipped_call = _find_skipped_call(mock_log_event, reason="loop_silent_period")
        assert skipped_call is not None
        cooldown_call = _find_skipped_call(mock_log_event, reason="cognitive_cooldown")
        assert cooldown_call is None
