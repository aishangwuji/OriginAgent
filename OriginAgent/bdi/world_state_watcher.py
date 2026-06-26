"""Event-driven reactivity for BDI DeliberationEngine."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from loguru import logger


class BeliefChangeSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class BeliefChangeEvent:
    """Emitted when a belief in the WorldState changes significantly."""

    source: str
    key: str
    old_value: str | None = None
    new_value: str | None = None
    severity: BeliefChangeSeverity = BeliefChangeSeverity.MEDIUM
    reason: str = ""
    timestamp: str = ""

    @property
    def is_critical(self) -> bool:
        return self.severity in (BeliefChangeSeverity.CRITICAL, BeliefChangeSeverity.HIGH)


BELIEF_CHANGED_EVENT = "belief.changed"


class WorldStateWatcher:
    """Listens for belief changes and triggers immediate BDI reconsideration."""

    def __init__(
        self,
        *,
        engine: Any,
        event_bus: Any | None = None,
        cooldown_s: float = 5.0,
        enabled: bool = True,
    ) -> None:
        self._engine = engine
        self._event_bus = event_bus
        self._cooldown_s = cooldown_s
        self._enabled = enabled
        self._running = False
        self._last_triggered_at: float = 0.0
        self._pending_events: list[BeliefChangeEvent] = []
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if not self._enabled:
            logger.info("BDI: WorldStateWatcher disabled")
            return
        if self._running:
            return
        self._running = True
        if self._event_bus:
            self._subscribe()
        logger.info("BDI: WorldStateWatcher started (cooldown={}s)", self._cooldown_s)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("BDI: WorldStateWatcher stopped")

    def _subscribe(self) -> None:
        if hasattr(self._event_bus, "subscribe"):
            self._event_bus.subscribe(BELIEF_CHANGED_EVENT, self._on_belief_changed)
        elif hasattr(self._event_bus, "add_listener"):
            self._event_bus.add_listener(BELIEF_CHANGED_EVENT, self._on_belief_changed)

    async def _on_belief_changed(self, event: BeliefChangeEvent) -> None:
        if not self._running:
            return
        if not event.is_critical:
            logger.debug("BDI: non-critical belief change ignored — key={} severity={}",
                          event.key, event.severity.value)
            return

        now = time.monotonic()
        if now - self._last_triggered_at < self._cooldown_s:
            logger.debug("BDI: belief change within cooldown, queued — key={}", event.key)
            self._pending_events.append(event)
            return

        self._last_triggered_at = now
        logger.info("BDI: CRITICAL belief change — triggering immediate deliberation "
                     "(source={} key={} new={})", event.source, event.key, event.new_value)

        try:
            await self._engine.trigger_now()
        except Exception:
            logger.exception("BDI: trigger_now failed on belief change")

        self._pending_events.clear()

    async def notify(
        self,
        source: str,
        key: str,
        *,
        old_value: str | None = None,
        new_value: str | None = None,
        severity: BeliefChangeSeverity = BeliefChangeSeverity.MEDIUM,
        reason: str = "",
    ) -> None:
        """Programmatic notification of a belief change."""
        event = BeliefChangeEvent(
            source=source, key=key, old_value=old_value, new_value=new_value,
            severity=severity, reason=reason,
        )
        await self._on_belief_changed(event)
