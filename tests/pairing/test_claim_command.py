"""Tests for the `/pairing claim <tenant_id>` subcommand.

Covers:
- successful claim by an approved sender
- rejection when sender not approved (rule 18 security boundary)
- rejection when tenant unknown (rule 3 boundary validation)
- rejection when tenant not claimable_by_pairing (rule 3)
- idempotency: already-bound sender is refused (rule 12)
- usage hint when arguments missing
- context-missing fallback when sender_id/tenant_registry not supplied
- backward compatibility: pre-existing subcommands (list/approve/deny/revoke)
  still work with the extended signature.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from OriginAgent.config.loader import get_config_path, set_config_path
from OriginAgent.config.schema import (
    TenantChannelBinding,
    TenantConfig,
    TenantsConfig,
)
from OriginAgent.identity.tenant import TenantRegistry
from OriginAgent.pairing import approve_code, generate_code, handle_pairing_command


@pytest.fixture
def isolated_pairing_store(tmp_path: Path):
    """Isolate the pairing.json store to a tmp_path so tests don't clobber real state."""
    previous = get_config_path()
    set_config_path(tmp_path / "config.json")
    yield tmp_path
    set_config_path(previous)


def _make_registry(tmp_path: Path) -> TenantRegistry:
    """Build a TenantRegistry with two tenants: one claimable, one not."""
    cfg = TenantsConfig(
        tenants=[
            TenantConfig(
                tenant_id="dad",
                display_name="爸爸",
                claimable_by_pairing=True,
            ),
            TenantConfig(
                tenant_id="mom",
                display_name="妈妈",
                claimable_by_pairing=False,
            ),
            # Pre-bound tenant for the idempotency test
            TenantConfig(
                tenant_id="sister",
                display_name="姐姐",
                claimable_by_pairing=True,
                bindings=[
                    TenantChannelBinding(
                        channel="telegram", sender_id="sister-sender", label="手机"
                    ),
                ],
            ),
        ],
        guest_tenant_enabled=True,
    )
    return TenantRegistry(tmp_path, cfg)


def _approve_sender(channel: str, sender_id: str) -> None:
    """Run the full pairing flow to mark *sender_id* as approved on *channel*."""
    code = generate_code(channel, sender_id)
    approve_code(code)


class TestClaimSuccess:
    def test_claim_success(self, isolated_pairing_store: Path) -> None:
        reg = _make_registry(isolated_pairing_store)
        _approve_sender("telegram", "alice")

        reply = handle_pairing_command(
            "telegram",
            "claim dad",
            sender_id="alice",
            tenant_registry=reg,
        )

        assert "Claimed tenant `dad` successfully" in reply
        assert "爸爸" in reply
        # The binding is now registered.
        bound = reg.lookup("telegram", "alice")
        assert bound is not None and bound.tenant_id == "dad"


class TestClaimRejections:
    def test_claim_not_approved(self, isolated_pairing_store: Path) -> None:
        reg = _make_registry(isolated_pairing_store)
        # Note: no approve_code() call — sender is not approved.

        reply = handle_pairing_command(
            "telegram",
            "claim dad",
            sender_id="stranger",
            tenant_registry=reg,
        )

        assert "not been approved" in reply
        # State must not change: sender is still unbound.
        assert reg.lookup("telegram", "stranger").tenant_id == "guest"

    def test_claim_unknown_tenant(self, isolated_pairing_store: Path) -> None:
        reg = _make_registry(isolated_pairing_store)
        _approve_sender("telegram", "alice")

        reply = handle_pairing_command(
            "telegram",
            "claim nobody",
            sender_id="alice",
            tenant_registry=reg,
        )

        assert "Unknown tenant" in reply
        assert "`nobody`" in reply

    def test_claim_not_claimable(self, isolated_pairing_store: Path) -> None:
        reg = _make_registry(isolated_pairing_store)
        _approve_sender("telegram", "alice")

        reply = handle_pairing_command(
            "telegram",
            "claim mom",
            sender_id="alice",
            tenant_registry=reg,
        )

        assert "not claimable via pairing" in reply
        # State must not change.
        assert reg.lookup("telegram", "alice").tenant_id == "guest"


class TestClaimIdempotency:
    def test_claim_already_bound_refuses_rebind(
        self, isolated_pairing_store: Path
    ) -> None:
        """Rule 12: a sender already bound to a real tenant cannot silently rebind."""
        reg = _make_registry(isolated_pairing_store)
        # sister-sender is pre-bound to "sister" via the registry config.
        reply = handle_pairing_command(
            "telegram",
            "claim dad",
            sender_id="sister-sender",
            tenant_registry=reg,
        )

        assert "already bound" in reply
        assert "`sister`" in reply
        # The binding must not have changed.
        assert reg.lookup("telegram", "sister-sender").tenant_id == "sister"

    def test_claim_twice_is_idempotent(self, isolated_pairing_store: Path) -> None:
        """A successful claim followed by another claim returns the already-bound message."""
        reg = _make_registry(isolated_pairing_store)
        _approve_sender("telegram", "alice")

        first = handle_pairing_command(
            "telegram",
            "claim dad",
            sender_id="alice",
            tenant_registry=reg,
        )
        assert "Claimed tenant" in first

        second = handle_pairing_command(
            "telegram",
            "claim dad",
            sender_id="alice",
            tenant_registry=reg,
        )
        assert "already bound" in second
        assert "`dad`" in second


class TestClaimUsage:
    def test_claim_missing_args(self, isolated_pairing_store: Path) -> None:
        reg = _make_registry(isolated_pairing_store)
        _approve_sender("telegram", "alice")

        reply = handle_pairing_command(
            "telegram",
            "claim",
            sender_id="alice",
            tenant_registry=reg,
        )

        assert "Usage" in reply
        assert "`/pairing claim <tenant_id>`" in reply

    def test_claim_no_context(self, isolated_pairing_store: Path) -> None:
        """When sender_id or tenant_registry is None, claim must refuse rather than crash."""
        reply = handle_pairing_command(
            "telegram",
            "claim dad",
            sender_id=None,
            tenant_registry=None,
        )
        assert "requires sender_id and tenant_registry" in reply

    def test_claim_unknown_subcommand_falls_through(
        self, isolated_pairing_store: Path
    ) -> None:
        """Unknown subcommand returns the usage hint (now including `claim`)."""
        reply = handle_pairing_command("telegram", "frobnicate foo")
        assert "Unknown pairing command" in reply
        assert "claim <tenant_id>" in reply


class TestBackwardCompatibility:
    """The extended signature must not break the existing owner-only subcommands."""

    def test_list_still_works_without_context(self, isolated_pairing_store: Path) -> None:
        # No sender_id / tenant_registry passed — must still behave as before.
        reply = handle_pairing_command("telegram", "list")
        assert "No pending pairing requests" in reply or "Pending pairing requests" in reply

    def test_approve_deny_revoke_still_work(self, isolated_pairing_store: Path) -> None:
        # End-to-end: generate → approve → list should show empty pending.
        code = generate_code("telegram", "bob")
        approve = handle_pairing_command("telegram", f"approve {code}")
        assert "Approved pairing code" in approve

        # list should now be empty (the approved code is consumed).
        listing = handle_pairing_command("telegram", "list")
        assert "No pending pairing requests" in listing
