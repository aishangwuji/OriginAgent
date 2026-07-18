"""Event types for the message bus."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Optional ``OutboundMessage.metadata`` key for structured, channel-agnostic UI
# payloads. Value is JSON-serializable with at least ``kind``; rich clients may
# render it and other channels may ignore unknown keys.
OUTBOUND_META_AGENT_UI = "_agent_ui"


@dataclass
class InboundMessage:
    """Message received from a chat channel.

    ``is_internal`` distinguishes user-originated messages from system-originated
    ones (cron jobs, cognitive nudges, scheduled reminders, subagent results).
    Internal messages are routed to a separate bus queue so a busy agent never
    starves real users behind a backlog of self-generated nudges.
    """

    channel: str  # telegram, discord, slack, whatsapp
    sender_id: str  # User identifier
    chat_id: str  # Chat/channel identifier
    content: str  # Message text
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
    session_key_override: str | None = None  # Optional override for thread-scoped sessions
    episode_id: str | None = None  # Active episode at time of message (set by bus)
    # False for real user messages; True for cron/cognitive/reminder/subagent.
    # Defaults to False so every existing call site keeps its current behavior.
    is_internal: bool = False

    @property
    def session_key(self) -> str:
        """Unique key for session identification."""
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Message to send to a chat channel.

    ``metadata`` can carry routing (``message_id``, ...), trace flags
    (``_progress``), and optional ``OUTBOUND_META_AGENT_UI`` blobs for rich
    clients; non-WebUI channels may ignore unknown keys.
    """

    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    buttons: list[list[str]] = field(default_factory=list)

