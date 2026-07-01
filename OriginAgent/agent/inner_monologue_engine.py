"""InnerMonologueEngine — structured inner monologue from BDI deliberation output.

The engine formalises what the BDI ``DeliberationEngine`` already produces into
a structured ``MonologueFrame`` contract, enriches the open ``ThoughtFrame``
in the ``ThoughtSubstrate``, and provides deterministic confidence/uncertainty
derivation without extra LLM calls (Phase 1).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from OriginAgent.agent.inner_monologue_models import MonologueFrame
from OriginAgent.agent.thought_substrate_store import ThoughtSubstrate
from OriginAgent.bdi.models import DeliberationResult, Desire, DesireStatus

# Regex for detecting uncertainty markers in reasoning text.
_UNCERTAINTY_RE = re.compile(r"\b(not sure|maybe|possibly|unclear|uncertain)\b", re.IGNORECASE)


# ── Bridge: DeliberationResult → MonologueFrame ──────────────────────────────


def derive_confidence_and_uncertainty(
    result: DeliberationResult,
    active_desires: list[Desire] | None = None,
) -> tuple[float, list[str]]:
    """Derive confidence score and uncertainty flags from a BDI cycle result.

    Deterministic derivation using only the BDI output fields — no extra LLM
    call needed.
    """
    flags: list[str] = []
    formed = result.intentions_formed
    desires = list(active_desires or [])
    reasoning = (result.reasoning or "").strip()

    # ── confidence ─────────────────────────────────────────────────
    base = 0.5
    if formed > 0:
        base += 0.3
    if formed >= 2:
        base += 0.1
    if formed == 0 and desires:
        base -= 0.2
    confidence = max(0.0, min(1.0, base))

    # ── uncertainty flags ──────────────────────────────────────────
    if formed == 0 and desires:
        flags.append("no_action_planned")
    if not reasoning or len(reasoning) < 20:
        flags.append("empty_reasoning")
    if len(desires) >= 3:
        flags.append("conflicting_desires")
    if _UNCERTAINTY_RE.search(reasoning):
        flags.append("reasoning_uncertain")
    if not desires:
        flags.append("no_desires")

    return confidence, flags


def bridge_deliberation_to_monologue(
    result: DeliberationResult,
    session_key: str,
    *,
    observation_summary: str = "",
    active_goal: str = "",
    active_desires: list[Desire] | None = None,
) -> MonologueFrame:
    """Convert a BDI ``DeliberationResult`` into a structured ``MonologueFrame``.

    Field mapping:

    =================================  ============================
    ``MonologueFrame`` field           Source
    =================================  ============================
    ``cycle_id``                       ``result.cycle_id``
    ``session_key``                    parameter
    ``observation_summary``            parameter (fallback from reasoning)
    ``active_goal``                    parameter or first desire content
    ``candidate_hypotheses``           ``[intent.reasoning for ...]``
    ``intended_strategy``              ``result.reasoning``
    ``verification_needs``             ``[]`` (Phase 1 placeholder)
    ``simulation_requests``            ``[]`` (Phase 1 placeholder)
    ``confidence``                     ``derive_confidence_and_uncertainty()``
    ``uncertainty_flags``              ``derive_confidence_and_uncertainty()``
    ``self_critique``                  ``""`` (Phase 1 placeholder)
    ``created_at``                     now
    =================================  ============================
    """
    desires = list(active_desires or [])
    confidence, flags = derive_confidence_and_uncertainty(result, active_desires=desires)

    # Strategy from reasoning
    strategy = (result.reasoning or "").strip()

    # Hypotheses from individual intentions' reasoning
    hypotheses: list[str] = []
    for intent in result.intentions:
        if intent.reasoning:
            hypotheses.append(intent.reasoning)

    # Goal from parameter or first desire
    goal = active_goal
    if not goal and desires:
        goal = desires[0].content

    # Observation summary from parameter or fallback
    obs = observation_summary
    if not obs:
        obs = strategy or f"BDI cycle: {result.desires_evaluated} desires, {result.intentions_formed} intentions"

    return MonologueFrame(
        cycle_id=result.cycle_id,
        session_key=session_key,
        observation_summary=obs,
        active_goal=goal,
        candidate_hypotheses=hypotheses,
        intended_strategy=strategy,
        verification_needs=[],
        simulation_requests=[],
        confidence=confidence,
        uncertainty_flags=flags,
        self_critique="",
        created_at=_utcnow_iso(),
    )


# ── InnerMonologueEngine service ─────────────────────────────────────────────


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class InnerMonologueEngine:
    """Orchestrates BDI deliberation → MonologueFrame → ThoughtSubstrate enrichment.

    Designed to be wired as the ``on_cycle_complete`` callback on the
    ``DeliberationEngine`` so that every BDI cycle automatically produces
    a structured monologue frame and enriches the current thought substrate.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        deliberation_engine: Any | None = None,
        substrate: ThoughtSubstrate | None = None,
        desire_store: Any | None = None,
        enabled: bool = True,
    ) -> None:
        self._workspace = Path(workspace)
        self._engine = deliberation_engine
        self._substrate = substrate
        self._desire_store = desire_store
        self._enabled = enabled

    # ── properties ─────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── public API ─────────────────────────────────────────────────

    async def on_bdi_cycle(self, result: DeliberationResult) -> MonologueFrame | None:
        """Called after each BDI deliberation cycle completes.

        Bridges the result to a ``MonologueFrame`` and enriches the open
        ``ThoughtFrame`` in the substrate.
        """
        if not self._enabled:
            return None

        active_desires: list[Desire] | None = None
        if self._desire_store is not None:
            try:
                active_desires = [
                    d for d in self._desire_store.list_deliberable()
                    if d.status == DesireStatus.ACTIVE
                ]
            except Exception:
                active_desires = []

        frame = bridge_deliberation_to_monologue(
            result=result,
            session_key="bdi:deliberation",
            observation_summary=f"BDI cycle: {result.desires_evaluated} desires, {result.intentions_formed} intentions",
            active_desires=active_desires,
        )

        if self._substrate is not None:
            try:
                self._substrate.open_frame(
                    session_key="bdi:deliberation",
                    trigger_refs=[result.cycle_id],
                    observation_summary=frame.observation_summary,
                    active_goal=frame.active_goal,
                    candidate_hypotheses=frame.candidate_hypotheses,
                    intended_strategy=frame.intended_strategy,
                    verification_needs=frame.verification_needs,
                    simulation_requests=frame.simulation_requests,
                    confidence=frame.confidence,
                    uncertainty_flags=frame.uncertainty_flags,
                )
            except Exception:
                from loguru import logger
                logger.opt(exception=True).warning("InnerMonologue: substrate open_frame failed")

        return frame

    async def cycle(
        self,
        session_key: str = "bdi:deliberation",
        *,
        observation_summary: str = "",
        trigger_refs: list[str] | None = None,
    ) -> MonologueFrame | None:
        """Run a full inner-monologue cycle: BDI → bridge → substrate."""
        if not self._enabled or self._engine is None:
            return None
        _ = trigger_refs  # unused in Phase 1
        result = await self._engine.run_cycle()
        return await self.on_bdi_cycle(result)

    def runtime_status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "has_deliberation_engine": self._engine is not None,
            "has_substrate": self._substrate is not None,
            "has_desire_store": self._desire_store is not None,
        }
