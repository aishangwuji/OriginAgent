"""Minimal sidecar cognitive loop for bounded backend cognition.

双循环架构(EPIC 认知架构):
- 审议循环(15s 节拍):遍历 session_keys 执行 session_processor
- 运动循环(50ms 节拍):调用 EpicMotorProcessor.tick() 派发缓冲动作

当 motor_processor 为 None 时仅运行审议循环(向后兼容)。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

if TYPE_CHECKING:
    # 运行时不导入以避免与 epic_motor 形成循环依赖
    from OriginAgent.agent.epic_motor import EpicMotorProcessor


@dataclass(frozen=True)
class CognitiveLoopConfig:
    enabled: bool = True
    interval_seconds: int = 15
    motor_tick_interval_ms: int = 50


class CognitiveLoop:
    """Background cognition sidecar that preserves AgentLoop as the only executor.

    当 motor_processor 非 None 时,run_forever 并发执行审议循环与运动循环;
    为 None 时仅执行审议循环(向后兼容)。
    """

    def __init__(
        self,
        *,
        config: CognitiveLoopConfig,
        session_keys_provider: Callable[[], list[str]],
        active_task_count_provider: Callable[[str], int],
        running_subagents_provider: Callable[[str], int],
        session_processor: Callable[..., Awaitable[Any]] | None = None,
        motor_processor: "EpicMotorProcessor | None" = None,
    ) -> None:
        self.config = config
        self._session_keys_provider = session_keys_provider
        self._active_task_count_provider = active_task_count_provider
        self._running_subagents_provider = running_subagents_provider
        self._session_processor = session_processor
        self._motor_processor = motor_processor
        if self._session_processor is None:
            raise ValueError("cognitive loop requires a session processor")

    async def run_forever(self, is_running: Callable[[], bool]) -> None:
        """运行认知循环。

        motor_processor 非 None 时并发执行审议循环 + 运动循环;
        为 None 时仅执行审议循环。
        """
        if self._motor_processor is not None:
            await asyncio.gather(
                self._run_deliberation_loop(is_running),
                self._run_motor_loop(is_running),
            )
        else:
            await self._run_deliberation_loop(is_running)

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

        异常隔离：CancelledError 传播，普通 Exception 记录后继续。
        """
        assert self._motor_processor is not None
        interval = max(0.001, self.config.motor_tick_interval_ms / 1000)
        while is_running():
            try:
                await self._motor_processor.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("CognitiveLoop: motor tick error")
            await asyncio.sleep(interval)

    async def run_once_for_session(self, session_key: str) -> Any:
        active_count = self._active_task_count_provider(session_key)
        running_subagents = self._running_subagents_provider(session_key)
        return await self._session_processor(
            session_key,
            active_task_count=active_count,
            running_subagents=running_subagents,
        )
