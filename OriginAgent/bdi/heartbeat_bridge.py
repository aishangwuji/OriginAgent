"""Bridge between HeartbeatService and DeliberationEngine.

The existing HeartbeatService reads HEARTBEAT.md and does skip/run.
This bridge replaces that logic with full BDI deliberation, while
keeping the existing HeartbeatService lifecycle (start/stop/interval).
"""

from __future__ import annotations

from typing import Awaitable, Callable

from loguru import logger

from OriginAgent.bdi.deliberation import DeliberationEngine
from OriginAgent.bdi.models import DeliberationIntention, DeliberationResult


class BDIHeartbeatBridge:
    """Wraps DeliberationEngine in HeartbeatService-compatible interface.

    Usage inside HeartbeatService._tick()::

        bridge = BDIHeartbeatBridge(engine=deliberation_engine, on_notify=...)
        result = await bridge.tick()
        if result and result.intentions_formed > 0:
            # Handle intentions
    """

    def __init__(
        self,
        *,
        engine: DeliberationEngine,
        on_notify: Callable[[str], Awaitable[None]] | None = None,
        on_execute: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        self._engine = engine
        self._on_notify = on_notify
        self._on_execute = on_execute

    async def tick(self) -> DeliberationResult | None:
        """Execute one BDI deliberation tick.

        Returns the DeliberationResult, or None if engine is disabled.
        """
        try:
            result = await self._engine.run_cycle()
        except Exception:
            logger.exception("BDI: heartbeat bridge tick failed")
            return None

        if not result.had_work:
            logger.debug("BDI: heartbeat tick — no intentions formed")
            return result

        # Execute intentions
        for intent in result.intentions:
            try:
                await self._execute_intention(intent)
            except Exception:
                logger.exception("BDI: failed to execute intention for desire {}", intent.desire_id)

        # Notify user about deliberation outcomes
        if self._on_notify and result.intentions:
            summary = self._summarize(result)
            if summary:
                try:
                    await self._on_notify(summary)
                except Exception:
                    logger.exception("BDI: notification failed")

        return result

    async def _execute_intention(self, intent: DeliberationIntention) -> None:
        """Execute a single intention through the action runtime."""
        if intent.action == "exec" and self._on_execute:
            command = intent.payload.get("command", "")
            if command:
                await self._on_execute(command)
        else:
            logger.info(
                "BDI: intention formed but no handler — desire={} action={} scope={}",
                intent.desire_id, intent.action, intent.scope,
            )

    @staticmethod
    def _summarize(result: DeliberationResult) -> str | None:
        """Build a user-facing summary of what the agent decided to do."""
        if not result.intentions:
            return None
        parts = [f"I've been thinking about {result.desires_evaluated} active tasks."]
        for i, intent in enumerate(result.intentions, 1):
            parts.append(f"{i}. {intent.reasoning}")
        return "\n".join(parts)
