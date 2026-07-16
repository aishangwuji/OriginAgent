"""Assembly-integration tests for the three BDI bridges.

Verifies that:
1. UtilityRewardBridge is wired into DeliberationEngine (use_actr_utility=True)
2. BDIHeartbeatBridge.tick() calls engine.run_cycle()
3. WorldStateManager publishes BELIEF_CHANGED_EVENT on belief changes
4. DeliberationEngine creates WorldStateWatcher with a real event_bus
5. E2E: reflection record → UtilityRewardBridge → desire utility update
6. E2E: heartbeat tick → BDI re-evaluation (run_cycle invoked)
7. E2E: belief change → event_bus → WorldStateWatcher → engine.trigger_now
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.bdi.deliberation import DeliberationEngine
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.heartbeat_bridge import BDIHeartbeatBridge
from OriginAgent.bdi.models import Desire, DesirePriority, DesireStatus
from OriginAgent.bdi.utility_reward_bridge import UtilityRewardBridge
from OriginAgent.bdi.world_state_watcher import (
    BELIEF_CHANGED_EVENT,
    BeliefChangeEvent,
    BeliefChangeSeverity,
)
from OriginAgent.bus.typed_events import TypedEventBus


# ---------------------------------------------------------------------------
# Shared fakes / fixtures
# ---------------------------------------------------------------------------

class FakeProvider:
    """Minimal LLM provider that returns a no-op deliberation result."""

    async def chat_with_retry(self, messages, tools, model):
        resp = MagicMock()
        resp.should_execute_tools = True
        resp.has_tool_calls = True
        tc = MagicMock()
        tc.arguments = {
            "reasoning": "no work",
            "intentions": [],
            "desires_to_satisfy": [],
            "desires_to_suspend": [],
            "desires_to_cancel": [],
            "next_check_at": None,
        }
        resp.tool_calls = [tc]
        return resp


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as td:
        yield DesireStore(Path(td))


@pytest.fixture
def engine(store):
    return DeliberationEngine(
        workspace=store.workspace,
        store=store,
        provider=FakeProvider(),
        model="test",
        enabled=True,
    )


# ---------------------------------------------------------------------------
# 1. UtilityRewardBridge assembled in DeliberationEngine
# ---------------------------------------------------------------------------

def test_utility_reward_bridge_assembled_in_engine(store):
    """DeliberationEngine with use_actr_utility=True + reward_bridge
    must not early-return in _update_utilities."""
    mock_ledger = MagicMock()
    bridge = UtilityRewardBridge(audit_ledger=mock_ledger, desire_store=store)
    eng = DeliberationEngine(
        workspace=store.workspace,
        store=store,
        provider=FakeProvider(),
        model="test",
        enabled=True,
        use_actr_utility=True,
        reward_bridge=bridge,
    )
    assert eng._use_actr_utility is True
    assert eng._reward_bridge is not None
    assert eng._reward_bridge._audit_ledger is mock_ledger


# ---------------------------------------------------------------------------
# 2. BDIHeartbeatBridge.tick() calls engine.run_cycle()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bdi_heartbeat_bridge_tick_calls_run_cycle(engine):
    """BDIHeartbeatBridge.tick() must delegate to engine.run_cycle()."""
    bridge = BDIHeartbeatBridge(engine=engine)
    # Mock run_cycle to return a result with had_work=False (no intentions)
    mock_result = SimpleNamespace(had_work=False, intentions=[], desires_evaluated=0)
    engine.run_cycle = AsyncMock(return_value=mock_result)

    result = await bridge.tick()

    assert engine.run_cycle.called
    assert result is mock_result


# ---------------------------------------------------------------------------
# 3. WorldStateManager publishes BELIEF_CHANGED_EVENT on belief change
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_world_state_publishes_belief_changed_event():
    """WorldStateManager must publish BELIEF_CHANGED_EVENT when belief changes."""
    from OriginAgent.agent.identity import ActorResolver
    from OriginAgent.agent.world_state import WorldStateManager
    from OriginAgent.session.manager import SessionManager

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        bus = TypedEventBus()
        received: list[BeliefChangeEvent] = []

        async def _on_event(event: BeliefChangeEvent):
            received.append(event)

        bus.subscribe(BELIEF_CHANGED_EVENT, _on_event)

        sessions = SessionManager(tmp_path)
        session = sessions.get_or_create("cli:direct")
        runtime_context = ActorResolver().resolve_runtime_context(
            channel="cli",
            chat_id="direct",
            sender_id="user-1",
            metadata={},
            session_key="cli:direct",
        )
        world = WorldStateManager(tmp_path, sessions, event_bus=bus)

        # Trigger a belief change via device discovery
        world.ingest_device_discovery(
            session,
            runtime_context=runtime_context,
            device_map={"device_1": {"name": "light", "online": True}},
        )

        # Flush pending publish tasks
        if world._pending_publishes:
            await __import__("asyncio").gather(*world._pending_publishes)

        assert len(received) >= 1
        assert isinstance(received[0], BeliefChangeEvent)


# ---------------------------------------------------------------------------
# 4. WorldStateWatcher subscribed to real event_bus
# ---------------------------------------------------------------------------

def test_watcher_subscribed_to_real_event_bus(store):
    """DeliberationEngine with a real event_bus must have a WorldStateWatcher
    that holds the same bus and is subscribed to BELIEF_CHANGED_EVENT."""
    bus = TypedEventBus()
    eng = DeliberationEngine(
        workspace=store.workspace,
        store=store,
        provider=FakeProvider(),
        model="test",
        enabled=True,
        event_bus=bus,
    )
    assert eng._watcher._event_bus is bus
    # The watcher subscribes on start(), not on __init__
    import asyncio

    async def _check():
        await eng._watcher.start()
        assert BELIEF_CHANGED_EVENT in bus._subscribers
        assert len(bus._subscribers[BELIEF_CHANGED_EVENT]) >= 1
        eng._watcher.stop()

    asyncio.run(_check())


# ---------------------------------------------------------------------------
# 5. E2E: reflection record → UtilityRewardBridge → desire utility update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_reflection_updates_desire_utility(store):
    """A reflection record in the audit ledger must produce a reward signal
    that updates the Desire's utility via _update_utilities."""
    from OriginAgent.agent.meta_cognition_models import ReflectionRecord

    # Create a desire with initial utility 0.5
    desire = Desire(
        desire_id="desire_test_001",
        owner_id="user-1",
        session_key="cli:direct",
        content="Test desire",
        status=DesireStatus.ACTIVE,
        priority=DesirePriority.MEDIUM,
        utility=0.5,
    )
    store.add(desire)
    original_utility = store.get("desire_test_001").utility

    # Create a reflection record that maps to the desire via evidence_refs
    reflection = ReflectionRecord(
        reflection_id="refl_001",
        session_key="cli:direct",
        outcome_class="success",
        confidence=0.9,
        payload={"evidence_refs": ["desire_test_001"]},
    )
    mock_ledger = MagicMock()
    mock_ledger.recent_reflections = MagicMock(return_value=[reflection.to_json()])

    bridge = UtilityRewardBridge(audit_ledger=mock_ledger, desire_store=store)
    eng = DeliberationEngine(
        workspace=store.workspace,
        store=store,
        provider=FakeProvider(),
        model="test",
        enabled=True,
        use_actr_utility=True,
        utility_learning_rate=0.2,
        reward_bridge=bridge,
    )

    # Call _update_utilities directly with the desire and its id
    await eng._update_utilities([desire], ["desire_test_001"])

    updated_desire = store.get("desire_test_001")
    assert updated_desire.utility != original_utility
    # ACT-R formula: U_new = U_old + α * (R - U_old)
    # R = 0.9 (success * confidence), α = 0.2, U_old = 0.5
    # U_new = 0.5 + 0.2 * (0.9 - 0.5) = 0.5 + 0.08 = 0.58
    assert abs(updated_desire.utility - 0.58) < 0.001


# ---------------------------------------------------------------------------
# 6. E2E: heartbeat tick → BDI re-evaluation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_heartbeat_triggers_bdi_reevaluation(store):
    """BDIHeartbeatBridge.tick() with a real engine must execute a
    deliberation cycle (provider is called when there are desires)."""
    provider = FakeProvider()
    # Wrap chat_with_retry with AsyncMock to track calls
    provider.chat_with_retry = AsyncMock(side_effect=provider.chat_with_retry)
    eng = DeliberationEngine(
        workspace=store.workspace,
        store=store,
        provider=provider,
        model="test",
        enabled=True,
    )
    bridge = BDIHeartbeatBridge(engine=eng)

    # Add an active desire so run_cycle reaches the LLM deliberation step
    desire = Desire(
        desire_id="desire_hb_001",
        owner_id="user-1",
        session_key="cli:direct",
        content="Heartbeat test desire",
        status=DesireStatus.ACTIVE,
        priority=DesirePriority.MEDIUM,
        utility=0.5,
    )
    store.add(desire)

    # tick() calls run_cycle(); with a desire the cycle should call the provider
    result = await bridge.tick()

    # result is a DeliberationResult (or None if disabled)
    assert result is not None
    # provider.chat_with_retry was called during the cycle
    assert provider.chat_with_retry.called


# ---------------------------------------------------------------------------
# 7. E2E: belief change → event_bus → WorldStateWatcher → engine.trigger_now
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_belief_change_triggers_bdi_replan(store):
    """A CRITICAL BeliefChangeEvent published to the bus must reach
    WorldStateWatcher and trigger engine.trigger_now()."""
    bus = TypedEventBus()
    eng = DeliberationEngine(
        workspace=store.workspace,
        store=store,
        provider=FakeProvider(),
        model="test",
        enabled=True,
        event_bus=bus,
    )

    # Start the watcher so it subscribes to the bus
    await eng._watcher.start()

    # Mock trigger_now to avoid running a full cycle
    eng.trigger_now = AsyncMock()

    # Publish a CRITICAL belief change event
    event = BeliefChangeEvent(
        source="world_state",
        key="device_map",
        old_value="empty",
        new_value="device_1",
        severity=BeliefChangeSeverity.CRITICAL,
        reason="New device discovered",
    )
    await bus.publish(BELIEF_CHANGED_EVENT, event)

    # trigger_now should have been called by the watcher
    assert eng.trigger_now.called

    eng._watcher.stop()
