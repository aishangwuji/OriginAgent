"""AgentHost — infrastructure lifecycle owner for the AgentLoop.

Owns MCP connection lifecycle, background task scheduling, run/stop
state machine, provider management, BDI deliberation engine, and
transcription.  Extracted from AgentLoop so the loop can focus purely
on message routing.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from loguru import logger

from OriginAgent.agent.local_awareness import (
    LocalAwarenessBackend,
    normalize_local_awareness_summary,
)

if TYPE_CHECKING:
    # Imported lazily at runtime inside _init_bdi_engine* to avoid a circular
    # import; declared here only so the field/property annotations below resolve
    # under static type checkers.
    from OriginAgent.bdi import DeliberationEngine


@dataclass(frozen=True)
class AgentHostDependencies:
    """Immutable dependency bundle for AgentHost.

    Phase 2a: tools, mcp_servers, cognitive_runtime, bus
    Phase 2b: provider, bdi, transcription dependencies
    Phase 2c: tenant identity
    """

    # Phase 2a
    tools: Any  # ToolRegistry
    mcp_servers: dict | None
    cognitive_runtime: Any  # AgentCognitiveRuntime
    bus: Any | None = None  # MessageBus

    # Phase 2b — Provider Management
    provider: Any = None  # LLMProvider
    model: str | None = None
    model_presets: dict | None = None
    model_preset: str | None = None
    provider_snapshot_loader: Any = None
    preset_snapshot_loader: Any = None
    runtime_model_publisher: Any = None
    provider_signature: Any = None
    runner: Any = None  # AgentRunner
    subagents: Any = None  # SubagentManager
    auxiliary_router: Any = None  # AuxiliaryLLMRouter
    background_review: Any = None  # BackgroundReviewService
    consolidator: Any = None  # Consolidator
    dream: Any = None  # Dream

    # Phase 2b — BDI
    workspace: Any = None  # Path
    bdi_config: Any = None  # BDIConfig | None
    meta_cognition_config: Any = None
    meta_cognition_audit: Any = None  # JsonlMetaCognitionAuditLedger | None
    event_bus: Any = None  # TypedEventBus | None — shared between WorldStateManager and BDI

    # Phase 2b — Transcription
    transcription_provider_config: dict | None = None
    tools_config: Any = None  # ToolsConfig

    # Phase 2c — Tenant identity
    tenants_config: Any = None  # TenantsConfig | None


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

        # ── BDI state ───────────────────────────────────────────
        self._desire_store: Any = None
        self._bdi_engines: dict[str, Any] = {}  # tenant_id -> DeliberationEngine
        self._legacy_bdi_engine: DeliberationEngine | None = None
        # Single-tenant BDI fallback (spec 3.18 / tech-debt Batch C13).
        #
        # Retention rationale: the multi-tenant path
        # (_init_bdi_engine_for_tenant) has NOT reached feature parity with
        # this legacy path, so the legacy path cannot yet be removed:
        #   1. No InnerMonologueEngine wiring — the legacy path registers
        #      set_on_cycle_complete -> InnerMonologueEngine.on_bdi_cycle; the
        #      per-tenant path does not.
        #   2. Missing config passthrough — max_desires_per_cycle and
        #      auto_create_from_foresight are not forwarded to the per-tenant
        #      DeliberationEngine.
        #   3. Per-tenant CronDesireBridge is a local variable, not stored on
        #      self._cron_bridge, so the "disable_cron" system action in
        #      _on_bdi_intention_for cannot reach it (hasattr check fails).
        #
        # Migration condition: once _init_bdi_engine_for_tenant closes the
        # three gaps above and a multi-tenant integration test covers the
        # inner-monologue + cron-bridge disable path, this field and
        # _init_bdi_engine() can be removed; bdi_engine will then read solely
        # from _bdi_engines.
        #
        # @DeferDecision — no fixed migration date: the gap is in optional
        #   cognitive features (inner monologue) that only single-tenant
        #   deployments currently exercise.  Re-evaluate when a multi-tenant
        #   deployment requests inner-monologue support.  Tracked in the
        #   tech-debt backlog under spec 3.18.
        self._inner_monologue_engine: Any = None

        # ── Transcription ───────────────────────────────────────
        self._transcription_provider: Any = self._build_transcription_provider(
            self._deps.transcription_provider_config
        )
        self._local_awareness_backend = LocalAwarenessBackend(
            tts_config=dict(
                (self._deps.transcription_provider_config or {}).get("tts_config") or {}
            ),
        )
        self._last_local_awareness_summary: dict[str, Any] = normalize_local_awareness_summary(
            self._deps.tools_config.local_awareness if self._deps.tools_config else {},
            backend=self._local_awareness_backend,
        )

        # ── BDI initialisation (lazy) ────────────────────────────
        self._init_bdi()

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
        if not self._deps.mcp_servers:
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
                self._deps.mcp_servers,
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

    # ── BDI Deliberation Engine ──────────────────────────────────

    @property
    def bdi_engine(self) -> Any | None:
        """Compat: return the default tenant's BDI engine for single-tenant mode.

        When tenants are configured, use _bdi_engines[tenant_id] instead.
        This property preserves backward compat for loop.py:429
        (self._bdi_engine = self._host._bdi_engine) and all existing callers.
        """
        if self._bdi_engines:
            # Multi-tenant: return first active tenant's engine
            for engine in self._bdi_engines.values():
                return engine
            return None
        return self._legacy_bdi_engine  # Single-tenant fallback — see retention rationale at declaration (spec 3.18)

    @property
    def desire_store(self) -> Any | None:
        return self._desire_store

    @property
    def inner_monologue_engine(self) -> Any | None:
        return self._inner_monologue_engine

    def _init_bdi(self) -> None:
        """Initialize BDI: legacy path if no tenants, per-tenant otherwise."""
        if self._deps.bdi_config is None:
            self._desire_store = None
            self._legacy_bdi_engine = None
            self._inner_monologue_engine = None
            return

        if self._deps.tenants_config and self._deps.tenants_config.tenants:
            # Multi-tenant: engines initialized lazily per-tenant on first message
            self._legacy_bdi_engine = None
            self._desire_store = None
            self._inner_monologue_engine = None
        else:
            # Single-tenant: use existing init path (unchanged behavior)
            self._init_bdi_engine_legacy()

    def _init_bdi_engine_legacy(self) -> None:
        """Existing single-tenant BDI init — preserved verbatim."""
        self._init_bdi_engine()

    def _init_bdi_engine_for_tenant(self, tenant: Any) -> None:
        """Initialize BDI for a single tenant (multi-tenant mode)."""
        if not tenant.bdi_enabled or tenant.tenant_id in self._bdi_engines:
            return

        from OriginAgent.bdi import DeliberationEngine, DesireStore
        from OriginAgent.bdi.cron_desire_bridge import CronDesireBridge
        from OriginAgent.bdi.cron_observation_store import CronObservationStore
        from OriginAgent.bdi.utility_reward_bridge import UtilityRewardBridge

        workspace = tenant.workspace_dir
        _sqlite = getattr(self._deps, "sqlite_stores", None)
        desire_store = DesireStore(workspace, sqlite_store=_sqlite.desires if _sqlite else None, jsonl_fallback_enabled=False)
        _cron_obs = CronObservationStore(workspace)
        _cron_obs._ensure_schema()
        _cron_bridge = CronDesireBridge(
            desire_store=desire_store,
            observation_store=_cron_obs,
            enabled=True,
        )
        _reward_bridge = UtilityRewardBridge(
            audit_ledger=self._deps.meta_cognition_audit,
            desire_store=desire_store,
        )
        engine = DeliberationEngine(
            workspace=workspace,
            store=desire_store,
            sqlite_stores=_sqlite,
            provider=self._deps.provider,
            model=self._deps.model or "",
            enabled=True,
            interval_s=getattr(self._deps.bdi_config, "interval_s", 120),
            on_intention=self._on_bdi_intention_for(tenant),
            cron_bridge=_cron_bridge,
            event_bus=self._deps.event_bus,
            use_actr_utility=True,
            reward_bridge=_reward_bridge,
        )
        self._bdi_engines[tenant.tenant_id] = engine

    def post_claim_init(
        self, channel: str, sender_id: str, tenant: Any
    ) -> None:
        """Post-``claim_pairing`` wiring: migrate the pairing-pending session
        file to the tenant's session key, then lazy-load the BDI engine.

        Called from ``cmd_pairing`` via the ``on_claim_success`` hook on
        ``handle_pairing_command``. The pairing store itself stays free of
        agent_host / session_manager concerns (rule 16 domain isolation):
        this method owns the post-claim side-effects.

        Session migration rationale: while a sender is in
        ``__pairing_pending__`` state, ``IdentityResolver.resolve`` returns
        a synthetic Tenant whose ``unified_session_key`` is
        ``tenant:pairing:{channel}:{sender_id}``, and the SessionManager
        persists that sender's turns under that key (file:
        ``sessions/tenant_pairing_<channel>_<sender>.jsonl``). After claim,
        the same sender resolves to the real Tenant with key
        ``tenant:<tenant_id>`` (file: ``sessions/tenant_<tenant_id>.jsonl``).
        Without migration, the user's pending-session history would be
        orphaned under the old key and invisible to the new tenant.

        BDI lazy-load: ``_init_bdi_engine_for_tenant`` is idempotent — it
        short-circuits when ``tenant.tenant_id`` is already in
        ``self._bdi_engines`` (rule 12). Calling it here eagerly at claim
        time satisfies the spec requirement "next message from this sender
        triggers ``_init_bdi_engine_for_tenant``" without needing a lazy
        hook on the message path.

        Note: ``tenants/_pairing/`` workspace directory is **not** migrated
        because the synthetic ``__pairing_pending__`` Tenant has
        ``bdi_enabled=False`` (see ``resolver.py:58-63``), so no BDI /
        DesireStore / CronObservationStore data was ever written there.
        Only the session JSONL file (under ``sessions/``) is migrated.
        """
        self._migrate_pairing_session_file(channel, sender_id, tenant)
        self._init_bdi_engine_for_tenant(tenant)

    def _migrate_pairing_session_file(
        self, channel: str, sender_id: str, tenant: Any
    ) -> None:
        """Rename the pairing-pending session file to the tenant's key.

        Source key matches ``IdentityResolver``'s ``__pairing_pending__``
        synthetic tenant: ``tenant:pairing:{channel}:{sender_id}``.
        Target key is ``tenant.unified_session_key`` (e.g. ``tenant:dad``).
        Both are run through ``SessionManager.safe_key`` so the on-disk
        filenames agree with what SessionManager will look up next.

        If the source file does not exist (sender never sent a turn while
        in ``__pairing_pending__``), this is a no-op. If the target file
        already exists, the source is left in place and a warning is
        logged — the operator can merge manually. Overwriting the target
        would silently destroy the tenant's prior session history.
        """
        from OriginAgent.session.manager import SessionManager
        from OriginAgent.utils.helpers import ensure_dir

        workspace = self._deps.workspace
        if workspace is None:
            logger.warning(
                "post_claim_init: no workspace on AgentHost — skipping session migration"
            )
            return

        source_key = f"tenant:pairing:{channel}:{sender_id}"
        target_key = tenant.unified_session_key
        sessions_dir = ensure_dir(workspace / "sessions")
        source_path = sessions_dir / f"{SessionManager.safe_key(source_key)}.jsonl"
        target_path = sessions_dir / f"{SessionManager.safe_key(target_key)}.jsonl"

        if not source_path.exists():
            # Sender never sent a turn while in __pairing_pending__ state —
            # nothing to migrate. This is the common case when claim happens
            # immediately after pairing approval.
            logger.debug(
                "post_claim_init: no pairing-pending session file at {} — nothing to migrate",
                source_path,
            )
            return

        if target_path.exists():
            # Rule 12 (idempotency) + rule 5 (don't destroy existing state):
            # do NOT overwrite. The operator must merge manually.
            logger.warning(
                "post_claim_init: target session file {} already exists; "
                "leaving source {} in place for manual merge",
                target_path, source_path,
            )
            return

        import os
        os.rename(str(source_path), str(target_path))
        logger.info(
            "post_claim_init: migrated pairing-pending session {} → {} for tenant={}",
            source_path.name, target_path.name, tenant.tenant_id,
        )

    def _on_bdi_intention_for(self, tenant: Any):
        """Create an on_intention callback scoped to *tenant*."""
        async def handler(intent: Any) -> None:
            if intent.action == "send_message":
                from OriginAgent.bus.events import OutboundMessage
                for binding in tenant.bindings:
                    channel = binding["channel"]
                    msg = OutboundMessage(
                        channel=channel,
                        content=intent.payload.get("text", ""),
                        chat_id=binding.get("chat_id", ""),
                        session_key=tenant.unified_session_key,
                    )
                    if self._deps.bus:
                        await self._deps.bus.publish_outbound(msg)
            elif intent.action == "system":
                sys_payload = intent.payload or {}
                sys_action = sys_payload.get("action")
                if sys_action == "disable_cron":
                    cron_job_id = sys_payload.get("cron_job_id", "")
                    if cron_job_id and hasattr(self, "_cron_bridge") and self._cron_bridge is not None:
                        self._cron_bridge.disable_cron_job(cron_job_id)
        return handler

    async def _on_bdi_intention(self, intent: Any) -> None:
        """Handle an intention formed by the BDI engine."""
        logger.info(
            "BDI: executing intention — desire={} action={} scope={}",
            intent.desire_id, intent.action, intent.scope,
        )
        if intent.action == "send_message":
            from OriginAgent.bus.events import OutboundMessage

            channel = intent.scope if intent.scope != "system" else "cli"
            msg = OutboundMessage(
                channel=channel,
                content=intent.payload.get("text", ""),
                chat_id="",
                session_key="bdi:deliberation",
            )
            bus = self._deps.bus
            if bus is not None:
                ok = await bus.publish_outbound(msg)
                if not ok:
                    logger.error(
                        "BDI: Failed to publish intention message for desire={}",
                        intent.desire_id,
                    )
        elif intent.action == "system":
            sys_payload = intent.payload or {}
            sys_action = sys_payload.get("action")
            if sys_action == "disable_cron":
                cron_job_id = sys_payload.get("cron_job_id", "")
                if cron_job_id and hasattr(self, "_cron_bridge") and self._cron_bridge is not None:
                    self._cron_bridge.disable_cron_job(
                        cron_job_id,
                        cron_service=getattr(self._deps, "cron_service", None),
                    )
                    logger.info(
                        "BDI: disabled cron job {} for desire {}",
                        cron_job_id, intent.desire_id,
                    )
            else:
                logger.warning("BDI: unknown system action {!r}", sys_action)

    def _init_bdi_engine(self) -> None:
        """Initialise the BDI deliberation engine if configured."""
        bdi_config = self._deps.bdi_config
        if bdi_config is None:
            self._desire_store = None
            self._legacy_bdi_engine = None
            self._inner_monologue_engine = None
            return

        from OriginAgent.bdi import DeliberationEngine, DesireStore
        from OriginAgent.bdi.cron_desire_bridge import CronDesireBridge
        from OriginAgent.bdi.cron_observation_store import CronObservationStore
        from OriginAgent.bdi.utility_reward_bridge import UtilityRewardBridge

        _sqlite = getattr(self._deps, "sqlite_stores", None)
        self._desire_store = DesireStore(
            self._deps.workspace,
            sqlite_store=_sqlite.desires if _sqlite else None,
            jsonl_fallback_enabled=False,
        )

        # ── Cron-BDI bridge ───────────────────────────────────────
        _cron_obs = CronObservationStore(self._deps.workspace)
        _cron_obs._ensure_schema()
        self._cron_bridge = CronDesireBridge(
            desire_store=self._desire_store,
            observation_store=_cron_obs,
            enabled=bool(bdi_config.enabled),
        )

        # ── Utility-Reward bridge (ACT-R utility learning) ───────
        _reward_bridge = UtilityRewardBridge(
            audit_ledger=self._deps.meta_cognition_audit,
            desire_store=self._desire_store,
        )

        self._legacy_bdi_engine = DeliberationEngine(
            workspace=self._deps.workspace,
            store=self._desire_store,
            provider=self._deps.provider,
            model=bdi_config.model_override or (self._deps.model or ""),
            enabled=bdi_config.enabled,
            interval_s=bdi_config.interval_s,
            max_desires_per_cycle=bdi_config.max_desires_per_cycle,
            auto_create_from_foresight=bdi_config.auto_create_from_foresight,
            on_intention=self._on_bdi_intention,
            cron_bridge=self._cron_bridge,
            event_bus=self._deps.event_bus,
            use_actr_utility=True,
            reward_bridge=_reward_bridge,
        )

        # InnerMonologueEngine
        self._inner_monologue_engine = None
        _ime_enabled = getattr(
            self._deps.meta_cognition_config or {},
            "inner_monologue_enabled",
            True,
        )
        if _ime_enabled:
            from OriginAgent.agent.inner_monologue_engine import InnerMonologueEngine

            self._inner_monologue_engine = InnerMonologueEngine(
                workspace=self._deps.workspace,
                deliberation_engine=self._legacy_bdi_engine,
                substrate=None,
                desire_store=self._desire_store,
                enabled=_ime_enabled,
            )
            self._legacy_bdi_engine.set_on_cycle_complete(
                self._inner_monologue_engine.on_bdi_cycle
            )

        logger.info("BDI: DeliberationEngine initialized via AgentHost")

    async def start_bdi(self) -> None:
        """Start the BDI engine(s) if configured."""
        if self._bdi_engines:
            for tenant_id, engine in self._bdi_engines.items():
                await engine.start()
                logger.info("BDI: started engine for tenant {}", tenant_id)
        elif self._legacy_bdi_engine is not None:
            await self._legacy_bdi_engine.start()

    def stop_bdi(self) -> None:
        """Stop the BDI engine(s) if configured."""
        if self._bdi_engines:
            for engine in self._bdi_engines.values():
                engine.stop()
        elif self._legacy_bdi_engine is not None:
            self._legacy_bdi_engine.stop()

    # ── Provider Management ──────────────────────────────────────

    def _apply_provider_snapshot(self, snapshot: Any) -> None:
        """Propagate provider changes to sub-services (runner, subagents, etc.).

        Loop-level identity fields (provider, model, context_window_tokens)
        are updated by the AgentLoop compat shell.  This method propagates
        the change to sub-services via AgentHost's dependency references.
        """
        provider = snapshot.provider
        model = snapshot.model
        context_window_tokens = snapshot.context_window_tokens

        if self._deps.runner is not None:
            self._deps.runner.provider = provider
        if self._deps.subagents is not None:
            self._deps.subagents.set_provider(provider, model)
        if self._deps.auxiliary_router is not None:
            self._deps.auxiliary_router.set_primary(provider, model)
        if self._deps.background_review is not None:
            self._deps.background_review.set_provider(provider, model)
        if self._deps.consolidator is not None:
            self._deps.consolidator.set_provider(provider, model, context_window_tokens)
        if self._deps.dream is not None:
            self._deps.dream.set_provider(provider, model)

        logger.info(
            "Runtime model propagated to sub-services via AgentHost: {} (ctx {})",
            model,
            context_window_tokens,
        )
        if self._deps.runtime_model_publisher:
            self._deps.runtime_model_publisher(model, self._deps.model_preset)

    def refresh_provider_snapshot(self) -> Any | None:
        """Load the latest provider config and return the new snapshot (or None)."""
        if self._deps.model_preset and self._deps.model_preset != "default":
            if self._deps.preset_snapshot_loader is None:
                return None
            try:
                snapshot = self._deps.preset_snapshot_loader(self._deps.model_preset)
            except Exception:
                logger.exception("Failed to refresh model preset config")
                return None
            if snapshot.signature == self._deps.provider_signature:
                return None
            return snapshot

        if self._deps.provider_snapshot_loader is None:
            return None
        try:
            snapshot = self._deps.provider_snapshot_loader()
        except Exception:
            logger.exception("Failed to refresh provider config")
            return None
        if snapshot.signature == self._deps.provider_signature:
            return None
        return snapshot

    def build_preset_snapshot(self, name: str) -> Any | None:
        """Build a runtime snapshot for the named preset."""
        from OriginAgent.agent import model_presets as preset_helpers

        preset_name = preset_helpers.normalize_preset_name(name, self._deps.model_presets)
        return preset_helpers.build_runtime_preset_snapshot(
            name=preset_name,
            presets=self._deps.model_presets,
            provider=self._deps.provider,
            loader=self._deps.preset_snapshot_loader,
        )

    # ── Transcription ───────────────────────────────────────────

    @property
    def transcription_provider(self) -> Any | None:
        return self._transcription_provider

    @property
    def local_awareness_backend(self) -> Any:
        return self._local_awareness_backend

    @staticmethod
    def _build_transcription_provider(config: dict | None = None) -> Any | None:
        """Build the transcription provider from config (moved from AgentLoop)."""
        from OriginAgent.providers.transcription import (
            GroqTranscriptionProvider,
            OpenAITranscriptionProvider,
            VolcengineTranscriptionProvider,
        )

        config = dict(config or {})
        provider_name = str(config.get("provider") or "groq").strip()
        provider_key = str(config.get("api_key") or "").strip()
        provider_base = str(config.get("api_base") or "").strip()
        language = config.get("language")
        resource_id = config.get("resource_id")
        user_id = config.get("user_id")
        if not provider_key:
            return None
        try:
            if provider_name == "openai":
                return OpenAITranscriptionProvider(
                    api_key=provider_key, api_base=provider_base or None, language=language or None
                )
            if provider_name == "volcengine":
                return VolcengineTranscriptionProvider(
                    api_key=provider_key,
                    api_base=provider_base or None,
                    language=language or None,
                    resource_id=resource_id or None,
                    user_id=user_id or None,
                )
            return GroqTranscriptionProvider(
                api_key=provider_key, api_base=provider_base or None, language=language or None
            )
        except Exception:
            return None
