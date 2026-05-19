"""Outbound helpers for WebSocket/WebUI turn status."""

from __future__ import annotations

import time
from typing import Any

from OpenHome.bus.events import InboundMessage, OutboundMessage
from OpenHome.bus.queue import MessageBus

_WEBSOCKET_TURN_WALL_STARTED_AT: dict[str, float] = {}


def websocket_turn_wall_started_at(chat_id: str) -> float | None:
    """Return the wall-clock start time for an active WebSocket turn."""
    return _WEBSOCKET_TURN_WALL_STARTED_AT.get(chat_id)


def websocket_turn_latency_ms(chat_id: str) -> int | None:
    """Return elapsed wall time for the current WebSocket turn, if known."""
    started_at = websocket_turn_wall_started_at(chat_id)
    if started_at is None:
        return None
    return max(0, int((time.time() - started_at) * 1000))


async def publish_turn_run_status(bus: MessageBus, msg: InboundMessage, status: str) -> None:
    """Notify WebSocket clients that a turn is running or idle."""
    if msg.channel != "websocket":
        return
    chat_id = str(msg.chat_id)
    meta: dict[str, Any] = {
        **dict(msg.metadata or {}),
        "_goal_status": True,
        "goal_status": status,
    }
    if status == "running":
        started_at = time.time()
        meta["started_at"] = started_at
        _WEBSOCKET_TURN_WALL_STARTED_AT[chat_id] = started_at
    else:
        _WEBSOCKET_TURN_WALL_STARTED_AT.pop(chat_id, None)
    await bus.publish_outbound(
        OutboundMessage(
            channel=msg.channel,
            chat_id=chat_id,
            content="",
            metadata=meta,
        )
    )
