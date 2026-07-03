"""Shared per-chat streaming accumulator (extracted from discord.py / telegram.py).

Eliminates the duplicated _StreamBuf dataclass across channel implementations (A2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

_MessageRef = TypeVar("_MessageRef")


@dataclass
class StreamBuffer(Generic[_MessageRef]):
    """Per-chat streaming accumulator for progressive message editing.

    Channels that support message editing during streaming (Discord, Telegram)
    use this buffer to track the current stream text and rate-limit edits.

    Type parameter ``_MessageRef`` is the platform-specific message handle
    (e.g. ``discord.Message``, ``int`` for Telegram message_id).
    """

    text: str = ""
    message_ref: _MessageRef | None = None
    last_edit: float = 0.0
    stream_id: str | None = None


# Default float type for channels that don't need a message reference.
StreamBufferAny = StreamBuffer[Any]
