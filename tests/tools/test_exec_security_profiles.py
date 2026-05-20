from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from OpenHome.agent.tools.shell import ExecTool
from OpenHome.config.schema import ExecToolConfig


class _FakeProcess:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"ok\n", b""


def test_exec_tool_config_defaults_to_secure_profile() -> None:
    config = ExecToolConfig()

    assert config.profile == "secure"
    assert config.allow_unsafe_exec is False
    assert config.shell_syntax_policy == "restricted"


def test_exec_tool_config_rejects_invalid_profile() -> None:
    with pytest.raises(ValidationError):
        ExecToolConfig(profile="unsafe")  # type: ignore[arg-type]


def test_exec_tool_config_rejects_invalid_shell_syntax_policy() -> None:
    with pytest.raises(ValidationError):
        ExecToolConfig(shell_syntax_policy="permissive")  # type: ignore[arg-type]


def test_shell_policy_only_enabled_for_explicit_unsafe_local_dev() -> None:
    secure = ExecTool(security_profile="secure", shell_syntax_policy="shell")
    local_safe = ExecTool(
        security_profile="local_dev",
        allow_unsafe_exec=False,
        shell_syntax_policy="shell",
    )
    local_unsafe = ExecTool(
        security_profile="local_dev",
        allow_unsafe_exec=True,
        shell_syntax_policy="shell",
    )

    assert secure.shell_syntax_policy == "restricted"
    assert local_safe.shell_syntax_policy == "restricted"
    assert local_unsafe.shell_syntax_policy == "shell"


@pytest.mark.asyncio
async def test_secure_workspace_exec_without_sandbox_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = ExecTool(
        working_dir=str(workspace),
        restrict_to_workspace=True,
        sandbox="",
        security_profile="secure",
    )

    result = await tool.execute(command="echo ok", working_dir=str(workspace))

    assert "requires a supported sandbox" in result
    assert "unsafe-exec" not in result


@pytest.mark.asyncio
async def test_local_dev_without_allow_unsafe_exec_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = ExecTool(
        working_dir=str(workspace),
        restrict_to_workspace=True,
        sandbox="",
        security_profile="local_dev",
        allow_unsafe_exec=False,
    )

    result = await tool.execute(command="echo ok", working_dir=str(workspace))

    assert "requires a supported sandbox" in result
    assert "unsafe-exec" not in result


@pytest.mark.asyncio
async def test_local_dev_allow_unsafe_exec_runs_with_marker(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = ExecTool(
        working_dir=str(workspace),
        restrict_to_workspace=True,
        sandbox="",
        timeout=5,
        security_profile="local_dev",
        allow_unsafe_exec=True,
    )

    result = await tool.execute(command="echo ok", working_dir=str(workspace))

    assert "[unsafe-exec profile=local_dev sandbox=none]" in result
    assert "does not provide sandbox isolation" in result
    assert "ok" in result
    assert "echo ok" not in result


@pytest.mark.asyncio
async def test_local_dev_allow_unsafe_exec_can_use_shell_policy(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = ExecTool(
        working_dir=str(workspace),
        restrict_to_workspace=True,
        sandbox="",
        timeout=5,
        security_profile="local_dev",
        allow_unsafe_exec=True,
        shell_syntax_policy="shell",
    )

    result = await tool.execute(command="echo ok && echo injected", working_dir=str(workspace))

    assert "[unsafe-exec profile=local_dev sandbox=none]" in result
    assert "ok" in result
    assert "injected" in result


@pytest.mark.asyncio
async def test_disabled_profile_fails_closed_when_instantiated_directly(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = ExecTool(
        working_dir=str(workspace),
        restrict_to_workspace=True,
        sandbox="",
        security_profile="disabled",
    )

    result = await tool.execute(command="echo ok", working_dir=str(workspace))

    assert "exec profile is disabled" in result
    assert "unsafe-exec" not in result


@pytest.mark.asyncio
async def test_local_dev_uses_sandbox_when_bwrap_available(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = ExecTool(
        working_dir=str(workspace),
        restrict_to_workspace=True,
        sandbox="bwrap",
        timeout=5,
        security_profile="local_dev",
        allow_unsafe_exec=True,
    )

    with patch("OpenHome.agent.tools.shell._IS_WINDOWS", False):
        with patch("OpenHome.agent.tools.shell.shutil.which", lambda name: "/usr/bin/bwrap"):
            with patch.object(tool, "_spawn") as spawn:
                spawn.return_value = _FakeProcess()
                result = await tool.execute(command="echo ok", working_dir=str(workspace))

    command = spawn.call_args.args[0]
    assert "bwrap" in command
    assert "--unshare-net" in command
    assert "unsafe-exec" not in result
    assert "ok" in result


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sandbox fallback is platform-specific")
@pytest.mark.asyncio
async def test_local_dev_windows_unsupported_sandbox_can_fallback_when_allowed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = ExecTool(
        working_dir=str(workspace),
        restrict_to_workspace=True,
        sandbox="bwrap",
        timeout=5,
        security_profile="local_dev",
        allow_unsafe_exec=True,
    )

    result = await tool.execute(command="echo ok", working_dir=str(workspace))

    assert "[unsafe-exec profile=local_dev sandbox=none]" in result
    assert "does not provide sandbox isolation" in result
