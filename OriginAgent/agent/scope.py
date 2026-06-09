"""Identity/scope helpers for continuity Phase 1."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


ScopeName = Literal["user", "session", "task"]


@dataclass(frozen=True)
class IdentityDescriptor:
    actor_id: str
    user_id: str
    session_id: str | None
    device_id: str | None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class ScopeResolver:
    """Minimal Phase 1 scope visibility rules."""

    @staticmethod
    def normalize_scope(scope: str | None, *, default: ScopeName = "session") -> ScopeName:
        value = str(scope or "").strip().lower()
        if value in {"user", "session", "task"}:
            return value  # type: ignore[return-value]
        return default

    def is_visible(
        self,
        *,
        scope: str | None,
        current_scope: str | None,
        owner_id: str | None = None,
        current_owner_id: str | None = None,
    ) -> bool:
        normalized = self.normalize_scope(scope)
        current = self.normalize_scope(current_scope)
        if normalized == "task":
            return current == "task"
        if normalized == "session":
            return current in {"session", "task"}
        if normalized == "user":
            if owner_id and current_owner_id and owner_id != current_owner_id:
                return False
            return current in {"user", "session", "task"}
        return False

    def can_propagate(
        self,
        *,
        from_scope: str | None,
        to_scope: str | None,
        allow_propagation: bool = False,
    ) -> bool:
        left = self.normalize_scope(from_scope)
        right = self.normalize_scope(to_scope)
        if left == right:
            return True
        if not allow_propagation:
            return False
        order = {"task": 0, "session": 1, "user": 2}
        return order[left] <= order[right]
