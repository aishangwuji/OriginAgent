"""Tool for agent-driven episode management.

Allows the agent to explicitly close the current topic episode or start
a new one, enabling the agent to manage conversation topic boundaries.
"""

from __future__ import annotations

from typing import Any

from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.context import ContextAware, RequestContext
from OriginAgent.session.manager import SessionManager


class CloseEpisodeTool(Tool, ContextAware):
    """Close the current topic episode and optionally start a new one."""

    def __init__(self, sessions: SessionManager) -> None:
        self._sessions = sessions
        self._session_key: str = ""

    def set_context(self, ctx: RequestContext) -> None:
        self._session_key = ctx.session_key or ""

    @property
    def name(self) -> str:
        return "close_episode"

    @property
    def description(self) -> str:
        return (
            "Close the current conversation topic and optionally start a new "
            "one with a descriptive label. Call this when you detect that the "
            "user has shifted to a significantly different topic."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": (
                        "Optional short label for the new topic episode. "
                        "E.g., 'database schema', 'user authentication'"
                    ),
                },
            },
        }

    async def execute(self, label: str = "", **kwargs: Any) -> str:
        if not self._session_key:
            return "Error: no active session"

        session = self._sessions.get_or_create(self._session_key)

        if label:
            session.start_new_episode(label)
            return f"Closed current topic and started new episode: \"{label}\""
        else:
            session.close_active_episode()
            return (
                "Closed current topic episode. The next user message will "
                "automatically begin a new topic."
            )
