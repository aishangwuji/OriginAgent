"""Tests for MSTeams inbound auth validation guard (rule 18 / P0 security).

Verifies that validate_inbound_auth=False is rejected in production-like
runtime profiles and only permitted in local_dev with a warning.
"""
from __future__ import annotations

import asyncio
import contextlib

import pytest

# Check optional msteams dependencies before running tests
try:
    from OriginAgent.channels import msteams
    MSTEAMS_AVAILABLE = getattr(msteams, "MSTEAMS_AVAILABLE", False)
except ImportError:
    MSTEAMS_AVAILABLE = False

if not MSTEAMS_AVAILABLE:
    pytest.skip(
        "MSTeams dependencies not installed (PyJWT, cryptography). "
        "Run: pip install OriginAgent[msteams]",
        allow_module_level=True,
    )

from OriginAgent.channels.msteams import MSTeamsChannel


class DummyBus:
    def __init__(self):
        self.inbound = []

    async def publish_inbound(self, msg):
        self.inbound.append(msg)


@pytest.fixture
def make_channel(tmp_path, monkeypatch):
    monkeypatch.setattr("OriginAgent.channels.msteams.get_workspace_path", lambda: tmp_path)

    def _make_channel(**config_overrides):
        config = {
            "enabled": True,
            "appId": "app-id",
            "appPassword": "secret",
            "tenantId": "tenant-id",
            "allowFrom": ["*"],
            "port": 0,  # ephemeral port to avoid conflicts
        }
        config.update(config_overrides)
        return MSTeamsChannel(config, DummyBus())

    return _make_channel


async def _start_and_confirm_running(ch: MSTeamsChannel) -> asyncio.Task:
    """Start channel as a background task, confirm it reaches running state."""
    task = asyncio.create_task(ch.start())
    for _ in range(20):
        if ch._running:
            return task
        await asyncio.sleep(0.05)
    raise AssertionError("Channel did not reach running state within 1s")


@pytest.mark.asyncio
async def test_production_profile_rejects_auth_disabled(make_channel):
    """Test 1: 生产 profile + validate_inbound_auth=False → 拒绝启动(抛异常)."""
    ch = make_channel(runtimeProfile="default", validateInboundAuth=False)
    with pytest.raises(RuntimeError, match="validate_inbound_auth"):
        await ch.start()
    assert not ch._running


@pytest.mark.asyncio
async def test_production_profile_allows_auth_enabled(make_channel):
    """Test 2: 生产 profile + validate_inbound_auth=True → 正常启动."""
    ch = make_channel(runtimeProfile="default", validateInboundAuth=True)
    task = await _start_and_confirm_running(ch)
    assert ch._running
    await ch.stop()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3.0)


@pytest.mark.asyncio
async def test_dev_profile_allows_auth_disabled_with_warning(make_channel):
    """Test 3: 开发 profile + validate_inbound_auth=False → 允许启动但 warning."""
    import loguru

    records: list[dict] = []

    def sink(message) -> None:
        record = message.record
        records.append({
            "level": record["level"].name,
            "message": record["message"],
        })

    handler_id = loguru.logger.add(sink, format="{message}", level="DEBUG")
    try:
        ch = make_channel(runtimeProfile="local_dev", validateInboundAuth=False)
        task = await _start_and_confirm_running(ch)
        assert ch._running
        await ch.stop()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3.0)
    finally:
        loguru.logger.remove(handler_id)

    auth_warnings = [
        r for r in records
        if "DISABLED" in r["message"] and r["level"] == "WARNING"
    ]
    assert len(auth_warnings) >= 1, "Expected a warning about disabled auth validation"


@pytest.mark.asyncio
async def test_dev_profile_allows_auth_enabled(make_channel):
    """Test 4: 开发 profile + validate_inbound_auth=True → 正常启动."""
    ch = make_channel(runtimeProfile="local_dev", validateInboundAuth=True)
    task = await _start_and_confirm_running(ch)
    assert ch._running
    await ch.stop()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3.0)
