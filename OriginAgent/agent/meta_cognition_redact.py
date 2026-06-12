"""Shared redaction and trimming helpers for meta-cognition artifacts."""

from __future__ import annotations

from typing import Any

from OriginAgent.agent.action_privacy import FORBIDDEN_METADATA_KEYS
from OriginAgent.agent.audit import AUDIT_FORBIDDEN_KEYS
from OriginAgent.agent.memory import redact_memory_text

_DEFAULT_TEXT_MAX_CHARS = 240


def trim_text(value: Any, *, max_chars: int = _DEFAULT_TEXT_MAX_CHARS) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def redact_meta_text(value: Any, *, max_chars: int = _DEFAULT_TEXT_MAX_CHARS) -> str:
    return trim_text(redact_memory_text(str(value or "")), max_chars=max_chars)


def redact_meta_list(
    values: Any,
    *,
    max_items: int = 8,
    max_chars: int = 160,
) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for item in values:
        text = redact_meta_text(item, max_chars=max_chars)
        if not text:
            continue
        out.append(text)
        if len(out) >= max_items:
            break
    return out


def redact_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    forbidden = {key.casefold() for key in FORBIDDEN_METADATA_KEYS | AUDIT_FORBIDDEN_KEYS}
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = str(key or "").strip()
        if not normalized_key or normalized_key.casefold() in forbidden:
            continue
        if item is None:
            continue
        if isinstance(item, dict):
            nested = redact_metadata(item)
            if nested:
                cleaned[normalized_key] = nested
            continue
        if isinstance(item, list):
            nested_list = redact_meta_list(item, max_items=8, max_chars=120)
            if nested_list:
                cleaned[normalized_key] = nested_list
            continue
        text = redact_meta_text(item, max_chars=160)
        if text:
            cleaned[normalized_key] = text
    return cleaned


def redact_rule_candidate(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "").strip().lower()
    summary = redact_meta_text(value.get("summary"), max_chars=240)
    if kind not in {"preference", "task_pattern", "constraint", "fact"} or not summary:
        return None
    scope_hint = redact_meta_text(value.get("scope_hint"), max_chars=120) or "user"
    sensitivity = redact_meta_text(value.get("sensitivity"), max_chars=40) or "low"
    return {
        "kind": kind,
        "summary": summary,
        "confidence": max(0.0, min(float(value.get("confidence") or 0.0), 1.0)),
        "scope_hint": scope_hint,
        "sensitivity": sensitivity,
        "supporting_refs": redact_meta_list(value.get("supporting_refs"), max_items=8, max_chars=160),
    }

