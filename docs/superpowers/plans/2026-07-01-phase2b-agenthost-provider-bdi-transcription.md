# Phase 2b: AgentHost — Provider Management + BDI + Transcription

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Provider Management (~60 lines), BDI Engine (~65 lines), and Transcription (~25 lines) from `AgentLoop` into `AgentHost`, completing the infrastructure lifecycle extraction started in Phase 2a.

**Architecture:** Extend `AgentHostDependencies` with 19 new fields for provider, BDI, and transcription dependencies. Move `_apply_provider_snapshot`, BDI init, and `_build_transcription_provider` into AgentHost. AgentLoop keeps compat shells that delegate. `agent_loop_components.py` unchanged (field defaults preserved for backward compat).

**Tech Stack:** Python 3.11+, asyncio, dataclasses

## Global Constraints

- `AgentLoop.__init__`, `from_config`, `from_options`, `run()`, `stop()`, `process_direct()` signatures MUST NOT change
- All existing tests MUST pass without modification
- `loop._transcription_provider`, `loop._local_awareness_backend`, `loop._bdi_engine` attribute access MUST continue to work (tests read these directly)
- `AgentLoop.__new__(AgentLoop)` bypass pattern MUST continue to work
- `ProviderSnapshot` is referenced by `_apply_provider_snapshot` — keep the import in agent_host.py local (lazy import pattern)
- `_register_default_tools` passes `provider_snapshot_loader` to tools — this reference must remain valid

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `OriginAgent/agent/agent_host.py` | **Modify** | Extend `AgentHostDependencies` + add provider/BDI/transcription methods |
| `OriginAgent/agent/loop.py` | **Modify** | Delegate provider/BDI/transcription to `_host`; keep compat shells + attributes |
| `OriginAgent/agent/agent_loop_components.py` | **No change** | Field defaults preserved for backward compat |

### Dependency Flow

```
AgentHostDependencies (extended)
├── Phase 2a: tools, mcp_servers, cognitive_runtime, bus
├── Provider (new): provider, model, model_presets, model_preset,
│   provider_snapshot_loader, preset_snapshot_loader, runtime_model_publisher,
│   provider_signature, runner, subagents, auxiliary_router,
│   background_review, consolidator, dream
├── BDI (new): workspace, bdi_config, meta_cognition_config
└── Transcription (new): transcription_provider_config, tools_config
```

---

### Task 1: Extend AgentHostDependencies + Wire into AgentLoop.__init__

**Files:**
- Modify: `OriginAgent/agent/agent_host.py:19-29` (AgentHostDependencies)
- Modify: `OriginAgent/agent/loop.py:493-500` (AgentHost construction)

**Interfaces:**
- Consumes: All provider/BDI/transcription fields from AgentLoop
- Produces: Extended `AgentHostDependencies` with 19 new fields

- [ ] **Step 1: Extend AgentHostDependencies**

Replace the current `AgentHostDependencies` in `agent_host.py`:

```python
@dataclass(frozen=True)
class AgentHostDependencies:
    """Immutable dependency bundle for AgentHost.

    Phase 2a: tools, mcp_servers, cognitive_runtime, bus
    Phase 2b: provider, bdi, transcription dependencies
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

    # Phase 2b — Transcription
    transcription_provider_config: dict | None = None
    tools_config: Any = None  # ToolsConfig
```

- [ ] **Step 2: Move AgentHost construction EARLIER in AgentLoop.__init__**

**Critical:** AgentHost must be constructed BEFORE the transcription and BDI compat blocks so they can read from `_host`.  Currently AgentHost is at line ~492 (after the BDI block).  Move it to right after the `build_loop_components` → `setattr` loop (around line 352), replacing BOTH the old transcription init AND the old AgentHost construction:

```python
        # ── AgentHost: infrastructure lifecycle ──────────────────────────
        # Constructed early so transcription/BDI compat attributes can
        # alias _host-owned state.
        gw = getattr(effective_config, "gateway", None) if effective_config else None
        _bdi_cfg: Any = getattr(gw, "bdi", None) if gw is not None else None
        self._host = AgentHost(AgentHostDependencies(
            # Phase 2a
            tools=self.tools,
            mcp_servers=self._mcp_servers,
            cognitive_runtime=self._cognitive_runtime,
            # Phase 2b — Provider
            provider=self.provider,
            model=self.model,
            model_presets=self.model_presets,
            model_preset=self.model_preset,
            provider_snapshot_loader=self._provider_snapshot_loader,
            preset_snapshot_loader=self._preset_snapshot_loader,
            runtime_model_publisher=self._runtime_model_publisher,
            provider_signature=self._provider_signature,
            runner=self.runner,
            subagents=self.subagents,
            auxiliary_router=self.auxiliary_router,
            background_review=self.background_review,
            consolidator=self.consolidator,
            dream=self.dream,
            # Phase 2b — BDI
            workspace=self.workspace,
            bdi_config=_bdi_cfg if _bdi_cfg and _bdi_cfg.enabled else None,
            meta_cognition_config=getattr(self, "_meta_cognition_config", None),
            # Phase 2b — Transcription
            transcription_provider_config=transcription_provider_config,
            tools_config=self.tools_config,
        ))
        # Re-point _background_tasks so tests and compat code that read
        # loop._background_tasks see the host-owned set.
        self._background_tasks = self._host._background_tasks  # type: ignore[assignment]

        # ── Transcription compat (aliases from AgentHost) ────────────────
        self._transcription_provider = self._host._transcription_provider
        self._local_awareness_backend = self._host._local_awareness_backend
        self._last_local_awareness_summary = self._host._last_local_awareness_summary

        # ── BDI compat (aliases from AgentHost) ─────────────────────────
        self._desire_store = self._host._desire_store
        self._bdi_engine = self._host._bdi_engine
        self._inner_monologue_engine = self._host._inner_monologue_engine
```

This replaces three code sections:
1. The old transcription init (lines 354-361)
2. The old BDI block (lines 424-491)
3. The old AgentHost construction (lines 493-500)

All in one consolidated block at the original transcription location.

- [ ] **Step 3: Verify module imports cleanly**

```bash
./.venv/Scripts/python.exe -c "from OriginAgent.agent.loop import AgentLoop; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/agent_host.py OriginAgent/agent/loop.py
git commit -m "feat: extend AgentHostDependencies for Phase 2b (Provider, BDI, Transcription)"
```

---

### Task 2: Move _apply_provider_snapshot into AgentHost

**Files:**
- Modify: `OriginAgent/agent/agent_host.py` (add `_apply_provider_snapshot`)
- Modify: `OriginAgent/agent/loop.py` (replace with compat shell)

**Interfaces:**
- Consumes: `self._deps.runner`, `self._deps.subagents`, etc.
- Produces: `AgentHost._apply_provider_snapshot(snapshot: ProviderSnapshot) -> None`

- [ ] **Step 1: Add `_apply_provider_snapshot` to AgentHost**

Insert after the `_close_mcp` method in `agent_host.py`:

```python
    # ── Provider Management ──────────────────────────────────────

    def _apply_provider_snapshot(self, snapshot: Any) -> None:
        """Swap model/provider for future turns without disturbing an active one."""
        from OriginAgent.agent import model_presets as preset_helpers

        _provider_signature = self._deps.provider_signature
        if snapshot.signature == _provider_signature:
            return
        provider = snapshot.provider
        model = snapshot.model
        context_window_tokens = snapshot.context_window_tokens

        # Update identity on the loop (done via compat shell)
        # The compat shell on AgentLoop handles self.provider / self.model /
        # self.context_window_tokens updates.  Here we propagate to sub-services.
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
            "Runtime model updated via AgentHost: {} (context window {})",
            model,
            context_window_tokens,
        )
        if self._deps.runtime_model_publisher:
            self._deps.runtime_model_publisher(model, self._deps.model_preset)
```

- [ ] **Step 2: Replace AgentLoop._apply_provider_snapshot with compat shell**

In `loop.py`, replace the existing `_apply_provider_snapshot` method:

```python
    def _apply_provider_snapshot(self, snapshot: ProviderSnapshot) -> None:
        """Swap model/provider for future turns without disturbing an active one.

        Delegates sub-service propagation to AgentHost; updates loop-level
        identity fields directly.
        """
        if snapshot.signature == self._provider_signature:
            return
        old_model = self.model
        old_context_window_tokens = self.context_window_tokens
        self.provider = snapshot.provider
        self.model = snapshot.model
        self.context_window_tokens = snapshot.context_window_tokens
        self._provider_signature = snapshot.signature
        self._default_selection_signature = preset_helpers.default_selection_signature(
            snapshot.signature
        )
        logger.info(
            "Runtime model updated for next turn: {} -> {} (context window {} -> {})",
            old_model,
            snapshot.model,
            old_context_window_tokens,
            snapshot.context_window_tokens,
        )
        if self._runtime_model_publisher:
            self._runtime_model_publisher(snapshot.model, self.model_preset)
        # Propagate to sub-services via AgentHost
        if hasattr(self, "_host") and self._host is not None:
            self._host._apply_provider_snapshot(snapshot)
```

- [ ] **Step 3: Run provider-related tests**

```bash
./.venv/Scripts/python.exe -m pytest tests/agent/ -k "provider" -v 2>&1 | tail -10
```

Expected: All tests in scope pass.

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/agent_host.py OriginAgent/agent/loop.py
git commit -m "feat: move _apply_provider_snapshot sub-service propagation to AgentHost"
```

---

### Task 3: Move _refresh_provider_snapshot and set_model_preset delegation to AgentHost

**Files:**
- Modify: `OriginAgent/agent/agent_host.py` (add `refresh_provider_snapshot`, `set_model_preset`)
- Modify: `OriginAgent/agent/loop.py` (convert to compat shells)

**Interfaces:**
- Consumes: `self._deps.provider_snapshot_loader`, `self._deps.preset_snapshot_loader`, `self._deps.model_presets`
- Produces: `AgentHost.refresh_provider_snapshot()`, `AgentHost.set_model_preset(name: str)`

- [ ] **Step 1: Add provider refresh methods to AgentHost**

Insert after `_apply_provider_snapshot` in `agent_host.py`:

```python
    def refresh_provider_snapshot(self) -> ProviderSnapshot | None:
        """Load the latest provider config and return the new snapshot (or None).

        The caller (AgentLoop compat shell) applies the snapshot to loop-level
        identity fields and then calls ``_apply_provider_snapshot`` for
        sub-service propagation.
        """
        from OriginAgent.agent import model_presets as preset_helpers

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
```

- [ ] **Step 2: Replace AgentLoop._refresh_provider_snapshot with compat shell**

In `loop.py`:

```python
    def _refresh_provider_snapshot(self) -> None:
        """Refresh the active provider snapshot before each turn.

        Delegates snapshot loading to AgentHost when available; falls back
        to the original logic for callers that bypass ``__init__``.
        """
        if hasattr(self, "_host") and self._host is not None:
            snapshot = self._host.refresh_provider_snapshot()
            if snapshot is not None:
                self._apply_provider_snapshot(snapshot)
            return

        # Fallback: original logic for tests that bypass __init__
        if self.model_preset and self.model_preset != "default":
            if self._preset_snapshot_loader is None:
                return
            try:
                snapshot = self._preset_snapshot_loader(self.model_preset)
            except Exception:
                logger.exception("Failed to refresh model preset config")
                return
            if snapshot.signature == self._provider_signature:
                return
            self._apply_provider_snapshot(snapshot)
            return

        if self._provider_snapshot_loader is None:
            return
        try:
            snapshot = self._provider_snapshot_loader()
        except Exception:
            logger.exception("Failed to refresh provider config")
            return
        if snapshot.signature == self._provider_signature:
            return
        self.model_preset = "default"
        self._apply_provider_snapshot(snapshot)
```

- [ ] **Step 3: Replace AgentLoop.set_model_preset with compat shell**

In `loop.py`:

```python
    def set_model_preset(self, name: str) -> None:
        """Switch the active runtime model preset for subsequent turns.

        Delegates snapshot building to AgentHost; applies identity updates
        to loop-level fields directly.
        """
        if hasattr(self, "_host") and self._host is not None:
            snapshot = self._host.build_preset_snapshot(name)
            if snapshot is not None:
                from OriginAgent.agent import model_presets as preset_helpers
                self.model_preset = preset_helpers.normalize_preset_name(name, self.model_presets)
                self._apply_provider_snapshot(snapshot)
            return

        # Fallback for tests that bypass __init__
        preset_name = preset_helpers.normalize_preset_name(name, self.model_presets)
        snapshot = preset_helpers.build_runtime_preset_snapshot(
            name=preset_name,
            presets=self.model_presets,
            provider=self.provider,
            loader=self._preset_snapshot_loader,
        )
        self.model_preset = preset_name
        self._apply_provider_snapshot(snapshot)
```

- [ ] **Step 4: Run provider-related tests**

```bash
./.venv/Scripts/python.exe -m pytest tests/agent/ -k "provider or preset or model" -v 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/agent/agent_host.py OriginAgent/agent/loop.py
git commit -m "feat: delegate provider refresh and model preset switching to AgentHost"
```

---

### Task 4: Move BDI Engine initialization to AgentHost

**Files:**
- Modify: `OriginAgent/agent/agent_host.py` (add `_init_bdi_engine`)
- Modify: `OriginAgent/agent/loop.py` (replace BDI block with delegation)

**Interfaces:**
- Consumes: `self._deps.workspace`, `self._deps.bdi_config`, `self._deps.meta_cognition_config`, `self._deps.provider`, `self._deps.model`, `self._deps.bus`
- Produces: `AgentHost._desire_store`, `AgentHost._bdi_engine`, `AgentHost._inner_monologue_engine`

- [ ] **Step 1: Add BDI init and lifecycle methods to AgentHost**

Insert before `# ── Provider Management ──` section in `agent_host.py`:

```python
    # ── BDI Deliberation Engine ──────────────────────────────────

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

    def init_bdi_engine(self) -> None:
        """Initialise the BDI deliberation engine if configured."""
        bdi_config = self._deps.bdi_config
        if bdi_config is None:
            self._desire_store = None
            self._bdi_engine = None
            self._inner_monologue_engine = None
            return

        from OriginAgent.bdi import DesireStore, DeliberationEngine

        self._desire_store = DesireStore(self._deps.workspace)

        self._bdi_engine = DeliberationEngine(
            workspace=self._deps.workspace,
            store=self._desire_store,
            provider=self._deps.provider,
            model=bdi_config.model_override or (self._deps.model or ""),
            enabled=bdi_config.enabled,
            interval_s=bdi_config.interval_s,
            max_desires_per_cycle=bdi_config.max_desires_per_cycle,
            auto_create_from_foresight=bdi_config.auto_create_from_foresight,
            on_intention=self._on_bdi_intention,
        )

        # InnerMonologueEngine
        self._inner_monologue_engine: Any = None
        _ime_enabled = getattr(
            self._deps.meta_cognition_config,
            "inner_monologue_enabled",
            True,
        )
        if _ime_enabled:
            from OriginAgent.agent.inner_monologue_engine import InnerMonologueEngine

            self._inner_monologue_engine = InnerMonologueEngine(
                workspace=self._deps.workspace,
                deliberation_engine=self._bdi_engine,
                substrate=None,  # _thought_substrate not on deps
                desire_store=self._desire_store,
                enabled=_ime_enabled,
            )
            self._bdi_engine.set_on_cycle_complete(
                self._inner_monologue_engine.on_bdi_cycle
            )

        logger.info("BDI: DeliberationEngine initialized via AgentHost")

    async def start_bdi(self) -> None:
        """Start the BDI engine if configured."""
        if self._bdi_engine is not None:
            await self._bdi_engine.start()

    def stop_bdi(self) -> None:
        """Stop the BDI engine if configured."""
        if self._bdi_engine is not None:
            self._bdi_engine.stop()

    @property
    def bdi_engine(self) -> Any | None:
        return self._bdi_engine

    @property
    def desire_store(self) -> Any | None:
        return self._desire_store

    @property
    def inner_monologue_engine(self) -> Any | None:
        return self._inner_monologue_engine
```

Also add these fields to `AgentHost.__init__` (after the `_active_intent_task` line):

```python
        # ── BDI state ───────────────────────────────────────────
        self._desire_store: Any = None
        self._bdi_engine: Any = None
        self._inner_monologue_engine: Any = None
```

- [ ] **Step 2: Delete old BDI block from AgentLoop.__init__**

The BDI block (lines 424-491 in old code) is already replaced by the compat attributes set in Task 1 Step 2.  The `init_bdi_engine()` call happens inside `AgentHost.__init__` (Task 4 Step 1).  No additional changes needed — the compat attributes `self._desire_store`, `self._bdi_engine`, `self._inner_monologue_engine` are already aliased from `self._host`.

- [ ] **Step 3: Update run() and stop() to delegate BDI to _host**

In `run()`, replace `if self._bdi_engine: await self._bdi_engine.start()` with:

```python
        await self._host.start_bdi()
```

In `stop()`, replace `if self._bdi_engine: self._bdi_engine.stop()` with:

```python
        self._host.stop_bdi()
```

- [ ] **Step 4: Verify module imports cleanly**

```bash
./.venv/Scripts/python.exe -c "from OriginAgent.agent.loop import AgentLoop; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/agent/agent_host.py OriginAgent/agent/loop.py
git commit -m "feat: move BDI engine initialization and lifecycle to AgentHost"
```

---

### Task 5: Move Transcription provider to AgentHost

**Files:**
- Modify: `OriginAgent/agent/agent_host.py` (add `_build_transcription_provider`, `local_awareness_backend`)
- Modify: `OriginAgent/agent/loop.py` (replace with delegation, keep compat attributes)

**Interfaces:**
- Consumes: `self._deps.transcription_provider_config`, `self._deps.tools_config`
- Produces: `AgentHost._transcription_provider`, `AgentHost._local_awareness_backend`, `AgentHost._last_local_awareness_summary`

- [ ] **Step 1: Add transcription to AgentHost**

Add to `AgentHost.__init__` (after BDI state):

```python
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
```

Add the `_build_transcription_provider` static/instance method:

```python
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
```

Add imports to `agent_host.py` top:

```python
from OriginAgent.agent.local_awareness import LocalAwarenessBackend, normalize_local_awareness_summary
```

Add these properties to AgentHost:

```python
    @property
    def transcription_provider(self) -> Any | None:
        return self._transcription_provider

    @property
    def local_awareness_backend(self) -> Any:
        return self._local_awareness_backend
```

- [ ] **Step 2: Delete old transcription init from AgentLoop.__init__**

The transcription init (lines 354-361 in old code) is already replaced by the compat attributes set in Task 1 Step 2.  No additional changes needed — the compat attributes `self._transcription_provider`, `self._local_awareness_backend`, `self._last_local_awareness_summary` are already aliased from `self._host`.

- [ ] **Step 3: Remove _build_transcription_provider from AgentLoop**

Delete the `_build_transcription_provider` method from `loop.py` (it's now on AgentHost).

- [ ] **Step 4: Verify module imports cleanly**

```bash
./.venv/Scripts/python.exe -c "from OriginAgent.agent.loop import AgentLoop; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Run transcription/local-awareness tests**

```bash
./.venv/Scripts/python.exe -m pytest tests/tools/test_local_awareness_tools.py tests/agent/test_loop_runtime_context.py -v 2>&1 | tail -10
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/agent/agent_host.py OriginAgent/agent/loop.py
git commit -m "feat: move transcription provider to AgentHost"
```

---

### Task 6: Final verification

**Files:**
- None (verification only)

- [ ] **Step 1: Run full agent test suite**

```bash
./.venv/Scripts/python.exe -m pytest tests/agent/ -v 2>&1 | tail -5
```

- [ ] **Step 2: Run runtime context tests**

```bash
./.venv/Scripts/python.exe -m pytest tests/agent/test_loop_runtime_context.py -v 2>&1 | tail -5
```

- [ ] **Step 3: Run local awareness tests**

```bash
./.venv/Scripts/python.exe -m pytest tests/tools/test_local_awareness_tools.py -v 2>&1 | tail -5
```

- [ ] **Step 4: Run continuity tests**

```bash
./.venv/Scripts/python.exe -m pytest tests/agent/test_continuity_phase1.py -v 2>&1 | tail -5
```

- [ ] **Step 5: Verify ruff check**

```bash
.\.venv\Scripts\python.exe -m ruff check OriginAgent/agent/agent_host.py OriginAgent/agent/loop.py
```

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "chore: Phase 2b final verification — Provider, BDI, Transcription on AgentHost"
```

---

## Post Phase 2b: AgentHost Complete State

After Phase 2b, AgentHost owns ALL infrastructure lifecycle:

```
AgentHost
├── MCP Lifecycle (Phase 2a)
│   ├── _connect_mcp, _run_mcp_runtime, _close_mcp
│   └── 12 MCP state fields
├── Background Tasks (Phase 2a)
│   └── _background_tasks, schedule_background
├── Lifecycle (Phase 2a)
│   └── _running, start, stop, shutdown, _active_intent_task
├── Provider Management (Phase 2b)
│   ├── _apply_provider_snapshot, refresh_provider_snapshot
│   ├── set_model_preset, build_preset_snapshot
│   └── deps: runner, subagents, consolidator, dream, etc.
├── BDI Engine (Phase 2b)
│   ├── _desire_store, _bdi_engine, _inner_monologue_engine
│   ├── init_bdi_engine, start_bdi, stop_bdi
│   └── _on_bdi_intention
└── Transcription (Phase 2b)
    ├── _transcription_provider, _local_awareness_backend
    └── _build_transcription_provider
```

**AgentLoop post-Phase 2b: ~2600 lines** (down from 2966, ~365 lines extracted across Phases 1, 2a, 2b).

---
