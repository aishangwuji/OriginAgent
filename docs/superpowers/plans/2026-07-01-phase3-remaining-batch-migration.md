# Phase 3: Batch Migration — Remaining Methods to AgentRuntime

> **For agentic workers:** Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Migrate the ~30 remaining high-impact methods from AgentLoop to AgentRuntime, reducing loop.py from ~2983 to ~2200 lines.

**Architecture:** Five migration batches. Each batch follows the same pattern: (1) copy method body to AgentRuntime, replacing `self.xxx` with `d.xxx` (`d = self._deps`), (2) replace on AgentLoop with `hasattr(self, "_runtime")`-guarded compat shell.

**Tech Stack:** Python 3.11+, asyncio

## Global Constraints

- Method signatures unchanged from caller perspective
- `hasattr(self, "_runtime")` fallback for tests bypassing __init__
- No test modifications required
- 1046 passed, 1 pre-existing failure must remain unchanged

---

## Migration Batches by Dependency

| Batch | Methods | Lines | Dependency Pattern |
|-------|---------|-------|-------------------|
| **A** | `_build_initial_messages`, `_build_prompt_self_model`, `_persist_user_message_early`, `_build_bus_progress_callback`, `_build_retry_wait_callback` | ~170 | Heavy `self.context`, `self.provider` usage — fully on deps |
| **B** | `_assemble_outbound`, `_consume_tool_approval_reply`, `_build_initial_messages` (append), `_process_message`, `process_direct` | ~130 | Turn output + approval logic |
| **C** | `_save_continuity_checkpoint`, `_snapshot_context_assembly_from_messages`, `_archive_session_file_cap`, `_load_continuity_checkpoint`, `_write_continuity_runtime_identity`, `_collect_pending_confirmation_refs`, `_pending_confirmation_ref` | ~150 | Continuity + checkpoint state |
| **D** | `_update_working_memory_from_turn`, `_schedule_background_review`, `_schedule_curator_review`, `_schedule_nearline_memory`, `_nearline_turn_completed_successfully` | ~170 | Post-turn effects |
| **E** | `_collect_cognitive_candidates`, `_candidate_to_cognitive_event`, `_write_cognitive_event_to_working_memory`, `_build_cognitive_runtime_context` | ~155 | Cognitive + working memory |
| **F** | Meta-cognition: `_record_meta_trigger`, `_scan_meta_triggers_for_turn`, `_maybe_apply_meta_fast_path`, `_schedule_meta_cognition_reflection`, `_reflect_meta_cognition_turn`, `_meta_world_summary_preview`, `_meta_runtime_context_summary` | ~200 | Meta-cognition (complex — references `_meta_coordinator`, `_meta_cognition_runtime`) |
| **G** | `_persist_shortcut_command_turn`, `_dispatch_command_inline`, `_is_webui_message`, `_append_webui_command_transcript`, `_save_turn`, `_sanitize_persisted_blocks`, `_persist_subagent_followup` | ~120 | Dispatch + persistence |

---

### Batch A: Message Construction (highest priority)

**Files:**
- Modify: `OriginAgent/agent/agent_runtime.py`
- Modify: `OriginAgent/agent/loop.py`

**Methods:** `_build_initial_messages` (130 lines), `_build_prompt_self_model` (13 lines), `_persist_user_message_early` (18 lines), `_build_bus_progress_callback` (6 lines), `_build_retry_wait_callback` (6 lines)

- [ ] **Step 1: Add `_build_bus_progress_callback` and `_build_retry_wait_callback`**

Add to AgentRuntime:

```python
    async def _build_bus_progress_callback(
        self, msg: Any
    ) -> Any:
        """Build a progress callback that publishes to the message bus."""
        from OriginAgent.agent.agent_runtime_context import build_bus_progress_callback
        return await build_bus_progress_callback(self._deps.bus, msg)

    async def _build_retry_wait_callback(
        self, msg: Any
    ) -> Any:
        """Build a retry-wait callback that publishes to the message bus."""
        from OriginAgent.agent.agent_runtime_context import build_retry_wait_callback
        return await build_retry_wait_callback(self._deps.bus, msg)
```

Add compat shells on AgentLoop:

```python
    async def _build_bus_progress_callback(self, msg: InboundMessage) -> Callable[..., Awaitable[None]]:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return await self._runtime._build_bus_progress_callback(msg)
        return await build_bus_progress_callback(self.bus, msg)

    async def _build_retry_wait_callback(self, msg: InboundMessage) -> Callable[[str], Awaitable[None]]:
        if hasattr(self, "_runtime") and self._runtime is not None:
            return await self._runtime._build_retry_wait_callback(msg)
        return await build_retry_wait_callback(self.bus, msg)
```

- [ ] **Step 2: Add `_build_prompt_self_model`**

Copy to AgentRuntime with `d.xxx` adaptation:

```python
    def _build_prompt_self_model(self) -> dict:
        d = self._deps
        snapshot = d.introspection.runtime_context_snapshot() if d.introspection else {}
        from OriginAgent.agent.self_model import SelfModelService
        return SelfModelService(
            d.workspace,
            audit_mode=getattr(d.tool_audit_config, "mode", "standard"),
            runtime_profile=d.runtime_profile,
            domain_pack_manager=d.domain_packs,
            skills_loader=d.context.skills,
            memory_store=d.context.memory,
            nearline_memory_config=d.nearline_memory,
            runtime_snapshot=snapshot,
        ).build()
```

- [ ] **Step 3: Add `_persist_user_message_early`**

```python
    def _persist_user_message_early(
        self, msg: Any, session: Any, pending_ask_id: str | None, **kwargs: Any
    ) -> bool:
        """Persist the triggering user message before the turn starts."""
        from OriginAgent.agent.agent_turn_persist import TurnPersistManager
        return TurnPersistManager(
            self._deps.max_tool_result_chars, self._deps.sessions
        ).persist_user_message_early(msg, session, pending_ask_id, **kwargs)
```

- [ ] **Step 4: Add `_build_initial_messages` (largest method)**

This method heavily references `self.context`, `self._last_runtime_context`, `self._state_holder`, `self.provider`, `self.context_window_tokens` — all available via `d.xxx`.

**Key adaptation:**
- `self._last_runtime_context` → `self._deps.state_holder.get(session.key).last_runtime_context`
- `self._last_context_assembly` → `self._deps.state_holder.get(session.key).last_context_assembly`
- `self.context.xxx` → `d.context.xxx`
- `self.provider.generation.max_tokens` → `d.provider.generation.max_tokens`
- `self.context_window_tokens` → `d.context_window_tokens`
- `self.introspection` → `d.introspection`
- `self._unified_session` → `d.unified_session`

On AgentLoop, replace with:

```python
    def _build_initial_messages(self, msg, session, history, pending_ask_id,
                                 pending_summary, internal_event=None,
                                 recovered_continuity_block=None):
        if hasattr(self, "_runtime") and self._runtime is not None:
            return self._runtime._build_initial_messages(
                msg, session, history, pending_ask_id, pending_summary,
                internal_event=internal_event,
                recovered_continuity_block=recovered_continuity_block,
            )
        # ... fallback unchanged ...
```

- [ ] **Step 5: Run tests**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/test_continuity_phase1.py tests/agent/test_ask_user.py -v 2>&1 | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py OriginAgent/agent/loop.py
git commit -m "feat: move message construction methods to AgentRuntime"
```

---

### Batch B: Outbound Assembly + Dispatch

**Files:**
- Modify: `OriginAgent/agent/agent_runtime.py`
- Modify: `OriginAgent/agent/loop.py`

**Methods:** `_assemble_outbound`, `_consume_tool_approval_reply`, `_process_message`, `process_direct`

- [ ] **Step 1: Move `_assemble_outbound`**

Key adaptation: `self.tools.get("message")` → `d.tools.get("message")`, `self._effective_session_key` → already moved.

- [ ] **Step 2: Move `_consume_tool_approval_reply`**

Key adaptation: `self._confirmation_manager` → `d.confirmation_manager`, `self._grant_store` → `d.grant_store`.

- [ ] **Step 3: Move `_process_message` and `process_direct`**

These are pure delegation methods — trivially moved.

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py OriginAgent/agent/loop.py
git commit -m "feat: move outbound assembly and dispatch to AgentRuntime"
```

---

### Batch C: Continuity + Checkpoint

**Files:**
- Modify: `OriginAgent/agent/agent_runtime.py`
- Modify: `OriginAgent/agent/loop.py`

**Methods:** `_save_continuity_checkpoint`, `_snapshot_context_assembly_from_messages`, `_archive_session_file_cap`, `_load_continuity_checkpoint`, `_write_continuity_runtime_identity`, `_collect_pending_confirmation_refs`, `_pending_confirmation_ref`

- [ ] **Step 1: Move all 7 methods**

- [ ] **Step 2: Run continuity tests**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/test_continuity_phase1.py -v 2>&1 | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py OriginAgent/agent/loop.py
git commit -m "feat: move continuity and checkpoint methods to AgentRuntime"
```

---

### Batch D: Post-Turn Effects

**Files:**
- Modify: `OriginAgent/agent/agent_runtime.py`
- Modify: `OriginAgent/agent/loop.py`

**Methods:** `_update_working_memory_from_turn` (77 lines), `_schedule_background_review` (33 lines), `_schedule_curator_review` (20 lines), `_schedule_nearline_memory` (25 lines), `_nearline_turn_completed_successfully` (11 lines, static)

- [ ] **Step 1: Move `_update_working_memory_from_turn`**

Key adaptation: `self.world_state` → `d.world_state`, `self.working_memory` → `d.working_memory`, `self.sessions` → `d.sessions`, `self.context._context_config` → `d.context._context_config`.

`self._last_world_attention_write` → `d.state_holder.get(session.key).last_world_attention_write`.

- [ ] **Step 2: Move background schedule methods**

The `_schedule_background_review` calls `_schedule_background(coro)` which stays on AgentLoop (delegates to AgentHost). In AgentRuntime, accept a `schedule_cb` parameter or reference `d.host.schedule_background`:

```python
    def _schedule_background_review(self, ctx: Any) -> None:
        d = self._deps
        if ctx.session is None:
            return
        d.background_review.refresh_config()
        if not d.background_review.enabled:
            return
        if ctx.stop_reason in {"ask_user", "error", "tool_error"}:
            return
        if ctx.msg.channel == "system" or ctx.msg.sender_id == "subagent":
            return
        if not (ctx.final_content or "").strip():
            return

        max_recent = int(getattr(d.background_review.config, "max_recent_messages", 12) or 12)
        messages = [dict(m) for m in ctx.session.messages if not m.get("_command")][-max_recent:]
        d.host.schedule_background(
            d.background_review.review_turn(
                session_key=ctx.session_key, turn_id=ctx.turn_id,
                channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
                message_id=ctx.msg.metadata.get("message_id"),
                messages=messages,
            )
        )
```

- [ ] **Step 3: Run automation tests (heaviest consumer of post-turn)**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/test_loop_automation_phase4.py -v 2>&1 | tail -3
```

- [ ] **Step 4: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py OriginAgent/agent/loop.py
git commit -m "feat: move post-turn effects to AgentRuntime"
```

---

### Batch E: Cognitive

**Files:**
- Modify: `OriginAgent/agent/agent_runtime.py`
- Modify: `OriginAgent/agent/loop.py`

- [ ] **Step 1: Move `_build_cognitive_runtime_context`, `_collect_cognitive_candidates`, `_candidate_to_cognitive_event`, `_write_cognitive_event_to_working_memory`**

- [ ] **Step 2: Run cognitive tests**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/test_cognitive_runtime_delegation.py -v 2>&1 | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py OriginAgent/agent/loop.py
git commit -m "feat: move cognitive methods to AgentRuntime"
```

---

### Batch F: Meta-Cognition

**Files:**
- Modify: `OriginAgent/agent/agent_runtime.py`
- Modify: `OriginAgent/agent/loop.py`

**7 methods, ~200 lines.** The hardest batch — `_install_meta_cognition_observer` creates an inner class that strongly references `self` (AgentLoop). **Keep this method on AgentLoop** — it mutates `d.tools._execution_observer` which is infrastructure-level. Move the remaining 7 methods that are message-routing logic.

- [ ] **Step 1: Move `_record_meta_trigger`, `_scan_meta_triggers_for_turn`, `_maybe_apply_meta_fast_path`, `_schedule_meta_cognition_reflection`, `_reflect_meta_cognition_turn`, `_meta_world_summary_preview`, `_meta_runtime_context_summary`**

Key adaptations:
- `self._meta_coordinator` → `d.meta_coordinator`
- `self._meta_cognition_runtime` → `d.meta_cognition_runtime`
- `self._meta_cognition_reflector` → `d.meta_cognition_reflector`
- `self._perception_fusion` → `d.perception_fusion`
- `self.world_state` → `d.world_state`
- `self.working_memory` → `d.working_memory`
- `self.sessions` → `d.sessions`
- `self._schedule_background(coro)` → `d.host.schedule_background(coro)`
- `getattr(self, "_last_runtime_context", None)` → `d.state_holder.get(session_key).last_runtime_context`
- `self._meta_coordinator.fast_path_refs` → `d.meta_coordinator.fast_path_refs`

- [ ] **Step 2: Run test**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/test_loop_runtime_status_tools.py -v 2>&1 | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py OriginAgent/agent/loop.py
git commit -m "feat: move meta-cognition methods to AgentRuntime"
```

---

### Batch G: Dispatch + Persistence

**Files:**
- Modify: `OriginAgent/agent/agent_runtime.py`
- Modify: `OriginAgent/agent/loop.py`

- [ ] **Step 1: Move `_persist_shortcut_command_turn`, `_dispatch_command_inline`, `_is_webui_message`, `_append_webui_command_transcript`, `_save_turn`, `_sanitize_persisted_blocks`, `_persist_subagent_followup`**

`_turn_persist_manager` stays on AgentLoop (it's a caching method that uses `self._persist` — a mutable attribute). The moved methods can call `TurnPersistManager(...)` directly:

```python
    def _save_turn(self, session: Any, messages: list[dict], skip: int) -> None:
        from OriginAgent.agent.agent_turn_persist import TurnPersistManager
        max_chars = self._deps.max_tool_result_chars
        TurnPersistManager(max_chars, self._deps.sessions).save_turn(session, messages, skip)
```

- [ ] **Step 2: Commit**

```bash
git add OriginAgent/agent/agent_runtime.py OriginAgent/agent/loop.py
git commit -m "feat: move dispatch and persistence methods to AgentRuntime"
```

---

## Final Verification

- [ ] **Step 1: Full test suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/agent/ tests/tools/test_runtime_status_tools.py -v 2>&1 | tail -3
```

- [ ] **Step 2: Check line counts**

```bash
wc -l OriginAgent/agent/loop.py OriginAgent/agent/agent_runtime.py OriginAgent/agent/agent_host.py OriginAgent/agent/session_state.py
```

Expected: loop.py ~2200, agent_runtime.py ~1200

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "chore: Phase 3 Batch A-G — remaining methods migrated to AgentRuntime"
```

---

## What Stays on AgentLoop (No Migration)

| Category | Methods | Reason |
|----------|---------|--------|
| Dep builders | `_build_turn_pipeline_deps`, `_build_system_turn_loop_context`, `_build_cognitive_runtime_deps` | Wire callbacks from all 3 components |
| Lazy accessors | `_get_consolidator`, `_get_tools`, `_get_context`, etc. | Trivial |
| Record methods | `_record_runtime_context`, `_record_governance_audit`, etc. | Write to state holder (infrastructure) |
| Provider lifecycle | `_apply_provider_snapshot`, `_refresh_provider_snapshot`, `set_model_preset` | Phase 2b — identity fields |
| Tool registration | `_register_default_tools`, `_build_tool_context`, `_register_domain_tools`, `_register_plugin_tools` | Called from `__init__` |
| Automation | `_automation_enabled`, `_bind_action_resume_precheck`, `_resume_action_confirmation_precheck`, `_device_action_executor_for_automation` | Complex domain logic |
| Turn state wrappers | `_state_restore` through `_state_respond` (8 methods) | Delegates to TurnPipeline |
| `_install_meta_cognition_observer` | Creates inner class with strong loop reference | Infrastructure |
| Public API | `run()`, `stop()`, `close_mcp()`, `from_config()`, `from_options()` | Must stay for backward compat |
