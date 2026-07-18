"""End-to-end integration tests for the A+C refactor.

验证 Phase 1-6 各机制的 *组合* 行为——这些场景在单元测试中无法发现，因为
每个单元测试只覆盖单一机制（dual queue、is_internal routing、user-active
gate、loop detection）。本文件的目的是验证它们组合后产生预期的涌现行为：

1. **MessageBus + is_internal routing**: cognitive nudge 通过
   ``bus.publish_inbound`` 以 ``is_internal=True`` 发布后，落入
   ``inbound_internal`` 队列，且被任何待处理的 user message 抢先消费。
2. **AgentCognitiveRuntime + MessageBus**: runtime 发射 nudge 时走真实
   bus 路径，可被消费者通过 ``consume_inbound`` 取到。
3. **User-active gate + dual queue**: 用户活跃时 runtime 不发射 nudge →
   ``inbound_internal`` 保持空 → user message 无竞争地被消费。
4. **Self-loop silent period + user message**: 静默期不影响 dual queue —
   user message 仍正常流经 ``inbound``，runtime 仍被静默期 gate 跳过。
5. **Cron burst vs single user message**: 多个 internal message 涌入
   ``inbound_internal`` 时，单条 user message 仍最先被消费。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from OriginAgent.agent.agent_cognitive_runtime import AgentCognitiveRuntime, CognitiveRuntimeDeps
from OriginAgent.agent.cognitive_events import CognitiveEvent
from OriginAgent.bus.events import InboundMessage
from OriginAgent.bus.queue import MessageBus


# -- Shared fixtures ---------------------------------------------------------


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
    # The "message" field is what gets passed to bus.publish_inbound, so it
    # must be a real InboundMessage with is_internal=True (matching production
    # behavior in active_intents._build_message / agent_runtime._collect_*
    # / subagent._announce_result).
    return {
        "event": _make_event(session_key),
        "cooldown_key": f"pending_confirmation:{session_key}:conf-1",
        "message": InboundMessage(
            channel="cron",
            sender_id="cognitive_runtime",
            chat_id=session_key,
            content=content,
            is_internal=True,
        ),
    }


def _normal_message() -> dict:
    return {"role": "assistant", "content": "Sure.", "stop_reason": "stop"}


def _build_runtime(
    *,
    session: SimpleNamespace,
    bus: MessageBus,
    collect_candidates_impl,
) -> AgentCognitiveRuntime:
    """Build a runtime wired to a *real* MessageBus.

    Most deps are still fakes (we don't want to spin up the full agent
    stack), but the bus is real so we can verify queue routing end-to-end.
    """
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
        bus=bus,  # real MessageBus
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
        collect_candidates=collect_candidates_impl,
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


# -- Scenario 1: cognitive nudge lands in inbound_internal, user wins --------


@pytest.mark.asyncio
async def test_cognitive_nudge_routes_to_internal_queue_and_user_wins() -> None:
    """runtime 发射的 nudge 通过真实 bus 落入 ``inbound_internal``，且
    一条稍后到达的 user message 被先消费。

    覆盖组合：Phase 1 (is_internal routing) + Phase 2 (dual queue priority)
    + Phase 4 (cognitive nudge 标记 is_internal=True)。
    """
    # Stale session so user-active gate doesn't skip the pass.
    stale_time = datetime.now() - timedelta(minutes=10)
    session = SimpleNamespace(
        key="cli:integration-1",
        messages=[_normal_message()],
        updated_at=stale_time,
    )
    bus = MessageBus(maxsize=10)
    runtime = _build_runtime(
        session=session,
        bus=bus,
        collect_candidates_impl=lambda _sk: [_make_candidate(_sk, content="nudge-A")],
    )

    with patch("OriginAgent.agent.agent_cognitive_runtime.log_event"):
        # Trigger a cognitive pass — should emit a nudge via bus.publish_inbound.
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:integration-1", active_task_count=0, running_subagents=0,
        )

    assert decisions, "Pass should have emitted a decision"
    assert decisions[0].outcome == "emitted"
    # Nudge landed in inbound_internal, not inbound.
    assert bus.stats["inbound_queue_depth"] == 0
    assert bus.stats["inbound_internal_queue_depth"] == 1

    # Now a user message arrives — should be consumed FIRST even though
    # the nudge was enqueued earlier.
    user_msg = InboundMessage(
        channel="websocket", sender_id="u1", chat_id="c1",
        content="user question",
    )
    await bus.publish_inbound(user_msg)

    first = await bus.consume_inbound()
    second = await bus.consume_inbound()

    assert first.content == "user question", "User message must be consumed first"
    assert second.content == "nudge-A", "Nudge consumed second"
    assert bus.stats["dropped_inbound"] == 0
    assert bus.stats["dropped_inbound_internal"] == 0


# -- Scenario 2: user-active gate suppresses nudge, no queue contention -----


@pytest.mark.asyncio
async def test_user_active_suppresses_nudge_so_no_queue_contention() -> None:
    """用户活跃时 runtime 跳过 nudge 发射 → ``inbound_internal`` 保持空 →
    user message 无竞争地被消费。

    覆盖组合：Phase 6 (user-active gate) + Phase 2 (dual queue)。
    这是"抢资源"问题在用户体验层的最终修复——用户在场时根本不再产生
    internal message，dual queue 的优先级机制没有用武之地，但即使产生
    了也能保证 user 优先。
    """
    # Session updated 1 minute ago — within user-active window.
    recent_time = datetime.now() - timedelta(minutes=1)
    session = SimpleNamespace(
        key="cli:active-user-2",
        messages=[_normal_message()],
        updated_at=recent_time,
    )
    bus = MessageBus(maxsize=10)
    runtime = _build_runtime(
        session=session,
        bus=bus,
        collect_candidates_impl=lambda _sk: [_make_candidate(_sk, content="nudge-B")],
    )

    with patch("OriginAgent.agent.agent_cognitive_runtime.log_event") as mock_log_event:
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:active-user-2", active_task_count=0, running_subagents=0,
        )

    # Pass skipped — no decision, no nudge emitted.
    assert decisions == []
    assert _find_skipped_call(mock_log_event, reason="user_active") is not None
    # No internal message was published — both queues empty.
    assert bus.stats["inbound_internal_queue_depth"] == 0
    assert bus.stats["inbound_queue_depth"] == 0
    assert bus.stats["published_inbound"] == 0, (
        "No bus publish should have occurred when pass was skipped"
    )

    # User message arrives and is consumed with zero competition.
    user_msg = InboundMessage(
        channel="websocket", sender_id="u1", chat_id="c1",
        content="hello",
    )
    await bus.publish_inbound(user_msg)
    consumed = await bus.consume_inbound()
    assert consumed.content == "hello"


# -- Scenario 3: self-loop silent period + user message flow independently ---


@pytest.mark.asyncio
async def test_loop_silent_period_does_not_block_user_messages() -> None:
    """自循环静默期触发后，runtime 不再发射 nudge，但 ``inbound`` 队列
    仍正常处理 user message——两条路径独立。

    覆盖组合：Phase 5 (loop detection) + Phase 2 (dual queue priority)。
    静默期是"runtime 不再生产 internal message"，不影响"bus 消费 user
    message"——验证二者解耦。
    """
    stale_time = datetime.now() - timedelta(minutes=10)
    session = SimpleNamespace(
        key="cli:loop-test",
        messages=[_normal_message()],
        updated_at=stale_time,
    )
    bus = MessageBus(maxsize=10)
    # Same content every pass — will hit the loop threshold on the 3rd emit.
    runtime = _build_runtime(
        session=session,
        bus=bus,
        collect_candidates_impl=lambda _sk: [_make_candidate(_sk, content="same-nudge")],
    )

    # Use real time — patching time.time() to a 1970-era value would make
    # _check_user_active compute (1000.0 - <2026 timestamp>) < 0, falsely
    # triggering the user_active skip. Real time keeps user_active False
    # (session is 10 min stale) and still lets loop_detection set
    # silent_until = now + 1h, which is in the future for pass 4.
    with patch("OriginAgent.agent.agent_cognitive_runtime.log_event"):
        # Pass 1, 2, 3: same content, third triggers silent period.
        for _ in range(3):
            await runtime.run_cognitive_pass_for_session(
                "cli:loop-test", active_task_count=0, running_subagents=0,
            )

    # All 3 nudges landed in inbound_internal.
    assert bus.stats["inbound_internal_queue_depth"] == 3

    # Drain the internal queue — silent period is about *future* passes,
    # not about clearing already-emitted messages.
    for _ in range(3):
        msg = await bus.consume_inbound()
        assert msg.content == "same-nudge"
        assert msg.is_internal is True

    # Pass 4: silent period now active — should be skipped.
    with patch("OriginAgent.agent.agent_cognitive_runtime.log_event") as mock_log_event:
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:loop-test", active_task_count=0, running_subagents=0,
        )

    assert decisions == []
    skipped_call = _find_skipped_call(mock_log_event, reason="loop_silent_period")
    assert skipped_call is not None, "Pass should be skipped by loop_silent_period"
    # No new internal message was published.
    assert bus.stats["inbound_internal_queue_depth"] == 0

    # Meanwhile, user messages flow through ``inbound`` completely unaffected.
    user_msg = InboundMessage(
        channel="websocket", sender_id="u1", chat_id="c1",
        content="user during silent period",
    )
    await bus.publish_inbound(user_msg)
    consumed = await bus.consume_inbound()
    assert consumed.content == "user during silent period"
    assert consumed.is_internal is False


# -- Scenario 4: cron burst vs single user message --------------------------


@pytest.mark.asyncio
async def test_cron_burst_does_not_delay_user_message() -> None:
    """多个 cron nudge 涌入 ``inbound_internal`` 时，单条 user message
    仍最先被消费——dual queue 的优先级在 burst 场景下仍然成立。

    覆盖组合：Phase 2 (dual queue priority under load) + Phase 1
    (is_internal routing for cron-like messages)。

    这是"cron 和用户会话交替抢资源"问题在 burst 场景下的回归测试。
    """
    bus = MessageBus(maxsize=50)

    # Simulate a cron burst — 10 internal messages arrive first.
    for i in range(10):
        nudge = InboundMessage(
            channel="cron", sender_id="cron", chat_id=f"job-{i}",
            content=f"cron-nudge-{i}", is_internal=True,
        )
        await bus.publish_inbound(nudge)

    # User message arrives AFTER the burst.
    user_msg = InboundMessage(
        channel="websocket", sender_id="u1", chat_id="c1",
        content="user message after burst",
    )
    await bus.publish_inbound(user_msg)

    # Consume — user message must be first.
    first = await bus.consume_inbound()
    assert first.content == "user message after burst"
    assert first.is_internal is False

    # Then all 10 cron nudges follow.
    for i in range(10):
        msg = await bus.consume_inbound()
        assert msg.content == f"cron-nudge-{i}"
        assert msg.is_internal is True

    # No drops — queue was big enough.
    assert bus.stats["dropped_inbound"] == 0
    assert bus.stats["dropped_inbound_internal"] == 0


# -- Scenario 5: full A+C chain — emit, race, consume, verify no self-loop ---


@pytest.mark.asyncio
async def test_full_chain_emit_race_consume_no_self_loop() -> None:
    """完整链路：runtime 发射 nudge → bus 路由到 internal → user message
    同时到达 → user 先消费 → nudge 后消费 → runtime 记录 _pending_nudges
    → 下一次 pass 检测上次 nudge 的 LLM 结果。

    覆盖组合：Phase 1 + 2 + 4 + 6（user-active gate 不触发，因为
    session 是 stale 的）+ circuit breaker 的 _pending_nudges 机制。
    这是"自产自消"问题修复后的完整正向链路验证。
    """
    stale_time = datetime.now() - timedelta(minutes=10)
    session = SimpleNamespace(
        key="cli:full-chain",
        messages=[_normal_message()],
        updated_at=stale_time,
    )
    bus = MessageBus(maxsize=10)
    runtime = _build_runtime(
        session=session,
        bus=bus,
        collect_candidates_impl=lambda _sk: [_make_candidate(_sk, content="chain-nudge")],
    )

    # Step 1: cognitive pass emits a nudge via the real bus.
    with patch("OriginAgent.agent.agent_cognitive_runtime.log_event"):
        decisions = await runtime.run_cognitive_pass_for_session(
            "cli:full-chain", active_task_count=0, running_subagents=0,
        )
    assert decisions[0].outcome == "emitted"
    assert "cli:full-chain" in runtime._pending_nudges, (
        "_pending_nudges must be set after emission (consumed by next pass's _update_failure_state)"
    )

    # Step 2: user message arrives concurrently — must be consumed first.
    user_msg = InboundMessage(
        channel="websocket", sender_id="u1", chat_id="c1",
        content="chain user message",
    )
    await bus.publish_inbound(user_msg)

    first = await bus.consume_inbound()
    second = await bus.consume_inbound()
    assert first.content == "chain user message"
    assert second.content == "chain-nudge"
    assert second.is_internal is True

    # Step 3: simulate the nudge being processed and producing a normal
    # (non-error) assistant response. Next pass should detect success and
    # reset the failure counter.
    session.messages = [_normal_message()]
    with patch("OriginAgent.agent.agent_cognitive_runtime.log_event"):
        await runtime.run_cognitive_pass_for_session(
            "cli:full-chain", active_task_count=0, running_subagents=0,
        )

    # _pending_nudges was consumed by _update_failure_state; a new nudge
    # was emitted which re-set it. Failure counter should be 0 (success).
    failure_state = runtime._session_failure_states.get("cli:full-chain", {})
    assert failure_state.get("consecutive_failures", 0) == 0, (
        "Successful nudge outcome must reset failure counter to 0"
    )
