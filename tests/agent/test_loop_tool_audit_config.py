from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from OpenHome.agent.loop import AgentLoop
from OpenHome.agent.tools.base import Tool
from OpenHome.bus.queue import MessageBus
from OpenHome.config.schema import Config, DeviceToolsConfig, ToolAuditConfig
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
async def test_tool_audit_off_does_not_disable_action_device_audit(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
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
