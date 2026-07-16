"""Tests for _validate_allow_from repair guidance and exit behavior.

Covers Task A7: when ``allow_from`` is empty and pairing is disabled, the
manager must print clear repair guidance (risk explanation + config example +
pairing alternative) before raising ``SystemExit``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from loguru import logger

from OriginAgent.bus.events import OutboundMessage
from OriginAgent.channels.base import BaseChannel
from OriginAgent.channels.manager import ChannelManager
from OriginAgent.config.schema import ChannelsConfig, PairingConfig


def _fake_security_config(pairing_enabled: bool = False):
    return SimpleNamespace(pairing=PairingConfig(enabled=pairing_enabled))


class _ChannelWithAllowFrom(BaseChannel):
    """Channel with configurable allow_from (mirrors test_channel_plugins helper)."""

    name = "withallow"
    display_name = "With Allow"

    def __init__(self, config, bus, allow_from):
        super().__init__(config, bus)
        if isinstance(self.config, dict):
            self.config["allow_from"] = allow_from
        else:
            self.config.allow_from = allow_from

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, msg: OutboundMessage) -> None:
        pass


def _make_manager(allow_from, *, pairing_enabled=False):
    """Build a ChannelManager bypassing __init__ for isolated testing."""
    fake_config = SimpleNamespace(
        channels=ChannelsConfig(),
        providers=SimpleNamespace(groq=SimpleNamespace(api_key="")),
        security=_fake_security_config(pairing_enabled=pairing_enabled),
    )
    mgr = ChannelManager.__new__(ChannelManager)
    mgr.config = fake_config
    mgr.channels = {"test": _ChannelWithAllowFrom(fake_config, None, allow_from)}
    mgr._dispatch_task = None
    return mgr


@pytest.fixture
def loguru_sink():
    """Capture loguru records for assertion, removed after test."""
    records = []

    def sink(message):
        records.append(message.record)

    handler_id = logger.add(sink, format="{message}", level="DEBUG")
    try:
        yield records
    finally:
        logger.remove(handler_id)


class TestValidateAllowFromRepairGuidance:
    """Verify _validate_allow_from emits clear repair guidance before exit."""

    def test_empty_allow_from_logs_repair_guidance(self, loguru_sink):
        """Test 1: empty allow_from + pairing off → repair guidance with risk, config example, pairing alternative."""
        mgr = _make_manager([], pairing_enabled=False)
        with pytest.raises(SystemExit):
            mgr._validate_allow_from()

        messages = [str(r["message"]) for r in loguru_sink if r["level"].name == "ERROR"]
        combined = " ".join(messages)

        # Risk explanation present (channel cannot receive messages)
        assert "empty allowFrom" in combined or "denies all" in combined, (
            f"risk explanation missing in: {combined!r}"
        )
        # Config example present (allow_from = ["*"] or similar)
        assert "allow_from" in combined, f"config key missing in: {combined!r}"
        assert '["*"]' in combined, f'config example ["*"] missing in: {combined!r}'
        # Pairing alternative present
        assert "pairing" in combined.lower(), f"pairing alternative missing in: {combined!r}"

    def test_empty_allow_from_raises_system_exit(self, loguru_sink):
        """Test 2: empty allow_from + pairing off → SystemExit raised."""
        mgr = _make_manager([], pairing_enabled=False)
        with pytest.raises(SystemExit):
            mgr._validate_allow_from()

    def test_non_empty_allow_from_does_not_raise(self, loguru_sink):
        """Test 3: non-empty allow_from → no exception."""
        mgr = _make_manager(["*"], pairing_enabled=False)
        mgr._validate_allow_from()  # should not raise

    def test_pairing_enabled_does_not_raise_on_empty(self, loguru_sink):
        """Test 4: pairing enabled + empty allow_from → no exception (only warning)."""
        mgr = _make_manager([], pairing_enabled=True)
        mgr._validate_allow_from()  # should not raise
