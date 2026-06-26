"""Tests for WorldStateWatcher — event-driven BDI reactivity."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.bdi.models import Desire, DesireStatus, DesirePriority, now_iso
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.deliberation import DeliberationEngine
from OriginAgent.bdi.world_state_watcher import (
    WorldStateWatcher, BeliefChangeEvent, BeliefChangeSeverity,
)


class FakeEventBus:
    def __init__(self):
        self._subscribers: dict[str, list] = {}

    def subscribe(self, event_type: str, callback):
        self._subscribers.setdefault(event_type, []).append(callback)

    async def publish(self, event_type: str, event):
        for cb in self._subscribers.get(event_type, []):
            await cb(event)


class FakeProvider:
    async def chat_with_retry(self, messages, tools, model):
        resp = MagicMock()
        resp.should_execute_tools = True
        resp.has_tool_calls = True
        tc = MagicMock()
        tc.arguments = {
            "reasoning": "Emergency!",
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
        workspace=store.workspace, store=store,
        provider=FakeProvider(), model="test", enabled=True,
    )


class TestBeliefChangeEvent:
    def test_is_critical_for_high_severity(self):
        evt = BeliefChangeEvent(
            source="smoke_sensor", key="smoke_level",
            old_value="0", new_value="HIGH",
            severity=BeliefChangeSeverity.CRITICAL, reason="Smoke detected",
        )
        assert evt.is_critical is True

    def test_is_critical_for_low_severity(self):
        evt = BeliefChangeEvent(
            source="thermostat", key="temperature",
            old_value="20", new_value="21",
            severity=BeliefChangeSeverity.LOW, reason="Minor drift",
        )
        assert evt.is_critical is False


class TestWorldStateWatcher:
    @pytest.mark.asyncio
    async def test_critical_event_triggers_immediate_deliberation(self, store, engine):
        store.add(Desire(
            desire_id="d1", owner_id="u", session_key="s",
            content="Monitor smoke alarms",
            status=DesireStatus.ACTIVE, priority=DesirePriority.CRITICAL,
        ))
        bus = FakeEventBus()
        watcher = WorldStateWatcher(engine=engine, event_bus=bus)
        trigger_spy = AsyncMock(wraps=engine.trigger_now)
        engine.trigger_now = trigger_spy

        await watcher.start()
        evt = BeliefChangeEvent(
            source="smoke_sensor", key="smoke_level",
            old_value="0", new_value="HIGH",
            severity=BeliefChangeSeverity.CRITICAL, reason="Smoke in kitchen",
        )
        await bus.publish("belief.changed", evt)
        await asyncio.sleep(0.1)

        assert trigger_spy.called, "trigger_now was not called on critical event"
        watcher.stop()

    @pytest.mark.asyncio
    async def test_low_severity_event_does_not_trigger(self, store, engine):
        bus = FakeEventBus()
        watcher = WorldStateWatcher(engine=engine, event_bus=bus)
        trigger_spy = AsyncMock(wraps=engine.trigger_now)
        engine.trigger_now = trigger_spy

        await watcher.start()
        evt = BeliefChangeEvent(
            source="thermostat", key="temperature",
            old_value="20", new_value="20.5",
            severity=BeliefChangeSeverity.LOW, reason="Minor drift",
        )
        await bus.publish("belief.changed", evt)
        await asyncio.sleep(0.1)

        assert not trigger_spy.called, "Low-severity event should not trigger"
        watcher.stop()

    @pytest.mark.asyncio
    async def test_debounce_prevents_trigger_storms(self, store, engine):
        bus = FakeEventBus()
        watcher = WorldStateWatcher(engine=engine, event_bus=bus, cooldown_s=1.0)
        trigger_spy = AsyncMock(wraps=engine.trigger_now)
        engine.trigger_now = trigger_spy

        await watcher.start()
        for i in range(5):
            evt = BeliefChangeEvent(
                source="motion_sensor", key="motion",
                old_value="clear", new_value=f"detected_{i}",
                severity=BeliefChangeSeverity.HIGH, reason=f"Motion {i}",
            )
            await bus.publish("belief.changed", evt)
        await asyncio.sleep(0.2)

        assert trigger_spy.call_count <= 1, f"Expected ≤1 trigger, got {trigger_spy.call_count}"
        watcher.stop()

    @pytest.mark.asyncio
    async def test_disabled_watcher_does_not_trigger(self, store, engine):
        bus = FakeEventBus()
        watcher = WorldStateWatcher(engine=engine, event_bus=bus, enabled=False)
        trigger_spy = AsyncMock(wraps=engine.trigger_now)
        engine.trigger_now = trigger_spy

        await watcher.start()
        evt = BeliefChangeEvent(
            source="smoke_sensor", key="smoke_level",
            old_value="0", new_value="HIGH",
            severity=BeliefChangeSeverity.CRITICAL, reason="Smoke!",
        )
        await bus.publish("belief.changed", evt)
        await asyncio.sleep(0.1)

        assert not trigger_spy.called, "Disabled watcher should not trigger"
        watcher.stop()
