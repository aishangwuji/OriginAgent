"""Contextvars-based current tenant for the turn pipeline."""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from OriginAgent.identity.tenant import Tenant

_current_tenant: contextvars.ContextVar["Tenant | None"] = contextvars.ContextVar(
    "current_tenant", default=None
)


def set_current_tenant(tenant: Tenant) -> None:
    _current_tenant.set(tenant)


def get_current_tenant() -> Tenant | None:
    return _current_tenant.get(None)


def current_tenant_id() -> str:
    t = _current_tenant.get(None)
    return t.tenant_id if t is not None else "unknown"


def current_tenant_session_key() -> str:
    t = _current_tenant.get(None)
    return t.unified_session_key if t is not None else "unified:default"
