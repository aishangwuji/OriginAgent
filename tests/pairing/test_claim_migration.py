"""Tests for the post-claim BDI lazy-load and workspace migration (Task 2.3).

Covers:
- ``handle_pairing_command``'s ``on_claim_success`` callback wiring
- ``AgentHost.post_claim_init`` migrates the pairing-pending session file
  to the claimed tenant's session key
- ``AgentHost.post_claim_init`` triggers ``_init_bdi_engine_for_tenant``
- Idempotency: if BDI engine already exists, claim does not re-init
  (rule 12)
- End-to-end: claim → callback → migration + BDI lazy-load

Note on workspace migration scope: the synthetic ``__pairing_pending__``
Tenant has ``bdi_enabled=False`` (see ``resolver.py:58-63``), so no BDI /
DesireStore / CronObservationStore data is ever written to
``tenants/_pairing/``. The only persistent pairing-pending state is the
session JSONL file under ``sessions/`` keyed by
``tenant:pairing:{channel}:{sender_id}`` — that is what gets migrated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.agent_host import AgentHost
from OriginAgent.config.loader import get_config_path, set_config_path
from OriginAgent.config.schema import (
    TenantConfig,
    TenantsConfig,
)
from OriginAgent.identity.tenant import Tenant, TenantRegistry
from OriginAgent.pairing import approve_code, generate_code, handle_pairing_command
from OriginAgent.session.manager import SessionManager


@pytest.fixture
def isolated_pairing_store(tmp_path: Path):
    """Isolate pairing.json to tmp_path so tests don't clobber real state."""
    previous = get_config_path()
    set_config_path(tmp_path / "config.json")
    yield tmp_path
    set_config_path(previous)


def _make_registry(tmp_path: Path) -> TenantRegistry:
    """Build a TenantRegistry with one claimable tenant (``dad``)."""
    cfg = TenantsConfig(
        tenants=[
            TenantConfig(
                tenant_id="dad",
                display_name="爸爸",
                claimable_by_pairing=True,
                bdi_enabled=True,
            ),
        ],
        guest_tenant_enabled=True,
    )
    return TenantRegistry(tmp_path, cfg)


def _approve_sender(channel: str, sender_id: str) -> None:
    """Run the full pairing flow to mark *sender_id* as approved on *channel*."""
    code = generate_code(channel, sender_id)
    approve_code(code)


def _make_stub_agent_host(workspace: Path) -> AgentHost:
    """Build a minimal AgentHost stub for ``post_claim_init`` tests.

    Bypasses ``__init__`` (which needs full ``AgentHostDependencies`` and
    constructs a real transcription provider) and sets only the attributes
    touched by ``post_claim_init``: ``_deps.workspace`` and ``_bdi_engines``.
    """
    host = AgentHost.__new__(AgentHost)
    deps = MagicMock()
    deps.workspace = workspace
    host._deps = deps
    host._bdi_engines = {}
    return host


def _pairing_session_path(workspace: Path, channel: str, sender_id: str) -> Path:
    """On-disk path of the ``__pairing_pending__`` session file for a sender."""
    source_key = f"tenant:pairing:{channel}:{sender_id}"
    return workspace / "sessions" / f"{SessionManager.safe_key(source_key)}.jsonl"


def _tenant_session_path(workspace: Path, tenant: Tenant) -> Path:
    """On-disk path of the tenant's session file."""
    target_key = tenant.unified_session_key
    return workspace / "sessions" / f"{SessionManager.safe_key(target_key)}.jsonl"


class TestHandlePairingCommandCallback:
    """Unit tests for the ``on_claim_success`` callback wiring."""

    def test_callback_invoked_with_claimed_tenant(
        self, isolated_pairing_store: Path
    ) -> None:
        reg = _make_registry(isolated_pairing_store)
        _approve_sender("telegram", "alice")

        captured: list[Tenant] = []
        reply = handle_pairing_command(
            "telegram",
            "claim dad",
            sender_id="alice",
            tenant_registry=reg,
            on_claim_success=captured.append,
        )

        assert "Claimed tenant `dad` successfully" in reply
        assert len(captured) == 1
        assert captured[0].tenant_id == "dad"

    def test_no_callback_still_works(self, isolated_pairing_store: Path) -> None:
        """Backward compat: claim succeeds when no callback is supplied."""
        reg = _make_registry(isolated_pairing_store)
        _approve_sender("telegram", "alice")

        reply = handle_pairing_command(
            "telegram",
            "claim dad",
            sender_id="alice",
            tenant_registry=reg,
        )

        assert "Claimed tenant `dad` successfully" in reply
        bound = reg.lookup("telegram", "alice")
        assert bound is not None and bound.tenant_id == "dad"

    def test_callback_failure_does_not_undo_binding(
        self, isolated_pairing_store: Path
    ) -> None:
        """A failing callback must not surface as a claim failure.

        The binding is already persisted by ``claim_pairing`` before the
        callback fires; the user must still see the success reply so they
        know the claim went through. The operator sees the error log.
        """
        reg = _make_registry(isolated_pairing_store)
        _approve_sender("telegram", "alice")

        def boom(_tenant: Tenant) -> None:
            raise RuntimeError("BDI init exploded")

        reply = handle_pairing_command(
            "telegram",
            "claim dad",
            sender_id="alice",
            tenant_registry=reg,
            on_claim_success=boom,
        )

        assert "Claimed tenant `dad` successfully" in reply
        bound = reg.lookup("telegram", "alice")
        assert bound is not None and bound.tenant_id == "dad"

    def test_callback_not_invoked_on_failed_claim(
        self, isolated_pairing_store: Path
    ) -> None:
        """If claim fails, the callback must not fire."""
        reg = _make_registry(isolated_pairing_store)
        _approve_sender("telegram", "alice")

        captured: list[Tenant] = []
        reply = handle_pairing_command(
            "telegram",
            "claim nobody",
            sender_id="alice",
            tenant_registry=reg,
            on_claim_success=captured.append,
        )

        assert "Unknown tenant" in reply
        assert captured == []


class TestPostClaimInitSessionMigration:
    """Tests for ``AgentHost._migrate_pairing_session_file``."""

    def test_migrates_session_file_to_tenant_key(
        self, isolated_pairing_store: Path
    ) -> None:
        """A pairing-pending session file is renamed to the tenant's key."""
        reg = _make_registry(isolated_pairing_store)
        dad = reg.get("dad")
        assert dad is not None

        host = _make_stub_agent_host(isolated_pairing_store)
        host._init_bdi_engine_for_tenant = MagicMock()  # avoid real BDI build

        source_path = _pairing_session_path(isolated_pairing_store, "telegram", "alice")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(
            '{"role":"user","content":"hi pending"}\n', encoding="utf-8"
        )

        host.post_claim_init("telegram", "alice", dad)

        target_path = _tenant_session_path(isolated_pairing_store, dad)
        assert target_path.exists()
        assert (
            target_path.read_text(encoding="utf-8")
            == '{"role":"user","content":"hi pending"}\n'
        )
        # Source is gone — it was renamed, not copied.
        assert not source_path.exists()

    def test_noop_when_source_missing(
        self, isolated_pairing_store: Path
    ) -> None:
        """No source file → migration is a no-op (common case: claim before any turn)."""
        reg = _make_registry(isolated_pairing_store)
        dad = reg.get("dad")
        assert dad is not None

        host = _make_stub_agent_host(isolated_pairing_store)
        host._init_bdi_engine_for_tenant = MagicMock()

        host.post_claim_init("telegram", "alice", dad)

        target_path = _tenant_session_path(isolated_pairing_store, dad)
        assert not target_path.exists()

    def test_does_not_overwrite_existing_target(
        self, isolated_pairing_store: Path
    ) -> None:
        """If target session file already exists, source is left in place.

        Rule 12 (idempotency) + rule 5 (don't destroy existing state): the
        operator must merge manually. We never silently overwrite a tenant's
        existing session history.
        """
        reg = _make_registry(isolated_pairing_store)
        dad = reg.get("dad")
        assert dad is not None

        host = _make_stub_agent_host(isolated_pairing_store)
        host._init_bdi_engine_for_tenant = MagicMock()

        source_path = _pairing_session_path(isolated_pairing_store, "telegram", "alice")
        target_path = _tenant_session_path(isolated_pairing_store, dad)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(
            '{"role":"user","content":"pending"}\n', encoding="utf-8"
        )
        target_path.write_text(
            '{"role":"user","content":"existing"}\n', encoding="utf-8"
        )

        host.post_claim_init("telegram", "alice", dad)

        # Target was NOT overwritten.
        assert (
            target_path.read_text(encoding="utf-8")
            == '{"role":"user","content":"existing"}\n'
        )
        # Source was NOT deleted (left for manual merge).
        assert source_path.exists()


class TestPostClaimInitBdiLazyLoad:
    """Tests for ``AgentHost.post_claim_init``'s BDI lazy-load behaviour."""

    def test_bdi_lazy_load_triggered_on_claim(
        self, isolated_pairing_store: Path
    ) -> None:
        """``post_claim_init`` calls ``_init_bdi_engine_for_tenant``.

        We mock ``_init_bdi_engine_for_tenant`` to avoid constructing a real
        ``DeliberationEngine`` (which needs provider, sqlite_stores, etc.).
        The migration step is a no-op because no session file exists.
        """
        reg = _make_registry(isolated_pairing_store)
        dad = reg.get("dad")
        assert dad is not None

        host = _make_stub_agent_host(isolated_pairing_store)
        host._init_bdi_engine_for_tenant = MagicMock()

        host.post_claim_init("telegram", "alice", dad)

        host._init_bdi_engine_for_tenant.assert_called_once_with(dad)

    def test_bdi_already_initialized_is_noop(
        self, isolated_pairing_store: Path
    ) -> None:
        """If BDI engine already exists for the tenant, claim does not re-init.

        ``_init_bdi_engine_for_tenant``'s idempotency guard short-circuits
        when ``tenant.tenant_id in self._bdi_engines`` (rule 12). We call
        the real (un-mocked) method to verify the guard fires.
        """
        reg = _make_registry(isolated_pairing_store)
        dad = reg.get("dad")
        assert dad is not None
        assert dad.bdi_enabled is True

        host = _make_stub_agent_host(isolated_pairing_store)
        # Pre-populate the engine — simulates a previous init.
        sentinel = object()
        host._bdi_engines["dad"] = sentinel

        # Real (un-mocked) _init_bdi_engine_for_tenant — should short-circuit.
        host._init_bdi_engine_for_tenant(dad)

        # The sentinel is preserved — no re-init happened.
        assert host._bdi_engines["dad"] is sentinel
        assert len(host._bdi_engines) == 1


class TestEndToEndClaimMigration:
    """End-to-end: handle_pairing_command → callback → post_claim_init."""

    def test_claim_triggers_bdi_init_and_session_migration(
        self, isolated_pairing_store: Path
    ) -> None:
        """Wiring test: claim success → callback fires → post_claim_init runs.

        Verifies that ``handle_pairing_command``'s ``on_claim_success`` hook
        correctly invokes ``AgentHost.post_claim_init``, which in turn
        migrates the session file and triggers BDI lazy-load.
        """
        reg = _make_registry(isolated_pairing_store)
        dad = reg.get("dad")
        assert dad is not None
        _approve_sender("telegram", "alice")

        host = _make_stub_agent_host(isolated_pairing_store)
        host._init_bdi_engine_for_tenant = MagicMock()

        source_path = _pairing_session_path(isolated_pairing_store, "telegram", "alice")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(
            '{"role":"user","content":"hi pending"}\n', encoding="utf-8"
        )

        def on_claim_success(tenant: Tenant) -> None:
            host.post_claim_init("telegram", "alice", tenant)

        reply = handle_pairing_command(
            "telegram",
            "claim dad",
            sender_id="alice",
            tenant_registry=reg,
            on_claim_success=on_claim_success,
        )

        assert "Claimed tenant `dad` successfully" in reply
        # BDI init was triggered.
        host._init_bdi_engine_for_tenant.assert_called_once_with(dad)
        # Session file was migrated.
        target_path = _tenant_session_path(isolated_pairing_store, dad)
        assert target_path.exists()
        assert not source_path.exists()
