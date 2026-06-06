"""Canonical data models for the nearline layered memory system."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

MessageRole = Literal["user", "assistant", "tool", "system"]
MemCellKind = Literal["conversation_turn", "tool_span", "mixed_turn"]
MemoryObjectKind = Literal["episode", "foresight", "agent_case", "profile"]


@dataclass(frozen=True)
class CanonicalMessage:
    """Normalized persisted message used as the nearline memory boundary input."""

    message_id: str
    session_key: str
    role: MessageRole
    content: str
    timestamp: str
    channel: str = ""
    chat_id: str = ""
    sender_id: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MemCell:
    """Smallest nearline memory unit before semantic extraction."""

    memcell_id: str
    session_key: str
    kind: MemCellKind
    started_at: str
    ended_at: str
    message_ids: list[str] = field(default_factory=list)
    roles: list[MessageRole] = field(default_factory=list)
    content: str = ""
    messages: list[CanonicalMessage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["messages"] = [message.to_json() for message in self.messages]
        return payload


@dataclass(frozen=True)
class EpisodeRecord:
    """Remembered user-facing conversation experience."""

    episode_id: str
    memcell_id: str
    session_key: str
    owner_id: str
    summary: str
    content: str
    timestamp: str
    source_message_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ForesightRecord:
    """Remembered future-facing intent, commitment, or time window."""

    foresight_id: str
    memcell_id: str
    session_key: str
    owner_id: str
    content: str
    evidence: str = ""
    start_at: str | None = None
    end_at: str | None = None
    timestamp: str = ""
    source_message_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentCaseRecord:
    """Remembered agent-side execution experience for later reuse."""

    case_id: str
    memcell_id: str
    session_key: str
    agent_id: str
    task_intent: str
    approach: str
    outcome_summary: str = ""
    quality_score: float = 0.0
    timestamp: str = ""
    source_message_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProfileSnapshot:
    """Synthesized user profile state derived from nearline memory objects."""

    profile_id: str
    owner_id: str
    summary: str
    explicit_traits: list[str] = field(default_factory=list)
    implicit_traits: list[str] = field(default_factory=list)
    source_memcell_ids: list[str] = field(default_factory=list)
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MemoryLayerSummary:
    """Read-only summary surfaced to introspection and self-model views."""

    nearline_enabled: bool = False
    pipeline_enabled: bool = False
    profile_shadow_write_enabled: bool = False
    memcell_count: int = 0
    episode_count: int = 0
    foresight_count: int = 0
    agent_case_count: int = 0
    profile_count: int = 0
    last_memcell_at: str | None = None
    last_episode_at: str | None = None
    last_foresight_at: str | None = None
    last_agent_case_at: str | None = None
    last_profile_at: str | None = None
    latest_cursor: int = 0
    status: str = "disabled"
    object_kinds: list[MemoryObjectKind] = field(
        default_factory=lambda: ["episode", "foresight", "agent_case", "profile"]
    )

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def now_iso() -> str:
    return datetime.utcnow().isoformat()
