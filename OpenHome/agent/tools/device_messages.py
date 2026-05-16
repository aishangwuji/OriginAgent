"""Stable user-facing messages for device tool results."""

from __future__ import annotations


def device_human_message(status: str, fallback: str | None = None) -> str:
    if status in {"executed", "dry_run", "success"}:
        return "Lighting action accepted."
    if status in {"pending_confirmation", "needs_confirmation"}:
        return "This action needs confirmation before it can run."
    if status in {"denied", "deny"}:
        return "This action was denied by the household safety policy."
    return "The lighting action could not be submitted."

