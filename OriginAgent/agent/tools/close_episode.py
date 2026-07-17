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
            "one with a descriptive label. Call this at most ONCE per turn — "
            "when you detect that the user has shifted to a significantly "
            "different topic. Do NOT call this tool repeatedly with different "
            "labels in the same turn."
        )

    @property
    def once_per_turn(self) -> bool:
        """close_episode is semantically once-per-turn.

        Calling it repeatedly with different labels is a death-loop pattern:
        the LLM keeps generating new labels, each call succeeds, and the
        idempotency check (tool_name+param_hash) is bypassed because the
        params differ. The runner enforces this flag by blocking subsequent
        calls after the first success in a turn.
        """
        return True

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

        # 注入守卫（Fix C）：在工具结果中追加"本 turn 不要再调用"提示，
        # 防止 LLM 在同一 turn 内反复调用 close_episode 换 label。
        _no_retry_hint = (
            "\n\n[This tool is once-per-turn. Do not call close_episode "
            "again in this turn — the episode has already been closed.]"
        )

        if label:
            session.start_new_episode(label)
            return f"Closed current topic and started new episode: \"{label}\"{_no_retry_hint}"
        else:
            session.close_active_episode()
            return (
                "Closed current topic episode. The next user message will "
                "automatically begin a new topic." + _no_retry_hint
            )
