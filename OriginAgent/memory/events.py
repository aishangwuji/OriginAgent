"""Event contracts for the nearline layered memory pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from OriginAgent.memory.models import AgentCaseRecord, EpisodeRecord, ForesightRecord, MemCell

MemoryEventName = Literal[
    "memcell_created",
    "episode_extracted",
    "foresight_extracted",
    "agent_case_extracted",
    "profile_refresh_requested",
]


@dataclass(frozen=True)
class MemoryEvent:
    event_name: MemoryEventName
    session_key: str
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MemCellCreated(MemoryEvent):
    memcell: MemCell | None = None

    def __init__(
        self,
        *,
        session_key: str,
        timestamp: str,
        memcell: MemCell,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        object.__setattr__(self, "event_name", "memcell_created")
        object.__setattr__(self, "session_key", session_key)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "memcell", memcell)
        object.__setattr__(self, "metadata", dict(metadata or {}))


@dataclass(frozen=True)
class EpisodeExtracted(MemoryEvent):
    episode: EpisodeRecord | None = None

    def __init__(
        self,
        *,
        session_key: str,
        timestamp: str,
        episode: EpisodeRecord,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        object.__setattr__(self, "event_name", "episode_extracted")
        object.__setattr__(self, "session_key", session_key)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "episode", episode)
        object.__setattr__(self, "metadata", dict(metadata or {}))


@dataclass(frozen=True)
class ForesightExtracted(MemoryEvent):
    foresight: ForesightRecord | None = None

    def __init__(
        self,
        *,
        session_key: str,
        timestamp: str,
        foresight: ForesightRecord,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        object.__setattr__(self, "event_name", "foresight_extracted")
        object.__setattr__(self, "session_key", session_key)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "foresight", foresight)
        object.__setattr__(self, "metadata", dict(metadata or {}))


@dataclass(frozen=True)
class AgentCaseExtracted(MemoryEvent):
    agent_case: AgentCaseRecord | None = None

    def __init__(
        self,
        *,
        session_key: str,
        timestamp: str,
        agent_case: AgentCaseRecord,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        object.__setattr__(self, "event_name", "agent_case_extracted")
        object.__setattr__(self, "session_key", session_key)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "agent_case", agent_case)
        object.__setattr__(self, "metadata", dict(metadata or {}))


@dataclass(frozen=True)
class ProfileRefreshRequested(MemoryEvent):
    owner_id: str = ""
    reason: str = ""
    source_memcell_ids: list[str] = field(default_factory=list)

    def __init__(
        self,
        *,
        session_key: str,
        timestamp: str,
        owner_id: str,
        reason: str,
        source_memcell_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        object.__setattr__(self, "event_name", "profile_refresh_requested")
        object.__setattr__(self, "session_key", session_key)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "owner_id", owner_id)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "source_memcell_ids", list(source_memcell_ids or []))
        object.__setattr__(self, "metadata", dict(metadata or {}))
