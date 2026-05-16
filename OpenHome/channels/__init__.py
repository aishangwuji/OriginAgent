"""Chat channels module with plugin architecture."""

from OpenHome.channels.base import BaseChannel
from OpenHome.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
