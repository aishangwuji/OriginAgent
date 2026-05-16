"""Message bus module for decoupled channel-agent communication."""

from OpenHome.bus.events import InboundMessage, OutboundMessage
from OpenHome.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
