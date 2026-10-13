"""Tenant model and registry — one person, one Jarvis instance."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from OriginAgent.config.schema import TenantChannelBinding, TenantConfig, TenantsConfig


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
        # Reason: protect runtime mutations from REST API against concurrent
        # reads/lookups; the original "Thread-safe" docstring was aspirational.
        self._lock = threading.RLock()
        # Preserved for to_config() round-trip; not used for lookup fallback
        # (unmatched senders fall back to guest via lookup()).
        self._default_tenant_id: str = ""

        if config:
            self._default_tenant_id = config.default_tenant_id
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
        """Find tenant by channel+sender_id.  Falls back to guest.

        Telegram's _sender_id produces ``"<user_id>|<username>"`` for
        allowlist dedup, but tenant bindings store the stable numeric
        ``"<user_id>"`` only (username can change). Try the exact key
        first; if that misses, strip the ``|username`` suffix and retry
        with the bare user.id — this lets users configure bindings using
        just the numeric Telegram ID without worrying about usernames.
        """
        sid = str(sender_id)
        tid = self._by_channel_sender.get((channel, sid))
        if tid is None and "|" in sid:
            bare = sid.split("|", 1)[0]
            tid = self._by_channel_sender.get((channel, bare))
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

    # ── Runtime tenant management (WebUI admin surface) ─────────────────
    # These methods mutate the in-memory registry. Callers (REST API) are
    # responsible for persisting the new state via save_config() using the
    # TenantsConfig returned by to_config().

    def add_tenant(self, tc: TenantConfig) -> Tenant:
        """Add a tenant at runtime. Raises ValueError if tenant_id exists."""
        with self._lock:
            if tc.tenant_id in self._tenants:
                raise ValueError(f"Tenant already exists: {tc.tenant_id}")
            return self._register_from_config(tc)

    def update_tenant(self, tenant_id: str, tc: TenantConfig) -> Tenant:
        """Replace an existing tenant's config. Raises KeyError if absent.

        The caller passes the target ``tenant_id`` separately so that
        PATCH /api/tenants/{tenant_id} can keep the path id as the source
        of truth even when the body carries a different ``tenant_id``.
        Channel bindings are rebuilt: old (channel,sender) entries are
        removed before the new config is registered, so stale mappings
        do not leak across the update (rule 7 — state audit).
        """
        with self._lock:
            existing = self._tenants.get(tenant_id)
            if existing is None:
                raise KeyError(f"Unknown tenant: {tenant_id}")
            for b in existing.bindings:
                self._by_channel_sender.pop((b["channel"], b["sender_id"]), None)
            del self._tenants[tenant_id]
            new_tc = tc.model_copy(update={"tenant_id": tenant_id})
            return self._register_from_config(new_tc)

    def delete_tenant(self, tenant_id: str) -> bool:
        """Remove a tenant at runtime, cascading to their data.

        Refuses to delete the ``guest`` tenant (rule 18 security boundary).
        For non-guest tenants, cascades:
          - channel bindings (removed from _by_channel_sender)
          - session file (sessions/tenant_<id>.jsonl)
          - workspace directory (workspace/tenants/<id>/)

        The caller is responsible for confirming the destructive cascade
        with the user — the REST API exposes a confirmation dialog.
        """
        import shutil

        with self._lock:
            if tenant_id == "guest":
                raise ValueError("Cannot delete the guest tenant")
            existing = self._tenants.get(tenant_id)
            if existing is None:
                raise KeyError(f"Unknown tenant: {tenant_id}")

            # 1. Drop channel bindings so inbound messages no longer resolve here.
            for b in existing.bindings:
                self._by_channel_sender.pop((b["channel"], b["sender_id"]), None)

            # 2. Forget the tenant from the registry.
            del self._tenants[tenant_id]

        # 3. Cascade-delete persistent data OUTSIDE the lock (filesystem IO).
        #    Session file: sessions/tenant_<id>.jsonl
        #    Workspace dir: workspace/tenants/<id>/
        session_file = self._workspace / "sessions" / f"tenant_{tenant_id}.jsonl"
        if session_file.exists():
            try:
                session_file.unlink()
            except OSError:
                # Best-effort — if unlink fails (locked on Windows), the
                # tenant is already gone from the registry; the orphaned
                # file will be ignored by future session loads.
                pass

        if existing and existing.workspace_dir.exists():
            try:
                shutil.rmtree(existing.workspace_dir)
            except OSError:
                # Same best-effort reasoning as above.
                pass

        return True

    def to_config(self) -> TenantsConfig:
        """Snapshot of the registry as a persistable TenantsConfig.

        Returns a deep copy: callers (REST API) typically merge this into
        a freshly loaded Config and then call save_config(). The snapshot
        captures the in-memory state including any runtime add/update/
        delete mutations, so what gets written to disk matches what the
        running agent actually sees.
        """
        with self._lock:
            tenants = [
                TenantConfig(
                    tenant_id=t.tenant_id,
                    display_name=t.display_name,
                    bindings=[
                        TenantChannelBinding(
                            channel=b["channel"],
                            sender_id=b["sender_id"],
                            label=b.get("label", ""),
                        )
                        for b in t.bindings
                    ],
                    bdi_enabled=t.bdi_enabled,
                    claimable_by_pairing=t.claimable_by_pairing,
                    permissions=dict(t.permissions),
                )
                for t in self._tenants.values()
            ]
            return TenantsConfig(
                tenants=tenants,
                default_tenant_id=self._default_tenant_id,
                guest_tenant_enabled=self._guest_tenant is not None,
            )
