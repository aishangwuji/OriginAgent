"""Factory for productized device action execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
import httpx

from OriginAgent.agent.action_runtime import SafeActionExecutor
from OriginAgent.agent.audit import AuditLogger
from OriginAgent.agent.confirmation import ConfirmationManager
from OriginAgent.agent.facts import FactStore
from OriginAgent.config.schema import DeviceToolsConfig

from .action_safety import SmartHomeActionSafetyGate
from .action_automation import ActionAutomationPreconditionGate
from .confirmation_prompts import SmartHomeConfirmationPromptBuilder
from .device_actions import DeviceActionSchemaRegistry, TypedActionPlanner
from .device_backends import DeviceActionExecutor
from .device_integrations import RealLightingBackend
from .devices import DEVICE_SCOPE_REDACTOR
from .facts import SMART_HOME_FACT_STORE_CONFIG
from .permissions import PermissionResolver
from .presence import PresenceStore


class _NoopLightingClient:
    def set_power(self, device_id: str, power: str):
        return {"ok": True}

    def set_brightness(self, device_id: str, brightness: int):
        return {"ok": True}

    def set_color_temperature(self, device_id: str, temperature: str):
        return {"ok": True}


class _HttpLightingClient:
    def __init__(self, endpoint: str, *, timeout_seconds: int = 5) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def set_power(self, device_id: str, power: str):
        return self._post("/lighting/power", {"device_id": device_id, "power": power})

    def set_brightness(self, device_id: str, brightness: int):
        return self._post("/lighting/brightness", {"device_id": device_id, "brightness": brightness})

    def set_color_temperature(self, device_id: str, temperature: str):
        return self._post("/lighting/color-temperature", {"device_id": device_id, "temperature": temperature})

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            f"{self._endpoint}{path}",
            json=payload,
            timeout=float(self._timeout_seconds),
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}


def build_device_action_executor(
    *,
    workspace: Path,
    config: DeviceToolsConfig,
    audit_logger: AuditLogger | None = None,
    presence_store: PresenceStore | None = None,
    fact_store: FactStore | None = None,
    permission_resolver: PermissionResolver | None = None,
    device_registry: Any | None = None,
) -> DeviceActionExecutor | None:
    if not config.enabled:
        return None
    if not config.lighting_enabled:
        return None
    if config.backend == "none":
        return None
    if config.mode == "real":
        if config.backend != "lighting_client":
            logger.warning("Real device mode only supports the lighting_client backend; device tools disabled.")
            return None
        if not config.real_execution_enabled:
            logger.warning("Real device mode requires real_execution_enabled=true; device tools disabled.")
            return None
        if not str(config.lighting_client_endpoint or "").strip():
            logger.warning("Real device mode requires lighting_client_endpoint; device tools disabled.")
            return None
    elif config.backend != "fake":
        logger.warning("Unsupported device backend '{}'; device tools disabled", config.backend)
        return None

    audit = audit_logger or AuditLogger(workspace, scope_redactor=DEVICE_SCOPE_REDACTOR)
    presence = presence_store or PresenceStore(workspace)
    facts = fact_store or FactStore(workspace, config=SMART_HOME_FACT_STORE_CONFIG)
    permissions = permission_resolver or PermissionResolver()
    if config.mode == "real":
        backend = RealLightingBackend(
            _HttpLightingClient(
                str(config.lighting_client_endpoint or "").strip(),
                timeout_seconds=int(config.lighting_client_timeout_seconds or 5),
            ),
            real_mode=True,
        )
    else:
        backend = RealLightingBackend(_NoopLightingClient(), real_mode=False)
    safe_executor = SafeActionExecutor(
        gate=SmartHomeActionSafetyGate(presence, facts),
        confirmation_manager=ConfirmationManager(
            workspace,
            audit_logger=audit,
            prompt_builder=SmartHomeConfirmationPromptBuilder(),
        ),
        backend=backend,
        permission_resolver=permissions,
        audit_logger=audit,
        scope_redactor=DEVICE_SCOPE_REDACTOR,
    )
    return DeviceActionExecutor(
        TypedActionPlanner(DeviceActionSchemaRegistry()),
        safe_executor,
        audit_logger=audit,
        automation_gate=ActionAutomationPreconditionGate(
            device_registry=device_registry,
            allowed_domains=tuple(config.automation_allowed_domains or ["lighting"]),
        ),
    )
