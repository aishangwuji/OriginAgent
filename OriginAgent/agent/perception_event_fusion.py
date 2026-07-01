"""PerceptionEventFusion — normalise runtime signals into unified MetaTriggers.

Provides source adapters for every runtime signal origin (cognitive loop,
tool executor, world state, user input, device telemetry) and a ``process()``
entrypoint that dispatches to the correct adapter based on input type.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from OriginAgent.agent.cognitive_events import CognitiveEvent
from OriginAgent.agent.meta_cognition_triggers import (
    _USER_CORRECTION_EXCLUDES,
    _USER_CORRECTION_PATTERNS,
)
from OriginAgent.agent.perception_event_models import RuntimeEvent


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _safe_digest(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _trim_text(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def _confidence_from_priority(priority: str) -> float:
    mapping = {"low": 0.3, "medium": 0.6, "high": 0.9}
    return mapping.get(str(priority).strip().lower(), 0.5)


_DEVICE_EVENT_TYPES = frozenset({
    "device_online", "device_offline", "device_error",
    "sensor_triggered", "telemetry_threshold",
})


class PerceptionEventFusion:
    """Coordinator for normalising runtime signals into MetaTriggers.

    Usage::

        fusion = PerceptionEventFusion(config=meta_config.perception_fusion)
        event = fusion.process(source=cognitive_event)
        if event is not None:
            trigger = bridge_runtime_event_to_trigger(event)
            runtime.record_trigger(trigger)
    """

    def __init__(self, *, config: Any | None = None) -> None:
        self._config = config

    # ── config properties ──────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        if self._config is None:
            return True
        return bool(getattr(self._config, "enabled", True))

    # ── source adapters ────────────────────────────────────────────

    @staticmethod
    def bridge_cognitive_event(event: CognitiveEvent) -> RuntimeEvent | None:
        """Convert a ``CognitiveEvent`` (backend nudge) into a ``RuntimeEvent``."""
        return RuntimeEvent(
            event_id=event.event_id,
            session_key=event.session_key,
            event_type="cognitive_nudge",
            source=event.source_type,
            scope="",
            owner_id=event.session_key,
            summary=event.summary,
            confidence=_confidence_from_priority(event.priority),
            payload=dict(event.payload),
            created_at=event.created_at,
        )

    @staticmethod
    def bridge_tool_result(
        *,
        session_key: str,
        tool_name: str,
        status: str,
        params: dict[str, Any] | None = None,
        error_kind: str | None = None,
        result: Any = None,
    ) -> RuntimeEvent | None:
        """Normalise a tool execution outcome into a ``RuntimeEvent``.

        *status* ``"error"`` or ``"policy_denied"`` produces event type
        ``"tool_failure"``; any other status produces ``"tool_result"``.
        """
        if not session_key or not tool_name:
            return None
        params = params or {}
        digest = _safe_digest(session_key, tool_name, json.dumps(params, sort_keys=True, default=str))
        is_failure = status in {"error", "policy_denied"}
        confidence = 0.3 if is_failure else 0.8
        event_type = "tool_failure" if is_failure else "tool_result"
        payload: dict[str, Any] = {
            "tool_name": tool_name,
            "status": status,
        }
        if error_kind:
            payload["error_kind"] = error_kind
        if result is not None:
            payload["result_preview"] = _trim_text(str(result), max_chars=200)

        return RuntimeEvent(
            event_id=f"runtime_event:{digest}",
            session_key=session_key,
            event_type=event_type,
            source="tool_executor",
            summary=f"tool {tool_name}: {status}" if is_failure else f"tool {tool_name} completed",
            confidence=confidence,
            payload=payload,
        )

    @staticmethod
    def bridge_world_change(
        *,
        session_key: str,
        change_summary: str,
        snapshot_diff: dict[str, Any] | None = None,
        confidence: float = 0.0,
    ) -> RuntimeEvent | None:
        """Normalise a world-state change into a ``RuntimeEvent``."""
        if not session_key or not change_summary:
            return None
        digest = _safe_digest(session_key, change_summary)
        return RuntimeEvent(
            event_id=f"runtime_event:{digest}",
            session_key=session_key,
            event_type="world_change",
            source="world_state",
            summary=_trim_text(change_summary, max_chars=320),
            confidence=max(0.0, min(1.0, float(confidence))),
            payload={"snapshot_diff": dict(snapshot_diff or {})},
        )

    @staticmethod
    def bridge_user_message(
        *,
        session_key: str,
        text: str,
        role: str = "user",
    ) -> RuntimeEvent | None:
        """Normalise a user message into a ``RuntimeEvent``.

        If *text* contains a correction pattern the returned event uses
        ``event_type="user_message"``; the caller (or the bridge to trigger
        function) is responsible for mapping correction patterns to
        ``trigger_type="user_correction"``.
        """
        cleaned = _trim_text(text, max_chars=800)
        if not cleaned or not session_key:
            return None
        if any(excluded in cleaned for excluded in _USER_CORRECTION_EXCLUDES):
            return None
        digest = _safe_digest(session_key, cleaned)
        return RuntimeEvent(
            event_id=f"runtime_event:{digest}",
            session_key=session_key,
            event_type="user_message",
            source="user_input",
            owner_id=session_key,
            summary=_trim_text(cleaned, max_chars=320),
            confidence=0.7,
            payload={
                "role": role,
                "text_preview": cleaned[:200],
                "matched_correction": next(
                    (p for p in _USER_CORRECTION_PATTERNS if p in cleaned),
                    None,
                ),
            },
        )

    @staticmethod
    def bridge_device_event(
        *,
        session_key: str,
        device_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEvent | None:
        """Normalise a device / telemetry event into a ``RuntimeEvent``.

        *event_type* must be one of the recognised ``_DEVICE_EVENT_TYPES``
        or the bridge returns ``None``.
        """
        if not session_key or not device_id or not event_type:
            return None
        et = str(event_type).strip().lower()
        if et not in _DEVICE_EVENT_TYPES:
            return None
        digest = _safe_digest(session_key, device_id, et)
        return RuntimeEvent(
            event_id=f"runtime_event:{digest}",
            session_key=session_key,
            event_type="device_event",
            source="device_orchestrator",
            scope=device_id,
            summary=f"device {device_id}: {et}",
            confidence=0.5,
            payload=dict(payload or {}),
        )

    # ── dispatch ───────────────────────────────────────────────────

    def process(
        self,
        source: Any,
        *,
        session_key: str = "",
        **kwargs: Any,
    ) -> RuntimeEvent | None:
        """Dispatch *source* to the correct adapter and return a ``RuntimeEvent``.

        ``RuntimeEvent`` can be further converted to a ``MetaTrigger`` via
        ``bridge_runtime_event_to_trigger()``.
        """
        if not self.enabled:
            return None

        if isinstance(source, CognitiveEvent):
            return self.bridge_cognitive_event(source)

        if isinstance(source, str):
            # source is an adapter hint: tool_result, world_change, ...
            adapter_map: dict[str, Any] = {
                "tool_result": self.bridge_tool_result,
                "world_change": self.bridge_world_change,
                "user_message": self.bridge_user_message,
                "device_event": self.bridge_device_event,
            }
            adapter = adapter_map.get(source)
            if adapter is not None:
                kw = {**kwargs, "session_key": kwargs.get("session_key") or session_key}
                return adapter(**kw)

        return None
