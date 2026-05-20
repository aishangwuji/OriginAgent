from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from OpenHome.agent.loop import AgentLoop
from OpenHome.bus.queue import MessageBus
from OpenHome.config.schema import Config, DomainPacksConfig, ToolAuditConfig

RUNTIME_TOOL_NAMES = {
    "openhome_runtime_status",
    "openhome_tool_audit_summary",
    "openhome_cron_summary",
    "openhome_confirmation_summary",
}


def _provider():
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    return provider


def _config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    return cfg


def test_agent_loop_registers_runtime_explain_tools_by_default(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )

    assert RUNTIME_TOOL_NAMES.issubset(set(loop.tools.tool_names))
    assert not any(name in loop.tools._audit_config.security_tools for name in RUNTIME_TOOL_NAMES)


@pytest.mark.asyncio
async def test_agent_loop_registers_active_domain_tools_and_reports_runtime_status(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "domain_packs" / "research"
    tools_dir = pack / "tools"
    tools_dir.mkdir(parents=True)
    (pack / "CAPABILITIES.md").write_text("# Research\n", encoding="utf-8")
    (pack / "domain_pack.yaml").write_text(
        "id: research\n"
        "name: Research\n"
        "version: 0.1.0\n"
        "tools:\n"
        "  - id: research_search\n"
        "    module: tools.search\n"
        "    class: ResearchSearchTool\n"
        "    permissions: []\n",
        encoding="utf-8",
    )
    (tools_dir / "search.py").write_text(
        "from OpenHome.agent.tools.base import Tool\n\n"
        "class ResearchSearchTool(Tool):\n"
        "    name = 'research_search'\n"
        "    @property\n"
        "    def description(self):\n"
        "        return 'search'\n"
        "    @property\n"
        "    def parameters(self):\n"
        "        return {'type': 'object', 'properties': {}, 'additionalProperties': False}\n"
        "    @property\n"
        "    def read_only(self):\n"
        "        return True\n"
        "    async def execute(self, **kwargs):\n"
        "        return 'ok'\n",
        encoding="utf-8",
    )

    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
        domain_packs_config=DomainPacksConfig(active=["research"]),
    )

    assert loop.tools.has("research_search")
    counts = loop.domain_packs.domain_tool_runtime_counts()
    assert counts["registered"] == 1
    assert counts["skipped"] == 0
    runtime_status = await loop.tools.execute("openhome_runtime_status", {})
    assert runtime_status["registered_domain_tools_count"] == 1
    assert runtime_status["active_domain_pack_ids"] == ["research"]
    assert runtime_status["self_model"]["domains"]["stats"]["active_domain_pack_count"] == 1


@pytest.mark.asyncio
async def test_runtime_status_reports_confirmation_store_available(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )

    result = await loop.tools.execute("openhome_runtime_status", {})

    assert result["confirmation_available"] is True
    assert result["self_model"]["runtime"]["confirmation_available"] is True


@pytest.mark.parametrize("mode", ["off", "minimal", "security"])
@pytest.mark.asyncio
async def test_runtime_explain_tools_do_not_require_capability_snapshot(
    tmp_path: Path,
    mode: str,
) -> None:
    cfg = _config(tmp_path)
    cfg.tools.audit = ToolAuditConfig(mode=mode)  # type: ignore[arg-type]
    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())

    for name in RUNTIME_TOOL_NAMES:
        result = await loop.tools.execute(name, {})
        assert not isinstance(result, str) or not result.startswith("Error"), name


@pytest.mark.asyncio
async def test_runtime_status_reflects_direct_constructor_audit_mode(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
        tool_audit_config=ToolAuditConfig(mode="security"),
    )

    result = await loop.tools.execute("openhome_runtime_status", {})

    assert result["audit_mode"] == "security"
    assert result["workspace_name"] == tmp_path.name
    assert result["self_model"]["identity"]["audit_mode"] == "security"


@pytest.mark.asyncio
async def test_runtime_status_reflects_from_config_audit_mode(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.tools.audit = ToolAuditConfig(mode="off")
    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=_provider())

    runtime_status = await loop.tools.execute("openhome_runtime_status", {})
    audit_summary = await loop.tools.execute("openhome_tool_audit_summary", {})

    assert runtime_status["audit_mode"] == "off"
    assert audit_summary["audit_mode"] == "off"
    assert audit_summary["enabled"] is False
    assert runtime_status["self_model"]["identity"]["audit_mode"] == "off"
