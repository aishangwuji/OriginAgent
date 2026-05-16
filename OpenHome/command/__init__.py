"""Slash command routing and built-in handlers."""

from OpenHome.command.builtin import register_builtin_commands
from OpenHome.command.router import CommandContext, CommandRouter

__all__ = ["CommandContext", "CommandRouter", "register_builtin_commands"]
