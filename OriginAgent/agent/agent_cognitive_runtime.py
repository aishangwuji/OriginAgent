"""Cognitive runtime extracted from AgentLoop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from OriginAgent.agent.active_intents import ActiveIntentRecord
from OriginAgent.agent.cognitive_events import CognitiveDecision, CognitiveEvent
from OriginAgent.agent.identity import RuntimeContext
from OriginAgent.bus.events import InboundMessage
from OriginAgent.session.manager import Session


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
        eligible, reason = self._deps.active_intents.eligible_session(
            session_key,
            active_task_count=active_task_count,
            running_subagents=running_subagents,
        )
        if not eligible:
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
                payload=event.payload,
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
        runtime_context = self._deps.build_runtime_context(session_key)
        candidates = self._deps.collect_candidates(session_key)
        decisions: list[CognitiveDecision] = []
        emitted_count = 0
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
            if not allowed:
                action = "suppress"
                outcome = "suppressed"
                self._deps.active_intents.ledger.append(ActiveIntentRecord(
                    timestamp=self._deps.utcnow_iso(),
                    session_key=session_key,
                    intent_type=event.event_type,
                    intent_id=candidate["cooldown_key"],
                    source_type=event.source_type,
                    source_reference=event.source_reference,
                    outcome="suppressed",
                    summary=event.summary,
                    suppression_reason=suppression_reason,
                ))
            else:
                await self._deps.bus.publish_inbound(candidate["message"])
                published_internal_event = True
                emitted_count += 1
                if event.event_type == "scheduled_reminder":
                    self._deps.reminder_store.mark_fired(event.source_reference)
                self._deps.active_intents.ledger.append(ActiveIntentRecord(
                    timestamp=self._deps.utcnow_iso(),
                    session_key=session_key,
                    intent_type=event.event_type,
                    intent_id=candidate["cooldown_key"],
                    source_type=event.source_type,
                    source_reference=event.source_reference,
                    outcome="emitted",
                    summary=event.summary,
                ))
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
