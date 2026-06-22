"""Helpers for standardized proactive/perception message metadata."""

from __future__ import annotations

from typing import Any

ORIGIN_METADATA_FIELDS = (
    "origin_kind",
    "is_inferred",
    "confidence",
    "fresh_until",
    "trigger_reason",
)

_ORIGIN_LABELS = {
    "active_intent": "Active Intent",
    "cognitive_event": "Cognitive Event",
    "scheduled_reminder": "Scheduled Reminder",
    "snapshot": "Snapshot",
    "inspection": "Inspection",
    "world_summary": "World Summary",
}


def build_origin_metadata(
    metadata: dict[str, Any] | None = None,
    *,
    origin_kind: str,
    is_inferred: bool,
    confidence: float | None = None,
    fresh_until: str | None = None,
    trigger_reason: str | None = None,
) -> dict[str, Any]:
    """Return metadata with standardized origin fields populated."""

    base = dict(metadata or {})
    base["origin_kind"] = origin_kind
    base["is_inferred"] = bool(is_inferred)
    base["confidence"] = confidence
    base["fresh_until"] = fresh_until
    base["trigger_reason"] = trigger_reason
    return base


def extract_origin_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Return only standardized origin metadata fields."""

    base = dict(metadata or {})
    extracted = {
        field: base.get(field)
        for field in ORIGIN_METADATA_FIELDS
        if field in base
    }
    if not extracted:
        return {}
    extracted["label"] = origin_label(base)
    return extracted


def origin_label(metadata: dict[str, Any] | None) -> str | None:
    """Return a human-friendly label for origin metadata."""

    origin_kind = str((metadata or {}).get("origin_kind") or "").strip().lower()
    if not origin_kind:
        return None
    if origin_kind == "cognitive_event":
        event_type = str((metadata or {}).get("cognitive_event_type") or "").strip().lower()
        if event_type == "scheduled_reminder":
            return _ORIGIN_LABELS["scheduled_reminder"]
    return _ORIGIN_LABELS.get(origin_kind) or origin_kind.replace("_", " ").title()


def maybe_prefix_origin_label(content: str, metadata: dict[str, Any] | None) -> str:
    """Prefix plain-text content with a normalized origin label when appropriate."""

    if not content:
        return content
    label = origin_label(metadata)
    if not label:
        return content
    prefix = f"[{label}] "
    return content if content.startswith(prefix) else f"{prefix}{content}"
