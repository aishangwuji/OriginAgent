"""Tests for MCP connection lifecycle in AgentLoop."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from OpenHome.agent.loop import AgentLoop
from OpenHome.bus.queue import MessageBus


class _OwnerRecordingStack:
    def __init__(self, owner_task: asyncio.Task | None) -> None:
        self.owner_task = owner_task
        self.close_task: asyncio.Task | None = None
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1
        self.close_task = asyncio.current_task()


def _make_loop(tmp_path, *, mcp_servers: dict | None = None) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    return AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        mcp_servers=mcp_servers or {"test": object()},
    )


@pytest.mark.asyncio
async def test_connect_mcp_retries_when_no_servers_connect(tmp_path, monkeypatch: pytest.MonkeyPatch):
    loop = _make_loop(tmp_path)
    attempts = 0

    async def _fake_connect(_servers, _registry, snapshot_out=None):
        nonlocal attempts
        attempts += 1
        if snapshot_out is not None:
            snapshot_out.clear()
        return {}

    monkeypatch.setattr("OpenHome.agent.tools.mcp.connect_mcp_servers", _fake_connect)

    await loop._connect_mcp()
    await loop._connect_mcp()

    assert attempts == 2
    assert loop._mcp_connected is False
    assert loop._mcp_stacks == {}


@pytest.mark.asyncio
async def test_close_mcp_from_different_task_uses_same_owner_task_cleanup(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    loop = _make_loop(tmp_path)
    seen: dict[str, asyncio.Task | _OwnerRecordingStack | None] = {}

    async def _fake_connect(_servers, _registry, snapshot_out=None):
        stack = _OwnerRecordingStack(asyncio.current_task())
        seen["stack"] = stack
        if snapshot_out is not None:
            snapshot_out.clear()
            snapshot_out["test"] = {"status": "connected"}
        return {"test": stack}

    monkeypatch.setattr("OpenHome.agent.tools.mcp.connect_mcp_servers", _fake_connect)

    connect_caller = asyncio.current_task()
    await loop._connect_mcp()

    async def _close_in_other_task() -> None:
        seen["closer"] = asyncio.current_task()
        await loop.close_mcp()

    await asyncio.create_task(_close_in_other_task())

    stack = seen["stack"]
    assert isinstance(stack, _OwnerRecordingStack)
    assert stack.owner_task is not None
    assert stack.owner_task is not connect_caller
    assert stack.close_task is stack.owner_task
    assert stack.close_task is not seen["closer"]
    assert loop._mcp_runtime_task is None
    assert loop._mcp_stacks == {}
    assert loop._mcp_connected is False
    assert loop._mcp_state == "disconnected"


@pytest.mark.asyncio
async def test_close_mcp_is_idempotent_while_runtime_task_exists(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    loop = _make_loop(tmp_path)
    seen: dict[str, _OwnerRecordingStack] = {}

    async def _fake_connect(_servers, _registry, snapshot_out=None):
        stack = _OwnerRecordingStack(asyncio.current_task())
        seen["stack"] = stack
        if snapshot_out is not None:
            snapshot_out.clear()
            snapshot_out["test"] = {"status": "connected"}
        return {"test": stack}

    monkeypatch.setattr("OpenHome.agent.tools.mcp.connect_mcp_servers", _fake_connect)

    await loop._connect_mcp()
    await asyncio.gather(loop.close_mcp(), loop.close_mcp())

    stack = seen["stack"]
    assert stack.close_calls == 1
    assert loop._mcp_runtime_task is None
    assert loop._mcp_connected is False
    assert loop._mcp_state == "disconnected"


@pytest.mark.asyncio
async def test_failed_mcp_runtime_startup_allows_retry(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    loop = _make_loop(tmp_path)
    attempts = 0

    async def _fake_connect(_servers, _registry, snapshot_out=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        if snapshot_out is not None:
            snapshot_out.clear()
            snapshot_out["test"] = {"status": "skipped"}
        return {}

    monkeypatch.setattr("OpenHome.agent.tools.mcp.connect_mcp_servers", _fake_connect)

    await loop._connect_mcp()
    assert attempts == 1
    assert loop._mcp_runtime_task is None
    assert loop._mcp_state == "disconnected"
    assert loop._mcp_snapshot == {}

    await loop._connect_mcp()
    assert attempts == 2
    assert loop._mcp_runtime_task is None
    assert loop._mcp_state == "disconnected"
    assert loop._mcp_snapshot == {"test": {"status": "skipped"}}
