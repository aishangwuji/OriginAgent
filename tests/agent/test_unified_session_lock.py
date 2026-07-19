"""Tests for unified per-session lock (Task 5: fix-cron-runtime-and-context-gaps).

Verifies that both ``dispatch_message`` (user path) and ``on_cron_job``
(cron path) acquire the per-session lock via ``agent.sessions.get_lock``
as the single source of truth, ensuring serialization between concurrent
user and cron turns targeting the same session.

Background: cron-triggered turns and user messages to the same session
were racing because ``on_cron_job`` called ``_process_message`` without
any lock, while ``dispatch_message`` held a per-session lock from
``_session_locks``. This caused "response lost and timing chaos"
(spec: Issue 5).
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from OriginAgent.agent.message_dispatcher import MessageDispatcher, MessageDispatcherDeps
from OriginAgent.bus.events import InboundMessage
from OriginAgent.cli.commands import app
from OriginAgent.config.schema import Config
from OriginAgent.cron.types import CronJob, CronPayload, CronSchedule
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
    """Signal that gateway setup completed and we can stop the CLI runner."""


def _setup_cron_env(monkeypatch, tmp_path: Path, *, process_message_mock=None):
    """Set up a minimal gateway env and return ``(cron.on_job, fake_agent, bus)``.

    Mirrors the setup in ``test_on_cron_job_session_extraction.py`` but wires
    a real per-session lock dict on the fake agent so ``on_cron_job``'s
    ``async with agent.sessions.get_lock(...)`` works for real (Task 5).
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
        lambda _payload, _grant_store: MagicMock(),
    )
    monkeypatch.setattr(
        "OriginAgent.utils.evaluator.evaluate_response",
        AsyncMock(return_value=False),
    )

    if process_message_mock is None:
        process_message_mock = AsyncMock(return_value=None)

    class _FakeAgentLoop:
        @classmethod
        def from_config(cls, config, bus=None, **extra):
            return cls(**extra)

        def __init__(self, *args, **kwargs) -> None:
            self.model = "test-model"
            self.provider = _fake_provider()
            self.tools = {}
            self._process_message = process_message_mock
            self._active_tasks: dict[str, list] = {}
            session = SimpleNamespace(
                messages=[
                    {"role": "user", "content": "reminder"},
                    {"role": "assistant", "content": "ok"},
                ]
            )
            self.sessions = MagicMock()
            self.sessions.get_or_create.return_value = session
            # Real per-session lock dict — mirrors SessionManager.get_lock
            # so ``async with agent.sessions.get_lock(key)`` serializes for real.
            _locks: dict[str, asyncio.Lock] = {}
            self.sessions.get_lock = MagicMock(
                side_effect=lambda key: _locks.setdefault(key, asyncio.Lock())
            )
            self.dream = MagicMock()
            self.dream.run = AsyncMock(return_value=None)
            # _host=None keeps _on_cron_bridge's cron_bridge callback skipped.
            self._host = None
            seen["agent"] = self

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
    return cron.on_job, agent, bus


def _make_cron_job(*, job_id="cron-test-1", session_key=None, message="提醒") -> CronJob:
    return CronJob(
        id=job_id,
        name="test-reminder",
        schedule=CronSchedule(kind="every", every_ms=60000),
        payload=CronPayload(
            kind="agent_turn",
            message=message,
            deliver=False,
            session_key=session_key,
        ),
    )


def _make_message(*, content="hello", channel="cli", metadata=None) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id="u",
        chat_id="c",
        content=content,
        metadata=metadata or {},
    )


# --------------------------------------------------------------------------- #
# Test 1: dispatch_message (user path) uses sessions.get_lock                 #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_dispatch_message_uses_sessions_get_lock() -> None:
    """dispatch_message must acquire the per-session lock via
    ``loop.sessions.get_lock(session_key)`` (unified source), not via the
    legacy ``loop._session_locks`` dict.
    """
    session_key = "cli:c"
    lock = asyncio.Lock()
    sessions = SimpleNamespace(
        get_lock=MagicMock(return_value=lock),
        get_or_create=MagicMock(return_value=SimpleNamespace(metadata={}, messages=[])),
        save=MagicMock(),
    )
    loop = SimpleNamespace(
        _effective_session_key=MagicMock(return_value=session_key),
        _concurrency_gate=None,
        _pending_queues={},
        _process_message=AsyncMock(return_value=None),
        _dispatch_command_inline=AsyncMock(),
        _schedule_background=MagicMock(),
        _restore_runtime_checkpoint=MagicMock(return_value=False),
        _clear_pending_user_turn=MagicMock(),
        sessions=sessions,
        bus=SimpleNamespace(publish_outbound=AsyncMock(), publish_inbound=AsyncMock()),
        provider=MagicMock(),
        model="test-model",
    )
    dispatcher = MessageDispatcher(MessageDispatcherDeps(loop=loop))

    await dispatcher.dispatch_message(_make_message())

    sessions.get_lock.assert_called_once_with(session_key)
    # Lock should have been acquired and released (not still held).
    assert not lock.locked()


# --------------------------------------------------------------------------- #
# Test 2: on_cron_job acquires sessions.get_lock for the cron session key     #
# --------------------------------------------------------------------------- #
def test_cron_acquires_sessions_get_lock_for_cron_session_key(
    monkeypatch, tmp_path: Path
) -> None:
    """on_cron_job must acquire ``agent.sessions.get_lock(cron_session_key)``
    before calling ``_process_message``. ``cron_session_key`` defaults to
    ``cron:{job.id}`` when ``payload.session_key`` is not set.
    """
    on_job, agent, _ = _setup_cron_env(monkeypatch, tmp_path)

    job = _make_cron_job(job_id="cron-lock-1")
    asyncio.run(on_job(job))

    agent.sessions.get_lock.assert_called_with("cron:cron-lock-1")


# --------------------------------------------------------------------------- #
# Test 3: cron + user message for same session are serialized                 #
# --------------------------------------------------------------------------- #
def test_cron_and_user_message_for_same_session_serialized(
    monkeypatch, tmp_path: Path
) -> None:
    """When cron and a user turn target the same session_key, they must be
    serialized by the shared per-session lock. Both paths call
    ``agent.sessions.get_lock(session_key)`` and get the same ``asyncio.Lock``,
    so the second turn waits for the first to release.

    Asserts total elapsed time >= 2 * per-turn delay (serial execution).
    """
    sleep_seconds = 0.1

    async def slow_process(_msg, **_kwargs):
        await asyncio.sleep(sleep_seconds)
        return None

    on_job, agent, _ = _setup_cron_env(
        monkeypatch, tmp_path, process_message_mock=AsyncMock(side_effect=slow_process)
    )

    shared_session = "shared-session"
    job = _make_cron_job(job_id="cron-serial-1", session_key=shared_session)

    async def user_path():
        # Simulates dispatch_message's lock acquisition for the same session.
        async with agent.sessions.get_lock(shared_session):
            await asyncio.sleep(sleep_seconds)

    async def main():
        t0 = time.monotonic()
        await asyncio.gather(on_job(job), user_path())
        return time.monotonic() - t0

    elapsed = asyncio.run(main())
    # Serialized: both turns run back-to-back, so >= 2 * sleep_seconds.
    # Allow small scheduling slack on the lower bound.
    assert elapsed >= 2 * sleep_seconds - 0.02, (
        f"Expected serialization (>= {2 * sleep_seconds}s), got {elapsed}s"
    )


# --------------------------------------------------------------------------- #
# Test 4: lock released on exception in cron path                             #
# --------------------------------------------------------------------------- #
def test_lock_released_on_exception_in_cron(monkeypatch, tmp_path: Path) -> None:
    """If ``_process_message`` raises inside ``on_cron_job``, the per-session
    lock must be released (via ``async with``) so subsequent cron turns to
    the same session don't block forever.
    """
    async def boom_process(_msg, **_kwargs):
        raise RuntimeError("cron boom")

    on_job, agent, _ = _setup_cron_env(
        monkeypatch, tmp_path, process_message_mock=AsyncMock(side_effect=boom_process)
    )

    job = _make_cron_job(job_id="cron-except-1")

    # First call should raise RuntimeError (propagated through _on_cron_bridge).
    with pytest.raises(RuntimeError, match="cron boom"):
        asyncio.run(on_job(job))

    # The lock for cron:cron-except-1 should be released now.
    # Verify by acquiring it with a short timeout.
    lock = agent.sessions.get_lock("cron:cron-except-1")

    async def try_acquire():
        try:
            await asyncio.wait_for(lock.acquire(), timeout=0.5)
            return True
        except asyncio.TimeoutError:
            return False

    acquired = asyncio.run(try_acquire())
    assert acquired, "Lock was not released after exception in on_cron_job"
    if acquired:
        lock.release()
