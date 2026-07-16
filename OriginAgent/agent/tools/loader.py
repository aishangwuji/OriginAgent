"""Tool discovery and registration via package scanning."""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

from loguru import logger

from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.registry import ToolRegistry


class ToolLoader:
    def __init__(self) -> None:
        self._plugins: dict[str, type[Tool]] | None = None

    def _discover_plugins(self) -> dict[str, type[Tool]]:
        """Discover external tool plugins registered via entry_points.

        插件机制预留:当前无官方插件注册 ``originagent.tools`` entry point
        group(全仓库 toml 搜索零命中)。保留此机制作为未来扩展点,不做
        额外维护——新增插件只需在 pyproject.toml 声明该 entry point group
        即可被自动发现,无需修改本文件。
        """
        if self._plugins is not None:
            return self._plugins
        plugins: dict[str, type[Tool]] = {}
        try:
            eps = entry_points(group="originagent.tools")
        except Exception:
            return plugins
        for ep in eps:
            try:
                cls = ep.load()
                if (
                    isinstance(cls, type)
                    and issubclass(cls, Tool)
                    and not getattr(cls, "__abstractmethods__", None)
                    and getattr(cls, "_plugin_discoverable", True)
                ):
                    plugins[ep.name] = cls
            except Exception:
                logger.exception("Failed to load tool plugin: %s", ep.name)
        self._plugins = plugins
        return plugins

    def load(self, ctx: Any, registry: ToolRegistry, *, scope: str = "core") -> list[str]:
        registered: list[str] = []
        core_names = set(getattr(registry, "_tools", {}).keys())
        sources = [(self._discover_plugins().values(), True)]
        for source, is_plugin_source in sources:
            for tool_cls in source:
                cls_label = tool_cls.__name__
                try:
                    if scope not in getattr(tool_cls, "_scopes", {"core"}):
                        continue
                    if not tool_cls.enabled(ctx):
                        continue
                    tool = tool_cls.create(ctx)
                    if registry.has(tool.name):
                        if is_plugin_source and tool.name in core_names:
                            logger.warning(
                                "Plugin {} skipped: conflicts with OriginAgent core tool {}",
                                cls_label, tool.name,
                            )
                            continue
                        if is_plugin_source:
                            logger.warning(
                                "Plugin {} skipped: tool name {} is already registered",
                                cls_label, tool.name,
                            )
                            continue
                        logger.warning(
                            "Tool name collision: {} from {} skipped",
                            tool.name, cls_label,
                        )
                        continue
                    registry.register(tool)
                    registered.append(tool.name)
                except Exception:
                    logger.exception("Failed to register tool: {}", cls_label)
        return registered
