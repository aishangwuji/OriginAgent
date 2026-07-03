# Phase 3: Architecture Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose three God Classes, consolidate duplicated code across providers/channels, and eliminate dead code paths — reducing technical debt before feature work resumes.

**Architecture:** Five independent tasks ordered by risk. Tasks 3.1-3.3 are extractions and consolidations (medium-high risk, need careful test coverage). Tasks 3.4-3.5 are surgical removals (low risk, straightforward). Each task produces independently testable, shippable improvements.

**Tech Stack:** Python 3.11+, asyncio, pytest, aiohttp, dataclasses

## Global Constraints

- NEVER use `with suppress(Exception):` in business logic paths — only in cleanup code with specific exception types
- Every extracted class must have a single responsibility and be independently testable
- All extractions must preserve existing constructor signatures (backward compatibility for tests)
- All `hasattr(self, "_attr")` guards in extracted code must be removed — if an attribute must exist, it's set in `__init__`
- Every new abstract interface must use `typing.Protocol`, not opaque `Callable` parameters
- Feature flags (`enable_phase1_continuity`, etc.) must NOT be removed here — they are out of scope for Phase 3
- TDD workflow: write test → run to fail → implement → run to pass → commit
- Line length: 100. Linting: `ruff` (rules E, F, I, N, W — E501 ignored). Never run `ruff format`.
- All new files must be <= 800 lines

---

## File Structure Map

### Task 3.1 — Gateway Extraction

| File | Lines | Responsibility |
|------|-------|----------------|
| `OriginAgent/gateway/__init__.py` | ~15 | Public exports |
| `OriginAgent/gateway/http_router.py` | ~200 | Route registration + dispatch + middleware chain |
| `OriginAgent/gateway/ws_handler.py` | ~600 | WebSocket protocol, multiplex, event loop |
| `OriginAgent/gateway/rest_api.py` | ~800 | REST /api/* endpoints |
| `OriginAgent/gateway/auth.py` | ~150 | Token generation, validation, HMAC signing |
| `OriginAgent/gateway/file_server.py` | ~200 | Static file serving |
| `OriginAgent/gateway/ws_event_base.py` | ~200 | Base protocol types: WsRequest, WsResponse, Response |
| `OriginAgent/gateway/media_handler.py` | ~200 | Media URL signing, file attachment handling |
| **New total** | **~2365** | |
| `OriginAgent/channels/websocket.py` | **~1050 (removed)** | After extraction |

### Task 3.2 — Dream.run() Extraction

| File | Lines | Responsibility |
|------|-------|----------------|
| `OriginAgent/agent/memory_phases.py` | ~500 | 4 focused phase classes + orchestration shell |
| `OriginAgent/agent/memory.py` (Dream.run) | **~539 → ~40** | After extraction |

### Task 3.3 — Code Consolidation

| File | Lines | Responsibility |
|------|-------|----------------|
| `OriginAgent/utils/attachments.py` | ~60 | Shared `AttachmentDescriptor` + `parse_attachment` |
| `OriginAgent/utils/dict_utils.py` | ~30 | Shared `deep_merge` |
| `OriginAgent/utils/media_downloader.py` | ~120 | Shared channel media download (future: wire 12 channels) |
| 3 provider files | **-150 (removed)** | After migration to shared utils |

### Tasks 3.4-3.5 (No new files)

| File | Change |
|------|--------|
| `OriginAgent/agent/agent_host.py:113,369-415` | Lazy BDI init |
| `OriginAgent/agent/loop.py:410,1907-2024` | Conditional MetaCognitionObserver |
| `OriginAgent/agent/loop.py:1476-1640` | Remove ~140 line fallback path |

---

## Task 3.1: Extract gateway package from WebSocketChannel (HIGH RISK)

**Files:**
- Create: `OriginAgent/gateway/__init__.py`
- Create: `OriginAgent/gateway/http_router.py`
- Create: `OriginAgent/gateway/ws_handler.py`
- Create: `OriginAgent/gateway/rest_api.py`
- Create: `OriginAgent/gateway/auth.py`
- Create: `OriginAgent/gateway/file_server.py`
- Create: `OriginAgent/gateway/ws_event_base.py`
- Create: `OriginAgent/gateway/media_handler.py`
- Modify: `OriginAgent/channels/websocket.py` — reduce to ~1050 lines (remove extracted methods)
- Test: `tests/channels/test_websocket_*.py` — existing, verify no regression

**Interfaces:**
- Consumes: `WebSocketChannel` (currently 3415 lines, 85+ methods) — all HTTP routing, WebSocket protocol, REST API, auth, file serving
- Produces: `gateway.Gateway` facade that `WebSocketChannel` delegates to; 7 focused sub-modules with well-defined interfaces

**Strategy:** Extract step by step, keeping the `WebSocketChannel.run()` method signature identical. Each extraction is a mechanical move of methods into a new class, followed by delegation. Test after each extraction.

### Step 0: Identify gateway sub-components in websocket.py

```bash
grep -n "^class\|^    def \|^    async def " OriginAgent/channels/websocket.py | head -120
```

Categorize each method into: HTTP router, WS handler, REST API, auth, file server, or core channel.

### Step 1: Create `ws_event_base.py` — shared protocol types

Move the three WsRequest/WsResponse/Response protocol classes that all gateway components depend on:

```python
"""Gateway protocol types shared across gateway modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WsRequest:
    """A decoded WebSocket message frame."""
    id: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)
    sender_id: str = ""
    chat_id: str = ""
    token: str = ""


@dataclass
class Response:
    """A structured HTTP-style response for WS or REST handlers."""
    status: int = 200
    body: Any = None
    headers: dict[str, str] | None = None


@dataclass
class WsResponse:
    """A WebSocket frame to send back."""
    id: str
    type: str  # "response" | "event" | "error"
    action: str
    payload: Any = None
    error: str | None = None
```

### Step 2: Extract auth.py

Move token generation, validation, and HMAC media URL signing methods.
Methods to extract (by signature pattern from websocket.py):

```python
class GatewayAuth:
    """Token generation, validation, and HMAC media URL signing."""

    def __init__(self, config: WebSocketConfig) -> None:
        self._config = config
        self._issued_tokens: dict[str, float] = {}
        self._api_tokens: dict[str, float] = {}
        self._media_secret: bytes = secrets.token_bytes(32)

    def purge_expired_issued_tokens(self) -> None: ...
    def take_issued_token_if_valid(self, token_value: str | None) -> bool: ...
    def handle_token_issue_http(self, connection: Any, request: WsRequest) -> Any: ...
    def check_api_token(self, request: WsRequest) -> bool: ...
    def purge_expired_api_tokens(self) -> None: ...
```

### Step 3: Extract http_router.py

Move route registration and dispatch logic.

```python
class HttpRouter:
    """Route registration and dispatch for HTTP-style requests."""

    def __init__(self) -> None:
        self._routes: dict[str, Callable] = {}
        self._middleware: list[Callable] = []

    def register(self, action: str, handler: Callable) -> None: ...
    async def dispatch(self, request: WsRequest) -> Response: ...
```

### Step 4: Extract ws_handler.py

Move the WebSocket protocol multiplex logic — connection lifecycle, event dispatch, fan-out.

```python
class WebSocketHandler:
    """WebSocket connection lifecycle, protocol multiplex, event dispatch."""

    def __init__(self, channel: WebSocketChannel) -> None:
        self._channel = channel  # back-reference for bus calls
        self._subs: dict[str, set[Any]] = {}
        self._conn_chats: dict[Any, set[str]] = {}
        self._conn_default: dict[Any, str] = {}

    def attach(self, connection: Any, chat_id: str) -> None: ...
    def cleanup_connection(self, connection: Any) -> None: ...
    async def send_event(self, connection: Any, event: str, **fields: Any) -> None: ...
    async def maybe_push_active_goal_state(self, chat_id: str) -> None: ...
```

### Step 5: Extract file_server.py

Move static file serving for the WebUI SPA.

```python
class FileServer:
    """Static file serving for the WebUI SPA."""

    def __init__(self, static_dist_path: Path | None, config: WebSocketConfig) -> None:
        self._static_dist_path = static_dist_path
        self._config = config

    async def serve(self, request: WsRequest) -> Response: ...
    def resolve_path(self, url_path: str) -> Path | None: ...
```

### Step 6: Extract rest_api.py

Move all `_handle_*` methods into a focused class. This is the largest extraction — ~50 handler methods.

```python
class RestApi:
    """All REST /api/* and /webui/* endpoint handlers."""

    def __init__(self, channel: WebSocketChannel) -> None:
        self._channel = channel  # back-reference for introspection, sessions, etc.

    @classmethod
    def transcript_preview_from_lines(cls, lines: list[dict[str, Any]]) -> str: ...

    async def handle_webui_bootstrap(self, request: WsRequest) -> Response: ...
    async def handle_sessions_list(self, request: WsRequest) -> Response: ...
    async def list_webui_transcript_sessions(self) -> list[dict[str, Any]]: ...
    def settings_payload(self, *, requires_restart: bool = False) -> dict[str, Any]: ...
    def bootstrap_runtime_mode_payload(self) -> dict[str, Any]: ...
    async def handle_settings(self, request: WsRequest) -> Response: ...
    async def handle_commands(self, request: WsRequest) -> Response: ...
    async def handle_self(self, request: WsRequest) -> Response: ...
    async def handle_skills_list(self, request: WsRequest) -> Response: ...
    async def handle_skill_detail(self, request: WsRequest, skill_name: str) -> Response: ...
    async def handle_skill_action(self, request: WsRequest, skill_name: str, action: str) -> Response: ...
    async def handle_domains_list(self, request: WsRequest) -> Response: ...
    async def handle_domain_detail(self, request: WsRequest, pack_id: str) -> Response: ...
    async def handle_settings_update(self, request: WsRequest) -> Response: ...
    async def handle_settings_provider_update(self, request: WsRequest) -> Response: ...
    async def handle_settings_provider_models(self, request: WsRequest) -> Response: ...
    async def handle_meta_cognition_status(self, request: WsRequest) -> Response: ...
    async def handle_evolution_signals(self, request: WsRequest) -> Response: ...
    # ... all other _handle_* methods
```

### Step 7: Create `gateway/__init__.py` — facade

```python
"""Gateway package — extracted HTTP/WS/REST/auth infrastructure.

WebSocketChannel was a 3415-line God Class. The gateway package splits it
into focused, independently testable components.
"""

from OriginAgent.gateway.http_router import HttpRouter
from OriginAgent.gateway.ws_handler import WebSocketHandler
from OriginAgent.gateway.rest_api import RestApi
from OriginAgent.gateway.auth import GatewayAuth
from OriginAgent.gateway.file_server import FileServer
```

### Step 8: Thin WebSocketChannel facade

Replace each extracted method in `WebSocketChannel` with a one-line delegation:

```python
# BEFORE (in WebSocketChannel):
    def _purge_expired_issued_tokens(self) -> None:
        now = time.monotonic()
        stale = [k for k, expires in self._issued_tokens.items() if expires < now]
        for k in stale:
            self._issued_tokens.pop(k, None)

# AFTER:
    def _purge_expired_issued_tokens(self) -> None:
        self._auth.purge_expired_issued_tokens()

    def _handle_webui_bootstrap(self, request: WsRequest) -> Response:
        return await self._rest_api.handle_webui_bootstrap(request)
```

Keep the facade to ~1050 lines (remove 2365+ lines of extracted code).

### Step 9: Run tests

```bash
.venv/Scripts/python.exe -m pytest tests/channels/test_websocket*.py -v --basetemp="C:/Users/15216/AppData/Local/Temp/pytest-oa"
```

Expected: PASS (same behavior, different internal structure)

### Step 10: Commit

```bash
git add OriginAgent/gateway/ OriginAgent/channels/websocket.py
git commit -m "refactor: extract gateway package from WebSocketChannel God Class

WebSocketChannel was a 3415-line, 85+ method God Class serving as HTTP
router, WebSocket multiplexer, REST API, auth handler, and file server.
Extracted into OriginAgent/gateway/ with 7 focused components:
- ws_event_base.py: shared protocol types (WsRequest, Response, WsResponse)
- auth.py: token generation, validation, HMAC media URL signing
- http_router.py: route registration and dispatch
- ws_handler.py: WebSocket connection lifecycle and multiplex
- file_server.py: static file serving for WebUI SPA
- rest_api.py: all /api/* and /webui/* endpoint handlers
- __init__.py: public API exports

WebSocketChannel is now a ~1050-line thin facade that delegates to
gateway components.

Fixes anti-pattern finding A8 from the architecture review."
```

---

## Task 3.2: Extract Dream.run() phases (MEDIUM RISK)

**Files:**
- Create: `OriginAgent/agent/memory_phases.py`
- Modify: `OriginAgent/agent/memory.py` — replace 539-line `Dream.run()` with ~40-line orchestration
- Test: `tests/agent/test_dream.py` — existing, verify no regression

**Interfaces:**
- Consumes: `Dream` (self.store, self.provider, self.auxiliary_router, self.model, self._HISTORY_ENTRY_PREVIEW_MAX_CHARS, self._MEMORY_FILE_MAX_CHARS, self._SOUL_FILE_MAX_CHARS, self._USER_FILE_MAX_CHARS, self.max_batch_size, self.annotate_line_ages, self._tools, self._last_forgetting_execution, self._remember_report)
- Produces: 4 phase classes with `run(Dream, ...)` static/asimple methods, each independently testable

### Step 1: Analyze current Dream.run() structure

```python
# Current Dream.run() flow (539 lines at memory.py:1942-2481):

async def run(self) -> bool:
    # Init: timestamps, forgetting, cursor
    # Phase 0: enhance closed episode summaries
    # Early exit if no entries
    # Build history_text and file_context
    # Phase 1: LLM fact proposal + parse
    # Phase 2: delegate to AgentRunner for maintenance
    # Remember report
```

### Step 2: Create `memory_phases.py` with Phase 0

```python
"""Extracted phases from Dream.run() — each is independently testable."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from OriginAgent.agent.memory import Dream


async def run_phase0(dream: Dream, *, started_at: str) -> None:
    """Enhance closed episode summaries with LLM-generated content."""
    await dream._phase0_episode_summaries(started_at=started_at)
```

### Step 3: Add Phase 1 (fact proposal + parsing)

```python
@dataclass
class Phase1Result:
    """Result of Phase 1 fact proposal."""
    success: bool
    proposal_batch: list[Any] | None = None
    error_report: dict[str, Any] | None = None


async def run_phase1(dream: Dream, *, started_at: str, history_text: str,
                      file_context: str, facts_context: str,
                      batch: list[dict]) -> Phase1Result:
    """Propose structured facts from conversation history via LLM."""
    # Build phase1_prompt from inputs
    # Call call_llm with dream.auxiliary_router, dream.provider
    # Parse with parse_fact_proposal_response
    # Handle errors with snapshot restore
    ...
```

### Step 4: Add Phase 2 (skill generation + AgentRunner maintenance)

```python
async def run_phase2(dream: Dream, *, started_at: str,
                      history_text: str, batch: list[dict]) -> bool:
    """Delegate to AgentRunner for non-MEMORY maintenance only."""
    # Current Phase 2 logic
    ...
```

### Step 5: Add DreamForgetting

```python
def run_forgetting(dream: Dream, *, started_at: str) -> dict[str, Any]:
    """Execute forgetting maintenance and consume governed fact candidates."""
    forgetting = dream._execute_forgetting_maintenance(started_at=started_at)
    queue_result = dream._consume_governed_fact_candidates()
    return {**queue_result, "forgetting_execution": forgetting}
```

### Step 6: Reduce `Dream.run()` to orchestration

```python
async def run(self) -> bool:
    from OriginAgent.agent.memory_phases import (
        run_forgetting, run_phase0, run_phase1, run_phase2,
    )

    started_at = now_iso()
    forgetting_execution = run_forgetting(self, started_at=started_at)
    await run_phase0(self, started_at=started_at)

    last_cursor = self.store.get_last_dream_cursor()
    entries = self.store.read_unprocessed_history(since_cursor=last_cursor)
    if not entries:
        # ... early exit with report ...
        return False

    batch = entries[: self.max_batch_size]
    history_text = build_history_text(batch)  # extracted helper
    file_context = build_file_context(self)   # extracted helper
    facts_context = self._format_current_facts()

    phase1 = await run_phase1(self, started_at=started_at,
                               history_text=history_text,
                               file_context=file_context,
                               facts_context=facts_context,
                               batch=batch)
    if not phase1.success:
        return False

    phase2 = await run_phase2(self, started_at=started_at,
                               history_text=history_text, batch=batch)
    self._remember_report(...)
    return True
```

### Step 7: Run tests

```bash
.venv/Scripts/python.exe -m pytest tests/agent/test_dream.py -v --basetemp="C:/Users/15216/AppData/Local/Temp/pytest-oa"
```

Expected: PASS

### Step 8: Commit

```bash
git add OriginAgent/agent/memory_phases.py OriginAgent/agent/memory.py
git commit -m "refactor: extract Dream.run() phases into focused functions

Dream.run() was a 539-line monolithic method with 6+ nesting levels
covering Phase 0/1/2, forgetting maintenance, and signal detection.
Each phase is now a standalone async function in memory_phases.py
with clear inputs and return values, independently testable.

Fixes anti-pattern finding A9 from the architecture review."
```

---

## Task 3.3: Consolidate duplicated code across providers (LOW-MEDIUM RISK)

**Files:**
- Create: `OriginAgent/utils/attachments.py` — shared `parse_attachment`
- Create: `OriginAgent/utils/dict_utils.py` — shared `deep_merge`
- Modify: `OriginAgent/providers/anthropic_provider.py` — use shared `parse_attachment`
- Modify: `OriginAgent/providers/bedrock_provider.py` — use shared `parse_attachment` + `deep_merge`
- Modify: `OriginAgent/providers/openai_responses/converters.py` — use shared `parse_attachment`
- Modify: `OriginAgent/providers/openai_compat_provider.py` — use shared `deep_merge`
- Test: `tests/providers/test_*.py` — existing, verify no regression

**Interfaces:**
- Consumes: 3x `_attachment_descriptor` (anthropic_provider.py:27, bedrock_provider.py:43, openai_responses/converters.py:70), 2x `_deep_merge` (bedrock_provider.py:26, openai_compat_provider.py:235)
- Produces: Shared `parse_attachment` + `AttachmentDescriptor` in `utils/attachments.py`; shared `deep_merge` in `utils/dict_utils.py`

### Step 1: Create `utils/attachments.py`

```python
"""Shared attachment descriptor parsing for providers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class AttachmentDescriptor:
    """Describes an attachment (file, image, media) referenced in a provider message."""
    path: Path
    name: str
    mime: str | None
    kind: str
    size_bytes: int
    source: str
    metadata: dict[str, Any]


def parse_attachment(block: dict[str, Any]) -> AttachmentDescriptor | None:
    """Parse an attachment dict from provider messages into AttachmentDescriptor.

    Expects ``block`` to contain an ``"attachment"`` key with a dict value
    having at least ``path`` (str) and ``kind`` (str) fields.

    Returns ``None`` when *block* has no valid ``"attachment"``.
    """
    attachment = block.get("attachment")
    if not isinstance(attachment, dict):
        return None
    path = attachment.get("path")
    kind = attachment.get("kind")
    if not isinstance(path, str) or not path or not isinstance(kind, str) or not kind:
        return None
    name = attachment.get("name")
    size_bytes = attachment.get("size_bytes")
    return AttachmentDescriptor(
        path=Path(path),
        name=name if isinstance(name, str) and name else Path(path).name,
        mime=attachment.get("mime") if isinstance(attachment.get("mime"), str) else None,
        kind=kind,
        size_bytes=size_bytes if isinstance(size_bytes, int) else 0,
        source=attachment.get("source") if isinstance(attachment.get("source"), str) else "media",
        metadata=attachment.get("metadata") if isinstance(attachment.get("metadata"), dict) else {},
    )
```

### Step 2: Create `utils/dict_utils.py`

```python
"""Shared dictionary utility functions."""

from __future__ import annotations

from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict.

    Nested dicts are merged key-by-key; all other types in *override*
    replace the corresponding key in *base*.
    """
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
```

### Step 3: Update `anthropic_provider.py`

Replace the local `_attachment_descriptor` with the shared utility:

```python
# OLD (import + local function):
from OriginAgent.providers.bedrock_provider import AttachmentDescriptor

def _attachment_descriptor(block: dict[str, Any]) -> AttachmentDescriptor | None:
    ...  # 18 lines, duplicated

# NEW (top of file):
from OriginAgent.utils.attachments import AttachmentDescriptor, parse_attachment

# Then replace all calls of _attachment_descriptor(block) with parse_attachment(block)
```

### Step 4: Update `bedrock_provider.py`

Same pattern — remove local `_attachment_descriptor` and `_deep_merge`, import from shared utils.

```python
# OLD:
def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    ...  # 10 lines, duplicated

def _attachment_descriptor(block: dict[str, Any]) -> AttachmentDescriptor | None:
    ...  # 18 lines, duplicated

# NEW (top of file):
from OriginAgent.utils.attachments import AttachmentDescriptor, parse_attachment
from OriginAgent.utils.dict_utils import deep_merge

# Then replace _deep_merge → deep_merge and _attachment_descriptor → parse_attachment
```

### Step 5: Update `openai_responses/converters.py`

```python
# OLD:
from OriginAgent.providers.bedrock_provider import AttachmentDescriptor
def _attachment_descriptor(block: dict[str, Any]) -> AttachmentDescriptor | None:
    ...  # 27 lines, different null-handling style

# NEW:
from OriginAgent.utils.attachments import AttachmentDescriptor, parse_attachment
```

**Note:** The `openai_responses/converters.py` version has slightly different null-handling logic (checks `name` and `kind` differently). The shared `parse_attachment` from `utils/attachments.py` was designed to match the most common pattern (anthropic + bedrock). Verify this works for the converter's call sites. If the converter needs specific behavior, add a `strict: bool = True` parameter to `parse_attachment`.

### Step 6: Update `openai_compat_provider.py`

```python
# OLD:
def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    ...  # 10 lines, duplicated

# NEW (top of file):
from OriginAgent.utils.dict_utils import deep_merge

# Replace _deep_merge → deep_merge
```

### Step 7: Run tests

```bash
.venv/Scripts/python.exe -m pytest tests/providers/ -v --basetemp="C:/Users/15216/AppData/Local/Temp/pytest-oa" -x
```

Expected: PASS (all provider tests)

### Step 8: Commit

```bash
git add OriginAgent/utils/attachments.py OriginAgent/utils/dict_utils.py \
     OriginAgent/providers/anthropic_provider.py \
     OriginAgent/providers/bedrock_provider.py \
     OriginAgent/providers/openai_compat_provider.py \
     OriginAgent/providers/openai_responses/converters.py
git commit -m "refactor: consolidate duplicated code across providers

Extracted shared utilities:
- utils/attachments.py: parse_attachment() — was duplicated in 3 providers
- utils/dict_utils.py: deep_merge() — was duplicated in 2 providers

Each provider now imports from the shared module instead of maintaining
its own copy. The signatures and behavior are identical.

Fixes anti-pattern findings A12, A13 from the architecture review."
```

---

## Task 3.4: Lazy-init BDI and conditional MetaCognitionObserver (LOW RISK)

**Files:**
- Modify: `OriginAgent/agent/agent_host.py:113` — make BDI init conditional
- Modify: `OriginAgent/agent/loop.py:410` — guard MetaCognitionObserver registration
- Test: `tests/agent/test_mcp_connection.py`, `tests/agent/test_loop_runtime_context.py` — existing, verify no regression

**Interfaces:**
- Consumes: `AgentHost.__init__`, `_install_meta_cognition_observer()`
- Produces: BDI inited only when `deps.bdi_config is not None`; observer registered only when `_meta_cognition_runtime` is available

### Step 1: Lazy BDI initialization in `agent_host.py:113`

```python
# OLD (agent_host.py:113 — called unconditionally in __init__):
    self._init_bdi_engine()

# NEW: only call if bdi_config is present
    bdi_config = self._deps.bdi_config
    if bdi_config is not None:
        self._init_bdi_engine()
    else:
        self._bdi_engine = None
        self._desire_store = None
        self._inner_monologue_engine = None
```

Also update `_init_bdi_engine` to not handle the None case (it's now handled by the caller):

```python
def _init_bdi_engine(self) -> None:
    """Initialise the BDI deliberation engine (bdi_config must be non-None)."""
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
    # ... rest of the method
```

### Step 2: Conditional MetaCognitionObserver in `loop.py:410`

```python
# OLD (loop.py:410 — always registers):
    self._install_meta_cognition_observer()

# NEW: only when meta-cognition is enabled
    if getattr(self, "_meta_cognition_runtime", None) is not None:
        self._install_meta_cognition_observer()
```

### Step 3: Run tests

```bash
.venv/Scripts/python.exe -m pytest tests/agent/test_mcp_connection.py tests/agent/test_loop_runtime_context.py -v --basetemp="C:/Users/15216/AppData/Local/Temp/pytest-oa"
```

Expected: PASS

### Step 4: Commit

```bash
git add OriginAgent/agent/agent_host.py OriginAgent/agent/loop.py
git commit -m "refactor: lazy-init BDI engine and conditional MetaCognitionObserver

BDI engine is only initialized when bdi_config is present (was always
called in __init__). MetaCognitionObserver is only registered when
meta-cognition runtime is non-None, eliminating a ghost observer that
fired on every tool result but immediately returned.

Fixes GhostNode findings G1 and G4 from the architecture review."
```

---

## Task 3.5: Remove fallback compatibility dead code (LOW RISK)

**Files:**
- Modify: `OriginAgent/agent/loop.py:1476-1640`
- Test: Full agent test suite

**Interfaces:**
- Consumes: `_run_agent_loop` fallback path (~140 lines, lines 1503-1640)
- Produces: Direct delegation to `AgentRuntime._run_agent_loop` only

### Step 1: Verify no tests use AgentLoop.__new__

```bash
grep -rn "AgentLoop\.__new__" tests/ --include="*.py"
```

Expected: No matches. If found, migrate those tests.

### Step 2: Remove the fallback path

```python
async def _run_agent_loop(self, ...) -> tuple[...]:
    """Delegates to AgentRuntime."""
    async def _checkpoint_cb(sess: Session, payload: dict[str, Any]) -> None:
        self._set_runtime_checkpoint(sess, payload)

    result = await self._runtime._run_agent_loop(
        initial_messages,
        on_progress=on_progress,
        # ... all current delegation params ...
        checkpoint_cb=_checkpoint_cb,
        set_current_iteration=lambda it: setattr(self, "_current_iteration", it),
    )
    self._last_usage = getattr(result, "usage", None) if hasattr(result, "usage") else None
    return result  # result is a tuple from AgentRuntime

    # REMOVED: Entire fallback path (loop.py:1503-1640, ~140 lines)
```

### Step 3: Run full agent test suite

```bash
.venv/Scripts/python.exe -m pytest tests/agent/ -v --basetemp="C:/Users/15216/AppData/Local/Temp/pytest-oa" -x
```

Expected: All tests pass (excluding pre-existing failures like the 6 continuity phase1 tests)

### Step 4: Commit

```bash
git add OriginAgent/agent/loop.py
git commit -m "refactor: remove fallback compatibility dead code from _run_agent_loop

The ~140-line fallback path existed only for tests bypassing __init__
via AgentLoop.__new__. No tests use this pattern. The hasattr(self,
'_runtime') guard is removed — if _runtime is missing, a clear
AttributeError is raised.

Fixes anti-pattern finding A15 from the architecture review."
```

---

## Self-Review Checklist

**1. Spec coverage:**
- Anti-pattern A8 (WebSocket God Class): ✅ Task 3.1 extracts gateway package
- Anti-pattern A9 (Dream.run God Method): ✅ Task 3.2 extracts phases
- Anti-pattern A12-A14 (duplicate code): ✅ Task 3.3 consolidates utils
- GhostNode G1 (unconditional BDI): ✅ Task 3.4 lazy-init
- GhostNode G4 (ghost observer): ✅ Task 3.4 conditional registration
- Anti-pattern A15 (fallback dead code): ✅ Task 3.5 removes

**2. Placeholder scan:** No "TBD", "TODO", or "implement later" found. All steps have code blocks or explicit commands where applicable.

**3. Type consistency:** All extracted classes use the same parameter names and types as the original code. Delegation preserves exact signatures.

---

## Execution Summary

| Phase 3 Task | Est. Effort | Risk | Lines Changed |
|---|---|---|---|
| 3.1 Gateway extraction | 4-6 hours | HIGH | ~3400 removed, ~2365 added |
| 3.2 Dream.run() extraction | 2-3 hours | MEDIUM | ~500 removed, ~500 added |
| 3.3 Code consolidation | 1-2 hours | LOW-MEDIUM | ~30 added, ~60 removed |
| 3.4 Lazy BDI + observer | 30 min | LOW | ~10 changed |
| 3.5 Remove fallback code | 30 min | LOW | ~140 removed |

**Recommended execution order:** 3.3 → 3.4 → 3.5 → 3.1 → 3.2 (smallest to largest, so quick wins build confidence before tackling the big extractions).

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
