"""AgentHost — infrastructure lifecycle owner for the AgentLoop.

Owns MCP connection lifecycle, background task scheduling, and the
run/stop/shutdown state machine.  Extracted from AgentLoop so the
loop can focus purely on message routing.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class AgentHostDependencies:
    """Immutable dependency bundle for AgentHost.

    New fields are appended in Phase 2b (Provider, BDI, Transcription)
    without changing AgentHost.__init__.
    """

    tools: Any  # ToolRegistry
    mcp_servers: dict | None
    cognitive_runtime: Any  # AgentCognitiveRuntime
    bus: Any | None = None  # MessageBus (needed by loop.run())


class AgentHost:
    """Infrastructure lifecycle owner — MCP, background tasks, run/stop.

    Extracted from AgentLoop to give each concern a single home:

    * **MCP lifecycle** — connect, runtime, shutdown
    * **Background tasks** — scheduled coroutines drained on shutdown
    * **Run/stop state machine** — ``_running`` flag, active-intent loop
    """

    def __init__(self, deps: AgentHostDependencies) -> None:
        self._deps = deps

        # ── MCP state ────────────────────────────────────────────
        self._mcp_lifecycle_lock = asyncio.Lock()
        self._mcp_state: str = "disconnected"
        self._mcp_connected = False
        self._mcp_connecting = False
        self._mcp_startup_error: BaseException | None = None
        self._mcp_stacks: dict[str, Any] = {}
        self._mcp_snapshot: dict[str, Any] = {}
        self._mcp_ready: asyncio.Future[bool] | None = None
        self._mcp_runtime_task: asyncio.Task[None] | None = None
        self._mcp_shutdown_event: asyncio.Event | None = None

        # ── Background tasks ─────────────────────────────────────
        self._background_tasks: set[asyncio.Task] = set()

        # ── Lifecycle ────────────────────────────────────────────
        self._running = False
        self._active_intent_task: asyncio.Task[None] | None = None

    # ── Public properties (read from AgentLoop compat shims) ─────

    @property
    def running(self) -> bool:
        return self._running

    @property
    def mcp_connected(self) -> bool:
        return self._mcp_connected

    @property
    def background_tasks(self) -> set[asyncio.Task]:
        return self._background_tasks

    @property
    def active_intent_task(self) -> asyncio.Task[None] | None:
        return self._active_intent_task

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> dict[str, bool]:
        """Bring infrastructure online.  Returns a status map so the
        caller can decide whether partial failures are fatal."""
        self._running = True
        status: dict[str, bool] = {"mcp_ready": False, "active_intent_started": False}
        try:
            await self._connect_mcp()
            status["mcp_ready"] = self._mcp_connected
        except Exception:
            logger.exception("MCP connection failed during AgentHost.start()")
        status["active_intent_started"] = self._start_active_intent_loop()
        logger.info("AgentHost started (mcp_ready={}, active_intent_started={})",
                     status["mcp_ready"], status["active_intent_started"])
        return status

    def stop(self) -> None:
        """Graceful signal — sets running=False."""
        self._running = False
        logger.info("AgentHost stopping")

    async def shutdown(self) -> None:
        """Hard drain: cancel active-intent, drain background tasks, close MCP."""
        if self._active_intent_task is not None:
            self._active_intent_task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.shield(self._active_intent_task)
            self._active_intent_task = None
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        await self._close_mcp()

    # ── Background tasks ─────────────────────────────────────────

    def schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    # ── Active Intent ────────────────────────────────────────────

    def start_active_intent_loop(
        self,
        existing_task: asyncio.Task[None] | None = None,
    ) -> asyncio.Task[None] | None:
        """Start (or restart) the active-intent background loop."""
        return self._deps.cognitive_runtime.start_active_intent_loop(existing_task)

    def _start_active_intent_loop(self) -> bool:
        self._active_intent_task = self._deps.cognitive_runtime.start_active_intent_loop(
            self._active_intent_task
        )
        return self._active_intent_task is not None

    # ── MCP lifecycle ────────────────────────────────────────────

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if not self._mcp_servers:
            return
        while True:
            ready: asyncio.Future[bool] | None = None
            runtime_task: asyncio.Task[None] | None = None
            async with self._mcp_lifecycle_lock:
                if self._mcp_state == "connected":
                    return
                if self._mcp_state == "connecting":
                    ready = self._mcp_ready
                elif self._mcp_state == "closing":
                    runtime_task = self._mcp_runtime_task
                else:
                    ready = asyncio.get_running_loop().create_future()
                    self._mcp_state = "connecting"
                    self._mcp_connected = False
                    self._mcp_connecting = True
                    self._mcp_startup_error = None
                    self._mcp_ready = ready
                    self._mcp_shutdown_event = asyncio.Event()
                    self._mcp_runtime_task = asyncio.create_task(
                        self._run_mcp_runtime(ready, self._mcp_shutdown_event),
                        name="originagent-mcp-runtime",
                    )
            if runtime_task is not None:
                with suppress(Exception):
                    await asyncio.shield(runtime_task)
                continue
            if ready is not None:
                with suppress(Exception):
                    await asyncio.shield(ready)
                return
            return

    async def _run_mcp_runtime(
        self,
        ready: asyncio.Future[bool],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Own the MCP connection lifecycle inside a single task."""
        from OriginAgent.agent.tools.mcp import connect_mcp_servers

        stacks: dict[str, AsyncExitStack] = {}
        clear_snapshot_on_exit = False
        try:
            stacks = await connect_mcp_servers(
                self._mcp_servers,
                self._deps.tools,
                snapshot_out=self._mcp_snapshot,
            )
            if not stacks:
                logger.warning("No MCP servers connected successfully (will retry next message)")
                async with self._mcp_lifecycle_lock:
                    self._mcp_stacks = {}
                    self._mcp_connected = False
                    self._mcp_connecting = False
                    self._mcp_state = "disconnected"
                    if not ready.done():
                        ready.set_result(False)
                return

            async with self._mcp_lifecycle_lock:
                self._mcp_stacks = stacks
                self._mcp_connected = True
                self._mcp_connecting = False
                self._mcp_state = "connected"
                self._mcp_startup_error = None
                if not ready.done():
                    ready.set_result(True)

            await shutdown_event.wait()
            clear_snapshot_on_exit = True
        except asyncio.CancelledError:
            clear_snapshot_on_exit = True
            logger.warning("MCP runtime cancelled (will retry next message)")
            async with self._mcp_lifecycle_lock:
                self._mcp_stacks.clear()
                self._mcp_snapshot.clear()
                self._mcp_connected = False
                self._mcp_connecting = False
                self._mcp_state = "disconnected"
                if not ready.done():
                    ready.set_result(False)
            raise
        except BaseException as e:
            clear_snapshot_on_exit = True
            logger.warning("Failed to connect MCP servers (will retry next message): {}", e)
            async with self._mcp_lifecycle_lock:
                self._mcp_stacks.clear()
                self._mcp_snapshot.clear()
                self._mcp_connected = False
                self._mcp_connecting = False
                self._mcp_state = "disconnected"
                self._mcp_startup_error = e
                if not ready.done():
                    ready.set_result(False)
            return
        finally:
            for name, stack in stacks.items():
                try:
                    await stack.aclose()
                except (RuntimeError, BaseExceptionGroup):
                    logger.debug("MCP server '{}' cleanup error (can be ignored)", name)
            async with self._mcp_lifecycle_lock:
                if self._mcp_runtime_task is asyncio.current_task():
                    self._mcp_stacks.clear()
                    if clear_snapshot_on_exit:
                        self._mcp_snapshot.clear()
                    self._mcp_connected = False
                    self._mcp_connecting = False
                    self._mcp_state = "disconnected"
                    self._mcp_runtime_task = None
                    self._mcp_ready = None
                    self._mcp_shutdown_event = None

    async def _close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        runtime_task: asyncio.Task[None] | None = None
        async with self._mcp_lifecycle_lock:
            runtime_task = self._mcp_runtime_task
            shutdown_event = self._mcp_shutdown_event
            if runtime_task is None:
                self._mcp_connected = False
                self._mcp_connecting = False
                self._mcp_state = "disconnected"
                self._mcp_stacks.clear()
                self._mcp_snapshot.clear()
                return
            self._mcp_connected = False
            self._mcp_connecting = False
            self._mcp_state = "closing"
            if shutdown_event is not None:
                shutdown_event.set()
        with suppress(Exception):
            await asyncio.shield(runtime_task)
