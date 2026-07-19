"""Integration test: on_cron_job passes payload.capability_snapshot through
to ``agent._process_message`` (spec P0-1 / Issue 1).

Before the fix, ``on_cron_job`` always used ``snapshot_for_cron_payload`` which
only consulted ``payload.grant_id`` — the explicit ``payload.capability_snapshot``
dict was silently ignored, and cron-triggered tool calls were always denied
with ``capability_*_denied`` (because ``scheduled_default()`` is all-False).

After the fix, when ``payload.capability_snapshot`` is non-empty,
``on_cron_job`` SHALL resolve the snapshot via
``snapshot_for_trigger("scheduled", payload_snapshot=...)`` and pass the
reconstructed ``CapabilitySnapshot`` to ``_process_message``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from typer.testing import CliRunner

from OriginAgent.cli.commands import app
from OriginAgent.config.schema import Config
from OriginAgent.cron.types import CronJob, CronPayload, CronSchedule
from OriginAgent.providers.factory import ProviderSnapshot
from OriginAgent.security.capabilities import CapabilitySnapshot

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


def test_on_cron_job_passes_payload_capability_snapshot_to_process_message(
    monkeypatch, tmp_path: Path
) -> None:
    """When ``payload.capability_snapshot`` is non-empty, ``on_cron_job`` MUST
    pass a reconstructed ``CapabilitySnapshot`` (with the payload's
    ``can_exec=True``) to ``agent._process_message``.

    This is the core fix for Issue 1: cron jobs with explicit
    ``capability_snapshot`` previously had it silently ignored.
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

    # Mock snapshot_for_cron_payload to return scheduled_default() (baseline
    # before payload.capability_snapshot override). This isolates the test
    # from grant_store complexity and confirms the override path works even
    # when snapshot_for_cron_payload would have returned all-False.
    monkeypatch.setattr(
        "OriginAgent.cli.commands.snapshot_for_cron_payload",
        lambda _payload, _grant_store: CapabilitySnapshot.scheduled_default(),
    )

    monkeypatch.setattr(
        "OriginAgent.utils.evaluator.evaluate_response",
        AsyncMock(return_value=False),
    )

    class _FakeAgentLoop:
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            instance = cls(**extra)
            seen["agent"] = instance
            return instance

        def __init__(self, *args, **kwargs) -> None:
            self.model = "test-model"
            self.provider = _fake_provider()
            self.tools = {}
            self._process_message = AsyncMock(return_value=None)
            self._active_tasks: dict[str, list] = {}
            self.sessions = MagicMock()
            self.sessions.get_or_create.return_value = SimpleNamespace(messages=[])
            self.dream = MagicMock()
            self.dream.run = AsyncMock(return_value=None)
            self._host = MagicMock()

        async def close_mcp(self) -> None:
            return None

        async def run(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class _StopAfterCronSetup:
        def __init__(self, *_args, **_kwargs) -> None:
            raise _StopGatewayError("stop")

    class _FakeCron:
        def __init__(self, _store_path: Path) -> None:
            self.on_job = None
            seen["cron"] = self

    monkeypatch.setattr("OriginAgent.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("OriginAgent.cli.commands.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("OriginAgent.channels.manager.ChannelManager", _StopAfterCronSetup)

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])
    assert isinstance(result.exception, _StopGatewayError)

    cron = seen["cron"]
    agent = seen["agent"]

    # Construct a CronJob with explicit capability_snapshot in payload.
    # session_key="tenant:guest" matches the spec scenario.
    payload_snapshot_dict = {"can_exec": True, "can_read_files": True}
    job = CronJob(
        id="cron-cap-passthrough-1",
        name="test-capability-passthrough",
        schedule=CronSchedule(kind="every", every_ms=60000),
        payload=CronPayload(
            kind="agent_turn",
            message="提醒喝水",
            deliver=False,
            session_key="tenant:guest",
            capability_snapshot=payload_snapshot_dict,
        ),
    )

    asyncio.run(cron.on_job(job))

    # _process_message MUST have been called with a CapabilitySnapshot
    # (not None, not a dict) reconstructed from payload.capability_snapshot.
    assert agent._process_message.called, "Expected _process_message to be called"
    kwargs = agent._process_message.call_args.kwargs
    assert "capability_snapshot" in kwargs, (
        f"capability_snapshot not in _process_message kwargs: {list(kwargs.keys())}"
    )

    passed_snapshot = kwargs["capability_snapshot"]
    assert isinstance(passed_snapshot, CapabilitySnapshot), (
        f"Expected CapabilitySnapshot, got {type(passed_snapshot).__name__}"
    )
    # The reconstructed snapshot MUST reflect the payload's can_exec=True
    # (the core fix — previously this was always False from scheduled_default()).
    assert passed_snapshot.can_exec is True, (
        f"Expected can_exec=True from payload.capability_snapshot, "
        f"got can_exec={passed_snapshot.can_exec}"
    )
    assert passed_snapshot.can_read_files is True, (
        f"Expected can_read_files=True from payload.capability_snapshot, "
        f"got can_read_files={passed_snapshot.can_read_files}"
    )


def test_on_cron_job_uses_scheduled_default_when_payload_capability_snapshot_empty(
    monkeypatch, tmp_path: Path
) -> None:
    """Backward compat: when ``payload.capability_snapshot`` is empty/None,
    ``on_cron_job`` MUST pass ``scheduled_default()`` (all False) to
    ``_process_message`` — identical to pre-fix behavior.
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
    monkeypatch.setattr(
        "OriginAgent.cli.commands.snapshot_for_cron_payload",
        lambda _payload, _grant_store: CapabilitySnapshot.scheduled_default(),
    )
    monkeypatch.setattr(
        "OriginAgent.utils.evaluator.evaluate_response",
        AsyncMock(return_value=False),
    )

    class _FakeAgentLoop:
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            instance = cls(**extra)
            seen["agent"] = instance
            return instance

        def __init__(self, *args, **kwargs) -> None:
            self.model = "test-model"
            self.provider = _fake_provider()
            self.tools = {}
            self._process_message = AsyncMock(return_value=None)
            self._active_tasks: dict[str, list] = {}
            self.sessions = MagicMock()
            self.sessions.get_or_create.return_value = SimpleNamespace(messages=[])
            self.dream = MagicMock()
            self.dream.run = AsyncMock(return_value=None)
            self._host = MagicMock()

        async def close_mcp(self) -> None:
            return None

        async def run(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class _StopAfterCronSetup:
        def __init__(self, *_args, **_kwargs) -> None:
            raise _StopGatewayError("stop")

    class _FakeCron:
        def __init__(self, _store_path: Path) -> None:
            self.on_job = None
            seen["cron"] = self

    monkeypatch.setattr("OriginAgent.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("OriginAgent.cli.commands.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("OriginAgent.channels.manager.ChannelManager", _StopAfterCronSetup)

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])
    assert isinstance(result.exception, _StopGatewayError)

    cron = seen["cron"]
    agent = seen["agent"]

    # payload.capability_snapshot defaults to {} (empty dict) per CronPayload schema
    job = CronJob(
        id="cron-cap-empty-1",
        name="test-empty-capability-snapshot",
        schedule=CronSchedule(kind="every", every_ms=60000),
        payload=CronPayload(
            kind="agent_turn",
            message="提醒",
            deliver=False,
            session_key="tenant:guest",
            # capability_snapshot not set — defaults to {} (empty)
        ),
    )

    asyncio.run(cron.on_job(job))

    assert agent._process_message.called
    kwargs = agent._process_message.call_args.kwargs
    passed_snapshot = kwargs["capability_snapshot"]
    assert isinstance(passed_snapshot, CapabilitySnapshot)
    # Backward compat: all False (scheduled_default)
    assert passed_snapshot.can_exec is False
    assert passed_snapshot.can_read_files is False
