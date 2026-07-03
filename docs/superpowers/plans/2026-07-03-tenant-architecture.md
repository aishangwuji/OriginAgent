# Tenant Architecture: Multi-Person Jarvis on a Family NAS

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace session-based identity with tenant-based identity so a single OriginAgent instance serves multiple family members, each with their own unified Jarvis (one memory, one BDI, one set of goals) across all their devices and channels.

**Architecture:** Three-layer identity model. Layer 1: `IdentityResolver` maps raw channel+sender signatures to `tenant_id` (and provides a speaker recognition plugin interface). Layer 2: `Tenant` bundles one person's unified session, BDI engine, memory, and permissions. Layer 3: `SharedSpace` holds cross-tenant facts, devices, and resources. The existing `session` concept is downgraded from "identity boundary" to "context window manager" — each tenant has exactly one session internally, surfaced across many channels.

**Tech Stack:** Python 3.11+, Pydantic v2 config, asyncio, loguru, contextvars

## Global Constraints

- Python >= 3.11
- Follow PEP 8 via ruff (select E, F, I, N, W; ignore E501)
- Line length: 100
- Never run `ruff format`
- Tests use pytest with `asyncio_mode = "auto"`
- Use `.\.venv\Scripts\python.exe` as Python interpreter
- Use `--basetemp=.pytest_tmp` for pytest
- All new config fields are Pydantic `Base` with `AliasChoices` for camelCase compat
- Backward compat: single-tenant mode (no config change) = current behavior preserved
- Existing session-based tests MUST continue to pass

---

## Architecture Diagram

```
OriginAgent (single process on family NAS)

┌──────────────────────────────────────────────────────────────┐
│                     IdentityResolver                          │
│  channel+sender_id ───────────────► tenant_id                │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────────────┐      │
│  │ Channel  │ │ Pairing  │ │ SpeakerRecognitionPlugin │      │
│  │ Lookup   │ │ Lookup   │ │ (预留接口)               │      │
│  └──────────┘ └──────────┘ └──────────────────────────┘      │
└──────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   ┌────▼─────┐         ┌────▼─────┐         ┌────▼─────┐
   │  Tenant  │         │  Tenant  │         │  Tenant  │
   │  "dad"   │         │  "mom"   │         │ "sister" │
   │          │         │          │         │          │
   │ 1 session│         │ 1 session│         │ 1 session│
   │ 1 BDI    │         │ 1 BDI    │         │ 1 BDI    │
   │ mem +    │         │ mem +    │         │ mem +    │
   │ desires  │         │ desires  │         │ desires  │
   └──┬───┬───┘         └──┬───┬───┘         └──┬───┬───┘
      │   │                │   │                │   │
   Telegram WebUI      Phone Watch        Watch Phone
   (开车)  (电脑)      (上班) (晨跑)      (上学) (放学)

┌──────────────────────────────────────────────────────────────┐
│                      SharedSpace                              │
│  shared facts · shared devices · shopping list · calendar     │
│  每个 Tenant 的 BDI _gather_beliefs() 同时拉自己的和 shared    │
└──────────────────────────────────────────────────────────────┘
```

## Identity Resolution: Multi-Device + Multi-Channel per Tenant

### Scenario Matrix

| Person | Device 1 | Device 2 | Device 3 | Identity Clue |
|--------|----------|----------|----------|---------------|
| 爸爸 | Telegram (手机) | WebUI (电脑) | 对智能音箱说话 | Telegram user_id, WebSocket token, 声纹 |
| 妈妈 | 手机 App | 手表 | 矩阵麦克风 | phone sender_id, watch token, 声纹 |
| 姐姐 | 手机 | 智能手表 | — | phone sender_id, watch token |
| 弟弟 | 智能手表 | — | — | watch token |

### Resolution Pipeline

```python
channel + raw_sender ──► [lookup table] ──► tenant_id
                                │ (miss)
                                ▼
                         [pairing store] ──► tenant_id  
                                │ (miss)
                                ▼
                         [speaker plugin] ──► tenant_id  (预留)
                                │ (miss)
                                ▼
                           "guest" / None
```

## File Map

| File | Role | Task |
|------|------|------|
| `OriginAgent/config/schema.py` | `TenantConfig`, `TenantsConfig`, `IdentityResolutionConfig` | 1 |
| `OriginAgent/identity/__init__.py` | **NEW** — identity package | 1 |
| `OriginAgent/identity/resolver.py` | **NEW** — `IdentityResolver` with plugin chain | 1 |
| `OriginAgent/identity/tenant.py` | **NEW** — `Tenant` dataclass, `TenantRegistry` | 1 |
| `OriginAgent/identity/speaker_plugin.py` | **NEW** — `SpeakerRecognitionPlugin` protocol (预留接口) | 1 |
| `OriginAgent/identity/shared_space.py` | **NEW** — `SharedSpace` facts + devices | 3 |
| `OriginAgent/agent/tenant_context.py` | **NEW** — contextvars-based current tenant | 2 |
| `OriginAgent/agent/loop.py` | Wire tenant into AgentLoop, downgrade session | 2 |
| `OriginAgent/agent/agent_host.py` | Per-tenant BDI, multi-tenant lifecycle | 2 |
| `OriginAgent/session/manager.py` | Tenant-aware session key, workspace layout | 2 |
| `OriginAgent/channels/base.py` | Channel registration with tenant hints | 2 |
| `OriginAgent/channels/telegram.py` | Register sender_id → tenant mapping | 2 |
| `OriginAgent/channels/websocket.py` | Register token → tenant mapping | 2 |
| `OriginAgent/bdi/deliberation.py` | Per-tenant desire store, shared belief pull | 3 |
| `tests/identity/test_resolver.py` | **NEW** — identity resolution tests | 1 |
| `tests/identity/test_tenant.py` | **NEW** — tenant registry + context tests | 2 |
| `tests/identity/test_shared_space.py` | **NEW** — shared space tests | 3 |
| `tests/integration/test_multi_tenant.py` | **NEW** — multi-tenant integration test | 2 |

---

## Phase 1: Identity Foundation (地基 — no behavior change)

### Task 1: Tenant config + IdentityResolver + Speaker plugin interface

**Files:**
- Create: `OriginAgent/identity/__init__.py`
- Create: `OriginAgent/identity/resolver.py`
- Create: `OriginAgent/identity/tenant.py`
- Create: `OriginAgent/identity/speaker_plugin.py`
- Modify: `OriginAgent/config/schema.py` — add `TenantConfig`, `TenantsConfig`, `IdentityResolutionConfig`
- Create: `tests/identity/test_resolver.py`
- Create: `tests/identity/test_tenant.py`

**Interfaces:**
- Consumes: `GatewayConfig` (existing), `pairing/store.py` (existing)
- Produces: `IdentityResolver.resolve(channel, sender_id) -> tenant_id | None`, `TenantRegistry`, `SpeakerRecognitionPlugin` protocol

- [ ] **Step 1: Add config models**

In `OriginAgent/config/schema.py`, add after `BDIConfig`:

```python
class TenantChannelBinding(Base):
    """Map a channel+sender pair to a tenant."""
    channel: str                                    # "telegram", "websocket", "cli"
    sender_id: str                                  # raw sender from channel
    label: str = ""                                 # "爸爸的手机", "姐姐的手表"


class TenantConfig(Base):
    """A person in the household."""
    tenant_id: str                                  # "dad", "mom", "sister"
    display_name: str = ""                          # "爸爸", "妈妈", "姐姐"
    bindings: list[TenantChannelBinding] = Field(default_factory=list)
    bdi_enabled: bool = True
    permissions: dict[str, bool] = Field(default_factory=lambda: {
        "exec": False, "write_files": False, "device_control": True,
    })


class TenantsConfig(Base):
    """All tenants known to this OriginAgent instance."""
    tenants: list[TenantConfig] = Field(default_factory=list)
    default_tenant_id: str = ""                     # fallback for unmatched senders
    guest_tenant_enabled: bool = True               # unmatched → "guest" tenant?


class SpeakerRecognitionConfig(Base):
    """Reserved interface for speaker recognition (voice/face)."""
    enabled: bool = False
    plugin: str = ""                                # fully qualified Python class path
    config: dict[str, Any] = Field(default_factory=dict)
    confidence_threshold: float = 0.7
```

Add to `GatewayConfig`:

```python
class GatewayConfig(Base):
    # ... existing fields ...
    bdi: BDIConfig = Field(default_factory=BDIConfig)
    tenants: TenantsConfig = Field(default_factory=TenantsConfig)
    speaker_recognition: SpeakerRecognitionConfig = Field(
        default_factory=SpeakerRecognitionConfig
    )
```

- [ ] **Step 2: Create `SpeakerRecognitionPlugin` protocol**

Create `OriginAgent/identity/speaker_plugin.py`:

```python
"""Reserved interface for speaker/face recognition plugins.

This is a PROTOCOL — no implementation is shipped.  Third-party plugins
(vocalprint, face recognition, device proximity) implement this interface
and are loaded by the IdentityResolver at runtime.

Example future implementations:
- Vocalprint matching against enrolled voice samples per tenant
- Face recognition from camera feed on a home robot
- BLE device proximity (phone/watch MAC address → tenant)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class RecognitionResult:
    tenant_id: str
    confidence: float          # 0.0–1.0
    method: str                # "vocalprint", "face", "ble_proximity"
    metadata: dict[str, Any]


@runtime_checkable
class SpeakerRecognitionPlugin(Protocol):
    """Protocol for identity recognition plugins.

    Plugins receive raw audio/video/sensor data and return a
    RecognitionResult if they can identify the speaker, or None.
    """

    @property
    def plugin_name(self) -> str: ...

    def enroll(self, tenant_id: str, sample: Any) -> bool:
        """Enroll a new identity sample for a tenant.  Returns success."""
        ...

    async def recognize(self, sample: Any) -> RecognitionResult | None:
        """Try to recognize who is speaking.  Returns None if uncertain."""
        ...

    def unenroll(self, tenant_id: str) -> bool:
        """Remove a tenant's enrollment data."""
        ...
```

- [ ] **Step 3: Create `Tenant` model and `TenantRegistry`**

Create `OriginAgent/identity/tenant.py`:

```python
"""Tenant model and registry — one person, one Jarvis instance."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from OriginAgent.config.schema import TenantConfig, TenantsConfig


@dataclass
class Tenant:
    """A single person in the household with their own Jarvis."""
    tenant_id: str                     # "dad"
    display_name: str                  # "爸爸"
    unified_session_key: str           # "tenant:dad"

    # Storage
    workspace_dir: Path                # workspace/tenants/dad/

    # BDI
    bdi_enabled: bool = True
    bdi_engine: Any = None             # DeliberationEngine (set during agent start)

    # Permissions
    permissions: dict[str, bool] = field(default_factory=dict)

    # Channel bindings (how this person connects)
    bindings: list[dict[str, str]] = field(default_factory=list)
    # Each: {"channel": "telegram", "sender_id": "12345", "label": "手机"}

    @property
    def active_channels(self) -> list[str]:
        return [b["channel"] for b in self.bindings]


class TenantRegistry:
    """Thread-safe registry of all tenants."""

    def __init__(self, workspace: Path, config: TenantsConfig | None = None):
        self._workspace = workspace
        self._tenants: dict[str, Tenant] = {}
        self._by_channel_sender: dict[tuple[str, str], str] = {}  # (channel,sender) → tenant_id
        self._guest_tenant: Tenant | None = None

        if config:
            for tc in config.tenants:
                self._register_from_config(tc)
            if config.guest_tenant_enabled:
                self._create_guest(workspace)

    def _register_from_config(self, tc: TenantConfig) -> Tenant:
        tenant = Tenant(
            tenant_id=tc.tenant_id,
            display_name=tc.display_name or tc.tenant_id,
            unified_session_key=f"tenant:{tc.tenant_id}",
            workspace_dir=self._workspace / "tenants" / tc.tenant_id,
            bdi_enabled=tc.bdi_enabled,
            permissions=dict(tc.permissions),
            bindings=[{
                "channel": b.channel,
                "sender_id": b.sender_id,
                "label": b.label or f"{b.channel}:{b.sender_id}",
            } for b in tc.bindings],
        )
        self._tenants[tc.tenant_id] = tenant
        for b in tc.bindings:
            self._by_channel_sender[(b.channel, b.sender_id)] = tc.tenant_id
        return tenant

    def _create_guest(self, workspace: Path) -> None:
        self._guest_tenant = Tenant(
            tenant_id="guest",
            display_name="Guest",
            unified_session_key="tenant:guest",
            workspace_dir=workspace / "tenants" / "guest",
            bdi_enabled=False,
        )

    def lookup(self, channel: str, sender_id: str) -> Tenant | None:
        """Find tenant by channel+sender_id.  Falls back to guest."""
        tid = self._by_channel_sender.get((channel, str(sender_id)))
        if tid and tid in self._tenants:
            return self._tenants[tid]
        return self._guest_tenant

    def get(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def all(self) -> list[Tenant]:
        tenants = list(self._tenants.values())
        if self._guest_tenant:
            tenants.append(self._guest_tenant)
        return tenants

    def register_binding(self, channel: str, sender_id: str, tenant_id: str) -> None:
        """Register a new channel binding for a tenant (e.g. via pairing flow)."""
        if tenant_id not in self._tenants:
            raise KeyError(f"Unknown tenant: {tenant_id}")
        key = (channel, str(sender_id))
        self._by_channel_sender[key] = tenant_id
        self._tenants[tenant_id].bindings.append({
            "channel": channel,
            "sender_id": str(sender_id),
            "label": f"{channel}:{sender_id}",
        })

    def claim_pairing(
        self, channel: str, sender_id: str, tenant_id: str
    ) -> Tenant:
        """After pairing approval, claim this sender for a tenant.

        Called when a paired-but-unbound sender says "I'm 爸爸".
        The existing __pairing_pending__ session data is moved to the
        tenant's workspace.
        """
        if tenant_id not in self._tenants:
            raise KeyError(f"Unknown tenant: {tenant_id}")
        self.register_binding(channel, sender_id, tenant_id)
        return self._tenants[tenant_id]
```

- [ ] **Step 4: Create `IdentityResolver`**

Create `OriginAgent/identity/resolver.py`:

```python
"""IdentityResolver — maps raw channel+sender signals to tenant_id.

Resolution chain (first match wins):
1. TenantRegistry lookup (channel + sender_id)
2. Pairing store lookup (approved pairings)
3. SpeakerRecognitionPlugin (if enabled and sample provided)
4. Guest / default fallback
"""

from __future__ import annotations

import importlib
from typing import Any

from loguru import logger

from OriginAgent.identity.speaker_plugin import SpeakerRecognitionPlugin
from OriginAgent.identity.tenant import Tenant, TenantRegistry
from OriginAgent.pairing.store import is_approved as pairing_is_approved


class IdentityResolver:
    """Resolve who is communicating through which channel."""

    def __init__(
        self,
        registry: TenantRegistry,
        *,
        speaker_plugin: SpeakerRecognitionPlugin | None = None,
        speaker_threshold: float = 0.7,
    ):
        self._registry = registry
        self._speaker_plugin = speaker_plugin
        self._speaker_threshold = speaker_threshold

    def resolve(
        self,
        channel: str,
        sender_id: str,
        *,
        speaker_sample: Any = None,
    ) -> Tenant:
        """Resolve a channel+sender_id to a Tenant.  Never returns None.

        *speaker_sample* is reserved for voice/face recognition data.
        """
        # Step 1: Direct channel binding
        tenant = self._registry.lookup(channel, str(sender_id))
        if tenant is not None and tenant.tenant_id != "guest":
            return tenant

        # Step 2: Pairing store (paired sender — need to ask which tenant)
        if pairing_is_approved(channel, str(sender_id)):
            # A paired but unbound sender: the pairing system approved them,
            # but no tenant claimed them yet.  Return a marker that tells
            # the caller to ask "which family member are you?"
            return Tenant(
                tenant_id="__pairing_pending__",
                display_name="New Device",
                unified_session_key=f"tenant:pairing:{channel}:{sender_id}",
                workspace_dir=self._registry._workspace / "tenants" / "_pairing",
                bdi_enabled=False,
            )

        # Step 3: Speaker recognition (reserved)
        if self._speaker_plugin is not None and speaker_sample is not None:
            try:
                result = self._speaker_plugin.recognize(speaker_sample)
                if result and result.confidence >= self._speaker_threshold:
                    t = self._registry.get(result.tenant_id)
                    if t is not None:
                        logger.info(
                            "Speaker recognition: {} → {} (conf={:.2f})",
                            channel, result.tenant_id, result.confidence,
                        )
                        return t
            except Exception:
                logger.exception("Speaker recognition plugin error")

        # Step 4: Guest fallback
        guest = self._registry.lookup(channel, "__guest__")
        if guest is not None:
            return guest

        # If absolutely nothing matches, return a synthetic guest
        return self._registry._guest_tenant or Tenant(
            tenant_id="guest",
            display_name="Guest",
            unified_session_key="tenant:guest",
            workspace_dir=self._registry._workspace / "tenants" / "guest",
            bdi_enabled=False,
        )

    @classmethod
    def from_config(
        cls,
        registry: TenantRegistry,
        speaker_config: Any | None = None,
    ) -> IdentityResolver:
        """Create resolver with optional speaker plugin loaded from config."""
        plugin: SpeakerRecognitionPlugin | None = None
        threshold = 0.7

        if speaker_config is not None and getattr(speaker_config, "enabled", False):
            plugin_path = getattr(speaker_config, "plugin", "")
            if plugin_path:
                try:
                    mod_name, cls_name = plugin_path.rsplit(".", 1)
                    mod = importlib.import_module(mod_name)
                    plugin_cls = getattr(mod, cls_name)
                    plugin = plugin_cls(**getattr(speaker_config, "config", {}))
                except Exception:
                    logger.exception("Failed to load speaker recognition plugin")
            threshold = getattr(speaker_config, "confidence_threshold", 0.7)

        return cls(registry, speaker_plugin=plugin, speaker_threshold=threshold)
```

- [ ] **Step 5: Write identity tests**

Create `tests/identity/test_resolver.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from OriginAgent.identity.tenant import Tenant, TenantRegistry
from OriginAgent.identity.resolver import IdentityResolver
from OriginAgent.config.schema import TenantChannelBinding, TenantConfig, TenantsConfig


def _make_config() -> TenantsConfig:
    return TenantsConfig(
        tenants=[
            TenantConfig(tenant_id="dad", display_name="爸爸", bindings=[
                TenantChannelBinding(channel="telegram", sender_id="tg-1", label="手机"),
            ]),
            TenantConfig(tenant_id="mom", display_name="妈妈", bindings=[
                TenantChannelBinding(channel="websocket", sender_id="ws-1", label="手表"),
            ]),
        ],
        guest_tenant_enabled=True,
    )


class TestTenantRegistry:
    def test_lookup_by_channel_sender(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        t = reg.lookup("telegram", "tg-1")
        assert t is not None and t.tenant_id == "dad"

    def test_unknown_sender_returns_guest(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        t = reg.lookup("telegram", "stranger")
        assert t is not None and t.tenant_id == "guest"
        assert t.bdi_enabled is False

    def test_register_binding_updates_registry(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        reg.register_binding("telegram", "new-device", "mom")
        t = reg.lookup("telegram", "new-device")
        assert t is not None and t.tenant_id == "mom"

    def test_multi_tenant_isolation(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        assert reg.get("dad").workspace_dir.name == "dad"
        assert reg.get("mom").workspace_dir.name == "mom"
        assert reg.get("dad").workspace_dir != reg.get("mom").workspace_dir

    def test_empty_config_no_tenants_no_guest(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, TenantsConfig(tenants=[], guest_tenant_enabled=False))
        assert len(reg.all()) == 0

    def test_register_binding_unknown_tenant_raises(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        with pytest.raises(KeyError):
            reg.register_binding("telegram", "x", "nobody")


class TestIdentityResolver:
    def test_resolve_known_sender(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        resolver = IdentityResolver(reg)
        t = resolver.resolve("telegram", "tg-1")
        assert t.tenant_id == "dad"

    def test_resolve_falls_back_to_guest(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        resolver = IdentityResolver(reg)
        t = resolver.resolve("discord", "stranger-999")
        assert t.tenant_id == "guest"

    def test_speaker_plugin_called_when_sample_provided(self, tmp_path: Path) -> None:
        reg = TenantRegistry(tmp_path, _make_config())
        mock_plugin = MagicMock()
        mock_recognize = MagicMock()
        mock_recognize.recognize = MagicMock(return_value=None)
        mock_plugin.recognize = mock_recognize.recognize

        resolver = IdentityResolver(reg, speaker_plugin=mock_plugin, speaker_threshold=0.7)
        resolver.resolve("mic_array", "", speaker_sample=b"audio-data")
        mock_recognize.recognize.assert_called_once_with(b"audio-data")

    def test_speaker_plugin_below_threshold_falls_back(self, tmp_path: Path) -> None:
        from OriginAgent.identity.speaker_plugin import RecognitionResult

        reg = TenantRegistry(tmp_path, _make_config())
        mock_plugin = MagicMock()
        mock_plugin.recognize = MagicMock(return_value=RecognitionResult(
            tenant_id="dad", confidence=0.3, method="vocalprint", metadata={}
        ))
        resolver = IdentityResolver(reg, speaker_plugin=mock_plugin, speaker_threshold=0.7)
        t = resolver.resolve("mic_array", "", speaker_sample=b"audio")
        assert t.tenant_id == "guest"  # below threshold → guest
```

Create `tests/identity/test_tenant.py`:

```python
from __future__ import annotations

from pathlib import Path

from OriginAgent.identity.tenant import Tenant


class TestTenant:
    def test_tenant_has_unified_session_key(self) -> None:
        t = Tenant(
            tenant_id="dad", display_name="爸爸",
            unified_session_key="tenant:dad",
            workspace_dir=Path("/tmp/tenants/dad"),
        )
        assert t.unified_session_key == "tenant:dad"
        assert t.tenant_id == "dad"

    def test_active_channels_lists_bound_channels(self) -> None:
        t = Tenant(
            tenant_id="mom", display_name="妈妈",
            unified_session_key="tenant:mom",
            workspace_dir=Path("/tmp/tenants/mom"),
            bindings=[
                {"channel": "telegram", "sender_id": "123", "label": "手机"},
                {"channel": "websocket", "sender_id": "456", "label": "手表"},
            ],
        )
        assert t.active_channels == ["telegram", "websocket"]

    def test_workspace_dir_is_tenant_scoped(self) -> None:
        t = Tenant(
            tenant_id="sister", display_name="姐姐",
            unified_session_key="tenant:sister",
            workspace_dir=Path("/workspace/tenants/sister"),
        )
        assert t.workspace_dir.name == "sister"
```

- [ ] **Step 6: Run tests**

```
.\.venv\Scripts\python.exe -m pytest tests/identity/ -v --basetemp=.pytest_tmp
```

Expected: All identity tests pass.

- [ ] **Step 7: Commit**

```bash
git add OriginAgent/identity/ OriginAgent/config/schema.py tests/identity/
git commit -m "feat: add tenant identity foundation

Tenant model: one person = one unified Jarvis instance.
IdentityResolver: channel+sender_id → tenant_id resolution chain
with SpeakerRecognitionPlugin protocol (reserved interface).

TenantRegistry with guest fallback. Workspace scoped per-tenant.
Single-tenant mode preserved: no config change = current behavior."
```

---

## Phase 2: Tenant-Aware Agent Core (behavior change — session downgrade)

### Task 2: Wire tenant into AgentLoop, per-tenant BDI, session downgrade

**Files:**
- Create: `OriginAgent/agent/tenant_context.py`
- Modify: `OriginAgent/agent/loop.py` — tenant registry init, session key from tenant
- Modify: `OriginAgent/agent/agent_host.py` — per-tenant BDI lifecycle
- Modify: `OriginAgent/session/manager.py` — tenant-aware workspace layout
- Modify: `OriginAgent/channels/base.py` — pass sender_id through
- Create: `tests/integration/test_multi_tenant.py`

**Interfaces:**
- Consumes: `TenantRegistry`, `IdentityResolver` (Task 1), `AgentLoop`, `MessageBus`
- Produces: Tenant-aware message routing, per-tenant BDI, per-tenant workspace

- [ ] **Step 1: Create tenant context (contextvars)**

Create `OriginAgent/agent/tenant_context.py`:

```python
"""Contextvars-based current tenant for the turn pipeline."""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from OriginAgent.identity.tenant import Tenant

_current_tenant: contextvars.ContextVar["Tenant | None"] = contextvars.ContextVar(
    "current_tenant", default=None
)


def set_current_tenant(tenant: Tenant) -> None:
    _current_tenant.set(tenant)


def get_current_tenant() -> Tenant | None:
    return _current_tenant.get(None)


def current_tenant_id() -> str:
    t = _current_tenant.get(None)
    return t.tenant_id if t is not None else "unknown"


def current_tenant_session_key() -> str:
    t = _current_tenant.get(None)
    return t.unified_session_key if t is not None else "unified:default"
```

- [ ] **Step 2: Wire tenant registry into AgentLoop**

In `OriginAgent/agent/loop.py`, in `__init__`:

```python
# After existing gateway config extraction (line ~390):
_tenants_cfg = getattr(gw, "tenants", None) if gw is not None else None
_speaker_cfg = getattr(gw, "speaker_recognition", None) if gw is not None else None

from OriginAgent.identity.tenant import TenantRegistry
from OriginAgent.identity.resolver import IdentityResolver

self._tenant_registry = TenantRegistry(self.workspace, _tenants_cfg)
self._identity_resolver = IdentityResolver.from_config(
    self._tenant_registry, _speaker_cfg
)
```

In `OriginAgent/agent/loop.py`, in the `_dispatch` method (near line ~1480, before
`self._runtime.process_message()`):

```python
async def _dispatch(self, msg: InboundMessage) -> None:
    # ── Tenant resolution (NEW) ──────────────────────────────────
    # Resolve who is talking BEFORE building session context.
    # This runs on every inbound message — the registry lookup is O(1).
    tenant = self._identity_resolver.resolve(
        channel=msg.channel,
        sender_id=msg.sender_id,
    )
    from OriginAgent.agent.tenant_context import set_current_tenant
    set_current_tenant(tenant)

    # Override session_key_override to use the tenant's unified session.
    # This is what "downgrades session from identity boundary to context
    # window manager" — all channels for the same tenant share one session.
    if not msg.session_key_override:
        msg.session_key_override = tenant.unified_session_key

    # ── Existing dispatch logic continues ─────────────────────────
    # ... self._runtime.process_message(...) etc. ...
```

- [ ] **Step 3: Per-tenant BDI in AgentHost (with single-tenant compat)**

In `OriginAgent/agent/agent_host.py`:

```python
# Replace single _bdi_engine with per-tenant engines
self._bdi_engines: dict[str, Any] = {}  # tenant_id → DeliberationEngine

@property
def _bdi_engine(self) -> Any | None:
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
    return self._legacy_bdi_engine  # Single-tenant fallback

def _init_bdi(self) -> None:
    """Initialize BDI: legacy path if no tenants, per-tenant otherwise."""
    if not self._deps.bdi_config:
        self._legacy_bdi_engine = None
        return

    if self._deps.tenants_config and self._deps.tenants_config.tenants:
        # Multi-tenant: engines initialized lazily per-tenant on first message
        self._legacy_bdi_engine = None
    else:
        # Single-tenant: use existing init path (unchanged behavior)
        self._legacy_bdi_engine = self._init_bdi_engine_legacy()

def _init_bdi_engine_legacy(self) -> Any:
    """Existing single-tenant BDI init — preserved verbatim."""
    # ... existing _init_bdi_engine code (lines 374-418) ...

def _init_bdi_engine_for_tenant(self, tenant: Tenant) -> None:
    """Initialize BDI for a single tenant (multi-tenant mode)."""
    if not tenant.bdi_enabled or tenant.tenant_id in self._bdi_engines:
        return

    from OriginAgent.bdi import DesireStore, DeliberationEngine

    workspace = tenant.workspace_dir
    desire_store = DesireStore(workspace)
    engine = DeliberationEngine(
        workspace=workspace,
        store=desire_store,
        provider=self._deps.provider,
        model=self._deps.model or "",
        enabled=True,
        interval_s=getattr(self._deps.bdi_config, "interval_s", 120),
        on_intention=self._on_bdi_intention_for(tenant),
    )
    self._bdi_engines[tenant.tenant_id] = engine

def _on_bdi_intention_for(self, tenant: Tenant):
    """Create an on_intention callback scoped to *tenant*."""
    async def handler(intent):
        if intent.action == "send_message":
            from OriginAgent.bus.events import OutboundMessage
            # Target the tenant's registered channels
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
    return handler
```

- [ ] **Step 4: Tenant-aware workspace layout**

In `OriginAgent/session/manager.py`, update workspace path resolution:

```python
# Workspace layout:
# workspace/
#   sessions/           → per-tenant sessions (legacy compat)
#   tenants/
#     {tenant_id}/
#       memory/          → facts, BDI desires, foresights
#       sessions/        → this tenant's history
#       bdi/
#   shared/
#     memory/            → shared facts, shared devices

def tenant_workspace(workspace: Path, tenant_id: str) -> Path:
    return workspace / "tenants" / tenant_id
```

- [ ] **Step 5: Integration test — multi-tenant messaging**

Create `tests/integration/test_multi_tenant.py`:

```python
"""Multi-tenant integration tests — one agent, multiple family members."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OriginAgent.identity.tenant import Tenant, TenantRegistry
from OriginAgent.identity.resolver import IdentityResolver
from OriginAgent.config.schema import TenantChannelBinding, TenantConfig, TenantsConfig


def _make_tenants_config() -> TenantsConfig:
    return TenantsConfig(
        tenants=[
            TenantConfig(
                tenant_id="dad",
                display_name="爸爸",
                bindings=[TenantChannelBinding(
                    channel="telegram", sender_id="tg-dad-001", label="手机"
                )],
                bdi_enabled=True,
                permissions={"exec": True, "device_control": True},
            ),
            TenantConfig(
                tenant_id="mom",
                display_name="妈妈",
                bindings=[TenantChannelBinding(
                    channel="websocket", sender_id="ws-mom-001", label="手机App"
                )],
                bdi_enabled=True,
                permissions={"exec": False, "device_control": True},
            ),
        ],
        guest_tenant_enabled=True,
    )


class TestTenantResolution:
    def test_dad_on_telegram_resolves_to_dad(self, tmp_path: Path) -> None:
        registry = TenantRegistry(tmp_path, _make_tenants_config())
        resolver = IdentityResolver(registry)
        tenant = resolver.resolve("telegram", "tg-dad-001")
        assert tenant.tenant_id == "dad"
        assert tenant.display_name == "爸爸"
        assert tenant.unified_session_key == "tenant:dad"

    def test_mom_on_websocket_resolves_to_mom(self, tmp_path: Path) -> None:
        registry = TenantRegistry(tmp_path, _make_tenants_config())
        resolver = IdentityResolver(registry)
        tenant = resolver.resolve("websocket", "ws-mom-001")
        assert tenant.tenant_id == "mom"
        assert tenant.unified_session_key == "tenant:mom"

    def test_unknown_sender_gets_guest(self, tmp_path: Path) -> None:
        registry = TenantRegistry(tmp_path, _make_tenants_config())
        resolver = IdentityResolver(registry)
        tenant = resolver.resolve("telegram", "unknown-stranger")
        assert tenant.tenant_id == "guest"
        assert tenant.bdi_enabled is False

    def test_two_tenants_have_different_workspaces(self, tmp_path: Path) -> None:
        registry = TenantRegistry(tmp_path, _make_tenants_config())
        dad = registry.get("dad")
        mom = registry.get("mom")
        assert dad is not None and mom is not None
        assert dad.workspace_dir != mom.workspace_dir
        assert dad.workspace_dir.name == "dad"
        assert mom.workspace_dir.name == "mom"


class TestTenantIsolation:
    def test_same_tenant_two_channels_same_session_key(self, tmp_path: Path) -> None:
        """Dad on Telegram AND WebUI = same unified session."""
        registry = TenantRegistry(tmp_path, _make_tenants_config())
        # Register a second channel for dad
        registry.register_binding("websocket", "ws-dad-webui", "dad")
        dad1 = registry.lookup("telegram", "tg-dad-001")
        dad2 = registry.lookup("websocket", "ws-dad-webui")
        assert dad1 is not None and dad2 is not None
        assert dad1.tenant_id == dad2.tenant_id == "dad"
        assert dad1.unified_session_key == dad2.unified_session_key

    def test_claim_pairing_binds_sender_to_tenant(self, tmp_path: Path) -> None:
        registry = TenantRegistry(tmp_path, _make_tenants_config())
        # Simulate: unknown sender paired, then says "I'm mom"
        tenant = registry.claim_pairing("websocket", "ws-new-device", "mom")
        assert tenant.tenant_id == "mom"
        # Now lookup works
        found = registry.lookup("websocket", "ws-new-device")
        assert found is not None and found.tenant_id == "mom"
```

- [ ] **Step 6: Run regression**

```
.\.venv\Scripts\python.exe -m pytest tests/identity/ tests/integration/test_multi_tenant.py tests/integration/test_minimal_turn_pipeline.py tests/agent/ -k "session" -v --basetemp=.pytest_tmp
```

Expected: Identity tests pass, multi-tenant integration passes, anchor test green, session tests green.

- [ ] **Step 7: Commit**

```bash
git add OriginAgent/agent/tenant_context.py OriginAgent/agent/loop.py \
        OriginAgent/agent/agent_host.py OriginAgent/session/manager.py \
        OriginAgent/channels/base.py tests/integration/test_multi_tenant.py
git commit -m "feat: wire tenant identity into AgentLoop and BDI

Channel+sender_id → tenant resolution before message dispatch.
Per-tenant BDI with scoped intention handlers.
Tenant-aware workspace layout (tenants/{id}/memory/...).
Session downgraded from identity boundary to context window manager."
```

---

## Phase 3: Shared Family Space

### Task 3: SharedSpace — cross-tenant facts, devices, and calendar

**Files:**
- Create: `OriginAgent/identity/shared_space.py`
- Modify: `OriginAgent/bdi/deliberation.py` — pull shared beliefs
- Modify: `OriginAgent/agent/facts.py` — shared fact store
- Create: `tests/identity/test_shared_space.py`

- [ ] **Step 1: Create `SharedSpace`**

```python
"""Shared cross-tenant space: facts, devices, calendar.

Every tenant's BDI _gather_beliefs() pulls from their own facts
AND from shared facts.  Devices (lights, AC, robot) are shared
by default — they belong to the house, not to a person.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SharedSpace:
    workspace: Path

    @property
    def facts_dir(self) -> Path:
        return self.workspace / "shared" / "facts"

    @property
    def device_domains(self) -> list[str]:
        return ["smart_home"]  # Extensible per config

    @property
    def calendar_path(self) -> Path:
        return self.workspace / "shared" / "calendar.jsonl"

    def shared_facts(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Read shared facts visible to all tenants."""
        facts = []
        facts_file = self.facts_dir / "shared_facts.jsonl"
        if facts_file.exists():
            import json
            with open(facts_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    facts.append(json.loads(line))
        return facts[-limit:]

    def add_shared_fact(self, fact: dict[str, Any], *, added_by: str = "") -> None:
        """Add a fact visible to all tenants."""
        from datetime import datetime, timezone
        self.facts_dir.mkdir(parents=True, exist_ok=True)
        import json
        fact["added_by"] = added_by
        fact["added_at"] = datetime.now(timezone.utc).isoformat()
        with open(self.facts_dir / "shared_facts.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(fact, ensure_ascii=False) + "\n")
```

- [ ] **Step 2: Add `shared_space` to DeliberationEngine and wire into beliefs**

In `OriginAgent/bdi/deliberation.py`, add `shared_space` parameter to `__init__`:

```python
def __init__(
    self,
    # ... existing parameters ...
    shared_space: Any = None,  # SharedSpace | None (NEW)
):
    # ... existing init ...
    self._shared_space = shared_space  # (NEW)
```

Then update `_gather_beliefs()`:

```python
def _gather_beliefs(self) -> dict[str, Any]:
    beliefs = {
        "own_facts": self._store.list_facts(limit=50),
        "active_desires": self._store.list_deliberable(),
    }
    # Pull shared space if available
    if self._shared_space is not None:
        beliefs["shared_facts"] = self._shared_space.shared_facts()
        beliefs["shared_devices"] = self._shared_space.device_domains
    return beliefs
```

In AgentHost, pass shared_space when constructing per-tenant engines. In single-tenant mode, pass `None` (backward compat).

- [ ] **Step 3: Commit**

```bash
git add OriginAgent/identity/shared_space.py OriginAgent/bdi/deliberation.py \
        OriginAgent/agent/facts.py tests/identity/test_shared_space.py
git commit -m "feat: add SharedSpace for cross-tenant facts and devices

SharedSpace holds household-level facts, device domains, and calendar
visible to all tenants. BDI _gather_beliefs() pulls from own + shared."
```

---

## Verification Checklist

```bash
# Anchor test
.\.venv\Scripts\python.exe -m pytest tests/integration/test_minimal_turn_pipeline.py -v --basetemp=.pytest_tmp

# Identity tests
.\.venv\Scripts\python.exe -m pytest tests/identity/ -v --basetemp=.pytest_tmp

# Multi-tenant integration
.\.venv\Scripts\python.exe -m pytest tests/integration/test_multi_tenant.py -v --basetemp=.pytest_tmp

# Session regression
.\.venv\Scripts\python.exe -m pytest tests/agent/ -k "session" -v --basetemp=.pytest_tmp

# Full evolution (BDI changes)
.\.venv\Scripts\python.exe -m pytest tests/evolution/ -v --basetemp=.pytest_tmp

# Full agent suite
.\.venv\Scripts\python.exe -m pytest tests/agent/ -v --basetemp=.pytest_tmp
```

Expected: All green.

---

## Example Config (family NAS deployment)

```json
{
  "gateway": {
    "tenants": {
      "tenants": [
        {
          "tenant_id": "dad",
          "display_name": "爸爸",
          "bindings": [
            {"channel": "telegram", "sender_id": "123456789", "label": "手机"},
            {"channel": "websocket", "sender_id": "dad-token-abc", "label": "电脑WebUI"}
          ],
          "bdi_enabled": true,
          "permissions": {"exec": true, "device_control": true}
        },
        {
          "tenant_id": "mom",
          "display_name": "妈妈",
          "bindings": [
            {"channel": "telegram", "sender_id": "987654321", "label": "手机"},
            {"channel": "websocket", "sender_id": "mom-token-xyz", "label": "手表"},
            {"channel": "websocket", "sender_id": "mom-token-phone", "label": "手机App"}
          ],
          "bdi_enabled": true,
          "permissions": {"exec": false, "device_control": true}
        },
        {
          "tenant_id": "sister",
          "display_name": "姐姐",
          "bindings": [
            {"channel": "websocket", "sender_id": "sister-watch-001", "label": "智能手表"}
          ],
          "bdi_enabled": false,
          "permissions": {"exec": false, "device_control": false}
        }
      ],
      "guest_tenant_enabled": true
    },
    "speaker_recognition": {
      "enabled": true,
      "plugin": "vendor.vocalprint_plugin.VocalprintRecognizer",
      "config": {
        "model_path": "/opt/originagent/models/vocalprint.onnx",
        "enrolled_users": {
          "dad": "samples/dad_vp.bin",
          "mom": "samples/mom_vp.bin"
        }
      },
      "confidence_threshold": 0.8
    }
  }
}
```

---

## Follow-up Work (not in this plan)

1. **Speaker recognition reference implementation** — a minimal vocalprint plugin using `speechbrain` or similar
2. **Per-tenant skill/personality** — different system prompts per tenant (kids get simpler language, dad gets technical)
3. **Device-bound channels** — smart watch push vs phone app vs smart speaker — different capabilities per channel
4. **Privacy boundaries** — "mom's calendar events" visible only to mom, "kids' screen time" visible to parents
5. **Tenant migration** — child grows up, tenant moves from "restricted" to "full" permissions
