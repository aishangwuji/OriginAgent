"""TaskStateTool — Agent-managed task state machine.

This is the concrete embodiment of the BDI / ACT-R / Soar / EPIC cognitive
architectures within OriginAgent. It tracks the **action-result causal
chain**: every time the Agent takes an action and observes its result, it
updates its *belief* about the current task's progress.

Architectural mapping:
- **BDI**: Belief = ``current_state``; Desire = ``SATISFIED``; Intention =
  the next action chosen given the current state.
- **ACT-R**: Declarative memory = ``history`` (chunks of past transitions
  with reasons); Procedural memory = ``_VALID_TRANSITIONS`` (production
  rules); Buffer = ``current_state`` (the active chunk).
- **Soar**: Problem space = the six states; Operator = a transition;
  Impasse = ``BLOCKED`` / ``WAITING``; Substate = ``RECOVERED`` (sub-goal
  activated to overcome the impasse); Chunking = history accumulation.
- **EPIC**: Motor processor = tool execution; Cognitive processor = this
  state machine; Perceptual processor = reading tool results to decide
  the next state.

Design decisions (rules 32 / 33 / 5 / 7 / 18 compliant):
- State is persisted to ``session.metadata["_task_state"]`` (NOT to
  ``priority_facts``) because ``priority_facts`` is consumed by
  ``MemoryGovernance`` (promotes to long-term memory), ``ActionAutomation``
  (treats as ``evidence_refs``) and ``context.build_working_memory_block``
  (renders to LLM). Task-state transitions are NOT facts, evidence, or
  working memory — they are the Agent's own meta-cognition and would
  pollute those consumers. ``session.metadata`` is session-scoped, not
  promoted, and not exposed via working_memory — correct semantics.
- The tool only needs ``SessionManager`` (same pattern as
  ``CloseEpisodeTool`` / ``EpisodeContextTool``).
- ``once_per_turn = False`` because a task's state may legitimately
  evolve multiple times within one turn (e.g.,
  ``EXPLORE → BLOCKED → RECOVERED`` after a denied-then-recovered tool).
- ``read_only = False`` → ``concurrency_safe = False`` → the runner
  serializes us, so no explicit lock is needed (rule 13).
- State is re-read from ``session.metadata`` on every call (rule 5: no
  stale snapshots; rule 7: shared state is re-read to avoid zombie
  references if the session object is rebuilt).
- Schema is minimal: only ``current_state`` / ``previous_state`` /
  ``history``. ``task_started_at`` etc. are NOT added (rule 32: no
  speculative fields without a known second consumer).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.context import ContextAware, RequestContext
from OriginAgent.session.manager import SessionManager

# ─── State machine definition ──────────────────────────────────────────────
# Encodes the 12 valid transitions of the action-result causal chain.
#
# Why BLOCKED → SATISFIED is INVALID:
#   BLOCKED means the Agent hit a barrier (e.g., tool denied by policy).
#   Skipping directly to SATISFIED would be confabulation — the Agent
#   would claim success without overcoming the barrier. It MUST go through
#   RECOVERED first, which proves the barrier was actually addressed.
#
# Why WAITING → SATISFIED is INVALID:
#   WAITING means the Agent needs external input (user response, async
#   callback). Skipping to SATISFIED would mean fabricating the user's
#   response. The Agent MUST return to EXPLORE first (process the input)
#   before it can declare satisfaction.
#
# Why RECOVERED → {EXPLORE, SATISFIED}:
#   After recovering from a barrier, the Agent either resumes exploration
#   (more work needed) or realizes the goal is now met (immediate
#   completion). Both are valid because RECOVERED proves the barrier was
#   addressed.
#
# Why terminal states (SATISFIED, ABANDONED) only allow EXPLORE:
#   A terminal state means the task is done (success or give-up). The only
#   valid next step is starting a NEW task, which always begins in EXPLORE.

_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "EXPLORE": frozenset({"BLOCKED", "WAITING", "SATISFIED", "ABANDONED"}),
    "BLOCKED": frozenset({"RECOVERED", "ABANDONED"}),
    "WAITING": frozenset({"EXPLORE", "ABANDONED"}),
    "RECOVERED": frozenset({"EXPLORE", "SATISFIED"}),
    "SATISFIED": frozenset({"EXPLORE"}),  # terminal — new task starts EXPLORE
    "ABANDONED": frozenset({"EXPLORE"}),  # terminal — new task starts EXPLORE
}

_ALL_STATES: frozenset[str] = frozenset(_VALID_TRANSITIONS.keys())

# FIFO cap on history. 20 entries is enough to see the recent causal chain
# (e.g., a full EXPLORE→BLOCKED→RECOVERED→SATISFIED cycle is 4 entries, so
# 20 entries ≈ 5 recent task cycles) without bloating session.metadata.
_MAX_HISTORY = 20

_METADATA_KEY = "_task_state"


def _utcnow_iso() -> str:
    """ISO 8601 UTC timestamp for history entries."""
    return datetime.now(timezone.utc).isoformat()


def _initial_state() -> dict[str, Any]:
    """Return a fresh task-state dict for a new session.

    Top-level ``reason`` mirrors the most recent transition's reason so
    callers can read "why are we in this state?" without scanning history.
    Initialized to empty string for a fresh session (no transition yet).
    """
    return {
        "current_state": "EXPLORE",
        "previous_state": None,
        "reason": "",
        "history": [],
    }


class TaskStateTool(Tool, ContextAware):
    """Agent-managed task state machine — the action-result causal chain.

    Persisted to ``session.metadata["_task_state"]``. Survives across
    tool instances because state lives in the session, not the tool.
    """

    def __init__(self, sessions: SessionManager | None) -> None:
        self._sessions = sessions
        self._session_key: str | None = None

    # ─── ContextAware (production registration path) ──────────────────────
    def set_context(self, ctx: RequestContext) -> None:
        """Receive session_key from the runtime (called per turn).

        Test stubs set ``_session_key`` directly; production code calls
        ``set_context`` via ``set_tools_runtime_context``.
        """
        self._session_key = ctx.session_key

    # ─── Tool properties ──────────────────────────────────────────────────
    @property
    def name(self) -> str:
        return "task_state"

    @property
    def description(self) -> str:
        return (
            "Track and update the current task's state in the action-result "
            "causal chain. This is your meta-cognitive state machine — call "
            "it to record transitions as you make progress toward the user's "
            "goal. States: EXPLORE (actively working, initial), BLOCKED (hit "
            "a barrier — tool denied or missing info), WAITING (need external "
            "input — user response or async callback), RECOVERED (overcame a "
            "barrier), SATISFIED (task complete, terminal), ABANDONED (gave "
            "up, terminal). Valid transitions: EXPLORE→{BLOCKED,WAITING,"
            "SATISFIED,ABANDONED}; BLOCKED→{RECOVERED,ABANDONED}; "
            "WAITING→{EXPLORE,ABANDONED}; RECOVERED→{EXPLORE,SATISFIED}; "
            "SATISFIED→EXPLORE (new task); ABANDONED→EXPLORE (new task). "
            "BLOCKED→SATISFIED is INVALID (must RECOVER first). "
            "WAITING→SATISFIED is INVALID (must return to EXPLORE first). "
            "Actions: 'get' (read current state), 'transition' (move to "
            "new_state with reason), 'reset' (clear back to EXPLORE)."
        )

    @property
    def once_per_turn(self) -> bool:
        """Allow multiple calls per turn — state may evolve within a turn.

        A single turn can legitimately contain
        ``EXPLORE → BLOCKED → RECOVERED`` (deny then recover), so this tool
        must remain callable. The runner's per-tool-call idempotency key
        (tool_name + param_hash) already prevents exact-duplicate calls.
        """
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "transition", "reset"],
                    "description": (
                        "Action to perform: 'get' reads current state "
                        "(no side effects); 'transition' moves to new_state; "
                        "'reset' clears state back to initial EXPLORE."
                    ),
                },
                "new_state": {
                    "type": "string",
                    "enum": sorted(_ALL_STATES),
                    "description": (
                        "Target state for 'transition' action. Must be a "
                        "valid transition from current_state. Ignored for "
                        "'get' and 'reset'."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Why this transition is being made (e.g., 'exec "
                        "denied by capability_snapshot_required', 'found "
                        "workaround via alternative tool', 'user provided "
                        "required input'). Recorded in history for audit."
                    ),
                },
                "action_id": {
                    "type": "string",
                    "description": (
                        "Optional: the action_id (from action_trace) that "
                        "triggered this transition. Links the state change "
                        "to the specific tool call that caused it, forming "
                        "the action→result→state causal chain. Omit if "
                        "the transition is not tied to a specific action."
                    ),
                },
                "evaluation_id": {
                    "type": "string",
                    "description": (
                        "Optional: the evaluation_id (from evaluate_action) "
                        "that justified this transition. Links the state "
                        "change to the Agent's meta-cognitive assessment, "
                        "forming the complete "
                        "action→result→evaluation→state causal chain. "
                        "Omit if the transition is not based on an explicit "
                        "evaluation."
                    ),
                },
            },
            "required": ["action"],
        }

    # ─── State access (used by context rendering in Phase 3) ──────────────
    def _get_task_state(self) -> dict[str, Any]:
        """Return the current task-state dict, initializing if absent.

        This is the canonical read path — both ``execute(action='get')``
        and external callers (Phase 3 context injection) use it.

        If ``_sessions`` is None or ``_session_key`` is unset, returns a
        fresh initial-state dict without persistence (read-only fallback).
        """
        if self._sessions is None or not self._session_key:
            return _initial_state()
        session = self._sessions.get_or_create(self._session_key)
        ts = session.metadata.get(_METADATA_KEY)
        if ts is None:
            ts = _initial_state()
            session.metadata[_METADATA_KEY] = ts
        return ts

    def _save_task_state(self, ts: dict[str, Any]) -> None:
        """Persist task-state dict back to session.metadata.

        Caller must have already validated that ``_sessions`` and
        ``_session_key`` are set.
        """
        session = self._sessions.get_or_create(self._session_key)  # type: ignore[union-attr]
        session.metadata[_METADATA_KEY] = ts

    # ─── Tool execution ──────────────────────────────────────────────────
    async def execute(
        self,
        action: str = "",
        new_state: str = "",
        reason: str = "",
        action_id: str = "",
        evaluation_id: str = "",
        **kwargs: Any,
    ) -> str:
        """Execute a task-state action and return JSON result.

        Args:
            action: "get" | "transition" | "reset".
            new_state: Target state for "transition".
            reason: Why this transition is being made.
            action_id: Optional — the action_id (from action_trace) that
                triggered this transition. Forms the
                action→result→state causal chain.
            evaluation_id: Optional — the evaluation_id (from
                evaluate_action) that justified this transition. Forms
                the complete action→result→evaluation→state causal chain.

        Returns:
            - For 'get' / successful 'transition': JSON string of the
              current state dict.
            - For invalid action / invalid transition / no session:
              human-readable error string (not JSON).
        """
        # Guard: no sessions or no session_key (e.g., dream/subagent paths
        # where SessionManager is unavailable).
        if self._sessions is None or not self._session_key:
            return (
                "Error: task_state unavailable — no active session. "
                "This tool requires a session-scoped context."
            )

        if action == "get":
            return self._handle_get()
        if action == "transition":
            return self._handle_transition(
                new_state, reason,
                action_id=action_id,
                evaluation_id=evaluation_id,
            )
        if action == "reset":
            return self._handle_reset()
        return (
            f"Error: unknown action '{action}'. "
            f"Valid actions: get, transition, reset."
        )

    # ─── Action handlers ─────────────────────────────────────────────────
    def _handle_get(self) -> str:
        ts = self._get_task_state()
        return json.dumps(ts, ensure_ascii=False, indent=2)

    def _handle_transition(
        self,
        new_state: str,
        reason: str,
        *,
        action_id: str = "",
        evaluation_id: str = "",
    ) -> str:
        ts = self._get_task_state()
        current = ts["current_state"]

        # Validate new_state is a known state.
        if new_state not in _ALL_STATES:
            return (
                f"Error: unknown state '{new_state}'. "
                f"Valid states: {sorted(_ALL_STATES)}."
            )

        # Idempotency: same-state transition is a no-op.
        # Returns current state without appending to history. This prevents
        # history bloat when the LLM redundantly re-asserts the current
        # state (a common pattern when the LLM is uncertain).
        # Note: action_id/evaluation_id are intentionally ignored here —
        # a no-op transition has no causal event to record.
        if new_state == current:
            return json.dumps(ts, ensure_ascii=False, indent=2)

        # Validate transition is allowed by the state machine.
        allowed = _VALID_TRANSITIONS.get(current, frozenset())
        if new_state not in allowed:
            return (
                f"Error: invalid transition from {current} to {new_state}. "
                f"Valid transitions from {current}: {sorted(allowed)}. "
                f"State unchanged."
            )

        # Apply transition.
        ts["previous_state"] = current
        ts["current_state"] = new_state
        # Top-level reason mirrors the latest transition's reason — lets
        # callers read "why are we in this state?" in O(1) without scanning
        # history. The authoritative record remains in history[] for audit.
        ts["reason"] = reason or ""

        # Build history entry. action_id / evaluation_id are included ONLY
        # when non-empty (rule 32: no speculative fields). This forms the
        # causal chain: action_id → action_trace, evaluation_id →
        # evaluate_action, both optional but together produce the full
        # action→result→evaluation→state chain.
        entry: dict[str, Any] = {
            "state": new_state,
            "ts": _utcnow_iso(),
            "reason": reason or "",
        }
        if action_id:
            entry["action_id"] = action_id
        if evaluation_id:
            entry["evaluation_id"] = evaluation_id
        ts["history"].append(entry)

        # FIFO cap: drop oldest entries beyond _MAX_HISTORY.
        # This bounds session.metadata size and prevents unbounded growth
        # across long-running sessions (rule 22: resource governance).
        if len(ts["history"]) > _MAX_HISTORY:
            ts["history"] = ts["history"][-_MAX_HISTORY:]

        self._save_task_state(ts)
        return json.dumps(ts, ensure_ascii=False, indent=2)

    def _handle_reset(self) -> str:
        """Reset task state to initial EXPLORE and clear history.

        Use case: the Agent realizes it has been following a wrong path
        and wants to start over with a clean causal chain.
        """
        ts = _initial_state()
        self._save_task_state(ts)
        return (
            "Task state reset: current_state=EXPLORE, history cleared. "
            "Previous state machine context has been discarded."
        )
