"""Factory for productized device action execution."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from OpenHome.agent.action_runtime import SafeActionExecutor
from OpenHome.agent.action_safety import ActionSafetyGate
from OpenHome.agent.audit import AuditLogger
from OpenHome.agent.confirmation import ConfirmationManager
from OpenHome.agent.device_actions import DeviceActionSchemaRegistry, TypedActionPlanner
from OpenHome.agent.device_backends import DeviceActionExecutor
from OpenHome.agent.device_integrations import RealLightingBackend
from OpenHome.agent.facts import FactStore
from OpenHome.agent.permissions import PermissionResolver
from OpenHome.agent.presence import PresenceStore
from OpenHome.config.schema import DeviceToolsConfig


class _NoopLightingClient:
    def set_power(self, device_id: str, power: str):
        return {"ok": True}

    def set_brightness(self, device_id: str, brightness: int):
        return {"ok": True}

    def set_color_temperature(self, device_id: str, temperature: str):
        return {"ok": True}


def build_device_action_executor(
    *,
    workspace: Path,
    config: DeviceToolsConfig,
    audit_logger: AuditLogger | None = None,
    presence_store: PresenceStore | None = None,
    fact_store: FactStore | None = None,
    permission_resolver: PermissionResolver | None = None,
) -> DeviceActionExecutor | None:
    if not config.enabled:
        return None
    if not config.lighting_enabled:
        return None
    if config.backend == "none":
        return None
    if config.mode == "real":
        logger.warning("Device gateway real mode is not enabled in this release; device tools disabled.")
        return None
    if config.backend != "fake":
        logger.warning("Unsupported device backend '{}'; device tools disabled", config.backend)
        return None

    audit = audit_logger or AuditLogger(workspace)
    presence = presence_store or PresenceStore(workspace)
    facts = fact_store or FactStore(workspace)
    permissions = permission_resolver or PermissionResolver()
    backend = RealLightingBackend(_NoopLightingClient(), real_mode=False)
    safe_executor = SafeActionExecutor(
        gate=ActionSafetyGate(presence, facts),
        confirmation_manager=ConfirmationManager(workspace, audit_logger=audit),
        backend=backend,
        permission_resolver=permissions,
        audit_logger=audit,
    )
    return DeviceActionExecutor(
        TypedActionPlanner(DeviceActionSchemaRegistry()),
        safe_executor,
        audit_logger=audit,
    )
