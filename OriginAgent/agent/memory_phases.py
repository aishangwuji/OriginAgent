"""Dream consolidation phases — extracted from Dream.run().

Each phase is independently testable with explicit inputs and return types.

Current extract structure:
- ``PhaseResult`` — Common result type for dream phases
- ``ForgettingResult`` — Result of forgetting maintenance + governed queue
- Phase 0 (episode summaries) → Dream._phase0_episode_summaries()
- Phase 1 (LLM fact proposal) — inline in Dream.run()
- Phase 2 (AgentRunner maintenance) — inline in Dream.run()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PhaseResult:
    """Result of a single dream consolidation phase."""

    success: bool = True
    skipped: bool = False
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ForgettingResult:
    """Combined result of forgetting maintenance and governed queue consumption."""

    forgetting_execution: dict[str, Any] = field(default_factory=dict)
    queue_result: dict[str, Any] = field(default_factory=dict)
    applied_count: int = 0
    consumed_count: int = 0

    @property
    def had_work(self) -> bool:
        return self.applied_count > 0 or bool(self.forgetting_execution)
