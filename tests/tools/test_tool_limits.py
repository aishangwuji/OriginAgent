from __future__ import annotations

import pytest
from unittest.mock import AsyncMock
from unittest.mock import patch

from OpenHome.agent.tools.filesystem import ReadFileTool
from OpenHome.agent.tools.limits import ToolLimits
from OpenHome.agent.tools.shell import ExecTool
from OpenHome.agent.tools.web import WebFetchTool


@pytest.mark.asyncio
async def test_exec_output_limit_is_injectable(tmp_path):
    tool = ExecTool(
        working_dir=str(tmp_path),
        restrict_to_workspace=True,
        sandbox="bwrap",
        limits=ToolLimits(exec_max_output_chars=100),
    )
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (("x" * 300).encode(), b"")
    mock_proc.returncode = 0

    with (
        patch("OpenHome.agent.tools.shell._IS_WINDOWS", False),
        patch("OpenHome.agent.tools.shell.shutil.which", lambda name: "/usr/bin/bwrap"),
        patch.object(tool, "_spawn", return_value=mock_proc),
    ):
        result = await tool.execute("python -c \"print('x' * 300)\"")

    assert "chars truncated" in result
    assert len(result) < 180


def test_exec_timeout_schema_uses_injected_limit(tmp_path):
    tool = ExecTool(
        working_dir=str(tmp_path),
        limits=ToolLimits(exec_max_timeout_seconds=7),
    )

    assert tool.parameters["properties"]["timeout"]["maximum"] == 7


@pytest.mark.asyncio
async def test_read_file_max_chars_is_injectable(tmp_path):
    path = tmp_path / "long.txt"
    path.write_text("\n".join(f"line-{i}" for i in range(50)), encoding="utf-8")
    tool = ReadFileTool(workspace=tmp_path, allowed_dir=tmp_path, limits=ToolLimits(read_file_max_chars=20))

    result = await tool.execute("long.txt", limit=50)

    assert "Showing lines 1-" in result
    assert len(result) < 140


def test_web_fetch_schema_uses_snake_case_and_injected_default():
    tool = WebFetchTool(limits=ToolLimits(web_fetch_max_chars=1234))
    props = tool.parameters["properties"]

    assert "extract_mode" in props
    assert "max_chars" in props
    assert "extractMode" not in props
    assert "maxChars" not in props
    assert props["max_chars"]["description"].endswith("(default 1,234)")
