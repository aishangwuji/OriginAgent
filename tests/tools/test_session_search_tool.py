from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.loop import AgentLoop
from OriginAgent.agent.tools.registry import ToolRegistry
from OriginAgent.agent.tools.session_search import SessionSearchTool
from OriginAgent.bus.queue import MessageBus
from OriginAgent.security.capabilities import CapabilitySnapshot


def _write_session(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"_type": "metadata", "key": "cli:direct"},
        {
            "role": "user",
            "content": "We discussed a session search plan.",
            "timestamp": "2026-05-19T12:00:00",
        },
    ]
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_session_search_tool_executes_and_is_read_only(tmp_path: Path) -> None:
    _write_session(tmp_path / "sessions" / "cli_direct.jsonl")
    tool = SessionSearchTool(tmp_path)

    result = await tool.execute(query="session search", sources=["sessions"])

    assert tool.read_only is True
    assert tool.concurrency_safe is True
    assert result["total_matches"] == 1
    assert result["results"][0]["source"] == "sessions"


def test_agent_loop_registers_session_search(tmp_path: Path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")

    assert "session_search" in loop.tools.tool_names


@pytest.mark.asyncio
async def test_session_search_allowed_without_snapshot(tmp_path: Path) -> None:
    _write_session(tmp_path / "sessions" / "cli_direct.jsonl")
    registry = ToolRegistry()
    registry.register(SessionSearchTool(tmp_path))

    result = await registry.execute("session_search", {"query": "session search", "sources": ["sessions"]})

    assert isinstance(result, dict)
    assert result["total_matches"] == 1


@pytest.mark.asyncio
async def test_session_search_tool_clamps_large_limit(tmp_path: Path) -> None:
    path = tmp_path / "sessions" / "cli_direct.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"_type": "metadata", "key": "cli:direct"}]
    rows.extend(
        {
            "role": "user",
            "content": f"repeat needle {index}",
            "timestamp": "2026-05-19T12:00:00",
        }
        for index in range(60)
    )
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    registry = ToolRegistry()
    registry.register(SessionSearchTool(tmp_path))

    result = await registry.execute(
        "session_search",
        {"query": "repeat needle", "sources": ["sessions"], "limit": 100},
    )

    assert isinstance(result, dict)
    assert result["total_matches"] == 60
    assert len(result["results"]) == 50
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_session_search_denied_when_read_files_capability_disabled(tmp_path: Path) -> None:
    registry = ToolRegistry(capability_snapshot=CapabilitySnapshot.scheduled_default())
    registry.register(SessionSearchTool(tmp_path))

    result = await registry.execute("session_search", {"query": "anything"})

    assert isinstance(result, str)
    assert "cannot read files under the current capability snapshot" in result
