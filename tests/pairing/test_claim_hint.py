"""Tests for the ``__pairing_pending__`` claim hint feature (Task 2.4).

Covers:
- ``format_claim_hint`` with claimable tenants and with an empty list
- ``is_hint_shown`` / ``mark_hint_shown`` dedup state and persistence
- ``TenantRegistry.list_claimable_tenants`` filters non-claimable tenants
  (rule 18: never leak non-claimable tenant info to an unbound sender)
- ``AgentLoop._maybe_show_claim_hint`` sends the hint exactly once per
  sender and does not mark dedup when the bus drops the message
- An already-claimed sender no longer resolves to ``__pairing_pending__``,
  so the hint path is never triggered for them
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.loop import AgentLoop
from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.config.loader import get_config_path, set_config_path
from OriginAgent.config.schema import TenantConfig, TenantsConfig
from OriginAgent.identity.tenant import TenantRegistry
from OriginAgent.pairing import (
    format_claim_hint,
    is_hint_shown,
    mark_hint_shown,
)


@pytest.fixture
def isolated_pairing_store(tmp_path: Path):
    """Isolate pairing.json to tmp_path so tests don't clobber real state."""
    previous = get_config_path()
    set_config_path(tmp_path / "config.json")
    yield tmp_path
    set_config_path(previous)


def _make_registry(tmp_path: Path) -> TenantRegistry:
    """Two tenants: ``dad`` claimable, ``mom`` not claimable."""
    cfg = TenantsConfig(
        tenants=[
            TenantConfig(
                tenant_id="dad", display_name="爸爸", claimable_by_pairing=True
            ),
            TenantConfig(
                tenant_id="mom", display_name="妈妈", claimable_by_pairing=False
            ),
        ],
        guest_tenant_enabled=True,
    )
    return TenantRegistry(tmp_path, cfg)


class TestFormatClaimHint:
    def test_format_claim_hint_with_claimable_tenants(self) -> None:
        text = format_claim_hint([("dad", "爸爸"), ("sister", "姐姐")])
        assert "You have been approved!" in text
        assert "/pairing claim dad" in text
        assert "/pairing claim sister" in text
        assert "爸爸" in text
        assert "姐姐" in text
        assert "full access" in text

    def test_format_claim_hint_empty(self) -> None:
        text = format_claim_hint([])
        assert "no claimable identity" in text
        # Empty case must not advertise any /pairing claim command.
        assert "/pairing claim" not in text


class TestHintDedup:
    def test_hint_shown_only_once(self, isolated_pairing_store: Path) -> None:
        assert is_hint_shown("telegram", "alice") is False
        mark_hint_shown("telegram", "alice")
        assert is_hint_shown("telegram", "alice") is True
        # Re-marking is a no-op (rule 12 idempotency).
        mark_hint_shown("telegram", "alice")
        assert is_hint_shown("telegram", "alice") is True
        # Other senders on the same channel are unaffected.
        assert is_hint_shown("telegram", "bob") is False
        # Other channels are unaffected.
        assert is_hint_shown("discord", "alice") is False

    def test_hint_shown_persists_across_loads(
        self, isolated_pairing_store: Path
    ) -> None:
        # is_hint_shown always reads from disk under the lock, so this call
        # already exercises a fresh load (simulates a process restart).
        mark_hint_shown("telegram", "alice")
        assert is_hint_shown("telegram", "alice") is True


class TestListClaimableTenants:
    def test_only_claimable_tenants_returned(self, tmp_path: Path) -> None:
        reg = _make_registry(tmp_path)
        claimable = reg.list_claimable_tenants()
        ids = [tid for tid, _ in claimable]
        assert "dad" in ids
        # Non-claimable tenant "mom" must NOT appear (rule 18: no leak).
        assert "mom" not in ids
        # Guest tenant is never claimable.
        assert "guest" not in ids
        names = dict(claimable)
        assert names["dad"] == "爸爸"


class TestMaybeShowClaimHint:
    """Unit tests for AgentLoop._maybe_show_claim_hint via a stub loop."""

    @staticmethod
    def _make_stub_loop(registry: TenantRegistry) -> AgentLoop:
        # __new__ bypasses __init__ so we don't need the full AgentLoop
        # machinery — only ``_tenant_registry`` and ``bus`` are touched by
        # _maybe_show_claim_hint.
        loop = AgentLoop.__new__(AgentLoop)
        loop._tenant_registry = registry
        loop.bus = MagicMock()
        loop.bus.publish_outbound = AsyncMock(return_value=True)
        return loop

    @staticmethod
    def _make_msg(
        channel: str = "telegram", sender_id: str = "alice"
    ) -> InboundMessage:
        return InboundMessage(
            channel=channel,
            sender_id=sender_id,
            chat_id="chat-1",
            content="hello",
        )

    async def test_hint_sent_on_first_message(
        self, isolated_pairing_store: Path
    ) -> None:
        reg = _make_registry(isolated_pairing_store)
        loop = self._make_stub_loop(reg)
        msg = self._make_msg()

        await loop._maybe_show_claim_hint(msg)

        loop.bus.publish_outbound.assert_awaited_once()
        sent: OutboundMessage = loop.bus.publish_outbound.await_args.args[0]
        assert sent.channel == "telegram"
        assert sent.chat_id == "chat-1"
        # Hint body includes the claimable tenant (dad) but NOT the
        # non-claimable one (mom) — rule 18.
        assert "/pairing claim dad" in sent.content
        assert "mom" not in sent.content
        # Dedup state was recorded.
        assert is_hint_shown("telegram", "alice") is True

    async def test_hint_not_resent_on_second_message(
        self, isolated_pairing_store: Path
    ) -> None:
        reg = _make_registry(isolated_pairing_store)
        loop = self._make_stub_loop(reg)
        msg = self._make_msg()

        await loop._maybe_show_claim_hint(msg)
        # Second call must short-circuit on is_hint_shown without publishing.
        await loop._maybe_show_claim_hint(msg)

        assert loop.bus.publish_outbound.await_count == 1

    async def test_hint_not_marked_when_publish_fails(
        self, isolated_pairing_store: Path
    ) -> None:
        """When the bus drops the hint, dedup must NOT be recorded."""
        reg = _make_registry(isolated_pairing_store)
        loop = self._make_stub_loop(reg)
        loop.bus.publish_outbound = AsyncMock(return_value=False)
        msg = self._make_msg()

        await loop._maybe_show_claim_hint(msg)

        assert loop.bus.publish_outbound.await_count == 1
        # Dedup was NOT recorded → next message will retry.
        assert is_hint_shown("telegram", "alice") is False


class TestAlreadyClaimedSender:
    """An already-claimed sender must NOT resolve to __pairing_pending__."""

    def test_resolver_returns_bound_tenant_after_claim(
        self, isolated_pairing_store: Path
    ) -> None:
        from OriginAgent.identity.resolver import IdentityResolver
        from OriginAgent.pairing import approve_code, generate_code

        reg = _make_registry(isolated_pairing_store)
        resolver = IdentityResolver(reg)

        # Approve the sender so that, without a binding, the resolver would
        # return __pairing_pending__.
        code = generate_code("telegram", "alice")
        approve_code(code)
        pre = resolver.resolve(channel="telegram", sender_id="alice")
        assert pre.tenant_id == "__pairing_pending__"

        # Claim a tenant.
        reg.claim_pairing("telegram", "alice", "dad")
        post = resolver.resolve(channel="telegram", sender_id="alice")
        # The claimed tenant is returned, not __pairing_pending__ — so
        # AgentLoop._dispatch's `if tenant.tenant_id == "__pairing_pending__"`
        # check will skip the hint path entirely for this sender.
        assert post.tenant_id == "dad"
