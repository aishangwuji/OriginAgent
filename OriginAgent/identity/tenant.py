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

    # Pairing
    claimable_by_pairing: bool = False  # may a paired sender claim this tenant?

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
            claimable_by_pairing=tc.claimable_by_pairing,
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

    def list_claimable_tenants(self) -> list[tuple[str, str]]:
        """Return ``(tenant_id, display_name)`` for every tenant with ``claimable_by_pairing=True``.

        Used by the ``__pairing_pending__`` claim-hint flow to show an
        approved-but-unbound sender which identities they may claim. Only
        claimable tenants are returned, so non-claimable tenant info is never
        leaked to an unbound sender (rule 18 security boundary).
        """
        return [
            (t.tenant_id, t.display_name)
            for t in self._tenants.values()
            if getattr(t, "claimable_by_pairing", False)
        ]

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
