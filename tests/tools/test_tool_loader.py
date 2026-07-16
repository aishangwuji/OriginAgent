from types import SimpleNamespace

from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.loader import ToolLoader
from OriginAgent.agent.tools.registry import ToolRegistry


class _NamedTool(Tool):
    tool_name = "demo"

    @property
    def name(self) -> str:
        return self.tool_name

    @property
    def description(self) -> str:
        return "test tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "ok"


class _CoreConflictPlugin(_NamedTool):
    tool_name = "core_tool"


class _ScopedPlugin(_NamedTool):
    tool_name = "plugin_tool"
    _scopes = {"plugins"}


class _CorePlugin(_NamedTool):
    tool_name = "plugin_tool"


def test_tool_loader_skips_plugin_that_conflicts_with_core_tool() -> None:
    registry = ToolRegistry()
    core = _NamedTool()
    core.tool_name = "core_tool"
    registry.register(core)

    loader = ToolLoader()
    loader._plugins = {"conflict": _CoreConflictPlugin}

    assert loader.load(SimpleNamespace(), registry) == []
    assert registry.get("core_tool") is core


def test_tool_loader_honors_scope_and_registers_plugin() -> None:
    registry = ToolRegistry()
    loader = ToolLoader()
    loader._plugins = {"scoped": _ScopedPlugin, "core": _CorePlugin}

    assert loader.load(SimpleNamespace(), registry, scope="core") == ["plugin_tool"]
    assert registry.has("plugin_tool")
