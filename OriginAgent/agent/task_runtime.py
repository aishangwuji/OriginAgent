"""Shared helpers for background task runtime reporting."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from OriginAgent.agent.runtime_models import TaskFaultClass, TaskRunReport, TaskStatus, now_iso


def build_task_report(
    *,
    task_name: str,
    status: TaskStatus,
    phase: str,
    fault_class: TaskFaultClass = "unknown",
    retryable: bool = False,
    degraded: bool = False,
    reason: str = "",
    started_at: str | None = None,
    finished_at: str | None = None,
    attempt_count: int = 1,
    details: dict[str, Any] | None = None,
) -> TaskRunReport:
    return TaskRunReport(
        task_name=task_name,
        status=status,
        phase=phase,
        fault_class=fault_class,
        retryable=retryable,
        degraded=degraded,
        reason=reason,
        started_at=started_at or now_iso(),
        finished_at=finished_at or now_iso(),
        attempt_count=attempt_count,
        details=dict(details or {}),
    )


def report_to_status_payload(
    report: TaskRunReport | None,
    *,
    consecutive_failures: int,
) -> dict[str, Any]:
    if report is None:
        return {
            "last_status": None,
            "last_fault_class": None,
            "last_retryable": None,
            "last_degraded": None,
            "last_reason": "",
            "last_started_at": None,
            "last_finished_at": None,
            "consecutive_failures": consecutive_failures,
            "last_report": None,
        }
    return {
        "last_status": report.status,
        "last_fault_class": report.fault_class,
        "last_retryable": report.retryable,
        "last_degraded": report.degraded,
        "last_reason": report.reason,
        "last_started_at": report.started_at,
        "last_finished_at": report.finished_at,
        "consecutive_failures": consecutive_failures,
        "last_report": report.to_json(),
    }


def remember_report(
    *,
    report: TaskRunReport,
    current_failures: int,
) -> int:
    if report.status in {"error", "blocked"}:
        return current_failures + 1
    return 0


async def maybe_retry_once(
    coro_factory,
    *,
    retry_count: int,
    backoff_ms: int = 0,
):
    attempt = 0
    last_exc: BaseException | None = None
    while attempt <= retry_count:
        try:
            return await coro_factory(attempt + 1), attempt + 1
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            attempt += 1
            if attempt > retry_count:
                raise
            if backoff_ms > 0:
                await asyncio.sleep(backoff_ms / 1000)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("retry loop exited unexpectedly")


def report_json(report: TaskRunReport | None) -> dict[str, Any] | None:
    return asdict(report) if report is not None else None

