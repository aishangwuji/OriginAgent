from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from OpenHome.agent.domain_packs import DomainPackManager
from OpenHome.agent.loop import UNIFIED_SESSION_KEY, AgentLoop
from OpenHome.agent.skills import SkillsLoader
from OpenHome.bus.events import InboundMessage
from OpenHome.bus.queue import MessageBus
from OpenHome.command.builtin import cmd_domain, cmd_mcp, cmd_skill
from OpenHome.command.router import CommandContext
from OpenHome.providers.base import LLMProvider, LLMResponse
from OpenHome.utils.webui_transcript import read_transcript_lines, replay_transcript_to_ui_messages


class _NoChatProvider(LLMProvider):
    async def chat(self, *args, **kwargs) -> LLMResponse:  # type: ignore[override]
        raise AssertionError("slash capability commands must not call the LLM")

    def get_default_model(self) -> str:
        return "test-model"


def _ctx(loop: MagicMock, raw: str) -> CommandContext:
    msg = MagicMock()
    msg.channel = "websocket"
    msg.chat_id = "chat1"
    msg.metadata = {}
    return CommandContext(msg=msg, session=None, key="websocket:chat1", raw=raw, loop=loop)


@pytest.mark.asyncio
async def test_mcp_command_reports_configured_servers() -> None:
    loop = MagicMock()
    loop._connect_mcp = AsyncMock()
    loop._mcp_servers = {"demo": MagicMock(type="stdio", command="demo-mcp", url="")}
    loop._mcp_stacks = {"demo": object()}
    loop._mcp_connected = True
    loop._mcp_connecting = False
    loop._mcp_snapshot = {
        "demo": {
            "status": "connected",
            "transport": "stdio",
            "registered_count": 2,
            "tools": [
                {
                    "name": "search",
                    "wrapped_name": "mcp_demo_search",
                    "description": "Search things",
                    "status": "registered",
                }
            ],
            "resources": [
                {
                    "name": "docs",
                    "wrapped_name": "mcp_demo_resource_docs",
                    "description": "Docs",
                    "status": "registered",
                }
            ],
            "prompts": [],
            "error": "",
        }
    }

    result = await cmd_mcp(_ctx(loop, "/mcp"))

    loop._connect_mcp.assert_awaited_once()
    assert result.content is not None
    assert "## MCP Servers" in result.content
    assert "`demo`" in result.content
    assert "`mcp_demo_search`" in result.content
    assert "`mcp_demo_resource_docs`" in result.content
    assert result.metadata["render_as"] == "text"


@pytest.mark.asyncio
async def test_skill_command_lists_workspace_and_builtin_skills(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    builtin = tmp_path / "builtin"
    (workspace / "skills" / "alpha").mkdir(parents=True)
    (workspace / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Workspace skill.\n---\n",
        encoding="utf-8",
    )
    (builtin / "beta").mkdir(parents=True)
    (builtin / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: Built-in skill.\n---\n",
        encoding="utf-8",
    )

    loop = MagicMock()
    loop.context.skills = SkillsLoader(workspace, builtin_skills_dir=builtin)

    result = await cmd_skill(_ctx(loop, "/skill"))

    assert result.content is not None
    assert "## Skills" in result.content
    assert "`alpha` [workspace]" in result.content
    assert "`beta` [builtin]" in result.content
    assert "Total: 2" in result.content
    assert result.metadata["render_as"] == "text"


@pytest.mark.asyncio
async def test_domain_command_lists_pack_statuses(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    pack = workspace / "domain_packs" / "research"
    pack.mkdir(parents=True)
    (pack / "domain_pack.yaml").write_text(
        "id: research\n"
        "name: Research\n"
        "version: 0.1.0\n"
        "capabilities:\n"
        "  - search_sources\n"
        "skills:\n"
        "  - source-synthesis\n"
        "tools:\n"
        "  - id: research_search\n"
        "    module: tools.search\n"
        "    class: ResearchSearchTool\n"
        "    permissions: []\n",
        encoding="utf-8",
    )
    (pack / "CAPABILITIES.md").write_text("# Research\n", encoding="utf-8")
    (pack / "skills" / "source-synthesis").mkdir(parents=True)
    (pack / "skills" / "source-synthesis" / "SKILL.md").write_text(
        "# Source Synthesis\n",
        encoding="utf-8",
    )
    (pack / "tools").mkdir()
    (pack / "tools" / "search.py").write_text("# placeholder\n", encoding="utf-8")
    invalid = workspace / "domain_packs" / "broken"
    invalid.mkdir(parents=True)

    loop = MagicMock()
    loop.domain_packs = DomainPackManager(workspace, builtin_dir=tmp_path / "empty")

    result = await cmd_domain(_ctx(loop, "/domain"))

    assert result.content is not None
    assert "## Domain Packs" in result.content
    assert "`research` [workspace]" in result.content
    assert "status: available" in result.content
    assert "Skills: declared 1, available 1, skipped 0" in result.content
    assert "`domain:research/source-synthesis`" in result.content
    assert "Tools: declared 1, registered 0, skipped 0" in result.content
    assert "`broken` [workspace]" in result.content
    assert "status: invalid" in result.content
    assert "missing domain_pack.yaml" in result.content
    assert result.metadata["render_as"] == "text"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/mcp", "## MCP Servers"),
        ("/skill", "## Skills"),
        ("/skills", "## Skills"),
        ("/domain", "## Domain Packs"),
        ("/domains", "## Domain Packs"),
    ],
)
async def test_capability_commands_shortcut_full_agent_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: str,
) -> None:
    monkeypatch.setattr("OpenHome.config.paths.get_data_dir", lambda: tmp_path / "data")
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_NoChatProvider(),
        workspace=tmp_path,
        model="test-model",
    )
    msg = InboundMessage(
        channel="websocket",
        sender_id="webui",
        chat_id="chat1",
        content=raw,
        metadata={"webui": True},
    )

    result = await loop._process_message(msg)

    assert result is not None
    assert expected in result.content
    assert result.metadata["render_as"] == "text"
    session = loop.sessions.get_or_create("websocket:chat1")
    assert [m["role"] for m in session.messages] == ["user", "assistant"]
    assert all(m.get("_command") is True for m in session.messages)
    assert session.get_history(max_messages=0) == []
    lines = read_transcript_lines("websocket:chat1")
    assert lines == [
        {"event": "message", "chat_id": "chat1", "text": result.content},
    ]
    messages = replay_transcript_to_ui_messages([
        {"event": "user", "chat_id": "chat1", "text": raw},
        *lines,
        {"event": "turn_end", "chat_id": "chat1"},
    ])
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert expected in messages[1]["content"]


@pytest.mark.asyncio
async def test_priority_command_inline_persists_and_records_webui_transcript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("OpenHome.config.paths.get_data_dir", lambda: tmp_path / "data")
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_NoChatProvider(),
        workspace=tmp_path,
        model="test-model",
    )
    msg = InboundMessage(
        channel="websocket",
        sender_id="webui",
        chat_id="chat1",
        content="/status",
        metadata={"webui": True},
    )

    await loop._dispatch_command_inline(
        msg,
        "websocket:chat1",
        "/status",
        loop.commands.dispatch_priority,
    )

    outbound = await loop.bus.consume_outbound()
    turn_end = await loop.bus.consume_outbound()
    assert "OpenHome v" in outbound.content
    assert outbound.metadata["_webui_transcript_recorded"] is True
    assert turn_end.content == ""
    assert turn_end.metadata["_turn_end"] is True
    session = loop.sessions.get_or_create("websocket:chat1")
    assert [m["role"] for m in session.messages] == ["user", "assistant"]
    assert all(m.get("_command") is True for m in session.messages)
    assert session.get_history(max_messages=0) == []
    lines = read_transcript_lines("websocket:chat1")
    assert lines == [
        {"event": "message", "chat_id": "chat1", "text": outbound.content},
    ]


@pytest.mark.asyncio
async def test_priority_stop_uses_effective_session_key_in_unified_mode(
    tmp_path: Path,
) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_NoChatProvider(),
        workspace=tmp_path,
        model="test-model",
        unified_session=True,
    )

    async def long_running() -> None:
        await asyncio.sleep(10)

    task = asyncio.create_task(long_running())
    loop._active_tasks[UNIFIED_SESSION_KEY] = [task]
    msg = InboundMessage(
        channel="websocket",
        sender_id="webui",
        chat_id="chat1",
        content="/stop",
        metadata={"webui": True},
    )

    await loop._dispatch_command_inline(
        msg,
        loop._effective_session_key(msg),
        "/stop",
        loop.commands.dispatch_priority,
    )

    outbound = await loop.bus.consume_outbound()
    await loop.bus.consume_outbound()
    assert task.cancelled() or task.done()
    assert "Stopped 1 task" in outbound.content
