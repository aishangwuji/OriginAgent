"""Generic permission request/decision model."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Any

VALID_PERMISSION_ACTIONS = {
    "execute_action",
    "confirm_action",
    "create_rule",
    "manage_presence",
    "manage_facts",
}
VALID_DEVICE_DOMAINS = {
    "lighting",
    "climate",
    "media",
    "lock",
    "security",
    "camera",
    "gas",
    "appliance",
    "presence",
    "general",
}
VALID_PERMISSION_DECISIONS = {"allow", "deny", "ask_admin"}
VALID_RISKS = {"low", "medium", "high"}
VALID_TRIGGERS = {"user_initiated", "scheduled", "system", "subagent"}


@dataclass
class HouseholdActor:
    actor_id: str
    role: str
    display_name: str | None = None

    def __post_init__(self) -> None:
        self.actor_id = _normalize_actor_id(self.actor_id) or "unknown"
        self.role = _normalize_household_role(self.role)
        if self.display_name is not None:
            self.display_name = self.display_name.strip() or None


@dataclass
class PermissionRequest:
    actor_id: str | None
    action: str
    scope: str
    risk: str
    trigger: str
    permission: str
    attributes: dict[str, str] = field(default_factory=dict)
    device_domain: InitVar[str | None] = None

    def __post_init__(self, device_domain: str | None) -> None:
        self.actor_id = _normalize_actor_id(self.actor_id)
        self.action = str(self.action or "").strip()
        self.scope = str(self.scope or "").strip()
        self.risk = str(self.risk or "low").strip().lower()
        self.trigger = str(self.trigger or "").strip().lower()
        self.permission = _normalize_permission(self.permission)
        if self.risk not in VALID_RISKS:
            raise ValueError(f"invalid risk: {self.risk!r}")
        if self.trigger not in VALID_TRIGGERS:
            raise ValueError(f"invalid trigger: {self.trigger!r}")
        self.attributes = _normalize_attributes(self.attributes)
        if device_domain is not None:
            self.attributes.setdefault("device_domain", _normalize_device_domain(device_domain))

    @property
    def device_domain(self) -> str:
        return self.attributes.get("device_domain", "general")

    def attribute(self, name: str, default: str | None = None) -> str | None:
        normalized_name = str(name or "").strip().lower()
        if not normalized_name:
            return default
        return self.attributes.get(normalized_name, default)


@dataclass
class PermissionDecision:
    decision: str
    reason: str
    actor_role: str = "unknown"

    def __post_init__(self) -> None:
        self.decision = _normalize_decision(self.decision)
        self.actor_role = _normalize_actor_role(self.actor_role)
        self.reason = str(self.reason or "")


class PermissionResolver:
    def __init__(self, actors: dict[str, HouseholdActor] | None = None):
        self.actors: dict[str, HouseholdActor] = {}
        for actor_id, actor in (actors or {}).items():
            normalized_id = _normalize_actor_id(actor_id)
            if normalized_id:
                self.actors[normalized_id] = _coerce_household_actor(actor)

    def resolve_actor(self, actor_id: str | None) -> HouseholdActor:
        smart_home = self._smart_home_resolver()
        if smart_home is not None:
            return _coerce_household_actor(smart_home.resolve_actor(actor_id))
        normalized_id = _normalize_actor_id(actor_id)
        if not normalized_id:
            return HouseholdActor("unknown", "unknown")
        return self.actors.get(normalized_id, HouseholdActor(normalized_id, "unknown"))

    def evaluate(self, request: PermissionRequest) -> PermissionDecision:
        smart_home = self._smart_home_resolver()
        if smart_home is not None and _should_use_smart_home_policy(request, self.actors):
            return smart_home.evaluate(request)
        actor = self.resolve_actor(request.actor_id)
        if request.permission in {"manage_presence", "manage_facts", "create_rule"}:
            return PermissionDecision(
                decision="ask_admin",
                reason=f"{request.permission} requires explicit administrator approval",
                actor_role=actor.role,
            )
        if request.permission in {"confirm_action", "execute_action"}:
            if request.risk == "high":
                return PermissionDecision(
                    decision="ask_admin",
                    reason="high-risk actions require explicit administrator approval",
                    actor_role=actor.role,
                )
            return PermissionDecision(
                decision="allow",
                reason="default resolver allows low/medium risk actions",
                actor_role=actor.role,
            )
        return _deny(actor.role, f"unsupported permission: {request.permission}")

    def _smart_home_resolver(self) -> Any | None:
        try:
            from OpenHome.domain_packs.smart_home.runtime.permissions import (
                HouseholdActor as SmartHomeActor,
                PermissionResolver as SmartHomePermissionResolver,
            )
        except Exception:
            return None
        actors = {
            actor_id: SmartHomeActor(
                actor.actor_id,
                actor.role,
                actor.display_name,
            )
            for actor_id, actor in self.actors.items()
        }
        return SmartHomePermissionResolver(actors)


def infer_device_domain(scope: str, action: str) -> str:
    try:
        from OpenHome.domain_packs.smart_home.runtime.permissions import infer_device_domain as impl
    except Exception:
        return "general"
    return impl(scope, action)


def _allow(role: str, reason: str) -> PermissionDecision:
    return PermissionDecision(decision="allow", reason=reason, actor_role=role)


def _deny(role: str, reason: str) -> PermissionDecision:
    return PermissionDecision(decision="deny", reason=reason, actor_role=role)


def _ask_admin_or_deny(role: str, permission: str) -> PermissionDecision:
    if role in {"resident", "elder", "guest", "child", "service_person", "unknown"}:
        return PermissionDecision(
            decision="ask_admin",
            reason=f"{permission} requires administrator permission",
            actor_role=role,
        )
    return _deny(role, f"{role} cannot {permission}")


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _normalize_actor_id(actor_id: str | None) -> str | None:
    if actor_id is None:
        return None
    normalized = str(actor_id).strip()
    return normalized or None


def _normalize_household_role(role: str | None) -> str:
    try:
        from OpenHome.domain_packs.smart_home.runtime.permissions import _normalize_role as impl
    except Exception:
        normalized = str(role or "unknown").strip().lower()
        if normalized in {
            "admin",
            "resident",
            "guest",
            "child",
            "elder",
            "service_person",
            "unknown",
        }:
            return normalized
        raise ValueError(f"invalid household role: {role!r}")
    return impl(role)


def _normalize_actor_role(role: str | None) -> str:
    normalized = str(role or "unknown").strip().lower()
    return normalized or "unknown"


def _normalize_permission(permission: str | None) -> str:
    normalized = str(permission or "").strip().lower()
    if normalized not in VALID_PERMISSION_ACTIONS:
        raise ValueError(f"invalid permission action: {permission!r}")
    return normalized


def _normalize_device_domain(device_domain: str | None) -> str:
    normalized = str(device_domain or "general").strip().lower()
    if not normalized:
        normalized = "general"
    return normalized


def _normalize_decision(decision: str | None) -> str:
    normalized = str(decision or "").strip().lower()
    if normalized not in VALID_PERMISSION_DECISIONS:
        raise ValueError(f"invalid permission decision: {decision!r}")
    return normalized


def _normalize_attributes(attributes: dict[str, Any] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    if not isinstance(attributes, dict):
        return normalized
    for key, value in attributes.items():
        key_text = str(key or "").strip().lower()
        if not key_text or value is None:
            continue
        value_text = str(value).strip()
        if not value_text:
            continue
        normalized[key_text] = value_text
    return normalized


def _coerce_household_actor(actor: Any) -> HouseholdActor:
    if isinstance(actor, HouseholdActor):
        return actor
    actor_id = getattr(actor, "actor_id", "unknown")
    role = getattr(actor, "role", "unknown")
    display_name = getattr(actor, "display_name", None)
    return HouseholdActor(str(actor_id), str(role), display_name)


def _should_use_smart_home_policy(
    request: PermissionRequest,
    actors: dict[str, HouseholdActor],
) -> bool:
    if actors:
        return True
    return request.device_domain != "general" or request.attribute("home_id") is not None
