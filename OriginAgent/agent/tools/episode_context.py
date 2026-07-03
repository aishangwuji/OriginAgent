"""Tool for retrieving full episode conversation context.

Returns raw messages from a specified episode, formatted for inclusion
in the next LLM turn. Designed for agent-driven context retrieval.
"""

from __future__ import annotations

from typing import Any

from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.context import ContextAware, RequestContext
from OriginAgent.session.manager import SessionManager


class EpisodeContextTool(Tool, ContextAware):
    """Fetch raw messages from a conversation episode by ID."""

    def __init__(self, sessions: SessionManager) -> None:
        self._sessions = sessions
        self._session_key: str = ""

    def set_context(self, ctx: RequestContext) -> None:
        self._session_key = ctx.session_key or ""

    @property
    def name(self) -> str:
        return "episode_context"

    @property
    def description(self) -> str:
        return (
            "Retrieve the full raw message history for a specific conversation "
            "episode. Use this when you need to review what was discussed in an "
            "earlier topic. The result includes timestamped user, assistant, "
            "and tool messages formatted as a readable transcript."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "episode_id": {
                    "type": "string",
                    "description": "The episode ID to retrieve messages for.",
                },
            },
            "required": ["episode_id"],
        }

    async def execute(self, episode_id: str, **kwargs: Any) -> str:
        if not self._session_key:
            return "Error: no active session"

        session = self._sessions.get_or_create(self._session_key)

        episode = next(
            (ep for ep in session.episodes if ep.episode_id == episode_id),
            None,
        )
        if episode is None:
            return f"Episode \"{episode_id}\" not found in current session."

        history = session.get_episode_history(
            episode_id,
            include_timestamps=True,
        )
        if not history:
            return f"Episode \"{episode_id}\" exists but has no messages."

        label = episode.label or "(untitled)"
        count = episode.message_count if hasattr(episode, 'message_count') else len(history)
        lines: list[str] = [
            f"--- Episode: \"{label}\" ({count} messages, {episode.status}) ---",
            "",
        ]
        for msg in history:
            role = msg["role"]
            content = msg.get("content", "") or ""
            if content.startswith("[Message Time: "):
                idx = content.index("]")
                content = content[idx + 2:]  # skip "] "
            tag = role.upper()
            if content:
                lines.append(f"[{tag}] {content[:500]}")
            else:
                lines.append(f"[{tag}] (tool call / empty)")

        lines.append(f"--- End of episode \"{label}\" ---")
        return "\n".join(lines)
