from types import SimpleNamespace

import pytest

from OriginAgent.bus.events import OutboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.channels.base import BaseChannel
from OriginAgent.config.loader import get_config_path, set_config_path
from OriginAgent.config.schema import PairingConfig
from OriginAgent.pairing import approve_code


class _DummyChannel(BaseChannel):
    name = "dummy"

    def __init__(self, config, bus: MessageBus):
        super().__init__(config, bus)
        self.sent: list[OutboundMessage] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, msg: OutboundMessage) -> None:
        self.sent.append(msg)
        return None


@pytest.fixture
def isolated_config(tmp_path):
    previous = get_config_path()
    set_config_path(tmp_path / "config.json")
    yield tmp_path
    set_config_path(previous)


def test_is_allowed_requires_exact_match() -> None:
    channel = _DummyChannel(SimpleNamespace(allow_from=["allow@email.com"]), MessageBus())

    assert channel.is_allowed("allow@email.com") is True
    assert channel.is_allowed("attacker|allow@email.com") is False


def test_is_allowed_supports_dict_allow_from_alias() -> None:
    channel = _DummyChannel({"allowFrom": ["alice"]}, MessageBus())

    assert channel.is_allowed("alice") is True


def test_is_allowed_denies_empty_dict_allow_from() -> None:
    channel = _DummyChannel({"allow_from": []}, MessageBus())

    assert channel.is_allowed("alice") is False


async def test_pairing_disabled_keeps_empty_allow_from_denied(isolated_config) -> None:
    bus = MessageBus()
    channel = _DummyChannel({"allow_from": []}, bus)
    channel.pairing_config = PairingConfig(enabled=False)

    await channel._handle_message("alice", "chat", "hello", is_dm=True)

    assert channel.sent == []
    assert bus.inbound.qsize() == 0


async def test_pairing_enabled_dm_sends_code_and_approves(isolated_config) -> None:
    bus = MessageBus()
    channel = _DummyChannel({"allow_from": []}, bus)
    channel.pairing_config = PairingConfig(enabled=True, ttlSeconds=600)

    await channel._handle_message("alice", "chat", "hello", is_dm=True)

    assert len(channel.sent) == 1
    code = channel.sent[0].metadata["_pairing_code"]
    assert approve_code(code) == ("dummy", "alice")
    assert channel.is_allowed("alice") is True
