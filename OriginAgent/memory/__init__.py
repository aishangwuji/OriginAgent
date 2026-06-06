"""Nearline memory scaffolding for layered memory objects.

Phase 1 intentionally exposes only inert types, events, and read-only
store helpers. Runtime behavior continues to be driven by Dream and
FactStore until later phases wire the pipeline in.
"""

from OriginAgent.memory.events import (
    AgentCaseExtracted,
    EpisodeExtracted,
    ForesightExtracted,
    MemCellCreated,
    ProfileRefreshRequested,
)
from OriginAgent.memory.models import (
    AgentCaseRecord,
    CanonicalMessage,
    EpisodeRecord,
    ForesightRecord,
    MemCell,
    MemoryLayerSummary,
    ProfileSnapshot,
)
from OriginAgent.memory.profile import NearlineProfileService
from OriginAgent.memory.pipeline import NearlineMemoryPipeline, NearlinePipelineResult
from OriginAgent.memory.segmenter import canonicalize_session_messages, segment_memcells
from OriginAgent.memory.store import NearlineMemoryStore

__all__ = [
    "AgentCaseExtracted",
    "AgentCaseRecord",
    "CanonicalMessage",
    "canonicalize_session_messages",
    "EpisodeExtracted",
    "EpisodeRecord",
    "ForesightExtracted",
    "ForesightRecord",
    "MemCell",
    "MemCellCreated",
    "MemoryLayerSummary",
    "NearlineMemoryPipeline",
    "NearlineProfileService",
    "NearlinePipelineResult",
    "NearlineMemoryStore",
    "ProfileRefreshRequested",
    "ProfileSnapshot",
    "segment_memcells",
]
