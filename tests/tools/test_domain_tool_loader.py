from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from OriginAgent.agent.domain_packs import DomainPackManager
from OriginAgent.agent.tools.domain_loader import DomainToolLoader
from OriginAgent.agent.tools.registry import ToolRegistry
from OriginAgent.config.schema import DomainPacksConfig


def _write_pack(
    workspace: Path,
    *,
    active: bool = True,
    tool_source: str,
    tool_manifest: str,
    pack_id: str = "research",
) -> DomainPackManager:
    pack = workspace / "domain_packs" / pack_id
    tools_dir = pack / "tools"
    tools_dir.mkdir(parents=True)
    (pack / "CAPABILITIES.md").write_text("# Research\n", encoding="utf-8")
    (tools_dir / "search.py").write_text(tool_source, encoding="utf-8")
    (pack / "domain_pack.yaml").write_text(
        f"id: {pack_id}\nname: Research\nversion: 0.1.0\n"
        f"tools:\n{tool_manifest}",
        encoding="utf-8",
    )
    return DomainPackManager(
        workspace,
        config=DomainPacksConfig(active=[pack_id] if active else []),
        builtin_dir=workspace / "empty",
    )


_READ_ONLY_TOOL = """
from OriginAgent.agent.tools.base import Tool

class ResearchSearchTool(Tool):
    name = "research_search"

    @property
    def description(self):
        return "search"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "additionalProperties": False}

    @property
    def read_only(self):
        return True

    async def execute(self, **kwargs):
        return "ok"
"""


_WRITE_TOOL = """
from OriginAgent.agent.tools.base import Tool

class ResearchSearchTool(Tool):
    name = "research_search"

    @property
    def description(self):
        return "search"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(self, **kwargs):
        return "ok"
"""


def test_domain_tool_loader_registers_active_read_only_tool(tmp_path: Path) -> None:
    manager = _write_pack(
        tmp_path,
        tool_source=_READ_ONLY_TOOL,
        tool_manifest=(
            "  - id: research_search\n"
            "    module: tools.search\n"
            "    class: ResearchSearchTool\n"
            "    permissions: []\n"
            "    audit: minimal\n"
        ),
    )
    registry = ToolRegistry()

    registered = DomainToolLoader(manager).load(SimpleNamespace(), registry)

    assert registered == ["research_search"]
    assert registry.has("research_search")
    assert manager.domain_tool_runtime_records("research")[0].status == "registered"


def test_domain_tool_loader_ignores_inactive_pack(tmp_path: Path) -> None:
    manager = _write_pack(
        tmp_path,
        active=False,
        tool_source=_READ_ONLY_TOOL,
        tool_manifest=(
            "  - id: research_search\n"
            "    module: tools.search\n"
            "    class: ResearchSearchTool\n"
            "    permissions: []\n"
        ),
    )
    registry = ToolRegistry()

    assert DomainToolLoader(manager).load(SimpleNamespace(), registry) == []
    assert not registry.has("research_search")
    assert manager.domain_tool_runtime_records("research") == []


def test_domain_tool_loader_skips_conflicts_and_non_read_only_without_permissions(
    tmp_path: Path,
) -> None:
    manager = _write_pack(
        tmp_path,
        tool_source=_WRITE_TOOL,
        tool_manifest=(
            "  - id: research_search\n"
            "    module: tools.search\n"
            "    class: ResearchSearchTool\n"
            "    permissions: []\n"
        ),
    )
    registry = ToolRegistry()

    assert DomainToolLoader(manager).load(SimpleNamespace(), registry) == []
    records = manager.domain_tool_runtime_records("research")
    assert records[0].status == "skipped"
    assert "non-read-only" in records[0].reason

    manager = _write_pack(
        tmp_path / "conflict",
        tool_source=_READ_ONLY_TOOL,
        tool_manifest=(
            "  - id: research_search\n"
            "    module: tools.search\n"
            "    class: ResearchSearchTool\n"
            "    permissions: []\n"
        ),
    )
    registry = ToolRegistry()
    existing = registry.get("research_search")
    if existing is None:
        DomainToolLoader(manager).load(SimpleNamespace(), registry)
    manager = _write_pack(
        tmp_path / "conflict2",
        tool_source=_READ_ONLY_TOOL,
        tool_manifest=(
            "  - id: research_search\n"
            "    module: tools.search\n"
            "    class: ResearchSearchTool\n"
            "    permissions: []\n"
        ),
    )
    assert DomainToolLoader(manager).load(SimpleNamespace(), registry) == []
    records = manager.domain_tool_runtime_records("research")
    assert records[0].status == "skipped"
    assert "already registered" in records[0].reason


def test_domain_tool_loader_records_import_and_class_failures(tmp_path: Path) -> None:
    manager = _write_pack(
        tmp_path,
        tool_source="class NotATool:\n    pass\n",
        tool_manifest=(
            "  - id: research_search\n"
            "    module: tools.search\n"
            "    class: NotATool\n"
            "    permissions: []\n"
        ),
    )
    registry = ToolRegistry()

    assert DomainToolLoader(manager).load(SimpleNamespace(), registry) == []
    records = manager.domain_tool_runtime_records("research")
    assert records[0].status == "skipped"
    assert "is not a Tool" in records[0].reason
