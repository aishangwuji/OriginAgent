"""Minimal sidecar cognitive loop for bounded backend cognition."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class CognitiveLoopConfig:
    enabled: bool = False
    interval_seconds: int = 30


class CognitiveLoop:
    """Background cognition sidecar that preserves AgentLoop as the only executor."""

    def __init__(
        self,
        *,
        config: CognitiveLoopConfig,
        session_keys_provider: Callable[[], list[str]],
        active_task_count_provider: Callable[[str], int],
        running_subagents_provider: Callable[[str], int],
        active_intent_processor: Callable[..., Awaitable[Any]] | None = None,
        session_processor: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self.config = config
        self._session_keys_provider = session_keys_provider
        self._active_task_count_provider = active_task_count_provider
        self._running_subagents_provider = running_subagents_provider
        self._session_processor = session_processor or active_intent_processor
        if self._session_processor is None:
            raise ValueError("cognitive loop requires a session processor")

    async def run_forever(self, is_running: Callable[[], bool]) -> None:
        interval = max(1, int(self.config.interval_seconds))
        while is_running():
            await asyncio.sleep(interval)
            for session_key in self._session_keys_provider():
                await self.run_once_for_session(session_key)

    async def run_once_for_session(self, session_key: str) -> Any:
        active_count = self._active_task_count_provider(session_key)
        running_subagents = self._running_subagents_provider(session_key)
        return await self._session_processor(
            session_key,
            active_task_count=active_count,
            running_subagents=running_subagents,
        )
