"""Dream consolidation phases — extracted from Dream.run().

Each phase is independently testable with explicit inputs and return types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:

    from OriginAgent.agent.memory import Dream


# ── Public result types ──────────────────────────────────────────────────────


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


@dataclass
class Phase1Result:
    """Result of Phase 1 (LLM fact proposal + parsing + application)."""

    success: bool = False
    proposal_json: str | None = None
    proposal_batch: list[dict] | None = None
    apply_result: Any | None = None
    post_apply_hashes: Any | None = None
    error_reason: str | None = None


@dataclass
class Phase2Result:
    """Result of Phase 2 (AgentRunner maintenance)."""

    success: bool = False
    result: Any | None = None


# ── Phase 1: LLM fact proposal ───────────────────────────────────────────────


async def run_phase1(
    dream: Dream,
    *,
    started_at: str,
    history_text: str,
    file_context: str,
    facts_context: str,
    batch: list[dict],
    snapshot: Any,
) -> Phase1Result:
    """Propose and apply structured facts from conversation history via LLM.

    Handles the full Phase 1 pipeline: prompt building, LLM call, response
    parsing, fact application, and error recovery (snapshot restore).
    Returns a ``Phase1Result`` — check ``.success`` before proceeding.
    """
    from OriginAgent.agent.auxiliary_llm import call_llm
    from OriginAgent.agent.facts import parse_fact_proposal_response
    from OriginAgent.agent.memory import _STALE_THRESHOLD_DAYS
    from OriginAgent.agent.runtime_models import now_iso
    from OriginAgent.agent.task_runtime import build_task_report
    from OriginAgent.utils.prompt_templates import render_template

    phase1_prompt = (
        f"## Conversation History\n{history_text}\n\n"
        f"## Current Facts\n{facts_context}\n\n"
        f"{file_context}"
    )

    # ── LLM call ─────────────────────────────────────────────────────────
    try:
        phase1_response = await call_llm(
            task="dream_phase1",
            router=dream.auxiliary_router,
            provider=dream.provider,
            model=dream.model,
            messages=[
                {
                    "role": "system",
                    "content": render_template(
                        "agent/dream_phase1.md",
                        strip=True,
                        stale_threshold_days=_STALE_THRESHOLD_DAYS,
                    ),
                },
                {"role": "user", "content": phase1_prompt},
            ],
            tools=None,
            tool_choice=None,
        )
        proposal_json = phase1_response.content or ""
        logger.debug(
            "Dream Phase 1 fact proposal JSON ({} chars): {}",
            len(proposal_json),
            proposal_json[:500],
        )
    except Exception:
        logger.exception("Dream Phase 1 failed")
        dream._remember_report(build_task_report(
            task_name="dream",
            status="error",
            phase="phase1",
            fault_class="external",
            retryable=True,
            reason="phase1_failed",
            started_at=started_at,
            finished_at=now_iso(),
        ))
        return Phase1Result(success=False, error_reason="phase1_failed")

    # ── Parse ────────────────────────────────────────────────────────────
    try:
        proposal_batch = parse_fact_proposal_response(proposal_json)
    except Exception:
        logger.exception("Dream Phase 1 returned invalid fact proposal JSON")
        if not snapshot.restore():
            logger.error("Dream parse failure: snapshot restore failed")
            dream._remember_report(build_task_report(
                task_name="dream",
                status="blocked",
                phase="phase1_parse",
                fault_class="restore",
                reason="phase1_parse_restore_failed",
                started_at=started_at,
                finished_at=now_iso(),
            ))
        else:
            dream._remember_report(build_task_report(
                task_name="dream",
                status="error",
                phase="phase1_parse",
                fault_class="invariant",
                reason="phase1_invalid_json",
                started_at=started_at,
                finished_at=now_iso(),
            ))
        return Phase1Result(success=False, error_reason="phase1_parse_failed")

    # ── Apply fact proposals ────────────────────────────────────────────
    try:
        apply_result = dream.store.apply_fact_proposals_and_rebuild_memory(
            proposal_batch,
            history_entries=batch,
        )
    except Exception:
        logger.exception("Dream fact proposal apply failed")
        if not snapshot.restore():
            logger.error("Dream fact apply failure: snapshot restore failed")
            dream._remember_report(build_task_report(
                task_name="dream",
                status="blocked",
                phase="apply",
                fault_class="restore",
                reason="fact_apply_restore_failed",
                started_at=started_at,
                finished_at=now_iso(),
            ))
        else:
            dream._remember_report(build_task_report(
                task_name="dream",
                status="error",
                phase="apply",
                fault_class="io",
                reason="fact_apply_failed",
                started_at=started_at,
                finished_at=now_iso(),
            ))
        return Phase1Result(success=False, error_reason="fact_apply_failed")

    logger.info(
        "Dream fact proposals: active={} pending={} rejected={} deprecated={}",
        len(apply_result.accepted),
        len(apply_result.pending),
        len(apply_result.rejected) + len(apply_result.parse_rejected),
        len(apply_result.deprecated),
    )
    post_apply_hashes = dream._memory_fact_hashes()

    return Phase1Result(
        success=True,
        proposal_json=proposal_json,
        proposal_batch=proposal_batch,
        apply_result=apply_result,
        post_apply_hashes=post_apply_hashes,
    )


# ── Phase 2: AgentRunner maintenance ─────────────────────────────────────────


async def run_phase2(
    dream: Dream,
    *,
    started_at: str,
    file_context: str,
    proposal_json: str,
    apply_result: Any,
) -> Phase2Result:
    """Delegate non-MEMORY maintenance to AgentRunner.

    Builds a Phase 2 prompt incorporating the fact proposal results and
    current file context, then dispatches to the AgentRunner for tool-based
    maintenance (skill creation, file updates, etc.).
    """
    from OriginAgent.agent.runner import AgentRunSpec
    from OriginAgent.agent.skills import BUILTIN_SKILLS_DIR
    from OriginAgent.utils.prompt_templates import render_template

    existing_skills = dream._list_existing_skills()
    skills_section = ""
    if existing_skills:
        skills_section = (
            "\n\n## Existing Skills\n"
            + "\n".join(f"- {s}" for s in existing_skills)
        )
    phase2_prompt = (
        f"## Fact Proposal Apply Result\n"
        f"{dream._format_apply_result(apply_result)}\n\n"
        f"## Fact Proposal JSON\n{proposal_json}\n\n"
        f"{file_context}{skills_section}"
    )

    tools = dream._tools
    skill_creator_path = BUILTIN_SKILLS_DIR / "skill-creator" / "SKILL.md"
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": render_template(
                "agent/dream_phase2.md",
                strip=True,
                skill_creator_path=str(skill_creator_path),
            ),
        },
        {"role": "user", "content": phase2_prompt},
    ]

    try:
        result = await dream._runner.run(AgentRunSpec(
            initial_messages=messages,
            tools=tools,
            model=dream.model,
            max_iterations=dream.max_iterations,
            max_tool_result_chars=dream.max_tool_result_chars,
            fail_on_tool_error=False,
        ))
        logger.debug(
            "Dream Phase 2 complete: stop_reason={}, tool_events={}",
            result.stop_reason, len(result.tool_events),
        )
        for ev in (result.tool_events or []):
            logger.info(
                "Dream tool_event: name={}, status={}, detail={}",
                ev.get("name"), ev.get("status"), str(ev.get("detail", ""))[:200],
            )
    except Exception:
        logger.exception("Dream Phase 2 failed")
        return Phase2Result(success=False, result=None)

    return Phase2Result(success=True, result=result)
