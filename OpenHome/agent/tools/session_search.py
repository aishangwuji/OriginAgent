"""Tool wrapper for searching persisted OpenHome conversation history."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from OpenHome.agent.tools.base import Tool
from OpenHome.agent.tools.schema import ArraySchema, IntegerSchema, StringSchema, tool_parameters_schema
from OpenHome.session.search import SessionSearchService


class SessionSearchTool(Tool):
    """Read-only structured search over session/history JSONL sources."""

    def __init__(self, workspace: Path):
        self._workspace = Path(workspace)
        self._service = SessionSearchService(self._workspace)

    @property
    def name(self) -> str:
        return "session_search"

    @property
    def description(self) -> str:
        return (
            "Search previous OpenHome conversations, memory/history archives, and WebUI "
            "transcripts using case-insensitive literal text matching. Use this for "
            "questions about prior discussions, previous plans, or what was said before; "
            "use grep for arbitrary project files. This is not semantic search: if no "
            "results appear, try alternate keywords or a wider since/until range. Prefer "
            "supplying since/until to reduce history scanning."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            required=["query"],
            additional_properties=False,
            query=StringSchema(
                "Literal text to search for in historical conversation records.",
                min_length=1,
                max_length=500,
            ),
            roles=ArraySchema(
                StringSchema(
                    "Role filter.",
                    enum=["user", "assistant", "tool", "system", "archive"],
                ),
                description="Optional roles to include.",
                max_items=8,
            ),
            sources=ArraySchema(
                StringSchema(
                    "History source.",
                    enum=["sessions", "history", "webui"],
                ),
                description="Optional sources to search. Defaults to all sources.",
                max_items=3,
            ),
            session_key=StringSchema(
                "Optional exact session key such as 'websocket:chat1'.",
                max_length=200,
            ),
            channel=StringSchema(
                "Optional channel filter, used with chat_id when known.",
                max_length=80,
            ),
            chat_id=StringSchema(
                "Optional chat id filter, used with channel when known.",
                max_length=160,
            ),
            since=StringSchema(
                "Optional inclusive start time, YYYY-MM-DD or ISO datetime.",
                max_length=80,
            ),
            until=StringSchema(
                "Optional inclusive end time, YYYY-MM-DD or ISO datetime.",
                max_length=80,
            ),
            limit=IntegerSchema(
                description="Maximum results to return. Defaults to 10; values above 50 are clamped.",
                minimum=1,
            ),
        )

    async def execute(
        self,
        *,
        query: str,
        roles: list[str] | None = None,
        sources: list[str] | None = None,
        session_key: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return self._service.search(
            query=query,
            roles=roles,
            sources=sources,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            since=since,
            until=until,
            limit=limit,
        )
