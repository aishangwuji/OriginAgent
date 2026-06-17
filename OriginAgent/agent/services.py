from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentServiceContainer:
    """Read-only container for stable AgentLoop services."""

    sessions: Any
    tools: Any
    context: Any
    working_memory: Any
    world_state: Any
    subagents: Any
    background_review: Any
    curator: Any
    auto_compact: Any
    action_planner: Any
    session_search_index: Any
    provider: Any
    workspace: Any
    model: Any
    commands: Any
    turn_pipeline: Any
    cognitive_runtime: Any
    consolidator: Any
    dream: Any
    memory_governance: Any
    rolling_episode_compaction: Any
    introspection: Any
    nearline_memory: Any
    domains: Any = None

    @classmethod
    def from_mapping(
        cls,
        values: dict[str, Any],
        *,
        commands: Any,
        turn_pipeline: Any,
        cognitive_runtime: Any,
    ) -> "AgentServiceContainer":
        return cls(
            sessions=values["sessions"],
            tools=values["tools"],
            context=values["context"],
            working_memory=values["working_memory"],
            world_state=values["world_state"],
            subagents=values["subagents"],
            background_review=values["background_review"],
            curator=values["curator"],
            auto_compact=values["auto_compact"],
            action_planner=values["action_planner"],
            session_search_index=values["session_search_index"],
            provider=values["provider"],
            workspace=values["workspace"],
            model=values["model"],
            commands=commands,
            turn_pipeline=turn_pipeline,
            cognitive_runtime=cognitive_runtime,
            consolidator=values["consolidator"],
            dream=values["dream"],
            memory_governance=values["memory_governance"],
            rolling_episode_compaction=values["rolling_episode_compaction"],
            introspection=values["introspection"],
            nearline_memory=values["nearline_memory"],
            domains=values.get("domain_packs"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "sessions": self.sessions,
            "tools": self.tools,
            "context": self.context,
            "working_memory": self.working_memory,
            "world_state": self.world_state,
            "subagents": self.subagents,
            "background_review": self.background_review,
            "curator": self.curator,
            "auto_compact": self.auto_compact,
            "action_planner": self.action_planner,
            "session_search_index": self.session_search_index,
            "provider": self.provider,
            "workspace": self.workspace,
            "model": self.model,
            "commands": self.commands,
            "turn_pipeline": self.turn_pipeline,
            "cognitive_runtime": self.cognitive_runtime,
            "consolidator": self.consolidator,
            "dream": self.dream,
            "memory_governance": self.memory_governance,
            "rolling_episode_compaction": self.rolling_episode_compaction,
            "introspection": self.introspection,
            "nearline_memory": self.nearline_memory,
            "domain_packs": self.domains,
        }
