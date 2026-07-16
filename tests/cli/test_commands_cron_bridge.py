"""Tests for Cron-BDI bridge callback error handling in _run_gateway.

Exercises the real ``_on_cron_bridge`` closure (commands.py L872-890) by running the
gateway command until ``cron.on_job`` is wired, then invoking it directly. This avoids
duplicating the closure body and tests the actual code path.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from OriginAgent.cli.commands import app, logger
from OriginAgent.config.schema import Config
from OriginAgent.cron.types import CronJob
from OriginAgent.providers.factory import ProviderSnapshot

runner = CliRunner()


def _fake_provider():
    p = MagicMock()
    p.generation.max_tokens = 4096
    return p


def _test_provider_snapshot(provider: object, config: Config) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider=provider,
        model=config.agents.defaults.model,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        signature=("test",),
    )


class _StopGatewayError(RuntimeError):
    pass


def test_cron_bridge_callback_failure_logs_warning(monkeypatch, tmp_path: Path) -> None:
    """When cron_bridge.on_job_completed raises, a warning is logged and the main task
    result is preserved (not affected by the callback exception).

    Locks the expected behavior for the TD-2026-010 fix at commands.py L885-890:
    a swallowed exception in the finally-block callback must still produce a
    warning-level log line (rule 1 — full-chain tracing), and must not propagate
    to the caller (the main task result is already returned/raised via the try block).
    """
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    seen: dict[str, object] = {}

    monkeypatch.setattr("OriginAgent.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("OriginAgent.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("OriginAgent.config.loader.resolve_config_env_vars", lambda c: c)
    monkeypatch.setattr("OriginAgent.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("OriginAgent.providers.factory.make_provider", lambda _config: _fake_provider())
    monkeypatch.setattr(
        "OriginAgent.providers.factory.build_provider_snapshot",
        lambda _config: _test_provider_snapshot(_fake_provider(), _config),
    )
    monkeypatch.setattr(
        "OriginAgent.providers.factory.load_provider_snapshot",
        lambda _config_path=None: _test_provider_snapshot(_fake_provider(), config),
    )
    monkeypatch.setattr("OriginAgent.bus.queue.MessageBus", lambda: bus)
    monkeypatch.setattr("OriginAgent.session.manager.SessionManager", lambda _workspace: object())

    class _FakeCron:
        def __init__(self, _store_path: Path) -> None:
            self.on_job = None
            seen["cron"] = self

    class _FakeAgentLoop:
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            return cls(**extra)

        def __init__(self, *args, **kwargs) -> None:
            self.model = "test-model"
            self.provider = object()
            self.tools = {}
            # dream path: agent.dream.run() returns None
            self.dream = MagicMock()
            self.dream.run = AsyncMock(return_value=None)
            # cron bridge whose callback raises — the scenario under test
            self._host = MagicMock()
            bridge = AsyncMock()
            bridge.on_job_completed = AsyncMock(side_effect=RuntimeError("bridge failed"))
            self._host._cron_bridge = bridge

        async def close_mcp(self) -> None:
            return None

        async def run(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class _StopAfterCronSetup:
        def __init__(self, *_args, **_kwargs) -> None:
            raise _StopGatewayError("stop")

    monkeypatch.setattr("OriginAgent.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("OriginAgent.cli.commands.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("OriginAgent.channels.manager.ChannelManager", _StopAfterCronSetup)

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])
    assert isinstance(result.exception, _StopGatewayError)

    cron = seen["cron"]
    assert cron.on_job is not None

    # Use the dream path so on_cron_job returns None without needing
    # capability_snapshot / evaluate_response mocks.
    job = CronJob(id="cron-bridge-1", name="dream")

    records: list[str] = []
    handler_id = logger.add(lambda m: records.append(str(m)), level="WARNING")
    try:
        response = asyncio.run(cron.on_job(job))
    finally:
        logger.remove(handler_id)

    # 主任务正常返回(dream 路径返回 None),不被回调异常影响
    assert response is None
    # 应有 warning 日志包含 "Cron-BDI bridge"
    assert any("Cron-BDI bridge" in r for r in records)
