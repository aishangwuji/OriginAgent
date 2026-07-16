"""Read-only config diagnostics and compatibility reporting."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, get_args, get_origin

from pydantic import AliasChoices, BaseModel

from OriginAgent.config.schema import ChannelsConfig, Config, TRANSCRIPTION_PROVIDERS as _TRANSCRIPTION_PROVIDERS

_SECRET_FIELD_NAMES = {
    "api_key",
    "apiKey",
    "token",
    "client_secret",
    "clientSecret",
    "secret",
    "password",
    "headers",
    "env",
}


@dataclass(frozen=True)
class ConfigDoctorReport:
    """Compatibility-oriented config doctor output."""

    raw_config_available: bool
    effective_config: dict[str, Any]
    unknown_fields: list[dict[str, Any]]
    ignored_fields: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    capability_warnings: list[dict[str, Any]]
    channel_provider_matrix: list[dict[str, Any]]
    legacy_channel_sections: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_config_doctor_report(
    *,
    config: Config | None = None,
    config_path: Path | None = None,
) -> ConfigDoctorReport:
    """Build a sanitized doctor report from effective and raw config state."""

    effective = config or Config()
    raw = _read_raw_config(config_path)
    unknown_fields: list[dict[str, Any]] = []
    legacy_channel_sections: list[str] = []
    if isinstance(raw, dict):
        unknown_fields, legacy_channel_sections = _collect_unknown_fields(raw, Config)

    ignored_fields = _ignored_field_warnings(effective)
    conflicts = _conflict_warnings(effective)
    capability_warnings = _capability_warnings(effective, legacy_channel_sections)

    return ConfigDoctorReport(
        raw_config_available=isinstance(raw, dict),
        effective_config=_mask_secrets(
            effective.model_dump(mode="json", by_alias=True)
        ),
        unknown_fields=unknown_fields,
        ignored_fields=ignored_fields,
        conflicts=conflicts,
        capability_warnings=capability_warnings,
        channel_provider_matrix=_channel_provider_matrix(effective, legacy_channel_sections),
        legacy_channel_sections=legacy_channel_sections,
    )


def _read_raw_config(config_path: Path | None) -> dict[str, Any] | None:
    if config_path is None:
        return None
    try:
        return json.loads(Path(config_path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _collect_unknown_fields(
    data: dict[str, Any],
    model_cls: type[BaseModel],
    *,
    prefix: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    key_map = _model_key_map(model_cls)
    extra_mode = getattr(model_cls, "model_config", {}).get("extra")
    unknown: list[dict[str, Any]] = []
    legacy_channels: list[str] = []

    for raw_key, value in data.items():
        field_name = key_map.get(raw_key)
        path = f"{prefix}.{raw_key}" if prefix else raw_key
        if field_name is None:
            if model_cls is ChannelsConfig and extra_mode == "allow":
                legacy_channels.append(raw_key)
                continue
            if extra_mode == "allow":
                continue
            unknown.append({
                "path": path,
                "reason": "unknown_field",
            })
            continue
        field = model_cls.model_fields[field_name]
        nested = _nested_model(field.annotation)
        if nested is None or not isinstance(value, dict):
            continue
        child_unknown, child_legacy = _collect_unknown_fields(
            value,
            nested,
            prefix=path,
        )
        unknown.extend(child_unknown)
        legacy_channels.extend(child_legacy)
    return unknown, legacy_channels


def _model_key_map(model_cls: type[BaseModel]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for field_name, field in model_cls.model_fields.items():
        for key in _accepted_field_keys(field_name, field):
            mapping[key] = field_name
    return mapping


def _accepted_field_keys(field_name: str, field: Any) -> set[str]:
    keys = {field_name}
    for value in (
        getattr(field, "alias", None),
        getattr(field, "serialization_alias", None),
    ):
        if isinstance(value, str) and value:
            keys.add(value)
    validation_alias = getattr(field, "validation_alias", None)
    if isinstance(validation_alias, str):
        keys.add(validation_alias)
    elif isinstance(validation_alias, AliasChoices):
        keys.update(str(choice) for choice in validation_alias.choices if isinstance(choice, str))
    elif hasattr(validation_alias, "choices"):
        keys.update(
            str(choice)
            for choice in getattr(validation_alias, "choices", [])
            if isinstance(choice, str)
        )
    return keys


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    if annotation is None:
        return None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = get_origin(annotation)
    if origin is None:
        return None
    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    if origin in {list, tuple, set} and args:
        return _nested_model(args[0])
    if origin is dict and len(args) == 2:
        return _nested_model(args[1])
    if args:
        for arg in args:
            nested = _nested_model(arg)
            if nested is not None:
                return nested
    return None


def _ignored_field_warnings(config: Config) -> list[dict[str, Any]]:
    defaults = config.agents.defaults
    local_awareness = config.tools.local_awareness
    audio = local_awareness.audio
    warnings: list[dict[str, Any]] = []

    if not local_awareness.enabled and any(
        (
            local_awareness.camera.enabled,
            local_awareness.screen.enabled,
            audio.input_enabled,
            audio.output_enabled,
            audio.transcription_enabled,
            audio.tts_enabled,
        )
    ):
        warnings.append({
            "path": "tools.localAwareness",
            "reason": "feature_disabled_but_subfeatures_configured",
            "message": "Local awareness is disabled, but perception or voice sub-features are still configured.",
        })

    if not audio.input_enabled and any(
        (
            audio.transcription_enabled,
            bool(audio.transcription_provider),
            bool(audio.device_id),
        )
    ):
        warnings.append({
            "path": "tools.localAwareness.audio",
            "reason": "voice_input_disabled_but_configured",
            "message": "Voice input is disabled, but transcription settings are still configured.",
        })

    if not audio.output_enabled and any(
        (
            audio.tts_enabled,
            bool(audio.voice),
        )
    ):
        warnings.append({
            "path": "tools.localAwareness.audio",
            "reason": "voice_output_disabled_but_configured",
            "message": "Voice output is disabled, but TTS settings are still configured.",
        })

    if not defaults.allow_agent_initiated_messages and (
        defaults.active_intent_interval_seconds != Config().agents.defaults.active_intent_interval_seconds
    ):
        warnings.append({
            "path": "agents.defaults.activeIntentIntervalSeconds",
            "reason": "agent_messages_disabled_but_active_intent_tuning_present",
            "message": "Agent-initiated messages are disabled, so active-intent timing overrides are currently ignored.",
        })
    return warnings


def _conflict_warnings(config: Config) -> list[dict[str, Any]]:
    defaults = config.agents.defaults
    audio = config.tools.local_awareness.audio
    conflicts: list[dict[str, Any]] = []

    if defaults.enable_backend_cognition and not defaults.allow_agent_initiated_messages:
        conflicts.append({
            "code": "backend_cognition_without_agent_messages",
            "message": "Backend cognition is enabled while agent-initiated messages are disabled; proactive outputs will stay internal or be suppressed.",
        })

    provider = audio.transcription_provider or config.channels.transcription_provider
    if provider not in _TRANSCRIPTION_PROVIDERS:
        conflicts.append({
            "code": "unsupported_transcription_provider",
            "message": f"Unsupported transcription provider: {provider!r}. Expected one of {sorted(_TRANSCRIPTION_PROVIDERS)}.",
        })

    if audio.tts_enabled and not _has_provider_credentials(config.providers.volcengine):
        conflicts.append({
            "code": "tts_provider_not_configured",
            "message": "Voice output is enabled, but Volcengine TTS credentials are not configured.",
        })

    return conflicts


def _capability_warnings(
    config: Config,
    legacy_channel_sections: list[str],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    defaults = config.agents.defaults
    audio = config.tools.local_awareness.audio
    transcription_provider = audio.transcription_provider or config.channels.transcription_provider

    if legacy_channel_sections:
        warnings.append({
            "code": "legacy_channel_sections",
            "message": "Channel sections are still being accepted via ChannelsConfig extra fields for compatibility; doctor output lists them explicitly for normalization.",
            "sections": list(sorted(legacy_channel_sections)),
        })

    if transcription_provider == "openai" and not _has_provider_credentials(config.providers.openai):
        warnings.append({
            "code": "openai_transcription_not_configured",
            "message": "OpenAI transcription is selected, but no OpenAI credentials are configured.",
        })
    if transcription_provider == "groq" and not _has_provider_credentials(config.providers.groq):
        warnings.append({
            "code": "groq_transcription_not_configured",
            "message": "Groq transcription is selected, but no Groq credentials are configured.",
        })

    if not audio.input_enabled and _has_provider_credentials(config.providers.groq):
        warnings.append({
            "code": "transcription_credentials_present_while_voice_input_disabled",
            "message": "Voice input is disabled, but transcription provider credentials are still configured.",
        })

    if not defaults.enable_backend_cognition and defaults.learning.background_review.enabled:
        warnings.append({
            "code": "background_review_enabled_while_backend_cognition_disabled",
            "message": "Background review stays enabled in config, but backend cognition is disabled at runtime.",
        })
    return warnings


def _channel_provider_matrix(
    config: Config,
    legacy_channel_sections: list[str],
) -> list[dict[str, Any]]:
    extras = dict(getattr(config.channels, "model_extra", {}) or {})
    names = sorted(set(legacy_channel_sections) | set(extras))
    transcription_provider = config.tools.local_awareness.audio.transcription_provider or config.channels.transcription_provider
    transcription_configured = _transcription_provider_configured(config, transcription_provider)
    rows: list[dict[str, Any]] = []
    for name in names:
        section = extras.get(name)
        enabled = False
        if isinstance(section, dict):
            enabled = bool(section.get("enabled", False))
        elif section is not None:
            enabled = bool(getattr(section, "enabled", False))
        rows.append({
            "channel": name,
            "enabled": enabled,
            "legacy_config": isinstance(section, dict),
            "transcription_provider": transcription_provider,
            "transcription_configured": transcription_configured,
            "send_progress": _section_bool(section, "send_progress", config.channels.send_progress),
            "send_tool_hints": _section_bool(section, "send_tool_hints", config.channels.send_tool_hints),
            "show_reasoning": _section_bool(section, "show_reasoning", config.channels.show_reasoning),
        })
    return rows


def _transcription_provider_configured(config: Config, provider: str) -> bool:
    if provider == "openai":
        return _has_provider_credentials(config.providers.openai)
    if provider == "groq":
        return _has_provider_credentials(config.providers.groq)
    return False


def _has_provider_credentials(provider_config: Any) -> bool:
    if provider_config is None:
        return False
    return bool(getattr(provider_config, "api_key", None) or getattr(provider_config, "api_base", None))


def _section_bool(section: Any, key: str, default: bool) -> bool:
    if isinstance(section, dict):
        camel = {
            "send_progress": "sendProgress",
            "send_tool_hints": "sendToolHints",
            "show_reasoning": "showReasoning",
        }.get(key)
        value = section.get(key)
        if value is None and camel:
            value = section.get(camel)
        return value if isinstance(value, bool) else default
    if section is None:
        return default
    value = getattr(section, key, None)
    return value if isinstance(value, bool) else default


def _mask_secrets(value: Any, *, field_name: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            key: _mask_secrets(item, field_name=key)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask_secrets(item, field_name=field_name) for item in value]
    if isinstance(value, str) and field_name in _SECRET_FIELD_NAMES:
        return _mask_secret(value)
    return value


def _mask_secret(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    if len(stripped) <= 8:
        return "••••"
    return f"{stripped[:4]}••••{stripped[-4:]}"
