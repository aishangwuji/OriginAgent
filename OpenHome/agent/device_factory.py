"""Compatibility bridge for smart-home device executor construction."""

from __future__ import annotations

from pathlib import Path

from OpenHome.agent.audit import AuditLogger
from OpenHome.agent.facts import FactStore
from OpenHome.agent.permissions import PermissionResolver
from OpenHome.agent.presence import PresenceStore
from OpenHome.config.schema import DeviceToolsConfig
from OpenHome.domain_packs.smart_home.runtime.device_backends import DeviceActionExecutor
from OpenHome.domain_packs.smart_home.runtime.device_factory import (
    build_device_action_executor as _build_smart_home_device_action_executor,
)


def build_device_action_executor(
    *,
    workspace: Path,
    config: DeviceToolsConfig,
    audit_logger: AuditLogger | None = None,
    presence_store: PresenceStore | None = None,
    fact_store: FactStore | None = None,
    permission_resolver: PermissionResolver | None = None,
) -> DeviceActionExecutor | None:
    return _build_smart_home_device_action_executor(
        workspace=workspace,
        config=config,
        audit_logger=audit_logger,
        presence_store=presence_store,
        fact_store=fact_store,
        permission_resolver=permission_resolver,
    )
