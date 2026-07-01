# Phase 3: AgentRuntime — Stateless Message Router

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract ~800 lines of message-routing logic from `AgentLoop` into a stateless `AgentRuntime` class, transforming the loop from a 2860-line "universal adapter" into a ~2000-line facade with clear responsibility boundaries.

**Architecture:** `AgentRuntime` is stateless — every method receives `session_key`, `session`, and other context as explicit parameters. It depends on `SessionStateHolder`, `AgentHost`, and the existing service objects (`tools`, `context`, `runner`, etc.) injected via a `RuntimeDependencies` dataclass. `AgentLoop` becomes a compat shell: its methods delegate to `self._runtime.*`, keeping the public API unchanged.

**Tech Stack:** Python 3.11+, asyncio, dataclasses

## ⚠️ Construction Order Constraint

``_build_turn_pipeline_deps``, ``_build_system_turn_loop_context``, and
``_build_cognitive_runtime_deps`` are called inside ``__init__`` and must
reference ``self._runtime``.  Therefore **AgentRuntime MUST be constructed
BEFORE** the turn pipeline / cognitive runtime / system turn handler.

The correct ``__init__`` order:
1. ``setattr`` loop (all services available on ``self``)
2. ``SessionStateHolder``
3. ``AgentHost``
4. **``AgentRuntime``** ← constructed here, before dep builders
5. Meta-cognition coordinator, commands
6. Turn pipeline (calls ``_build_turn_pipeline_deps`` → ``self._runtime``)
7. Cognitive runtime, services, system turn handler, orchestrator, dispatcher
8. Meta-cognition observer

``RuntimeDependencies`` fields ``turn_pipeline``, ``system_turn_handler``,
``turn_orchestrator``, ``message_dispatcher`` default to ``None`` — they are
NOT needed by any method moving to AgentRuntime.  Remove them from the
dataclass if unused.

---

## Global Constraints

- `AgentLoop.__init__`, `from_config`, `from_options`, `run()`, `stop()`, `process_direct()` signatures MUST NOT change
- All existing tests MUST pass without modification
- `AgentLoop.__new__(AgentLoop)` bypass pattern MUST continue to work (fallback in every compat shell)
- `TurnPipelineDeps`, `SystemTurnLoopContext`, `CognitiveRuntimeDeps` builders MUST still produce valid callables — they now reference `self._runtime` instead of `self`
- `_install_meta_cognition_observer` references `loop` directly — must be adapted to use `AgentRuntime`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `OriginAgent/agent/agent_runtime.py` | **Create** | `RuntimeDependencies` dataclass + `AgentRuntime` class |
| `OriginAgent/agent/loop.py` | **Modify** | Replace ~40 methods with compat shells; update dep builders |
| `OriginAgent/agent/agent_loop_components.py` | **No change** | Field defaults preserved |
| `OriginAgent/agent/agent_host.py` | **No change** | Already complete from Phase 2 |
| `OriginAgent/agent/session_state.py` | **No change** | Already complete from Phase 1 |

---

## Scope Decision: What Moves, What Stays

**Moves to AgentRuntime** (message routing pipeline):

```
AgentRuntime
├── Core Turn Pipeline
│   ├── _run_agent_loop          (~180 lines)  ← the heart
│   ├── _build_initial_messages   (~120 lines)  ← message construction
│   ├── _build_prompt_self_model  (~13 lines)
│   ├── _assemble_outbound        (~40 lines)
│   ├── _set_tool_context         (~16 lines)
│   ├── _resolve_runtime_context  (~18 lines)
│   ├── _build_bus_progress_cb    (~6 lines)
│   ├── _build_retry_wait_cb      (~6 lines)
│   ├── _sync_subagent_limits     (~5 lines)
│   ├── _effective_session_key    (~6 lines)
│   ├── _replay_token_budget      (~12 lines)
│   └── _tool_hint                (~6 lines)
│
├── Post-Turn Side Effects
│   ├── _save_turn                (~4 lines)
│   ├── _persist_user_msg_early   (~18 lines)
│   ├── _save_continuity_chkpt    (~35 lines)
│   ├── _update_working_memory    (~77 lines)
│   ├── _write_continuity_identity(~15 lines)
│   ├── _schedule_bg_review       (~33 lines)
│   ├── _schedule_curator_review  (~20 lines)
│   ├── _schedule_nearline_memory (~25 lines)
│   └── _nearline_turn_success    (~11 lines) static
│
├── Meta-Cognition
│   ├── _install_meta_observer    (~120 lines)
│   ├── _record_meta_trigger      (~5 lines)
│   ├── _scan_meta_triggers       (~28 lines)
│   ├── _schedule_meta_reflection (~35 lines)
│   ├── _reflect_meta_turn        (~27 lines)
│   ├── _maybe_apply_fast_path    (~33 lines)
│   ├── _meta_world_summary       (~26 lines)
│   └── _meta_runtime_ctx_summary (~13 lines)
│
├── Cognitive
│   ├── _collect_candidates       (~74 lines)
│   ├── _build_cognitive_ctx      (~31 lines)
│   ├── _write_cognitive_event    (~31 lines)
│   └── _candidate_to_event       (~20 lines)
│
├── Message Dispatch
│   ├── _process_message          (~22 lines)
│   ├── process_direct            (~23 lines)
│   ├── _dispatch_command_inline  (~35 lines)
│   ├── _persist_shortcut_turn    (~26 lines)
│   └── _append_webui_transcript  (~16 lines)
│
├── Continuity
│   ├── _archive_session_file_cap (~11 lines)
│   ├── _load_continuity_chkpt    (~15 lines) static
│   ├── _snapshot_ctx_assembly    (~50 lines)
│   ├── _collect_pending_refs     (~17 lines)
│   ├── _pending_confirmation_ref (~14 lines) static
│   └── _consume_tool_approval    (~45 lines)
│
└── Utilities
    ├── _strip_think               (~9 lines) static
    ├── _runtime_chat_id           (~5 lines) static
    ├── _snapshot_for_trigger      (~5 lines) static
    ├── _cancel_active_tasks       (~13 lines)
    ├── _turn_persist_manager      (~12 lines)
    └── _sanitize_persisted_blocks (~14 lines)
```

**Stays on AgentLoop:**
- `__init__`, `from_config`, `from_options` — factory methods
- `run()`, `stop()`, `close_mcp()` — lifecycle shells (delegate to AgentHost)
- `_get_*` lazy accessors — used by dep builders
- `_build_turn_pipeline_deps()`, `_build_system_turn_loop_context()`, `_build_cognitive_runtime_deps()` — updated to reference `self._runtime`
- All `_state_*` compat wrappers — stay
- `_record_*` methods — stay (write to state holder)
- `_apply_provider_snapshot`, `_refresh_provider_snapshot`, `set_model_preset` — stay
- `_register_default_tools`, `_build_tool_context`, `_register_domain_tools`, `_register_plugin_tools` — stay (called from __init__)
- `_active_intent_loop`, `_run_cognitive_pass_for_session`, `_active_task_count` — cognitive compat shells stay
- Automation methods (`_automation_enabled`, `_bind_action_resume_precheck`, `_resume_action_confirmation_precheck`, `_device_action_executor_for_automation`) — complex domain logic, defer to future phase

---

### Task 1: Create AgentRuntime skeleton + RuntimeDependencies

**Files:**
- Create: `OriginAgent/agent/agent_runtime.py`

**Interfaces:**
- Produces: `RuntimeDependencies` dataclass, `AgentRuntime` class (skeleton only)

- [ ] **Step 1: Create the module**

```python
"""AgentRuntime — stateless message router extracted from AgentLoop.

Receives all context (session_key, session, messages, etc.) as explicit
method parameters.  Depends on SessionStateHolder for session-scoped
scratchpad and on AgentHost for infrastructure lifecycle.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from OriginAgent.agent import model_presets as preset_helpers
from OriginAgent.agent.action_summary import normalize_action_summary
from OriginAgent.agent.agent_turn_persist import TurnPersistManager
from OriginAgent.agent.error_classifier import ClassifiedError, ErrorKind, user_facing_message
from OriginAgent.agent.hook import AgentHook, CompositeHook
from OriginAgent.agent.progress_hook import AgentProgressHook
from OriginAgent.agent.runner import _MAX_INJECTIONS_PER_TURN, AgentRunner, AgentRunSpec
from OriginAgent.agent.session_state import SessionStateHolder
from OriginAgent.agent.tools.ask import (
    ask_user_options_from_messages,
    ask_user_outbound,
    pending_ask_user_id,
)
from OriginAgent.agent.tools.file_state import bind_file_states, reset_file_states
from OriginAgent.agent.tools.message import MessageTool
from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.config.schema import AgentDefaults
from OriginAgent.security.capabilities import CapabilitySnapshot
from OriginAgent.session.goal_state import goal_state_ws_blob, runner_wall_llm_timeout_s
from OriginAgent.utils.image_generation_intent import image_generation_prompt
from OriginAgent.utils.webui_titles import mark_webui_session
from OriginAgent.utils.webui_transcript import append_transcript_object, delete_webui_transcript

from OriginAgent.agent.agent_runtime_context import (
    build_bus_progress_callback,
    build_retry_wait_callback,
    runtime_chat_id,
    set_tool_context as set_tools_runtime_context,
    snapshot_for_trigger,
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim_text(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


@dataclass(frozen=True)
class RuntimeDependencies:
    """Immutable dependency bundle for AgentRuntime.

    All dependencies are injected at construction time.  AgentRuntime
    never reaches back to AgentLoop — every method receives context
    explicitly.
    """

    # Infrastructure
    state_holder: SessionStateHolder
    host: Any  # AgentHost

    # Core services
    tools: Any  # ToolRegistry
    provider: Any  # LLMProvider
    runner: Any  # AgentRunner
    context: Any  # ContextBuilder
    sessions: Any  # SessionManager
    bus: Any  # MessageBus
    workspace: Path

    # Sub-services
    subagents: Any = None
    working_memory: Any = None
    commands: Any = None
    action_planner: Any = None
    consolidation_ratio: float = 0.5
    active_intents: Any = None
    reminder_store: Any = None
    world_state: Any = None

    # Config
    model: str | None = None
    max_iterations: int = 10
    context_window_tokens: int = 0
    context_block_limit: int = 0
    max_tool_result_chars: int = 0
    provider_retry_mode: str = "standard"
    tool_hint_max_length: int | None = None
    tools_config: Any = None
    web_config: Any = None
    exec_config: Any = None
    restrict_to_workspace: bool = False
    unified_session: bool = False
    runtime_profile: str = "default"
    tool_audit_config: Any = None
    evolution_config: Any = None
    domain_packs: Any = None
    domain_runtime_overrides: dict | None = None
    domain_runtime_contributions: list = None

    # Background services
    background_review: Any = None
    curator: Any = None
    nearline_memory: Any = None
    session_search_index: Any = None
    consolidation: Any = None
    consolidator: Any = None
    dream: Any = None
    dream_feature_flags: Any = None
    cognitive_loop: Any = None
    cognitive_scheduler: Any = None
    cognitive_audit: Any = None
    session_cold_archive: Any = None
    rolling_episode_compaction: Any = None
    memory_governance: Any = None
    auto_compact: Any = None

    # Meta-cognition
    meta_cognition_runtime: Any = None
    meta_cognition_reflector: Any = None
    meta_cognition_regulator: Any = None
    meta_cognition_config: Any = None
    meta_coordinator: Any = None
    perception_fusion: Any = None

    # Misc
    meta_cognition_fast_path_refs: Any = None
    pending_queues: dict | None = None
    extra_hooks: list | None = None
    runtime_model_publisher: Any = None
    provider_signature: Any = None
    model_presets: dict | None = None
    model_preset: str | None = None
    preset_snapshot_loader: Any = None
    provider_snapshot_loader: Any = None
    file_state_store: Any = None
    confirmation_store: Any = None
    confirmation_manager: Any = None
    grant_store: Any = None
    cron_service: Any = None
    introspection: Any = None
    actor_resolver: Any = None
    auxiliary_router: Any = None
    max_messages: int = 120

    # Turn pipeline refs (set after AgentLoop builds them)
    turn_pipeline: Any = None
    system_turn_handler: Any = None
    turn_orchestrator: Any = None
    message_dispatcher: Any = None

    # BDI
    bdi_engine: Any = None


class AgentRuntime:
    """Stateless message router — processes inbound messages through
    the turn pipeline and returns outbound responses.

    Every public method receives ``session_key`` and other context
    explicitly.  No mutable state is stored on ``self`` beyond the
    injected dependencies.
    """

    def __init__(self, deps: RuntimeDependencies) -> None:
        self._deps = deps

    @property
    def deps(self) -> RuntimeDependencies:
        return self._deps
```

- [ ] **Step 2: Verify the module imports**

```bash
./.venv/Scripts/python.exe -c "from OriginAgent.agent.agent_runtime import AgentRuntime, RuntimeDependencies; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py
git commit -m "feat: add AgentRuntime skeleton with RuntimeDependencies"
```

---

### Task 2: Wire AgentRuntime into AgentLoop and update dep builders

**Files:**
- Modify: `OriginAgent/agent/loop.py`

**Strategy:** Create `AgentRuntime` after `AgentHost` in `__init__`. The dep builders (`_build_turn_pipeline_deps`, `_build_system_turn_loop_context`, `_build_cognitive_runtime_deps`) are the bridge — they currently pass `self` (AgentLoop) as callables to the pipeline. After this task, they pass `self._runtime` callables instead. Each callable on AgentLoop becomes a compat shell.

**Key insight:** This is the critical architectural pivot. Before Task 2, the turn pipeline calls `self._run_agent_loop(...)`. After Task 2, it calls `self._runtime._run_agent_loop(...)`.

- [ ] **Step 1: Add import and create AgentRuntime in __init__**

After the AgentHost construction block in `__init__`, add:

```python
        # ── AgentRuntime: stateless message router ────────────────────────
        self._runtime = AgentRuntime(RuntimeDependencies(
            state_holder=self._state_holder,
            host=self._host,
            tools=self.tools,
            provider=self.provider,
            runner=self.runner,
            context=self.context,
            sessions=self.sessions,
            bus=self.bus,
            workspace=self.workspace,
            subagents=self.subagents,
            working_memory=self.working_memory,
            commands=self.commands,
            action_planner=self.action_planner,
            consolidation_ratio=self._consolidation_ratio,
            active_intents=self.active_intents,
            reminder_store=self._reminder_store,
            world_state=self.world_state,
            model=self.model,
            max_iterations=self.max_iterations,
            context_window_tokens=self.context_window_tokens,
            context_block_limit=self.context_block_limit,
            max_tool_result_chars=self.max_tool_result_chars,
            provider_retry_mode=self.provider_retry_mode,
            tool_hint_max_length=self.tool_hint_max_length,
            tools_config=self.tools_config,
            web_config=self.web_config,
            exec_config=self.exec_config,
            restrict_to_workspace=self.restrict_to_workspace,
            unified_session=self._unified_session,
            runtime_profile=self._runtime_profile,
            domain_packs=self.domain_packs,
            background_review=self.background_review,
            curator=self.curator,
            nearline_memory=self.nearline_memory,
            session_search_index=self.session_search_index,
            consolidator=self.consolidator,
            dream=self.dream,
            session_cold_archive=self.session_cold_archive,
            rolling_episode_compaction=self.rolling_episode_compaction,
            memory_governance=self.memory_governance,
            auto_compact=self.auto_compact,
            meta_cognition_runtime=getattr(self, "_meta_cognition_runtime", None),
            meta_cognition_reflector=getattr(self, "_meta_cognition_reflector", None),
            meta_cognition_regulator=getattr(self, "_meta_cognition_regulator", None),
            meta_cognition_config=getattr(self, "_meta_cognition_config", None),
            meta_coordinator=self._meta_coordinator,
            perception_fusion=getattr(self, "_perception_fusion", None),
            pending_queues=self._pending_queues,
            extra_hooks=self._extra_hooks,
            file_state_store=self._file_state_store,
            confirmation_store=self._confirmation_store,
            confirmation_manager=self._confirmation_manager,
            grant_store=self._grant_store,
            cron_service=self.cron_service,
            introspection=self.introspection,
            actor_resolver=self.actor_resolver,
            auxiliary_router=self.auxiliary_router,
            max_messages=self._max_messages,
            turn_pipeline=self._turn_pipeline,
            system_turn_handler=self._system_turn_handler,
            turn_orchestrator=self._turn_orchestrator,
            message_dispatcher=self._message_dispatcher,
            bdi_engine=self._bdi_engine,
        ))
```

- [ ] **Step 2: Verify module still imports**

```bash
./.venv/Scripts/python.exe -c "from OriginAgent.agent.loop import AgentLoop; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py OriginAgent/agent/loop.py
git commit -m "feat: wire AgentRuntime into AgentLoop with full RuntimeDependencies"
```

---

### Task 3: Move core turn pipeline methods to AgentRuntime

**Files:**
- Modify: `OriginAgent/agent/agent_runtime.py` (add methods)
- Modify: `OriginAgent/agent/loop.py` (replace with compat shells)

**Methods moved:**
- `_run_agent_loop` (~180 lines)
- `_build_initial_messages` (~120 lines)
- `_build_prompt_self_model` (~13 lines)
- `_assemble_outbound` (~40 lines)
- `_set_tool_context` (~16 lines)
- `_resolve_runtime_context` (~18 lines)
- `_build_bus_progress_callback` (~6 lines)
- `_build_retry_wait_callback` (~6 lines)
- `_sync_subagent_runtime_limits` (~5 lines)
- `_effective_session_key` (~6 lines)
- `_replay_token_budget` (~12 lines)
- `_tool_hint` (~6 lines)

**Strategy:** Copy each method body to AgentRuntime, adapting `self.xxx` references to `self._deps.xxx`. On AgentLoop, replace each method with a compat shell:

```python
def _run_agent_loop(self, ...):
    return await self._runtime._run_agent_loop(...)
```

- [ ] **Step 1: Move `_run_agent_loop`**

Copy the full method body to AgentRuntime. All `self.xxx` service references become `self._deps.xxx`. Key adaptations:
- `self.provider` → `self._deps.provider`
- `self.tools` → `self._deps.tools`
- `self.runner` → `self._deps.runner`
- `self.subagents` → `self._deps.subagents`
- `self.context` → `self._deps.context`
- `self.sessions` → `self._deps.sessions`
- `self.workspace` → `self._deps.workspace`
- `self.model` → `self._deps.model`
- `self.max_iterations` → `self._deps.max_iterations`
- `self.max_tool_result_chars` → `self._deps.max_tool_result_chars`
- `self.context_window_tokens` → `self._deps.context_window_tokens`
- `self.context_block_limit` → `self._deps.context_block_limit`
- `self.provider_retry_mode` → `self._deps.provider_retry_mode`
- `self.tool_hint_max_length` → `self._deps.tool_hint_max_length`
- `self._tool_concurrency_limit` → `self._deps.tools_config.tool_concurrency_limit if self._deps.tools_config else None`
- `self._capability_snapshot` → stays as a local variable (turn-scoped, set at method entry)
- `self._current_iteration` → replaced by local `_current_iteration` variable
- `self._set_tool_context` → `self._set_tool_context(...)` (moved method)
- `self._sync_subagent_runtime_limits()` → `self._sync_subagent_runtime_limits()` (moved method)
- `self._snapshot_for_trigger` → becomes local call (imported)
- `self._file_state_store` → `self._deps.file_state_store`
- `self._extra_hooks` → `self._deps.extra_hooks`

On AgentLoop, replace with:

```python
async def _run_agent_loop(self, initial_messages, on_progress=None, on_stream=None,
                           on_stream_end=None, on_retry_wait=None, *, session=None,
                           channel="cli", chat_id="direct", message_id=None,
                           metadata=None, session_key=None, pending_queue=None,
                           actor_id=None, trigger=None, capability_snapshot=None):
    return await self._runtime._run_agent_loop(
        initial_messages, on_progress=on_progress, on_stream=on_stream,
        on_stream_end=on_stream_end, on_retry_wait=on_retry_wait,
        session=session, channel=channel, chat_id=chat_id,
        message_id=message_id, metadata=metadata, session_key=session_key,
        pending_queue=pending_queue, actor_id=actor_id, trigger=trigger,
        capability_snapshot=capability_snapshot,
    )
```

- [ ] **Step 2: Move leaf methods**

Move one at a time, testing after each:
- `_sync_subagent_runtime_limits`
- `_set_tool_context`
- `_effective_session_key`
- `_replay_token_budget`
- `_tool_hint`
- `_resolve_runtime_context`
- `_build_bus_progress_callback`
- `_build_retry_wait_callback`
- `_build_prompt_self_model`
- `_build_initial_messages`
- `_assemble_outbound`

- [ ] **Step 3: Run core turn tests**

```bash
./.venv/Scripts/python.exe -m pytest tests/agent/ -k "run_agent or build_message or ask_user" -v 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py OriginAgent/agent/loop.py
git commit -m "feat: move core turn pipeline to AgentRuntime"
```

---

### Task 4: Update dep builders to reference AgentRuntime

**Files:**
- Modify: `OriginAgent/agent/loop.py` (`_build_turn_pipeline_deps`, `_build_system_turn_loop_context`, `_build_cognitive_runtime_deps`)

**Strategy:** Change all callable references from `self._foo` to `self._runtime._foo`.

- [ ] **Step 1: Update `_build_turn_pipeline_deps`**

Replace `self.xxx` callables with `self._runtime.xxx`:

```python
def _build_turn_pipeline_deps(self) -> TurnPipelineDeps:
    rt = self._runtime
    return TurnPipelineDeps(
        # ... existing fields ...
        run_agent_loop=rt._run_agent_loop,
        build_initial_messages=rt._build_initial_messages,
        persist_user_message_early=rt._persist_user_message_early,
        assemble_outbound=rt._assemble_outbound,
        set_tool_context=rt._set_tool_context,
        build_progress_callback=rt._build_bus_progress_callback,
        build_retry_wait_callback=rt._build_retry_wait_callback,
        save_turn=rt._save_turn,
        schedule_background=self._schedule_background,  # stays on loop (delegates to host)
        schedule_background_review=rt._schedule_background_review,
        schedule_curator_review=rt._schedule_curator_review,
        schedule_nearline_memory=rt._schedule_nearline_memory,
        update_working_memory_from_turn=rt._update_working_memory_from_turn,
        save_continuity_checkpoint=rt._save_continuity_checkpoint,
        replay_token_budget=rt._replay_token_budget,
        # ... keep remaining self.xxx references for items that stay on loop ...
    )
```

- [ ] **Step 2: Update `_build_system_turn_loop_context`**

Same pattern — `self._runtime.xxx` for moved methods.

- [ ] **Step 3: Update `_build_cognitive_runtime_deps`**

Same pattern.

- [ ] **Step 4: Run full test suite**

```bash
./.venv/Scripts/python.exe -m pytest tests/agent/ -v 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add OriginAgent/agent/loop.py
git commit -m "refactor: update dep builders to reference AgentRuntime instead of AgentLoop"
```

---

### Task 5: Move post-turn side effects to AgentRuntime

**Files:**
- Modify: `OriginAgent/agent/agent_runtime.py`
- Modify: `OriginAgent/agent/loop.py`

**Methods moved:** `_save_turn`, `_persist_user_message_early`, `_save_continuity_checkpoint`, `_update_working_memory_from_turn`, `_write_continuity_runtime_identity`, `_schedule_background_review`, `_schedule_curator_review`, `_schedule_nearline_memory`, `_nearline_turn_completed_successfully`

- [ ] **Step 1: Move methods one at a time, test after each**

- [ ] **Step 2: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py OriginAgent/agent/loop.py
git commit -m "feat: move post-turn side effects to AgentRuntime"
```

---

### Task 6: Move meta-cognition + cognitive to AgentRuntime

**Files:**
- Modify: `OriginAgent/agent/agent_runtime.py`
- Modify: `OriginAgent/agent/loop.py`

**Methods moved:** Meta-cognition (8 methods, ~290 lines) + Cognitive (4 methods, ~155 lines)

- [ ] **Step 1: Move meta-cognition methods**

`_install_meta_cognition_observer` requires special handling — it creates a closure class that references `loop`. Adapt to use `self._deps` instead.

- [ ] **Step 2: Move cognitive methods**

- [ ] **Step 3: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py OriginAgent/agent/loop.py
git commit -m "feat: move meta-cognition and cognitive methods to AgentRuntime"
```

---

### Task 7: Move dispatch + continuity + utilities to AgentRuntime

**Files:**
- Modify: `OriginAgent/agent/agent_runtime.py`
- Modify: `OriginAgent/agent/loop.py`

**Methods moved:** All remaining methods from the scope table above.

- [ ] **Step 1: Move dispatch methods** (`_process_message`, `process_direct`, `_dispatch_command_inline`, etc.)

- [ ] **Step 2: Move continuity methods**

- [ ] **Step 3: Move utility methods**

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py OriginAgent/agent/loop.py
git commit -m "feat: move dispatch, continuity, and utilities to AgentRuntime"
```

---

### Task 8: Final verification

- [ ] **Step 1: Run full test suite**

```bash
./.venv/Scripts/python.exe -m pytest tests/agent/ tests/tools/test_runtime_status_tools.py -v 2>&1 | tail -5
```

- [ ] **Step 2: Run ruff check**

```bash
ruff check OriginAgent/agent/agent_runtime.py OriginAgent/agent/loop.py
```

- [ ] **Step 3: Check line counts**

```bash
wc -l OriginAgent/agent/loop.py OriginAgent/agent/agent_runtime.py OriginAgent/agent/agent_host.py OriginAgent/agent/session_state.py
```

Expected: loop.py ~2000 lines, agent_runtime.py ~900 lines.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: Phase 3 final verification — AgentRuntime extraction complete"
```

---

## Post Phase 3: Final Architecture

```
外部调用者 (tests, CLI, SDK, Channels)
    ↓
AgentLoop（门面 Facade ~2000 行）
    ├── AgentHost          ← 基础设施生命周期 (MCP/BDI/Provider/后台任务) — 543 行
    ├── AgentRuntime       ← 无状态消息路由 (Turn/认知/元认知/持久化) — ~900 行
    ├── SessionStateHolder ← 会话隔离状态 — 96 行
    └── AgentServiceContainer ← 服务容器 (不变)

AgentLoop 剩余职责：
    - 工厂方法 (__init__, from_config, from_options)
    - 生命周期外壳 (run, stop, close_mcp → 委托给 AgentHost)
    - 兼容性外壳 (~40 个方法 → 委托给 AgentRuntime)
    - 依赖组装 (_build_*_deps)
    - Record 方法 (写入 SessionStateHolder)
    - 自动化预检查 (复杂的领域逻辑，推迟到未来阶段)
```
