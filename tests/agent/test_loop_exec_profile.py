from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

from OpenHome.agent.loop import AgentLoop
from OpenHome.agent.runner import AgentRunResult, AgentRunSpec
from OpenHome.agent.subagent import SubagentManager, SubagentStatus
from OpenHome.agent.tools.shell import ExecTool
from OpenHome.bus.queue import MessageBus
from OpenHome.config.schema import Config, ExecToolConfig
from OpenHome.security.capabilities import CapabilitySnapshot


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    return provider


def _config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    return cfg


def test_exec_enable_false_does_not_register_exec(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
        exec_config=ExecToolConfig(enable=False),
    )

    assert "exec" not in loop.tools.tool_names


def test_exec_disabled_profile_does_not_register_exec(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
        exec_config=ExecToolConfig(profile="disabled"),
    )

    assert "exec" not in loop.tools.tool_names


def test_exec_secure_profile_registers_when_enabled(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
        exec_config=ExecToolConfig(profile="secure"),
    )

    tool = loop.tools.get("exec")
    assert isinstance(tool, ExecTool)
    assert tool.security_profile == "secure"
    assert tool.allow_unsafe_exec is False


def test_exec_local_dev_profile_registers_when_enabled(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
        exec_config=ExecToolConfig(profile="local_dev", allow_unsafe_exec=True),
    )

    tool = loop.tools.get("exec")
    assert isinstance(tool, ExecTool)
    assert tool.security_profile == "local_dev"
    assert tool.allow_unsafe_exec is True


def test_from_config_preserves_disabled_profile(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.tools.exec = ExecToolConfig(profile="disabled")

    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())

    assert "exec" not in loop.tools.tool_names


def test_from_config_preserves_local_dev_profile(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.tools.exec = ExecToolConfig(profile="local_dev", allow_unsafe_exec=True)

    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())

    tool = loop.tools.get("exec")
    assert isinstance(tool, ExecTool)
    assert tool.security_profile == "local_dev"
    assert tool.allow_unsafe_exec is True


async def test_subagent_exec_tool_receives_profile_when_snapshot_allows_exec(
    tmp_path: Path,
) -> None:
    captured: dict[str, ExecTool | None] = {}
    manager = SubagentManager(
        provider=_provider(),
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=1000,
        exec_config=ExecToolConfig(profile="local_dev", allow_unsafe_exec=True),
        restrict_to_workspace=True,
    )

    async def fake_run(spec: AgentRunSpec) -> AgentRunResult:
        tool = spec.tools.get("exec")
        captured["exec"] = tool if isinstance(tool, ExecTool) else None
        return AgentRunResult(final_content="done", messages=[])

    manager.runner.run = fake_run  # type: ignore[method-assign]
    snapshot = replace(CapabilitySnapshot.user_turn(), source="subagent", trigger="subagent")
    status = SubagentStatus(task_id="task1", label="label", task_description="task", started_at=time.monotonic())

    await manager._run_subagent(
        "task1",
        "task",
        "label",
        {"channel": "cli", "chat_id": "direct", "session_key": "cli:direct"},
        status,
        capability_snapshot=snapshot,
    )

    tool = captured["exec"]
    assert tool is not None
    assert tool.security_profile == "local_dev"
    assert tool.allow_unsafe_exec is True
