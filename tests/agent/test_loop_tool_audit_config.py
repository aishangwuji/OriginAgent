from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from OpenHome.agent.action_runtime import ActionExecutionResult
from OpenHome.agent.domain_packs import DomainPackManager
from OpenHome.agent.loop import AgentLoop
from OpenHome.agent.tools.audit import JsonlToolAuditSink
from OpenHome.agent.tools.base import Tool
from OpenHome.bus.queue import MessageBus
from OpenHome.config.schema import Config, DeviceToolsConfig, DomainPacksConfig, ToolAuditConfig
from OpenHome.security.capabilities import CapabilitySnapshot


def _provider():
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return provider


class _FakeTool(Tool):
    def __init__(self, name: str, result: Any = "ok"):
        self._name = name
        self._result = result

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": True}

    async def execute(self, **kwargs: Any) -> Any:
        return self._result


def _config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    return cfg


def _tool_audit_path(tmp_path: Path) -> Path:
    return tmp_path / "memory" / "audit" / "tool_calls.jsonl"


def test_agent_loop_direct_constructor_defaults_to_minimal_tool_audit(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )

    assert loop.tools._audit_config.mode == "minimal"


def test_agent_loop_from_config_defaults_to_minimal_tool_audit(tmp_path: Path) -> None:
    loop = AgentLoop.from_config(_config(tmp_path), bus=MessageBus(), provider=_provider())

    assert loop.tools._audit_config.mode == "minimal"


@pytest.mark.asyncio
async def test_tool_audit_off_does_not_write_tool_calls_jsonl(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.tools.audit = ToolAuditConfig(mode="off")
    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())
    loop.tools.set_capability_snapshot(CapabilitySnapshot.user_turn())
    loop.tools.unregister("exec")
    loop.tools.register(_FakeTool("exec"))

    result = await loop.tools.execute("exec", {"command": "echo ok"})

    assert result == "ok"
    assert not _tool_audit_path(tmp_path).exists()


@pytest.mark.asyncio
async def test_tool_audit_security_writes_full_summary(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.tools.audit = ToolAuditConfig(mode="security")
    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())
    loop.tools.set_capability_snapshot(CapabilitySnapshot.user_turn())
    loop.tools.set_audit_context(actor_id="alice", session_key="cli:direct")
    loop.tools.unregister("exec")
    loop.tools.register(_FakeTool("exec"))

    await loop.tools.execute("exec", {"command": "echo ok"})

    rows = [
        json.loads(line)
        for line in _tool_audit_path(tmp_path).read_text(encoding="utf-8").splitlines()
    ]
    event = rows[0]
    assert event["tool_name"] == "exec"
    assert event["status"] == "success"
    assert event["actor_id_hash"]
    assert event["session_key_hash"]
    assert event["target_kind"] == "command"
    assert event["target_hash"]
    assert event["result_size"] is not None
    assert "echo ok" not in json.dumps(event, ensure_ascii=False)


@pytest.mark.asyncio
async def test_tool_audit_jsonl_hash_chain_survives_concurrent_records(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.tools.audit = ToolAuditConfig(mode="security")
    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())
    loop.tools.set_capability_snapshot(CapabilitySnapshot.user_turn())
    loop.tools.register(_FakeTool("hash_test", result={"ok": True}))

    await asyncio.gather(*(loop.tools.execute("hash_test", {"index": index}) for index in range(20)))

    rows = [
        json.loads(line)
        for line in _tool_audit_path(tmp_path).read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 20
    previous_hash = None
    for row in rows:
        assert row["prev_hash"] == previous_hash
        event_hash = row["event_hash"]
        payload = dict(row)
        payload["event_hash"] = None
        assert JsonlToolAuditSink._hash_event(payload) == event_hash
        previous_hash = event_hash


@pytest.mark.asyncio
async def test_tool_audit_off_does_not_disable_action_device_audit(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.agents.defaults.domain_packs.active = ["smart_home"]
    cfg.tools.audit = ToolAuditConfig(mode="off")
    cfg.tools.device = DeviceToolsConfig(
        enabled=True,
        lighting_enabled=True,
        mode="dry_run",
        backend="fake",
    )
    loop = AgentLoop.from_config(
        cfg,
        bus=MessageBus(),
        provider=_provider(),
    )
    loop._set_tool_context(
        "chat",
        "home",
        actor_id="alice",
        trigger="user_initiated",
        capability_snapshot=CapabilitySnapshot.user_turn(),
    )

    result = await loop.tools.execute(
        "openhome_device_lighting_set_power",
        {"device_id": "lamp", "power": "on"},
    )

    assert result["status"] == "success"
    assert not _tool_audit_path(tmp_path).exists()
    assert (tmp_path / "memory" / "audit" / "action_decisions.jsonl").exists()


@pytest.mark.asyncio
async def test_device_tool_submit_runs_off_event_loop_thread(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.agents.defaults.domain_packs.active = ["smart_home"]
    cfg.tools.device = DeviceToolsConfig(
        enabled=True,
        lighting_enabled=True,
        mode="dry_run",
        backend="fake",
    )
    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())
    loop._set_tool_context(
        "chat",
        "home",
        actor_id="alice",
        trigger="user_initiated",
        capability_snapshot=CapabilitySnapshot.user_turn(),
    )
    tool = loop.tools.get("openhome_device_lighting_set_power")
    assert tool is not None
    submit_data: dict[str, Any] = {}

    def submit_typed(action):
        submit_data["thread_id"] = threading.get_ident()
        submit_data["requested_by"] = action.requested_by
        submit_data["trigger"] = action.trigger
        return ActionExecutionResult(
            status="dry_run",
            action_id="action_thread",
            reason="ok",
            backend_called=True,
        )

    tool._executor.submit_typed = submit_typed
    loop_thread_id = threading.get_ident()

    result = await loop.tools.execute(
        "openhome_device_lighting_set_power",
        {"device_id": "lamp", "power": "on"},
    )

    assert result["status"] == "success"
    assert submit_data["thread_id"] != loop_thread_id
    assert submit_data["requested_by"] == "alice"
    assert submit_data["trigger"] == "user_initiated"
