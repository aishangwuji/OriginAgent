"""Meta-cognition subsystem unified facade.

Internal structure (10 files):
  runtime   — trigger collection bounded by turn lifecycle
  reflector — turn-end structured artifact generation
  regulator — online monitoring, depth control, budget scheduling
  coordinator — lifecycle owner (turn start/end, scan scheduling)
  models    — structured contracts (MetaTrigger, ReflectionRecord, etc.)
  patterns  — rule-based consolidation into ErrorPattern
  triggers  — builder functions for standard trigger types
  audit     — append-only sidecar audit ledger
  redact    — shared redaction/trimming helpers
  evolution_bridge — patterns → governed evolution signals

External consumers should ONLY import from this facade.
Internal files may import each other directly.
"""

from __future__ import annotations

from typing import Any

from OriginAgent.agent.meta_cognition_audit import JsonlMetaCognitionAuditLedger
from OriginAgent.agent.meta_cognition_coordinator import MetaCognitionCoordinator
from OriginAgent.agent.meta_cognition_models import MetaTrigger
from OriginAgent.agent.meta_cognition_reflector import MetaCognitionReflector
from OriginAgent.agent.meta_cognition_regulator import MetaCognitionRegulator
from OriginAgent.agent.meta_cognition_runtime import MetaCognitionRuntime


class MetaCognitionFacade:
    """Unified entry point for the meta-cognition subsystem.

    Wraps the 10-file internal decomposition behind a stable API.
    External callers (AgentLoop, AgentRuntime) should depend on this
    facade rather than importing individual meta_cognition_* modules.

    API surface:
      observe()   — record a meta-cognition trigger
      reflect()   — run end-of-turn structured reflection
      regulate()  — check depth/budget and recommend throttle action
      start_turn() / end_turn() — turn lifecycle hooks
      status()    — runtime health and summary
    """

    def __init__(
        self,
        *,
        runtime: MetaCognitionRuntime | None = None,
        reflector: MetaCognitionReflector | None = None,
        regulator: MetaCognitionRegulator | None = None,
        coordinator: MetaCognitionCoordinator | None = None,
        config: Any = None,
    ):
        self._coordinator = coordinator or MetaCognitionCoordinator(
            runtime=runtime,
            reflector=reflector,
            regulator=regulator,
            config=config,
        )

    # ── Turn lifecycle ──────────────────────────────────────────────

    def start_turn(self, turn_id: str) -> None:
        self._coordinator.start_turn(turn_id)

    def end_turn(self, turn_id: str) -> None:
        self._coordinator.end_turn(turn_id)

    # ── Observation ─────────────────────────────────────────────────

    def observe(self, trigger: MetaTrigger, *, turn_id: str | None = None) -> Any:
        """Record a meta-cognition trigger and update internal state."""
        return self._coordinator.record_trigger(trigger, turn_id=turn_id)

    # ── Reflection ──────────────────────────────────────────────────

    def reflect(self, *, turn_id: str | None = None) -> Any:
        """Run end-of-turn structured reflection via the coordinator."""
        return self._coordinator.perform_reflection(turn_id=turn_id)

    # ── Regulation ──────────────────────────────────────────────────

    def regulate(self) -> Any:
        """Check depth/budget and return a throttle recommendation."""
        return self._coordinator.regulate()

    # ── Status ──────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Return runtime health and summary information."""
        return self._coordinator.status()


__all__ = [
    "JsonlMetaCognitionAuditLedger",
    "MetaCognitionCoordinator",
    "MetaCognitionFacade",
    "MetaCognitionReflector",
    "MetaCognitionRegulator",
    "MetaCognitionRuntime",
    "MetaTrigger",
]
