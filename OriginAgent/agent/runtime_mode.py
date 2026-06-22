"""Read-only runtime mode summary helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from OriginAgent.config.schema import Config


@dataclass(frozen=True)
class RuntimeModeSummary:
    """Normalized runtime-mode read model for CLI, WebUI, and tools."""

    mode: str
    enabled_capabilities: list[str]
    controls: dict[str, Any]
    memory: dict[str, Any]
    perception: dict[str, Any]
    automation: dict[str, Any]
    voice: dict[str, Any]
    channels: list[dict[str, Any]]
    providers: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_runtime_mode_summary(
    *,
    config: Config | None = None,
    loop: Any | None = None,
    enabled_channels: Iterable[str] | None = None,
    model: str | None = None,
    provider_name: str | None = None,
) -> RuntimeModeSummary:
    """Build a stable runtime-mode snapshot from config and/or loop state."""

    defaults = getattr(getattr(config, "agents", None), "defaults", None)
    tools = getattr(config, "tools", None)
    channels_config = getattr(config, "channels", None)
    runtime_cfg = getattr(config, "runtime", None)
    providers = getattr(config, "providers", None)
    gateway = getattr(config, "gateway", None)

    active_intents_enabled = _bool_from(
        defaults,
        "allow_agent_initiated_messages",
        fallback=bool(
            getattr(getattr(loop, "_active_intent_config", None), "enabled", False)
        ),
    )
    backend_cognition_enabled = _bool_from(
        defaults,
        "enable_backend_cognition",
        fallback=bool(getattr(loop, "_cognitive_loop_enabled", False)),
    )

    working_memory_enabled = bool(getattr(loop, "working_memory", None) is not None)
    nearline_enabled = _bool_from(
        getattr(defaults, "nearline_memory", None),
        "enabled",
        fallback=_bool_from(getattr(loop, "_nearline_memory_config", None), "enabled"),
    )
    dream_enabled = getattr(defaults, "dream", None) is not None

    local_awareness = getattr(tools, "local_awareness", None)
    local_audio = getattr(local_awareness, "audio", None)
    device_tools = getattr(tools, "device", None)
    learning = getattr(defaults, "learning", None)

    resolved_model = model or _string_from(loop, "model") or (
        config.resolve_preset().model if config is not None else None
    )
    resolved_provider = provider_name or (
        config.get_provider_name(resolved_model) if config is not None and resolved_model else None
    )

    transcription_provider = (
        _string_from(local_audio, "transcription_provider")
        or _string_from(channels_config, "transcription_provider")
        or "groq"
    )
    tts_provider = "volcengine" if _bool_from(local_audio, "tts_enabled") or _bool_from(local_audio, "output_enabled") else None

    voice = {
        "input_enabled": _bool_from(local_audio, "input_enabled"),
        "output_enabled": _bool_from(local_audio, "output_enabled"),
        "transcription_enabled": _bool_from(local_audio, "transcription_enabled"),
        "tts_enabled": _bool_from(local_audio, "tts_enabled"),
        "transcription_provider": transcription_provider,
        "tts_provider": tts_provider,
    }
    perception = {
        "enabled": _bool_from(local_awareness, "enabled"),
        "camera_enabled": _bool_from(getattr(local_awareness, "camera", None), "enabled"),
        "screen_enabled": _bool_from(getattr(local_awareness, "screen", None), "enabled"),
        "audio_input_enabled": _bool_from(local_audio, "input_enabled"),
        "media_inspection_enabled": _bool_from(
            getattr(local_awareness, "media_inspection", None),
            "enabled",
            fallback=True,
        ),
        "device_discovery_enabled": _bool_from(local_awareness, "device_discovery_enabled"),
        "lan_discovery_enabled": _bool_from(local_awareness, "lan_discovery_enabled"),
    }
    background_review_configured = _bool_from(
        getattr(learning, "background_review", None),
        "enabled",
    )
    curator_configured = _bool_from(getattr(learning, "curator", None), "enabled")
    automation = {
        "backend_cognition_enabled": backend_cognition_enabled,
        "background_review_enabled": backend_cognition_enabled and background_review_configured,
        "curator_enabled": backend_cognition_enabled and curator_configured,
        "device_automation_enabled": _bool_from(device_tools, "automation_enabled"),
        "gateway_heartbeat_enabled": _bool_from(getattr(gateway, "heartbeat", None), "enabled"),
    }
    memory = {
        "working_memory_enabled": working_memory_enabled,
        "nearline_enabled": nearline_enabled,
        "dream_enabled": dream_enabled,
    }
    controls = {
        "allow_agent_initiated_messages": active_intents_enabled,
        "enable_backend_cognition": backend_cognition_enabled,
        "runtime_profile": _string_from(runtime_cfg, "profile")
        or _string_from(loop, "_runtime_profile")
        or "default",
    }

    channel_rows = _channel_rows(
        channels_config=channels_config,
        enabled_channels=enabled_channels,
    )
    providers_summary = {
        "primary_provider": resolved_provider or "unknown",
        "model": resolved_model or "unknown",
        "transcription_provider": transcription_provider,
        "tts_provider": tts_provider,
        "primary_api_base": _string_from(
            getattr(providers, resolved_provider, None) if providers is not None and resolved_provider else None,
            "api_base",
        ),
    }

    enabled_capabilities: list[str] = []
    if active_intents_enabled:
        enabled_capabilities.append("agent_initiated_messages")
    if backend_cognition_enabled:
        enabled_capabilities.append("backend_cognition")
    if working_memory_enabled:
        enabled_capabilities.append("working_memory")
    if nearline_enabled:
        enabled_capabilities.append("nearline_memory")
    if perception["enabled"]:
        enabled_capabilities.append("perception")
    if voice["input_enabled"]:
        enabled_capabilities.append("voice_input")
    if voice["output_enabled"]:
        enabled_capabilities.append("voice_output")
    if automation["device_automation_enabled"]:
        enabled_capabilities.append("device_automation")
    if automation["background_review_enabled"]:
        enabled_capabilities.append("background_review")
    if automation["curator_enabled"]:
        enabled_capabilities.append("curator")

    mode = "autonomous" if any(
        (
            active_intents_enabled,
            backend_cognition_enabled,
            automation["device_automation_enabled"],
            automation["background_review_enabled"],
            automation["curator_enabled"],
        )
    ) else "reactive"

    return RuntimeModeSummary(
        mode=mode,
        enabled_capabilities=enabled_capabilities,
        controls=controls,
        memory=memory,
        perception=perception,
        automation=automation,
        voice=voice,
        channels=channel_rows,
        providers=providers_summary,
    )


def _channel_rows(
    *,
    channels_config: Any,
    enabled_channels: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    extras = dict(getattr(channels_config, "model_extra", {}) or {}) if channels_config is not None else {}
    enabled_lookup = set(enabled_channels or [])
    names = sorted(set(extras) | enabled_lookup)
    rows: list[dict[str, Any]] = []
    for name in names:
        section = extras.get(name)
        enabled = (
            _section_enabled(section)
            if name in extras
            else name in enabled_lookup
        )
        rows.append({
            "name": name,
            "enabled": enabled,
            "legacy_config": isinstance(section, dict),
        })
    return rows


def _section_enabled(section: Any) -> bool:
    if isinstance(section, dict):
        value = section.get("enabled")
        return bool(value) if isinstance(value, bool) else False
    return bool(getattr(section, "enabled", False))


def _bool_from(obj: Any, attr: str, *, fallback: bool = False) -> bool:
    if obj is None:
        return fallback
    value = getattr(obj, attr, fallback)
    return bool(value)


def _string_from(obj: Any, attr: str) -> str | None:
    if obj is None:
        return None
    value = getattr(obj, attr, None)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
