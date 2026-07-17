"""EvaluateActionTool — LLM meta-cognitive assessment of action→result.

This is the **evaluation layer** of the action-result causal chain. After
the system captures an action's result (via ``action_trace``), the Agent
uses this tool to explicitly assess whether that action advanced its goal.

Architectural mapping (continued from action_trace.py):
- **BDI**: this is the Belief update — the Agent revises its belief about
  task progress based on the observed action result.
- **ACT-R**: this is the production-matching step — the Agent decides
  whether the current chunk (action result) matches the goal buffer's
  expectations, producing a new declarative chunk (the evaluation).
- **Soar**: this is the impasse detection — if the Agent detects
  ``outcome=blocked`` or ``outcome=regressed``, it's equivalent to a Soar
  impasse that triggers sub-state reasoning (which in our architecture
  maps to transitioning task_state to BLOCKED).
- **EPIC**: this is the cognitive processor's decision — after the
  perceptual processor (action_trace) captures the motor result, the
  cognitive processor (this tool) decides what it means for the goal.

The complete causal chain is:

    action (tool_call) → result (tool output)
        → [action_trace: automatic capture]
        → evaluation (this tool: Agent's assessment)
        → [task_state transition: state machine update]

Each link has a unique ID (action_id, evaluation_id) so the full chain
is traceable across turns and sessions.

Design decisions (rules 5 / 7 / 32 / 33):
- ``action_id`` defaults to the most recent action in the trace — this
  is a convenience for the common case (evaluate the action just taken).
  The Agent can also reference a specific action_id for delayed evaluation.
- Unknown action_ids are NOT rejected — the trace may have been FIFO-
  capped, or the Agent may reference a cross-turn action. We record the
  evaluation and let the Agent decide validity (rule 32: no speculative
  validation for scenarios that don't need blocking).
- ``goal_alignment`` is optional, defaulting to ``"unclear"`` — not every
  action has a clear goal alignment (e.g., exploratory actions), and
  forcing the Agent to classify would produce noise.
- ``once_per_turn = False`` — the Agent may evaluate multiple actions
  in a single turn (e.g., a batch of tool calls).
- State is re-read from ``session.metadata`` on every call (rule 5/7).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from OriginAgent.agent.action_trace import get_recent_actions
from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.context import ContextAware, RequestContext
from OriginAgent.session.manager import SessionManager

# ─── Constants ──────────────────────────────────────────────────────────────

_EVAL_METADATA_KEY = "_action_evaluations"

# FIFO cap. 50 evaluations ≈ 10 recent task cycles (each cycle ~5 actions).
# Matches _MAX_TRACE in action_trace.py for symmetry.
_MAX_EVALUATIONS = 50

# Valid outcome values — the Agent's assessment of whether the action
# advanced its goal.
#
# Why these five (rule 32: no speculative values):
# - progressed: action moved the task forward (success).
# - no_progress: action completed but didn't advance (neutral, e.g., info
#   gathering that confirmed current state).
# - regressed: action made things worse (e.g., overwrote a needed file).
# - blocked: action was denied or hit a barrier (maps to task_state BLOCKED).
# - uncertain: Agent cannot determine outcome (insufficient info, ambiguous
#   result).
_VALID_OUTCOMES: frozenset[str] = frozenset({
    "progressed", "no_progress", "regressed", "blocked", "uncertain",
})

# Valid goal_alignment values — whether the action was relevant to the goal.
#
# Why these three (rule 32):
# - aligned: action directly serves the current goal.
# - misaligned: action is irrelevant or counter to the goal.
# - unclear: Agent cannot determine alignment (exploratory, side-effects).
_VALID_GOAL_ALIGNMENTS: frozenset[str] = frozenset({
    "aligned", "misaligned", "unclear",
})

_DEFAULT_GOAL_ALIGNMENT = "unclear"


def _utcnow_iso() -> str:
    """ISO 8601 UTC timestamp for evaluation entries."""
    return datetime.now(timezone.utc).isoformat()


def _generate_evaluation_id() -> str:
    """Generate a unique evaluation_id: ``eval_`` + 8 hex chars.

    Uses uuid4 (random) — collision probability is negligible within a
    session's lifetime (50-entry FIFO cap makes collisions moot even if
    they occurred).
    """
    return f"eval_{uuid.uuid4().hex[:8]}"


class EvaluateActionTool(Tool, ContextAware):
    """LLM meta-cognitive assessment of an action's result.

    Called by the Agent after observing a tool's result. Records a
    structured evaluation (outcome + goal_alignment + assessment text)
    that can be referenced by ``task_state`` transitions to form the
    complete action→result→evaluation→state causal chain.
    """

    def __init__(self, sessions: SessionManager | None) -> None:
        self._sessions = sessions
        self._session_key: str | None = None

    # ─── ContextAware (production registration path) ──────────────────────
    def set_context(self, ctx: RequestContext) -> None:
        """Receive session_key from the runtime (called per turn)."""
        self._session_key = ctx.session_key

    # ─── Tool properties ──────────────────────────────────────────────────
    @property
    def name(self) -> str:
        return "evaluate_action"

    @property
    def description(self) -> str:
        return (
            "Assess the outcome of a recent action in the action-result "
            "causal chain. Call this after observing a tool's result to "
            "record whether it advanced your goal. This is your "
            "meta-cognitive evaluation layer — it creates an evaluation "
            "that task_state transitions can reference, forming the "
            "complete causal chain: action → result → evaluation → state. "
            f"Outcomes: {sorted(_VALID_OUTCOMES)} — 'progressed' (advanced "
            "goal), 'no_progress' (neutral, e.g., info gathering), "
            "'regressed' (made things worse), 'blocked' (denied or "
            "barrier-hit, maps to task_state BLOCKED), 'uncertain' "
            "(cannot determine). "
            f"Goal alignments: {sorted(_VALID_GOAL_ALIGNMENTS)} — "
            "'aligned' (serves goal), 'misaligned' (irrelevant), "
            "'unclear' (cannot determine). "
            "action_id defaults to the most recent action if omitted. "
            "goal_alignment defaults to 'unclear' if omitted."
        )

    @property
    def once_per_turn(self) -> bool:
        """Allow multiple calls per turn — one evaluation per action."""
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action_id": {
                    "type": "string",
                    "description": (
                        "The action_id to evaluate (from action_trace). "
                        "If omitted or empty, evaluates the most recent "
                        "action in the trace."
                    ),
                },
                "outcome": {
                    "type": "string",
                    "enum": sorted(_VALID_OUTCOMES),
                    "description": (
                        "Your assessment of whether this action advanced "
                        "the goal. 'progressed': moved forward; "
                        "'no_progress': neutral; 'regressed': moved "
                        "backward; 'blocked': denied or barrier-hit; "
                        "'uncertain': cannot determine."
                    ),
                },
                "assessment": {
                    "type": "string",
                    "description": (
                        "Free-text explanation of why you assessed this "
                        "outcome. Record what the action produced, "
                        "whether it matched expectations, and what it "
                        "means for the task. This is the causal reasoning "
                        "that justifies the evaluation."
                    ),
                },
                "goal_alignment": {
                    "type": "string",
                    "enum": sorted(_VALID_GOAL_ALIGNMENTS),
                    "description": (
                        "Whether this action was relevant to the current "
                        "goal. 'aligned': directly serves goal; "
                        "'misaligned': irrelevant or counter; 'unclear': "
                        "cannot determine. Defaults to 'unclear' if "
                        "omitted."
                    ),
                },
            },
            "required": ["outcome", "assessment"],
        }

    # ─── State access ──────────────────────────────────────────────────────
    def _get_session(self) -> Any:
        """Return the current session, or None if unavailable."""
        if self._sessions is None or not self._session_key:
            return None
        return self._sessions.get_or_create(self._session_key)

    def _resolve_action_id(self, action_id: str) -> tuple[str, str | None]:
        """Resolve the action_id to evaluate.

        If ``action_id`` is provided and non-empty, use it directly.
        Otherwise, look up the most recent action in the trace.

        Returns:
            A tuple of (resolved_action_id, error_message). If
            resolution fails, error_message is non-None.
        """
        if action_id:
            return action_id, None

        # Default: most recent action in trace.
        session = self._get_session()
        if session is None:
            return "", "Error: no active session to look up recent actions."

        recent = get_recent_actions(session, limit=1)
        if not recent:
            return "", (
                "Error: no action_trace found. Cannot evaluate without a "
                "recent action. Either call a tool first or provide an "
                "explicit action_id."
            )
        return recent[0].get("action_id", ""), None

    def _save_evaluation(self, entry: dict[str, Any]) -> None:
        """Persist an evaluation entry to session.metadata (FIFO capped)."""
        session = self._get_session()
        # Caller must have already validated session is not None.
        evals = session.metadata.get(_EVAL_METADATA_KEY)
        if evals is None:
            evals = []
            session.metadata[_EVAL_METADATA_KEY] = evals
        evals.append(entry)

        # FIFO cap: drop oldest entries beyond _MAX_EVALUATIONS.
        if len(evals) > _MAX_EVALUATIONS:
            del evals[: len(evals) - _MAX_EVALUATIONS]

    # ─── Tool execution ──────────────────────────────────────────────────
    async def execute(
        self,
        action_id: str = "",
        outcome: str = "",
        assessment: str = "",
        goal_alignment: str = "",
        **kwargs: Any,
    ) -> str:
        """Record an evaluation of a recent action.

        Returns a JSON string containing the evaluation_id and full
        evaluation entry, or a human-readable error string on failure.
        """
        if self._sessions is None or not self._session_key:
            return (
                "Error: evaluate_action unavailable — no active session. "
                "This tool requires a session-scoped context."
            )

        # Validate outcome (required).
        if not outcome:
            return (
                "Error: outcome is required. "
                f"Valid outcomes: {sorted(_VALID_OUTCOMES)}."
            )
        if outcome not in _VALID_OUTCOMES:
            return (
                f"Error: invalid outcome '{outcome}'. "
                f"Valid outcomes: {sorted(_VALID_OUTCOMES)}."
            )

        # Validate goal_alignment (optional, defaults to unclear).
        effective_alignment = goal_alignment or _DEFAULT_GOAL_ALIGNMENT
        if effective_alignment not in _VALID_GOAL_ALIGNMENTS:
            return (
                f"Error: invalid goal_alignment '{goal_alignment}'. "
                f"Valid values: {sorted(_VALID_GOAL_ALIGNMENTS)}."
            )

        # Resolve action_id (default to most recent).
        resolved_id, error = self._resolve_action_id(action_id)
        if error:
            return error

        # Build evaluation entry.
        evaluation_id = _generate_evaluation_id()
        entry: dict[str, Any] = {
            "evaluation_id": evaluation_id,
            "action_id": resolved_id,
            "outcome": outcome,
            "assessment": assessment or "",
            "goal_alignment": effective_alignment,
            "ts": _utcnow_iso(),
        }

        self._save_evaluation(entry)

        return json.dumps(entry, ensure_ascii=False, indent=2)
