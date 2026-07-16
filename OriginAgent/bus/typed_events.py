"""Minimal typed event bus for in-process domain events.

Provides ``subscribe`` / ``publish`` with string-typed event names.
Used by ``WorldStateWatcher`` (subscriber) and ``WorldStateManager``
(publisher) to connect belief changes to BDI re-planning.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable


class TypedEventBus:
    """In-process typed pub/sub event bus.

    Callbacks registered via :meth:`subscribe` may be sync or async.
    :meth:`publish` is async and awaits async callbacks.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable]] = {}

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """Register *callback* for events of *event_type*."""
        self._subscribers.setdefault(event_type, []).append(callback)

    async def publish(self, event_type: str, event: Any) -> None:
        """Dispatch *event* to all subscribers of *event_type*.

        Async callbacks are awaited; sync callbacks are called directly.
        Exceptions in individual callbacks are logged and swallowed so
        one failing subscriber does not block the others.
        """
        for cb in self._subscribers.get(event_type, []):
            try:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass
