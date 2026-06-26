"""Channel bootstrap adapter and typed runtime descriptors."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from OriginAgent.channels.base import BaseChannel
from OriginAgent.config.schema import Config


@dataclass(frozen=True)
class ChannelDescriptor:
    """Normalized channel descriptor built from the current config."""

    name: str
    display_name: str
    enabled: bool
    legacy_config: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "legacy_config": self.legacy_config,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ChannelRuntimeSettings:
    """Resolved runtime settings applied uniformly to all channels."""

    transcription_provider: str
    transcription_api_key: str
    transcription_api_base: str
    transcription_language: str | None
    pairing_config: Any
    send_progress: bool
    send_tool_hints: bool
    show_reasoning: bool
    init_kwargs: dict[str, Any] = field(default_factory=dict)


class ChannelBootstrapAdapter:
    """Bootstrap contract for channel discovery, validation, and instantiation."""

    def validate(self, *, descriptor: ChannelDescriptor, section: Any, core_config: Config) -> None:
        raise NotImplementedError

    def resolve_runtime_settings(
        self,
        *,
        descriptor: ChannelDescriptor,
        section: Any,
        core_config: Config,
        webui_runtime_introspection: Callable[[], dict | None] | None = None,
    ) -> ChannelRuntimeSettings:
        raise NotImplementedError

    def create_channel(
        self,
        *,
        channel_cls: type[BaseChannel],
        section: Any,
        bus: Any,
        settings: ChannelRuntimeSettings,
    ) -> BaseChannel:
        raise NotImplementedError


class DefaultChannelBootstrapAdapter(ChannelBootstrapAdapter):
    """Compatibility-first bootstrap adapter used by ChannelManager."""

    def __init__(
        self,
        *,
        session_manager: Any | None = None,
        webui_runtime_model_name: Callable[[], str | None] | None = None,
        webui_runtime_introspection: Callable[[], dict | None] | None = None,
        webui_dist_resolver: Callable[[], Path | None] | None = None,
    ) -> None:
        self._session_manager = session_manager
        self._webui_runtime_model_name = webui_runtime_model_name
        self._webui_runtime_introspection = webui_runtime_introspection
        self._webui_dist_resolver = webui_dist_resolver

    def validate(self, *, descriptor: ChannelDescriptor, section: Any, core_config: Config) -> None:
        provider = str(core_config.channels.transcription_provider or "groq").strip().lower()
        if provider not in {"groq", "openai"}:
            raise ValueError(
                f"unsupported transcription provider {provider!r}; expected 'groq' or 'openai'"
            )

    def resolve_runtime_settings(
        self,
        *,
        descriptor: ChannelDescriptor,
        section: Any,
        core_config: Config,
        webui_runtime_introspection: Callable[[], dict | None] | None = None,
    ) -> ChannelRuntimeSettings:
        provider = str(core_config.channels.transcription_provider or "groq").strip().lower()
        if provider == "openai":
            provider_config = core_config.providers.openai
        else:
            provider_config = core_config.providers.groq

        init_kwargs: dict[str, Any] = {}
        if descriptor.name == "websocket":
            if self._session_manager is not None:
                init_kwargs["session_manager"] = self._session_manager
                if self._webui_dist_resolver is not None:
                    static_path = self._webui_dist_resolver()
                    if static_path is not None:
                        init_kwargs["static_dist_path"] = static_path
            if self._webui_runtime_model_name is not None:
                init_kwargs["runtime_model_name"] = self._webui_runtime_model_name
            if self._webui_runtime_introspection is not None:
                init_kwargs["runtime_introspection"] = self._webui_runtime_introspection
            elif webui_runtime_introspection is not None:
                init_kwargs["runtime_introspection"] = webui_runtime_introspection

        return ChannelRuntimeSettings(
            transcription_provider=provider,
            transcription_api_key=str(getattr(provider_config, "api_key", "") or ""),
            transcription_api_base=str(getattr(provider_config, "api_base", "") or ""),
            transcription_language=core_config.channels.transcription_language,
            pairing_config=core_config.security.pairing,
            send_progress=_section_bool(section, "send_progress", core_config.channels.send_progress),
            send_tool_hints=_section_bool(section, "send_tool_hints", core_config.channels.send_tool_hints),
            show_reasoning=_section_bool(section, "show_reasoning", core_config.channels.show_reasoning),
            init_kwargs=init_kwargs,
        )

    def create_channel(
        self,
        *,
        channel_cls: type[BaseChannel],
        section: Any,
        bus: Any,
        settings: ChannelRuntimeSettings,
    ) -> BaseChannel:
        channel = channel_cls(section, bus, **settings.init_kwargs)
        channel.transcription_provider = settings.transcription_provider
        channel.transcription_api_key = settings.transcription_api_key
        channel.transcription_api_base = settings.transcription_api_base
        channel.transcription_language = settings.transcription_language
        channel.pairing_config = settings.pairing_config
        channel.send_progress = settings.send_progress
        channel.send_tool_hints = settings.send_tool_hints
        channel.show_reasoning = settings.show_reasoning
        return channel


def build_channel_descriptor(name: str, channel_cls: type[BaseChannel], section: Any) -> ChannelDescriptor:
    """Return a compatibility-oriented channel descriptor."""

    return ChannelDescriptor(
        name=name,
        display_name=getattr(channel_cls, "display_name", name),
        enabled=_section_enabled(section),
        legacy_config=isinstance(section, dict),
        warnings=[
            "raw dict channel config accepted via compatibility mode"
            if isinstance(section, dict)
            else ""
        ] if isinstance(section, dict) else [],
    )


def _section_enabled(section: Any) -> bool:
    if isinstance(section, dict):
        value = section.get("enabled", False)
        return bool(value) if isinstance(value, bool) else False
    return bool(getattr(section, "enabled", False))


def _section_bool(section: Any, key: str, default: bool) -> bool:
    if isinstance(section, dict):
        aliases = {
            "send_progress": "sendProgress",
            "send_tool_hints": "sendToolHints",
            "show_reasoning": "showReasoning",
        }
        value = section.get(key)
        if value is None:
            alias = aliases.get(key)
            if alias is not None:
                value = section.get(alias)
        return value if isinstance(value, bool) else default
    value = getattr(section, key, None)
    return value if isinstance(value, bool) else default
