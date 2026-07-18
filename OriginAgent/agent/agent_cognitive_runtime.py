"""Cognitive runtime extracted from AgentLoop."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Callable

from OriginAgent.agent.cognitive_events import CognitiveDecision, CognitiveEvent
from OriginAgent.agent.identity import RuntimeContext
from OriginAgent.utils.tracing import log_event

# Circuit-breaker tuning constants (session-level cognitive cooldown).
_COGNITIVE_FAILURE_THRESHOLD = 3
_COGNITIVE_COOLDOWN_SECONDS = 30 * 60

# Self-loop detection constants (content-fingerprint based).
# If the same nudge content hash is emitted this many consecutive passes,
# the session enters a silent period to break the "自产自消" loop.
_LOOP_REPEAT_THRESHOLD = 3
_LOOP_SILENT_PERIOD_SECONDS = 60 * 60


@dataclass
class CognitiveRuntimeDeps:
    cognitive_loop: Any
    cognitive_scheduler: Any
    bus: Any
    sessions: Any
    active_intents: Any
    reminder_store: Any
    working_memory: Any
    cognitive_audit: Any
    running_flag: Callable[[], bool]
    build_runtime_context: Callable[[str], RuntimeContext]
    collect_candidates: Callable[[str], list[dict[str, Any]]]
    write_cognitive_event_to_working_memory: Callable[..., bool]
    record_last_scan: Callable[[dict[str, Any]], None]
    utcnow_iso: Callable[[], str]


class AgentCognitiveRuntime:
    """Own the cognitive sidecar runtime while preserving AgentLoop compatibility."""

    def __init__(self, deps: CognitiveRuntimeDeps) -> None:
        self._deps = deps
        # Session-level circuit breaker state for consecutive LLM failures.
        # session_key -> {"consecutive_failures": int, "last_failure_at": str,
        #                 "cooldown_until": float | None}
        self._session_failure_states: dict[str, dict] = {}
        # session_key -> emitted_at_iso (marks that a nudge was emitted last pass,
        # so the next pass can inspect whether it produced an LLM error).
        self._pending_nudges: dict[str, str] = {}
        # P5 (方案 C): content-fingerprint self-loop detection.
        # session_key -> {"last_hash": str | None, "consecutive_repeats": int,
        #                 "silent_until": float | None}
        # When the same nudge content hash is emitted _LOOP_REPEAT_THRESHOLD
        # consecutive passes, the session enters a silent period
        # (_LOOP_SILENT_PERIOD_SECONDS) to break the "自产自消" loop where
        # cognitive_scheduler keeps re-firing the same nudge without progress.
        # Independent from the LLM-failure circuit breaker above — that one
        # catches "LLM errored 3x", this one catches "same nudge 3x".
        self._loop_detection: dict[str, dict] = {}

    def _check_session_cooldown(self, session_key: str) -> tuple[bool, str | None]:
        """Return (True, "cognitive_cooldown") if the session is still in cooldown.

        If the cooldown has expired, the session state is cleared (reset) and
        (False, None) is returned so the pass proceeds normally.
        """
        state = self._session_failure_states.get(session_key)
        if not state:
            return False, None
        cooldown_until = state.get("cooldown_until")
        if cooldown_until is None:
            return False, None
        if time.time() < cooldown_until:
            return True, "cognitive_cooldown"
        # Cooldown expired: reset state so subsequent failures count from zero.
        self._session_failure_states.pop(session_key, None)
        return False, None

    def _check_loop_silent_period(self, session_key: str) -> tuple[bool, str | None]:
        """Return (True, "loop_silent_period") if the session is in a
        content-fingerprint-induced silent period.

        If the silent period has expired, the loop state is reset (so the next
        emit starts a fresh repeat count) and (False, None) is returned.
        """
        state = self._loop_detection.get(session_key)
        if not state:
            return False, None
        silent_until = state.get("silent_until")
        if silent_until is None:
            return False, None
        if time.time() < silent_until:
            return True, "loop_silent_period"
        # Silent period expired: reset so the next emit starts fresh.
        self._loop_detection.pop(session_key, None)
        return False, None

    def _update_loop_detection(self, session_key: str, content_hash: str) -> None:
        """Track consecutive same-content nudge emissions. After
        ``_LOOP_REPEAT_THRESHOLD`` consecutive repeats, trigger a silent
        period to break the self-loop.

        Different content resets the counter — only *repeated identical*
        nudges count as a loop signal. This catches the "自产自消" pattern
        where a pending_confirmation or goal_nudge re-fires with the same
        content because the underlying item isn't being resolved (user
        absent, LLM can't progress, etc.).
        """
        state = self._loop_detection.get(session_key, {
            "last_hash": None,
            "consecutive_repeats": 0,
            "silent_until": None,
        })
        if state.get("last_hash") == content_hash:
            state["consecutive_repeats"] = int(state.get("consecutive_repeats", 0)) + 1
        else:
            # Different content: reset counter, clear any stale silent marker.
            state["last_hash"] = content_hash
            state["consecutive_repeats"] = 1
            state.pop("silent_until", None)
        if int(state["consecutive_repeats"]) >= _LOOP_REPEAT_THRESHOLD:
            state["silent_until"] = time.time() + _LOOP_SILENT_PERIOD_SECONDS
        self._loop_detection[session_key] = state

    def _detect_last_turn_failure(self, session: Any) -> bool:
        """Inspect the last session message to decide if the previous nudge's
        LLM call ended in an error."""
        messages = session.messages
        assert isinstance(messages, list), "session.messages must be a list"
        if not messages:
            return False
        last = messages[-1]
        if not isinstance(last, dict):
            return False
        if last.get("role") != "assistant":
            return False
        if last.get("stop_reason") == "error":
            return True
        content = last.get("content")
        if not isinstance(content, str):
            return False
        # Match the placeholder written by _append_model_error_placeholder
        # ("[Assistant reply unavailable due to model error.]") as well as
        # provider-returned error text ("Error: ...").
        lowered = content.lower()
        if "error:" in lowered or lowered.startswith("error") or "model error" in lowered:
            return True
        return False

    def _update_failure_state(self, session_key: str, session: Any) -> None:
        """Update the consecutive-failure counter based on the outcome of the
        nudge emitted during the previous cognitive pass.

        Only runs when a nudge was actually emitted last pass (tracked via
        ``self._pending_nudges``). On success the counter resets to 0; on
        failure it increments, and once it reaches the threshold a cooldown
        window is stamped onto the session state.
        """
        if session_key not in self._pending_nudges:
            return
        del self._pending_nudges[session_key]
        state = self._session_failure_states.get(session_key, {})
        if self._detect_last_turn_failure(session):
            consecutive = int(state.get("consecutive_failures", 0)) + 1
            state["consecutive_failures"] = consecutive
            state["last_failure_at"] = self._deps.utcnow_iso()
            if consecutive >= _COGNITIVE_FAILURE_THRESHOLD:
                state["cooldown_until"] = time.time() + _COGNITIVE_COOLDOWN_SECONDS
            self._session_failure_states[session_key] = state
        else:
            # Success: reset the counter and clear any stale cooldown marker.
            state["consecutive_failures"] = 0
            state.pop("cooldown_until", None)
            self._session_failure_states[session_key] = state

    def start_active_intent_loop(self, active_intent_task: asyncio.Task[None] | None) -> asyncio.Task[None] | None:
        if not self._deps.cognitive_loop.config.enabled or active_intent_task is not None:
            return active_intent_task
        scheduler_mode = self._deps.cognitive_scheduler.start()
        if scheduler_mode == "cron":
            return None
        return asyncio.create_task(self.active_intent_loop())

    async def active_intent_loop(self) -> None:
        try:
            await self._deps.cognitive_loop.run_forever(self._deps.running_flag)
        except asyncio.CancelledError:
            raise

    async def run_cognitive_pass_for_session(
        self,
        session_key: str,
        *,
        active_task_count: int,
        running_subagents: int,
    ) -> list[CognitiveDecision]:
        if not self._deps.cognitive_loop.config.enabled:
            return []

        eligible, reason = self._deps.active_intents.eligibility_for_cognition(
            session_key,
            active_task_count=active_task_count,
            running_subagents=running_subagents,
        )
        if not eligible:
            log_event(
                "cognitive.pass.skipped",
                session_key=session_key,
                reason=(reason or "ineligible"),
                active_task_count=active_task_count,
                running_subagents=running_subagents,
            )
            event = CognitiveEvent(
                event_id=f"skip:{session_key}:{reason or 'ineligible'}",
                session_key=session_key,
                event_type="goal_nudge",
                source_type="runtime",
                source_reference="eligibility",
                summary=f"Skipped cognitive pass: {reason or 'ineligible'}",
                priority="low",
                payload={
                    "active_task_count": active_task_count,
                    "running_subagents": running_subagents,
                },
            )
            decision = CognitiveDecision(
                decision_id=f"decision:{event.event_id}",
                event_id=event.event_id,
                session_key=session_key,
                action="skip",
                outcome="skipped",
                suppression_reason=reason or "ineligible",
                payload={
                    **event.payload,
                    "event_type": event.event_type,
                    "source_type": event.source_type,
                    "source_reference": event.source_reference,
                    "intent_id": event.event_id,
                    "summary": event.summary,
                },
            )
            self._deps.cognitive_audit.append_event(event)
            self._deps.cognitive_audit.append_decision(decision)
            self._deps.record_last_scan({
                "session_key": session_key,
                "eligible": False,
                "reason": reason or "ineligible",
                "candidate_count": 0,
                "decision_count": 1,
                "timestamp": self._deps.utcnow_iso(),
            })
            return [decision]

        session = self._deps.sessions.get_or_create(session_key)

        # Update failure counter based on the previous pass's nudge outcome,
        # then enforce the session-level cognitive cooldown (circuit breaker).
        self._update_failure_state(session_key, session)
        in_cooldown, cooldown_reason = self._check_session_cooldown(session_key)
        if in_cooldown:
            log_event(
                "cognitive.pass.skipped",
                session_key=session_key,
                reason=cooldown_reason,
                active_task_count=active_task_count,
                running_subagents=running_subagents,
            )
            cooldown_state = self._session_failure_states.get(session_key, {})
            cooldown_event = CognitiveEvent(
                event_id=f"skip:{session_key}:{cooldown_reason}",
                session_key=session_key,
                event_type="goal_nudge",
                source_type="runtime",
                source_reference="cognitive_cooldown",
                summary=f"Skipped cognitive pass: {cooldown_reason}",
                priority="low",
                payload={
                    "active_task_count": active_task_count,
                    "running_subagents": running_subagents,
                    "cooldown_until": cooldown_state.get("cooldown_until"),
                },
            )
            cooldown_decision = CognitiveDecision(
                decision_id=f"decision:{cooldown_event.event_id}",
                event_id=cooldown_event.event_id,
                session_key=session_key,
                action="skip",
                outcome="skipped",
                suppression_reason=cooldown_reason,
                payload={
                    **cooldown_event.payload,
                    "event_type": cooldown_event.event_type,
                    "source_type": cooldown_event.source_type,
                    "source_reference": cooldown_event.source_reference,
                    "intent_id": cooldown_event.event_id,
                    "summary": cooldown_event.summary,
                },
            )
            self._deps.cognitive_audit.append_event(cooldown_event)
            self._deps.cognitive_audit.append_decision(cooldown_decision)
            self._deps.record_last_scan({
                "session_key": session_key,
                "eligible": True,
                "reason": cooldown_reason,
                "candidate_count": 0,
                "decision_count": 1,
                "timestamp": self._deps.utcnow_iso(),
            })
            return [cooldown_decision]

        # P5 (方案 C): content-fingerprint silent period. If the previous
        # passes emitted the same nudge content _LOOP_REPEAT_THRESHOLD
        # consecutive times, the session is in a self-loop — skip the pass
        # entirely until _LOOP_SILENT_PERIOD_SECONDS elapses. Independent
        # from the failure-based cooldown above (that one catches LLM
        # errors; this one catches "same nudge, no progress").
        in_silent, silent_reason = self._check_loop_silent_period(session_key)
        if in_silent:
            log_event(
                "cognitive.pass.skipped",
                session_key=session_key,
                reason=silent_reason,
                active_task_count=active_task_count,
                running_subagents=running_subagents,
            )
            self._deps.record_last_scan({
                "session_key": session_key,
                "eligible": True,
                "reason": silent_reason,
                "candidate_count": 0,
                "decision_count": 0,
                "timestamp": self._deps.utcnow_iso(),
            })
            return []

        runtime_context = self._deps.build_runtime_context(session_key)
        candidates = self._deps.collect_candidates(session_key)
        decisions: list[CognitiveDecision] = []
        emitted_count = 0
        messaging_allowed = bool(self._deps.active_intents.config.enabled)
        for candidate in candidates:
            event = candidate["event"]
            self._deps.cognitive_audit.append_event(event)
            written_to_working_memory = self._deps.write_cognitive_event_to_working_memory(
                session,
                runtime_context=runtime_context,
                event=event,
            )
            allowed, suppression_reason = self._deps.active_intents.passes_cooldown(
                session_key,
                candidate["cooldown_key"],
            )
            published_internal_event = False
            action = "emit"
            outcome = "emitted"
            if emitted_count >= self._deps.active_intents.config.max_messages_per_session_per_pass:
                allowed = False
                suppression_reason = "session_message_limit"
            if not messaging_allowed:
                allowed = False
                suppression_reason = "agent_messages_disabled"
            if not allowed:
                action = "suppress"
                outcome = "suppressed"
            else:
                await self._deps.bus.publish_inbound(candidate["message"])
                published_internal_event = True
                emitted_count += 1
                # Mark that a nudge was emitted so the next pass can inspect
                # whether it produced an LLM error (circuit-breaker input).
                self._pending_nudges[session_key] = self._deps.utcnow_iso()
                # P5 (方案 C): update content-fingerprint loop detection.
                # Hash the nudge content (not the LLM response) — same content
                # across consecutive passes means the underlying pending item
                # isn't being resolved, which is the "自产自消" signal.
                content_hash = hashlib.sha256(
                    candidate["message"].content.encode("utf-8", errors="replace")
                ).hexdigest()[:16]
                self._update_loop_detection(session_key, content_hash)
                if event.event_type == "scheduled_reminder":
                    self._deps.reminder_store.mark_fired(event.source_reference)
                log_event("cognitive.event.emitted", session_key=session_key, event_type=event.event_type, summary=str(event.summary)[:80])
            decision = CognitiveDecision(
                decision_id=f"decision:{event.event_id}",
                event_id=event.event_id,
                session_key=session_key,
                action=action,
                outcome=outcome,
                suppression_reason=suppression_reason,
                cooldown_key=candidate["cooldown_key"],
                written_to_working_memory=written_to_working_memory,
                published_internal_event=published_internal_event,
                payload={
                    "event_type": event.event_type,
                    "source_type": event.source_type,
                    "source_reference": event.source_reference,
                    "intent_id": candidate["cooldown_key"],
                    "summary": event.summary,
                },
            )
            self._deps.cognitive_audit.append_decision(decision)
            decisions.append(decision)
        self._deps.record_last_scan({
            "session_key": session_key,
            "eligible": True,
            "reason": None,
            "candidate_count": len(candidates),
            "decision_count": len(decisions),
            "emitted_count": sum(1 for item in decisions if item.outcome == "emitted"),
            "suppressed_count": sum(1 for item in decisions if item.outcome == "suppressed"),
            "event_types": [item["event"].event_type for item in candidates],
            "timestamp": self._deps.utcnow_iso(),
        })
        return decisions


__all__ = [
    "AgentCognitiveRuntime",
    "CognitiveRuntimeDeps",
]
