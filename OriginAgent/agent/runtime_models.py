"""Shared read-only runtime models for background tasks and prompt context."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

TaskStatus = Literal["ok", "skipped", "degraded", "error", "blocked"]
TaskFaultClass = Literal["transient", "config", "invariant", "io", "restore", "external", "unknown"]


@dataclass(frozen=True)
class TaskRunReport:
    task_name: str
    status: TaskStatus
    phase: str
    fault_class: TaskFaultClass
    retryable: bool
    degraded: bool
    reason: str
    started_at: str
    finished_at: str
    attempt_count: int = 1
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeContextSnapshot:
    runtime: dict[str, Any] = field(default_factory=dict)
    confirmations: dict[str, Any] = field(default_factory=dict)
    reviews: dict[str, Any] = field(default_factory=dict)
    background_tasks: dict[str, Any] = field(default_factory=dict)
    domains_summary: dict[str, Any] = field(default_factory=dict)
    skills_summary: dict[str, Any] = field(default_factory=dict)
    facts_summary: dict[str, Any] = field(default_factory=dict)
    memory_summary: dict[str, Any] = field(default_factory=dict)
    nearline_memory_summary: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
