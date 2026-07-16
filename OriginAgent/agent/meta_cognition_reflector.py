"""Turn-end sidecar for structured meta-cognition artifacts and bridges."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from OriginAgent.agent.auxiliary_llm import AuxiliaryLLMRouter, call_llm
from OriginAgent.agent.meta_cognition_audit import JsonlMetaCognitionAuditLedger
from OriginAgent.agent.meta_cognition_evolution_bridge import bridge_patterns_to_signals
from OriginAgent.agent.meta_cognition_models import (
    ConfidenceTrace,
    ErrorPattern,
    EvolutionSeed,
    MetaTrigger,
    ReflectionRecord,
    ThoughtJournalEntry,
)
from OriginAgent.agent.meta_cognition_patterns import consolidate_error_patterns
from OriginAgent.agent.meta_cognition_redact import (
    redact_meta_list,
    redact_meta_text,
    redact_metadata,
    redact_rule_candidate,
)
from OriginAgent.agent.runtime_models import TaskRunReport, now_iso
from OriginAgent.agent.task_runtime import (
    build_task_report,
    maybe_retry_once,
    remember_report,
    report_to_status_payload,
)
from OriginAgent.memory.candidates import GovernedMemoryWriter, MemoryCandidate
from OriginAgent.session.goal_state import goal_state_raw, parse_goal_state
from OriginAgent.utils.constants import RoleConstants
from OriginAgent.utils.prompt_templates import render_template

_ARTIFACT_RUNTIME_STATUS = {
    "journals_written": 0,
    "reflections_written": 0,
    "confidence_traces_written": 0,
    "patterns_written": 0,
    "evolution_seeds_written": 0,
    "working_memory_bridge": {
        "enabled": False,
        "last_status": "idle",
        "decision_counts": {},
    },
    "memory_candidate_bridge": {
        "enabled": False,
        "last_status": "idle",
        "decision_counts": {},
    },
}
_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_PATTERN_RETRIEVAL_LIMIT = 200
_MAX_SIMILAR_PATTERNS = 3
_MAX_SIMILAR_REFLECTIONS = 2


def _build_observation_summary(turn_snapshot: dict[str, Any]) -> str:
    """Compact snapshot summary for the ThoughtFrame observation field."""
    parts: list[str] = []
    user_msg = turn_snapshot.get("user_message")
    if user_msg:
        parts.append(f"user: {redact_meta_text(user_msg, max_chars=240)}")
    world = turn_snapshot.get("world_summary_preview")
    if world:
        parts.append(f"world: {redact_meta_text(world, max_chars=200)}")
    tool_count = len(turn_snapshot.get("tool_results") or [])
    if tool_count:
        parts.append(f"tools: {tool_count}")
    return " | ".join(parts) if parts else "(empty)"


@dataclass(frozen=True)
class MetaReflectionArtifacts:
    journals: list[ThoughtJournalEntry] = field(default_factory=list)
    reflections: list[ReflectionRecord] = field(default_factory=list)
    confidence_traces: list[ConfidenceTrace] = field(default_factory=list)
    patterns: list[ErrorPattern] = field(default_factory=list)
    evolution_seeds: list[EvolutionSeed] = field(default_factory=list)


@dataclass(frozen=True)
class MetaReflectionResult:
    status: str
    reason: str = ""
    trigger_count: int = 0
    journals_written: int = 0
    reflections_written: int = 0
    confidence_traces_written: int = 0
    patterns_written: int = 0
    evolution_seeds_written: int = 0
    bridge_decision_counts: dict[str, int] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""


class MetaCognitionReflector:
    """Produce structured artifacts from accepted triggers without blocking foreground turns."""

    def __init__(
        self,
        *,
        workspace: Path,
        config: Any,
        audit: JsonlMetaCognitionAuditLedger,
        auxiliary_router: AuxiliaryLLMRouter | None,
        provider: Any | None,
        model: str | None,
        sessions: Any,
        working_memory: Any,
        context_config: Any | None = None,
        substrate: Any | None = None,
        output_language: str | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.config = config
        self.audit = audit
        self.auxiliary_router = auxiliary_router
        self.provider = provider
        self.model = model
        self.sessions = sessions
        self.working_memory = working_memory
        self.context_config = context_config
        self._substrate = substrate
        # 元认知产出语言：从 AgentDefaults.output_language 注入，影响 journal/reflection 等字段
        self.output_language = output_language or None
        self._candidate_writer = GovernedMemoryWriter(self.workspace)
        self._running = 0
        self._consecutive_failures = 0
        self._last_report: TaskRunReport | None = None
        self._last_result: MetaReflectionResult | None = None
        self._last_artifacts = MetaReflectionArtifacts()
        self._working_memory_bridge_counts: dict[str, int] = {}
        self._memory_candidate_bridge_counts: dict[str, int] = {}
        self._evolution_bridge_counts: dict[str, int] = {}
        self._last_signal_upserts: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", False))

    @property
    def structured_reflection_enabled(self) -> bool:
        return bool(getattr(self.config, "structured_reflection_enabled", False))

    @property
    def working_memory_bridge_enabled(self) -> bool:
        return bool(getattr(self.config, "working_memory_bridge_enabled", False))

    @property
    def memory_candidate_bridge_enabled(self) -> bool:
        return bool(getattr(self.config, "memory_candidate_bridge_enabled", False))

    @property
    def pattern_consolidation_enabled(self) -> bool:
        return bool(getattr(self.config, "pattern_consolidation_enabled", False))

    @property
    def evolution_bridge_enabled(self) -> bool:
        return bool(getattr(self.config, "evolution_bridge_enabled", False))

    def runtime_status(self) -> dict[str, Any]:
        last_payload = report_to_status_payload(
            self._last_report,
            consecutive_failures=self._consecutive_failures,
        )
        return {
            "enabled": self.enabled,
            "structured_reflection_enabled": self.structured_reflection_enabled,
            "running_count": self._running,
            "artifact_status": {
                **_ARTIFACT_RUNTIME_STATUS,
                "journals_written": len(self.audit.recent_journals(limit=200)),
                "reflections_written": len(self.audit.recent_reflections(limit=200)),
                "confidence_traces_written": len(self.audit.recent_confidence_traces(limit=200)),
                "patterns_written": len(self.audit.recent_patterns(limit=200)),
                "evolution_seeds_written": len(self.audit.recent_evolution_seeds(limit=200)),
                "working_memory_bridge": {
                    "enabled": self.working_memory_bridge_enabled,
                    "last_status": "idle" if not self._last_result else self._last_result.status,
                    "decision_counts": dict(self._working_memory_bridge_counts),
                },
                "memory_candidate_bridge": {
                    "enabled": self.memory_candidate_bridge_enabled,
                    "last_status": "idle" if not self._last_result else self._last_result.status,
                    "decision_counts": dict(self._memory_candidate_bridge_counts),
                },
            },
            "bridge_decision_counts": self._bridge_decision_counts(),
            "last_signal_upserts": list(self._last_signal_upserts),
            "last_result": asdict(self._last_result) if self._last_result is not None else None,
            **last_payload,
        }

    def _extract_active_goal(self, session_key: str) -> str:
        """Extract the active goal from working memory for the reflection frame.

        Reads ``current_goal`` from the working memory snapshot. The
        ``WorkingMemoryManager`` already skips goal hydration when the
        underlying goal_state is older than 30 minutes, so an expired goal
        surfaces here as an empty string.
        """
        session = self.sessions.get_or_create(session_key)
        snapshot = self.working_memory.load(session)
        goal = snapshot.current_goal.strip()
        if not goal:
            logger.debug("No active goal available for session {}", session_key)
            return ""
        return goal

    async def reflect_turn(
        self,
        *,
        session_key: str,
        turn_id: str,
        turn_snapshot: dict[str, Any],
        accepted_triggers: list[MetaTrigger],
        runtime_context: Any | None,
    ) -> MetaReflectionResult:
        started_at = now_iso()
        if not self.enabled:
            return self._remember(
                MetaReflectionResult(
                    status="skipped",
                    reason="disabled",
                    started_at=started_at,
                    finished_at=now_iso(),
                ),
                build_task_report(
                    task_name="meta_cognition",
                    status="skipped",
                    phase="preflight",
                    reason="disabled",
                    started_at=started_at,
                    finished_at=now_iso(),
                ),
            )
        triggers = list(accepted_triggers or [])
        if not triggers:
            return self._remember(
                MetaReflectionResult(
                    status="skipped",
                    reason="no_triggers",
                    started_at=started_at,
                    finished_at=now_iso(),
                ),
                build_task_report(
                    task_name="meta_cognition",
                    status="skipped",
                    phase="preflight",
                    reason="no_triggers",
                    started_at=started_at,
                    finished_at=now_iso(),
                ),
            )

        # ── open ThoughtFrame (CogniSphere CS-002) ─────────────────────
        substrate = self._substrate
        if substrate is not None:
            substrate.open_frame(
                session_key,
                trigger_refs=[t.trigger_id for t in triggers],
                observation_summary=_build_observation_summary(turn_snapshot),
                active_goal=self._extract_active_goal(session_key),
                confidence=0.0,
            )

        self._running += 1
        try:
            session = self.sessions.get_or_create(session_key)
            journals = [self._append_minimal_journal(trigger=trigger, turn_id=turn_id) for trigger in triggers]
            self._last_artifacts = MetaReflectionArtifacts(journals=journals)
            reflectable_triggers = [
                trigger
                for trigger in triggers
                if not (
                    trigger.trigger_type == "tool_failure"
                    and str(trigger.payload.get("status") or "").strip().lower() == "policy_denied"
                )
            ]
            if not self.structured_reflection_enabled:
                if substrate is not None:
                    substrate.close_frame(session_key, enrichment={"strategy_summary": "minimal_journal_only"})
                return self._remember(
                    MetaReflectionResult(
                        status="ok",
                        reason="minimal_only",
                        trigger_count=len(triggers),
                        journals_written=len(journals),
                        started_at=started_at,
                        finished_at=now_iso(),
                    ),
                    build_task_report(
                        task_name="meta_cognition",
                        status="ok",
                        phase="minimal_journal",
                        reason="minimal_only",
                        started_at=started_at,
                        finished_at=now_iso(),
                        details={"journals_written": len(journals)},
                    ),
                )
            if not reflectable_triggers:
                if substrate is not None:
                    substrate.close_frame(session_key, enrichment={"strategy_summary": "journal_only"})
                return self._remember(
                    MetaReflectionResult(
                        status="ok",
                        reason="journal_only",
                        trigger_count=len(triggers),
                        journals_written=len(journals),
                        started_at=started_at,
                        finished_at=now_iso(),
                    ),
                    build_task_report(
                        task_name="meta_cognition",
                        status="ok",
                        phase="minimal_journal",
                        reason="journal_only",
                        started_at=started_at,
                        finished_at=now_iso(),
                        details={"journals_written": len(journals)},
                    ),
                )

            prompt = self._build_prompt(
                session=session,
                turn_id=turn_id,
                accepted_triggers=reflectable_triggers,
                turn_snapshot=turn_snapshot,
                runtime_context=runtime_context,
                historical_context=self._retrieve_historical_context(
                    accepted_triggers=reflectable_triggers,
                    turn_snapshot=turn_snapshot,
                    owner_id=str(getattr(runtime_context, "user_id", "") or "user").strip() or "user",
                ),
            )

            async def _call(_attempt_count: int):
                return await call_llm(
                    task="meta_cognition",
                    router=self.auxiliary_router,
                    provider=self.provider,
                    model=self.model,
                    messages=[
                        {
                            "role": RoleConstants.SYSTEM,
                            "content": render_template(
                                "agent/meta_cognition_reflection.md",
                                strip=True,
                                output_language=self.output_language,
                            ),
                        },
                        {"role": RoleConstants.USER, "content": prompt},
                    ],
                    tools=None,
                    tool_choice=None,
                    max_tokens=2048,
                    temperature=0.1,
                )

            response, attempt_count = await maybe_retry_once(_call, retry_count=0, backoff_ms=0)
            if response.finish_reason == "error":
                return self._remember(
                    MetaReflectionResult(
                        status="error",
                        reason=response.content or "llm_error",
                        trigger_count=len(triggers),
                        journals_written=len(journals),
                        started_at=started_at,
                        finished_at=now_iso(),
                    ),
                    build_task_report(
                        task_name="meta_cognition",
                        status="error",
                        phase="llm",
                        fault_class="external",
                        retryable=True,
                        reason=response.content or "llm_error",
                        started_at=started_at,
                        finished_at=now_iso(),
                        attempt_count=attempt_count,
                    ),
                )

            # 优先使用 content，若空则尝试 reasoning_content（reasoning 模型可能把 JSON 放在此字段）
            response_text = response.content or response.reasoning_content or ""
            enriched, reflection, trace = self._parse_response(
                response_text,
                session_key=session_key,
                journals=journals,
                triggers=reflectable_triggers,
                owner_id=str(getattr(runtime_context, "user_id", "") or "user").strip() or "user",
            )
            # 三者均为 None 表示 LLM 返回空内容或非法 JSON，优雅降级为 minimal_only
            result_reason = (
                "minimal_only"
                if enriched is None and reflection is None and trace is None
                else "ok"
            )
            if enriched is not None:
                journals[-1] = enriched
                self.audit.append_journal(enriched)
            reflections: list[ReflectionRecord] = []
            confidence_traces: list[ConfidenceTrace] = []
            patterns: list[ErrorPattern] = []
            evolution_seeds: list[EvolutionSeed] = []
            if reflection is not None:
                reflections.append(reflection)
                self.audit.append_reflection(reflection)
                await asyncio.to_thread(self._apply_bridges, session, reflection, runtime_context)
                patterns, evolution_seeds = await asyncio.to_thread(
                    self._apply_evolution_bridge,
                    reflection,
                    runtime_context,
                )
            if trace is not None:
                confidence_traces.append(trace)
                self.audit.append_confidence_trace(trace)
            self._last_artifacts = MetaReflectionArtifacts(
                journals=journals,
                reflections=reflections,
                confidence_traces=confidence_traces,
                patterns=patterns,
                evolution_seeds=evolution_seeds,
            )
            if substrate is not None:
                last_reflection = reflections[-1] if reflections else None
                enrichment: dict[str, Any] = {
                    "strategy_summary": (enriched.strategy_summary if enriched else ""),
                    "expected_outcome": (enriched.expected_outcome if enriched else ""),
                    "actual_outcome": (enriched.actual_outcome if enriched else ""),
                    "mismatch_summary": (enriched.mismatch_summary if enriched else ""),
                    "assumptions": (enriched.assumptions if enriched else []),
                    "evidence_refs": [t.trigger_id for t in triggers],
                    "retention_hint": (last_reflection.retention_hint if last_reflection is not None else "discard"),
                }
                substrate.close_frame(session_key, enrichment=enrichment)
            return self._remember(
                MetaReflectionResult(
                    status="ok",
                    reason=result_reason,
                    trigger_count=len(triggers),
                    journals_written=len(journals) + (1 if enriched is not None else 0),
                    reflections_written=len(reflections),
                    confidence_traces_written=len(confidence_traces),
                    patterns_written=len(patterns),
                    evolution_seeds_written=len(evolution_seeds),
                    bridge_decision_counts=self._bridge_decision_counts(),
                    started_at=started_at,
                    finished_at=now_iso(),
                ),
                build_task_report(
                    task_name="meta_cognition",
                    status="ok",
                    phase="persist",
                    reason=result_reason,
                    started_at=started_at,
                    finished_at=now_iso(),
                    attempt_count=attempt_count,
                    details={
                        "journals_written": len(journals) + (1 if enriched is not None else 0),
                        "reflections_written": len(reflections),
                        "confidence_traces_written": len(confidence_traces),
                        "patterns_written": len(patterns),
                        "evolution_seeds_written": len(evolution_seeds),
                    },
                ),
            )
        except Exception as exc:
            logger.exception("Meta cognition reflection failed")
            if substrate is not None:
                substrate.close_frame(session_key)
            return self._remember(
                MetaReflectionResult(
                    status="error",
                    reason=str(exc),
                    trigger_count=len(triggers),
                    started_at=started_at,
                    finished_at=now_iso(),
                ),
                build_task_report(
                    task_name="meta_cognition",
                    status="error",
                    phase="reflect_turn",
                    reason=str(exc),
                    started_at=started_at,
                    finished_at=now_iso(),
                ),
            )
        finally:
            self._running = max(0, self._running - 1)

    def _append_minimal_journal(self, *, trigger: MetaTrigger, turn_id: str) -> ThoughtJournalEntry:
        journal = ThoughtJournalEntry(
            entry_id=f"meta_journal_{uuid.uuid4().hex}",
            session_key=trigger.session_key,
            trigger_type=trigger.trigger_type,
            task_reference=turn_id,
            evidence_refs=redact_meta_list(trigger.evidence_refs, max_items=6, max_chars=120),
            actual_outcome=redact_meta_text(trigger.payload.get("status") or trigger.trigger_type, max_chars=160),
            summary=redact_meta_text(
                f"{trigger.trigger_type} observed for {turn_id}",
                max_chars=160,
            ),
            payload={
                "source_type": redact_meta_text(trigger.source_type, max_chars=80),
                "source_reference": redact_meta_text(trigger.source_reference, max_chars=160),
                "severity": trigger.severity,
                "trigger_id": trigger.trigger_id,
            },
        )
        self.audit.append_journal(journal)
        return journal

    def _build_prompt(
        self,
        *,
        session: Any,
        turn_id: str,
        accepted_triggers: list[MetaTrigger],
        turn_snapshot: dict[str, Any],
        runtime_context: Any | None,
        historical_context: dict[str, list[dict[str, Any]]] | None = None,
    ) -> str:
        goal = parse_goal_state(goal_state_raw(session.metadata))
        working_summary = self.working_memory.inspect(
            session,
            identity=getattr(runtime_context, "identity", None) if runtime_context is not None else None,
        )
        lines = [
            "## Meta Cognition Turn Snapshot",
            f"- session_key: {redact_meta_text(session.key, max_chars=120)}",
            f"- turn_id: {redact_meta_text(turn_id, max_chars=120)}",
            f"- trigger_count: {len(accepted_triggers)}",
            "",
            "## Accepted Triggers",
        ]
        for index, trigger in enumerate(accepted_triggers, start=1):
            lines.extend([
                f"[{index}] type={trigger.trigger_type} severity={trigger.severity}",
                f"source={redact_meta_text(trigger.source_type, max_chars=80)} ref={redact_meta_text(trigger.source_reference, max_chars=120)}",
                f"evidence_refs={json.dumps(redact_meta_list(trigger.evidence_refs, max_items=6, max_chars=120), ensure_ascii=False)}",
                f"payload={json.dumps(redact_metadata(trigger.payload), ensure_ascii=False, sort_keys=True)}",
                "",
            ])
        lines.extend([
            "## Current User Message",
            redact_meta_text(turn_snapshot.get("user_message"), max_chars=1200) or "(empty)",
            "",
            "## Current Assistant Final Content",
            redact_meta_text(turn_snapshot.get("assistant_final_content"), max_chars=1200) or "(empty)",
            "",
            "## Previous Assistant Message",
            redact_meta_text(turn_snapshot.get("previous_assistant_message"), max_chars=400) or "(empty)",
            "",
            "## Working Memory Summary",
            json.dumps(redact_metadata(working_summary), ensure_ascii=False, sort_keys=True),
            "",
            "## Goal State Summary",
            json.dumps(redact_metadata(goal if isinstance(goal, dict) else {}), ensure_ascii=False, sort_keys=True),
            "",
            "## Historical Similar Patterns",
        ])
        for item in list((historical_context or {}).get("patterns") or []):
            lines.append(json.dumps(redact_metadata(item), ensure_ascii=False, sort_keys=True))
        if not list((historical_context or {}).get("patterns") or []):
            lines.append("(none)")
        lines.extend([
            "",
            "## Historical Similar Reflections",
        ])
        for item in list((historical_context or {}).get("reflections") or []):
            lines.append(json.dumps(redact_metadata(item), ensure_ascii=False, sort_keys=True))
        if not list((historical_context or {}).get("reflections") or []):
            lines.append("(none)")
        lines.extend([
            "",
            "## World Summary Preview",
            redact_meta_text(turn_snapshot.get("world_summary_preview"), max_chars=800) or "(empty)",
            "",
            "## Runtime Context Summary",
            json.dumps(redact_metadata(turn_snapshot.get("runtime_context") or {}), ensure_ascii=False, sort_keys=True),
        ])
        return "\n".join(lines).strip()

    def _parse_response(
        self,
        text: str,
        *,
        session_key: str,
        journals: list[ThoughtJournalEntry],
        triggers: list[MetaTrigger],
        owner_id: str,
    ) -> tuple[ThoughtJournalEntry | None, ReflectionRecord | None, ConfidenceTrace | None]:
        payload = self._load_json_payload(text)
        if not isinstance(payload, dict):
            preview = (text or "").strip()[:300]
            logger.warning("Meta cognition reflection returned invalid JSON ({} chars). First 300 chars: {}", len(text or ""), preview)
            # 优雅降级：空内容或非法 JSON 时返回全 None，由调用方走 minimal journal 路径
            return (None, None, None)
        latest_journal = journals[-1]
        journal_raw = payload.get("journal_enrichment")
        reflection_raw = payload.get("reflection")
        trace_raw = payload.get("confidence_trace")

        enriched: ThoughtJournalEntry | None = None
        if isinstance(journal_raw, dict):
            enriched = ThoughtJournalEntry(
                entry_id=latest_journal.entry_id,
                session_key=session_key,
                created_at=latest_journal.created_at,
                trigger_type=latest_journal.trigger_type,
                task_reference=latest_journal.task_reference,
                strategy_summary=redact_meta_text(journal_raw.get("strategy_summary"), max_chars=240),
                assumptions=redact_meta_list(journal_raw.get("assumptions"), max_items=6, max_chars=160),
                evidence_refs=latest_journal.evidence_refs,
                confidence=max(
                    latest_journal.confidence,
                    float(journal_raw.get("confidence") or latest_journal.confidence or 0.0),
                ),
                expected_outcome=redact_meta_text(journal_raw.get("expected_outcome"), max_chars=240),
                actual_outcome=redact_meta_text(journal_raw.get("actual_outcome"), max_chars=240) or latest_journal.actual_outcome,
                mismatch_summary=redact_meta_text(journal_raw.get("mismatch_summary"), max_chars=240),
                suggested_next_action=redact_meta_text(journal_raw.get("suggested_next_action"), max_chars=240),
                summary=redact_meta_text(journal_raw.get("summary") or latest_journal.summary, max_chars=240),
                payload=latest_journal.payload,
            )

        reflection: ReflectionRecord | None = None
        if isinstance(reflection_raw, dict):
            candidate = redact_rule_candidate(reflection_raw.get("learned_rule_candidate"))
            reflection = ReflectionRecord(
                reflection_id=f"meta_reflection_{uuid.uuid4().hex}",
                session_key=session_key,
                source_entry_ids=[journal.entry_id for journal in journals],
                reflection_kind=redact_meta_text(reflection_raw.get("reflection_kind"), max_chars=80),
                outcome_class=redact_meta_text(reflection_raw.get("outcome_class"), max_chars=80),
                root_cause_hypotheses=redact_meta_list(reflection_raw.get("root_cause_hypotheses"), max_items=6, max_chars=180),
                what_worked=redact_meta_list(reflection_raw.get("what_worked"), max_items=6, max_chars=180),
                what_failed=redact_meta_list(reflection_raw.get("what_failed"), max_items=6, max_chars=180),
                learned_rule_candidate=candidate,
                confidence=max(0.0, min(float(reflection_raw.get("confidence") or 0.0), 1.0)),
                retention_hint=str(reflection_raw.get("retention_hint") or "discard").strip().lower() or "discard",
                summary=redact_meta_text(reflection_raw.get("summary") or "", max_chars=240),
                payload={
                    "owner_id": owner_id,
                    "trigger_types": [trigger.trigger_type for trigger in triggers],
                    "evidence_refs": redact_meta_list(
                        [
                            ref
                            for trigger in triggers
                            for ref in (
                                list(trigger.evidence_refs or [])
                                or [trigger.source_reference]
                            )
                        ],
                        max_items=8,
                        max_chars=160,
                    ),
                    "trigger_contexts": [
                        {
                            "trigger_type": trigger.trigger_type,
                            "status": redact_meta_text(trigger.payload.get("status"), max_chars=40),
                        }
                        for trigger in triggers
                    ],
                },
            )

        trace: ConfidenceTrace | None = None
        if isinstance(trace_raw, dict):
            final_confidence = float(trace_raw.get("final_confidence") or 0.0)
            if final_confidence > 0:
                trace = ConfidenceTrace(
                    trace_id=f"meta_confidence_{uuid.uuid4().hex}",
                    session_key=session_key,
                    subject_type=redact_meta_text(trace_raw.get("subject_type"), max_chars=80),
                    subject_reference=redact_meta_text(trace_raw.get("subject_reference"), max_chars=160),
                    initial_confidence=trace_raw.get("initial_confidence"),
                    final_confidence=final_confidence,
                    change_reason=redact_meta_text(trace_raw.get("change_reason"), max_chars=180) or "initial_record",
                    evidence_refs=redact_meta_list(trace_raw.get("evidence_refs"), max_items=6, max_chars=120),
                    summary=redact_meta_text(trace_raw.get("summary") or "", max_chars=240),
                )
        if reflection is not None:
            uncertainty_score, reason_codes = self._compute_uncertainty(
                reflection=reflection,
                trace=trace,
                trigger_count=len(triggers),
            )
            reflection_payload = dict(reflection.payload or {})
            reflection_payload["uncertainty_score"] = uncertainty_score
            reflection_payload["uncertainty_reason_codes"] = list(reason_codes)
            reflection = ReflectionRecord(
                reflection_id=reflection.reflection_id,
                session_key=reflection.session_key,
                created_at=reflection.created_at,
                source_entry_ids=list(reflection.source_entry_ids),
                reflection_kind=reflection.reflection_kind,
                outcome_class=reflection.outcome_class,
                root_cause_hypotheses=list(reflection.root_cause_hypotheses),
                what_worked=list(reflection.what_worked),
                what_failed=list(reflection.what_failed),
                learned_rule_candidate=reflection.learned_rule_candidate,
                confidence=reflection.confidence,
                retention_hint=reflection.retention_hint,
                summary=reflection.summary,
                payload=reflection_payload,
            )
            if trace is not None:
                trace_payload = dict(trace.payload or {})
                trace_payload["uncertainty_score"] = uncertainty_score
                trace_payload["uncertainty_reason_codes"] = list(reason_codes)
                trace = ConfidenceTrace(
                    trace_id=trace.trace_id,
                    session_key=trace.session_key,
                    created_at=trace.created_at,
                    subject_type=trace.subject_type,
                    subject_reference=trace.subject_reference,
                    initial_confidence=trace.initial_confidence,
                    final_confidence=trace.final_confidence,
                    change_reason=trace.change_reason,
                    evidence_refs=list(trace.evidence_refs),
                    summary=trace.summary,
                    payload=trace_payload,
                )
        return enriched, reflection, trace

    def _apply_bridges(self, session: Any, reflection: ReflectionRecord, runtime_context: Any | None) -> None:
        if self.working_memory_bridge_enabled:
            try:
                self._bridge_to_working_memory(session, reflection, runtime_context)
            except Exception:
                logger.exception("Meta cognition working memory bridge failed")
                self._increment(self._working_memory_bridge_counts, "bridge_error")
        else:
            self._increment(self._working_memory_bridge_counts, "disabled")
        if self.memory_candidate_bridge_enabled:
            try:
                self._bridge_to_memory_candidates(reflection, runtime_context)
            except Exception:
                logger.exception("Meta cognition memory candidate bridge failed")
                self._increment(self._memory_candidate_bridge_counts, "bridge_error")
        else:
            self._increment(self._memory_candidate_bridge_counts, "disabled")

    def _apply_evolution_bridge(
        self,
        reflection: ReflectionRecord,
        runtime_context: Any | None,
    ) -> tuple[list[ErrorPattern], list[EvolutionSeed]]:
        if not self.pattern_consolidation_enabled:
            self._increment(self._evolution_bridge_counts, "pattern_disabled")
            return [], []
        recent_rows = self.audit.recent_reflections(
            limit=max(1, int(getattr(self.config, "pattern_window_max_reflections", 200) or 200))
        )
        reflections: list[ReflectionRecord] = []
        for row in recent_rows:
            try:
                reflections.append(ReflectionRecord.from_json(row))
            except ValueError:
                continue
        owner_id = str(getattr(runtime_context, "user_id", "") or "user").strip() or "user"
        consolidation = consolidate_error_patterns(
            reflections=reflections,
            config=self.config,
            owner_id=owner_id,
            current_turn_reflection_ids={reflection.reflection_id},
        )
        patterns = consolidation.patterns
        for pattern in patterns:
            self.audit.append_pattern(pattern)
        if not self.evolution_bridge_enabled:
            self._increment(self._evolution_bridge_counts, "bridge_disabled")
            return patterns, []
        artifact_lookup = self._artifact_lookup(
            journals=self.audit.recent_journals(limit=200),
            reflections=recent_rows,
            patterns=patterns,
        )
        bridge_results = bridge_patterns_to_signals(
            workspace=self.workspace,
            patterns=consolidation.eligible_patterns,
            config=self.config,
            artifact_lookup=artifact_lookup,
        )
        seeds: list[EvolutionSeed] = []
        signal_upserts: list[dict[str, Any]] = []
        for result in bridge_results:
            self._increment(self._evolution_bridge_counts, result.decision)
            if result.seed is not None:
                seeds.append(result.seed)
                self.audit.append_evolution_seed(result.seed)
            if result.signal is not None:
                signal_upserts.append(dict(result.signal))
        self._last_signal_upserts = signal_upserts[-10:]
        return patterns, seeds

    def _bridge_to_working_memory(self, session: Any, reflection: ReflectionRecord, runtime_context: Any | None) -> None:
        try:
            snapshot = self.working_memory.load(
                session,
                identity=getattr(runtime_context, "identity", None) if runtime_context is not None else None,
            )
        except Exception:
            self._increment(self._working_memory_bridge_counts, "load_error")
            return
        max_budget = max(
            1,
            int(getattr(self.context_config, "world_attention_max_items", 3) or 3),
        )
        if len(list(snapshot.attention_items or [])) >= max_budget and len(list(snapshot.pending_questions or [])) >= max_budget:
            self._increment(self._working_memory_bridge_counts, "dropped_budget")
            return
        wrote_any = False
        if reflection.what_failed:
            caution = redact_meta_text(reflection.what_failed[0], max_chars=160)
            if caution and len(list(snapshot.attention_items or [])) < max_budget:
                fast_path_refs = set(getattr(runtime_context, "meta_cognition_fast_path_refs", set()) or set()) if runtime_context is not None else set()
                payload = reflection.payload if isinstance(reflection.payload, dict) else {}
                source_reference = str(payload.get("source_reference") or "")
                if source_reference and source_reference in fast_path_refs:
                    self._increment(self._working_memory_bridge_counts, "fast_path_duplicate_skipped")
                    caution = ""
                if not caution:
                    pass
                else:
                    self.working_memory.append_attention_item(
                        session,
                        caution,
                        identity=getattr(runtime_context, "identity", None) if runtime_context is not None else None,
                    )
                    wrote_any = True
                    self._increment(self._working_memory_bridge_counts, "attention_appended")
        if reflection.what_failed and len(list(snapshot.pending_questions or [])) < max_budget:
            question = redact_meta_text(
                reflection.summary or reflection.what_failed[0],
                max_chars=160,
            )
            if question:
                self.working_memory.append_pending_question(
                    session,
                    question,
                    identity=getattr(runtime_context, "identity", None) if runtime_context is not None else None,
                )
                wrote_any = True
                self._increment(self._working_memory_bridge_counts, "question_appended")
        if not wrote_any:
            self._increment(self._working_memory_bridge_counts, "skipped_empty")

    def _bridge_to_memory_candidates(self, reflection: ReflectionRecord, runtime_context: Any | None) -> None:
        candidate = reflection.learned_rule_candidate
        if not isinstance(candidate, dict):
            self._increment(self._memory_candidate_bridge_counts, "no_candidate")
            return
        if reflection.retention_hint != "candidate":
            self._increment(self._memory_candidate_bridge_counts, "retention_not_candidate")
            return
        if str(candidate.get("sensitivity") or "").strip().lower() == "review-only":
            self._increment(self._memory_candidate_bridge_counts, "review_only")
            return
        min_confidence = float(getattr(self.config, "memory_candidate_min_confidence", 0.85) or 0.85)
        confidence = max(0.0, min(float(candidate.get("confidence") or 0.0), 1.0))
        if confidence < min_confidence:
            self._increment(self._memory_candidate_bridge_counts, "below_confidence")
            return
        kind = str(candidate.get("kind") or "").strip().lower()
        if kind not in {"preference", "task_pattern", "constraint", "fact"}:
            self._increment(self._memory_candidate_bridge_counts, "invalid_kind")
            return
        owner_id = str(getattr(runtime_context, "user_id", "") or "user").strip() or "user"
        scope = "session" if kind == "task_pattern" else (redact_meta_text(candidate.get("scope_hint"), max_chars=80) or "user")
        metadata = {
            "origin": "meta_cognition",
            "reflection_id": reflection.reflection_id,
            "trigger_types": list(reflection.payload.get("trigger_types") or []),
            "retention_hint": reflection.retention_hint,
        }
        allowed_kinds = self._allowed_memory_candidate_kinds(reflection.payload)
        if kind not in allowed_kinds:
            self._increment(self._memory_candidate_bridge_counts, "kind_disallowed")
            return
        memory_candidate = MemoryCandidate(
            candidate_id=f"meta_memcand_{uuid.uuid4().hex}",
            kind=kind,  # type: ignore[arg-type]
            summary=redact_meta_text(candidate.get("summary"), max_chars=240),
            source_session_key=reflection.session_key,
            source_refs=redact_meta_list(candidate.get("supporting_refs"), max_items=6, max_chars=120),
            source_excerpt=redact_meta_text(reflection.summary or candidate.get("summary"), max_chars=220),
            confidence=confidence,
            sensitivity=redact_meta_text(candidate.get("sensitivity"), max_chars=40) or "low",
            scope=scope,
            owner_id=owner_id,
            created_at=reflection.created_at,
            metadata=metadata,
        )
        self._candidate_writer.append(memory_candidate)
        self._increment(self._memory_candidate_bridge_counts, "queued")

    @staticmethod
    def _load_json_payload(text: str) -> Any:
        payload = str(text or "").strip()
        if not payload:
            return None
        if payload.startswith("```"):
            lines = payload.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            payload = "\n".join(lines).strip()
            if not payload:
                return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            pass
        # Last resort: extract the first balanced JSON object from noisy text
        match = re.search(r'\{.*\}', payload, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None

    def recent_artifacts(self, *, limit: int = 10) -> dict[str, list[dict[str, Any]]]:
        return {
            "recent_journals": self.audit.recent_journals(limit=limit),
            "recent_reflections": self.audit.recent_reflections(limit=limit),
            "recent_confidence_traces": self.audit.recent_confidence_traces(limit=limit),
            "recent_patterns": self.audit.recent_patterns(limit=limit),
            "recent_evolution_seeds": self.audit.recent_evolution_seeds(limit=limit),
        }

    def _retrieve_historical_context(
        self,
        *,
        accepted_triggers: list[MetaTrigger],
        turn_snapshot: dict[str, Any],
        owner_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        trigger_types = {
            str(trigger.trigger_type or "").strip().lower()
            for trigger in accepted_triggers
            if str(trigger.trigger_type or "").strip()
        }
        query_text = " ".join(
            part
            for part in [
                str(turn_snapshot.get("user_message") or "").strip(),
                str(turn_snapshot.get("assistant_final_content") or "").strip(),
                str(turn_snapshot.get("previous_assistant_message") or "").strip(),
                " ".join(str(trigger.source_reference or "").strip() for trigger in accepted_triggers),
                " ".join(str(trigger.trigger_type or "").strip() for trigger in accepted_triggers),
            ]
            if part
        )
        pattern_candidates: list[tuple[float, str, dict[str, Any]]] = []
        for row in self.audit.recent_patterns(limit=_PATTERN_RETRIEVAL_LIMIT):
            if str(row.get("owner_id") or "").strip() != owner_id:
                continue
            row_trigger_types = {
                str(item or "").strip().lower()
                for item in list(row.get("trigger_types") or [])
                if str(item or "").strip()
            }
            if not (row_trigger_types & trigger_types):
                continue
            similarity = self._jaccard_similarity(
                query_text,
                " ".join(
                    [
                        str(row.get("summary") or ""),
                        " ".join(str(item or "") for item in list(row.get("trigger_types") or [])),
                        str(row.get("capability_domain") or ""),
                    ]
                ),
            )
            pattern_candidates.append(
                (
                    similarity,
                    str(row.get("updated_at") or ""),
                    {
                        "summary": redact_meta_text(row.get("summary"), max_chars=240),
                        "trigger_types": redact_meta_list(row.get("trigger_types"), max_items=6, max_chars=80),
                        "severity": redact_meta_text(row.get("severity"), max_chars=40),
                        "pattern_score": round(float(row.get("pattern_score") or 0.0), 4),
                        "updated_at": str(row.get("updated_at") or ""),
                    },
                )
            )
        reflection_candidates: list[tuple[float, str, dict[str, Any]]] = []
        for row in self.audit.recent_reflections(limit=_PATTERN_RETRIEVAL_LIMIT):
            payload = dict(row.get("payload") or {}) if isinstance(row.get("payload"), dict) else {}
            if str(payload.get("owner_id") or "").strip() != owner_id:
                continue
            row_trigger_types = {
                str(item or "").strip().lower()
                for item in list(payload.get("trigger_types") or [])
                if str(item or "").strip()
            }
            if not (row_trigger_types & trigger_types):
                continue
            similarity = self._jaccard_similarity(
                query_text,
                " ".join(
                    [
                        str(row.get("summary") or ""),
                        " ".join(str(item or "") for item in list(row.get("root_cause_hypotheses") or [])),
                        " ".join(str(item or "") for item in list(row.get("what_failed") or [])),
                    ]
                ),
            )
            reflection_candidates.append(
                (
                    similarity,
                    str(row.get("created_at") or ""),
                    {
                        "summary": redact_meta_text(row.get("summary"), max_chars=240),
                        "outcome_class": redact_meta_text(row.get("outcome_class"), max_chars=80),
                        "confidence": float(row.get("confidence") or 0.0),
                        "created_at": str(row.get("created_at") or ""),
                    },
                )
            )
        pattern_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        reflection_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return {
            "patterns": [item[2] for item in pattern_candidates[:_MAX_SIMILAR_PATTERNS]],
            "reflections": [item[2] for item in reflection_candidates[:_MAX_SIMILAR_REFLECTIONS]],
        }

    @staticmethod
    def _tokenize_for_similarity(text: Any) -> set[str]:
        normalized = _TOKEN_RE.sub(" ", str(text or "").strip().lower())
        return {token for token in normalized.split() if token}

    @classmethod
    def _jaccard_similarity(cls, left: Any, right: Any) -> float:
        left_tokens = cls._tokenize_for_similarity(left)
        right_tokens = cls._tokenize_for_similarity(right)
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    @staticmethod
    def _compute_uncertainty(
        *,
        reflection: ReflectionRecord,
        trace: ConfidenceTrace | None,
        trigger_count: int,
    ) -> tuple[float, list[str]]:
        confidence_values = [float(reflection.confidence or 0.0)]
        if trace is not None:
            confidence_values.append(float(trace.final_confidence or 0.0))
        avg_conf = sum(confidence_values) / max(1, len(confidence_values))
        reason_codes: list[str] = []
        missing_count = 0
        if avg_conf < 0.5:
            reason_codes.append("low_reflection_confidence")
        if not list(reflection.root_cause_hypotheses or []):
            missing_count += 1
            reason_codes.append("missing_root_cause_hypotheses")
        if not list(reflection.what_failed or []):
            missing_count += 1
            reason_codes.append("missing_what_failed")
        if not isinstance(reflection.learned_rule_candidate, dict):
            missing_count += 1
            reason_codes.append("missing_learned_rule_candidate")
        if not str(reflection.summary or "").strip():
            missing_count += 1
            reason_codes.append("missing_summary")
        evidence_refs = list(reflection.payload.get("evidence_refs") or []) if isinstance(reflection.payload, dict) else []
        evidence_count = len(
            {
                *[str(item or "").strip() for item in list(reflection.source_entry_ids or []) if str(item or "").strip()],
                *[str(item or "").strip() for item in evidence_refs if str(item or "").strip()],
            }
        )
        if evidence_count < 2:
            reason_codes.append("sparse_evidence")
        if trigger_count < 2:
            reason_codes.append("sparse_triggers")
        trace_gap = 0.0
        if trace is not None:
            trace_gap = min(abs(float(reflection.confidence or 0.0) - float(trace.final_confidence or 0.0)) / 0.5, 1.0)
            if trace_gap > 0.0:
                reason_codes.append("trace_reflection_mismatch")
        uncertainty_score = max(
            0.0,
            min(
                0.40 * (1.0 - avg_conf)
                + 0.25 * (missing_count / 4.0)
                + 0.15 * (1.0 if evidence_count < 2 else 0.0)
                + 0.10 * (1.0 if trigger_count < 2 else 0.0)
                + 0.10 * trace_gap,
                1.0,
            ),
        )
        unique_reasons: list[str] = []
        seen: set[str] = set()
        for reason in reason_codes:
            if reason in seen:
                continue
            seen.add(reason)
            unique_reasons.append(reason)
        return uncertainty_score, unique_reasons

    def _bridge_decision_counts(self) -> dict[str, int]:
        merged: dict[str, int] = {}
        for source in (
            self._working_memory_bridge_counts,
            self._memory_candidate_bridge_counts,
            self._evolution_bridge_counts,
        ):
            for key, value in source.items():
                merged[key] = merged.get(key, 0) + int(value or 0)
        return merged

    @staticmethod
    def _artifact_lookup(
        *,
        journals: list[dict[str, Any]],
        reflections: list[dict[str, Any]],
        patterns: list[ErrorPattern],
    ) -> dict[str, dict[str, Any]]:
        lookup: dict[str, dict[str, Any]] = {}
        for row in journals:
            entry_id = str(row.get("entry_id") or "").strip()
            if entry_id:
                lookup[f"meta:journal:{entry_id}"] = row
        for row in reflections:
            reflection_id = str(row.get("reflection_id") or "").strip()
            if reflection_id:
                lookup[f"meta:reflection:{reflection_id}"] = row
        for pattern in patterns:
            lookup[f"meta:pattern:{pattern.pattern_id}"] = pattern.to_json()
        return lookup

    @staticmethod
    def _allowed_memory_candidate_kinds(payload: dict[str, Any]) -> set[str]:
        contexts = list(payload.get("trigger_contexts") or [])
        allowed: set[str] = set()
        for item in contexts:
            if not isinstance(item, dict):
                continue
            trigger_type = str(item.get("trigger_type") or "").strip().lower()
            status = str(item.get("status") or "").strip().lower()
            if trigger_type == "user_correction":
                allowed.update({"preference", "fact", "constraint"})
            elif trigger_type == "task_completion":
                allowed.update({"task_pattern", "preference", "constraint"})
            elif trigger_type == "tool_failure" and status == "error":
                allowed.update({"task_pattern", "constraint"})
        return allowed

    def _remember(self, result: MetaReflectionResult, report: TaskRunReport) -> MetaReflectionResult:
        self._last_result = result
        self._last_report = report
        self._consecutive_failures = remember_report(
            report=report,
            current_failures=self._consecutive_failures,
        )
        return result

    @staticmethod
    def _increment(counter: dict[str, int], key: str) -> None:
        counter[key] = counter.get(key, 0) + 1
