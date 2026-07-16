"""Runtime profile presets for common OriginAgent operating modes."""

from __future__ import annotations

from OriginAgent.config.schema import Config, DeviceToolsConfig, ExecToolConfig, RuntimeProfile, ToolAuditConfig


def build_runtime_profile_defaults(profile: RuntimeProfile) -> Config:
    """Return a config object containing only conservative profile defaults."""

    config = Config()
    config.runtime.profile = profile
    if profile == "default":
        return config
    if profile in {"safe", "household_safe"}:
        config.tools.audit = ToolAuditConfig(mode="minimal")
        config.tools.exec = ExecToolConfig(profile="secure", allow_unsafe_exec=False)
        config.tools.device = DeviceToolsConfig(enabled=False, mode="dry_run")
        return config
    if profile == "local_dev":
        config.tools.audit = ToolAuditConfig(mode="minimal")
        config.tools.exec = ExecToolConfig(profile="local_dev", allow_unsafe_exec=True)
        config.tools.device = DeviceToolsConfig(enabled=False, mode="dry_run")
        return config
    if profile == "automation":
        config.tools.audit = ToolAuditConfig(mode="minimal")
        config.tools.exec = ExecToolConfig(profile="secure", allow_unsafe_exec=False)
        config.tools.device = DeviceToolsConfig(
            enabled=False,
            mode="dry_run",
            automation_enabled=False,
            automation_allowed_domains=["lighting"],
            automation_max_actions_per_pass=1,
            automation_dry_run_only=True,
        )
        return config
    return config


def apply_runtime_profile(config: Config) -> Config:
    """Apply conservative profile defaults without overriding explicit user values.

    Uses field-presence detection (Pydantic's ``__pydantic_fields_set__``) rather
    than value comparison, so user-explicit values equal to schema defaults are
    preserved (spec 3.16).
    """

    profile = config.runtime.profile
    if profile == "default":
        return config
    profile_defaults = build_runtime_profile_defaults(profile)
    updated = config.model_copy(deep=True)
    tools_fields_set = config.tools.__pydantic_fields_set__
    if "audit" not in tools_fields_set:
        updated.tools.audit = profile_defaults.tools.audit
    if "exec" not in tools_fields_set:
        updated.tools.exec = profile_defaults.tools.exec
    if "device" not in tools_fields_set:
        updated.tools.device = profile_defaults.tools.device
    return updated
