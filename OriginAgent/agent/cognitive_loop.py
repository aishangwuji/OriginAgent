"""Minimal sidecar cognitive loop for bounded backend cognition."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

if TYPE_CHECKING:
    # 仅用于类型注解；运行时不导入以避免与 epic_motor 形成循环依赖
    from OriginAgent.agent.epic_motor import EpicMotorProcessor


@dataclass(frozen=True)
class CognitiveLoopConfig:
    enabled: bool = True
    interval_seconds: int = 15
    motor_tick_interval_ms: int = 50  # EPIC 认知周期默认 50ms


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
        motor_processor: "EpicMotorProcessor | None" = None,
    ) -> None:
        self.config = config
        self._session_keys_provider = session_keys_provider
        self._active_task_count_provider = active_task_count_provider
        self._running_subagents_provider = running_subagents_provider
        self._session_processor = session_processor or active_intent_processor
        if self._session_processor is None:
            raise ValueError("cognitive loop requires a session processor")
        self._motor_processor = motor_processor

    async def run_forever(self, is_running: Callable[[], bool]) -> None:
        """并发运行审议循环 + 运动节拍器循环（如果配置了 motor_processor）。

        - motor_processor=None：仅运行审议循环（保持向后兼容）
        - motor_processor 启用：用 asyncio.gather 并发两循环，异常互相隔离
        """
        if self._motor_processor is None:
            # 向后兼容：仅运行审议循环
            await self._run_deliberation_loop(is_running)
            return

        # 双循环并发：审议循环 + 运动节拍器
        await asyncio.gather(
            self._run_deliberation_loop(is_running),
            self._run_motor_loop(is_running),
        )

    async def _run_deliberation_loop(self, is_running: Callable[[], bool]) -> None:
        """15s 节拍审议循环：sleep → 遍历 session_keys → run_once_for_session。

        异常隔离：CancelledError 传播（stop 时取消），普通 Exception 记录后继续。
        """
        interval = max(0.001, float(self.config.interval_seconds))
        while is_running():
            try:
                await asyncio.sleep(interval)
                if not is_running():
                    break
                for session_key in self._session_keys_provider():
                    await self.run_once_for_session(session_key)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("CognitiveLoop: deliberation cycle error")

    async def _run_motor_loop(self, is_running: Callable[[], bool]) -> None:
        """50ms 节拍运动循环：调用 motor_processor.tick()。

        异常隔离：单 tick 异常不影响下一 tick 或审议循环。
        """
        tick_interval_s = max(0.001, self.config.motor_tick_interval_ms / 1000.0)
        while is_running():
            try:
                await asyncio.sleep(tick_interval_s)
                if not is_running():
                    break
                await self._motor_processor.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("CognitiveLoop: motor tick error")

    async def run_once_for_session(self, session_key: str) -> Any:
        active_count = self._active_task_count_provider(session_key)
        running_subagents = self._running_subagents_provider(session_key)
        return await self._session_processor(
            session_key,
            active_task_count=active_count,
            running_subagents=running_subagents,
        )
