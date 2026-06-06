"""Shared runtime policy helpers for nearline memory."""

from __future__ import annotations

from typing import Any


def nearline_runtime_enabled(config: Any | None) -> bool:
    """Whether nearline sidecar artifacts may influence runtime behavior."""
    if config is None:
        return True
    return bool(getattr(config, "enabled", False) and getattr(config, "pipeline_enabled", False))


def nearline_declared_enabled(config: Any | None) -> bool:
    """Whether nearline is configured on, regardless of runtime side effects."""
    if config is None:
        return True
    return bool(getattr(config, "enabled", False))
