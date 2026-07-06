"""Central factory for all SQLite-backed stores.

Creates, migrates, and provides access to every SQLite store in OriginAgent.
``agent_loop_components.py`` imports from here instead of instantiating
individual stores inline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

# ── Specialized stores ──────────────────────────────────────────────
from OriginAgent.agent.active_intents_sqlite import ActiveIntentLedgerSqlite
from OriginAgent.agent.cognitive_audit_sqlite import (
    CognitiveDecisionsSqlite,
    CognitiveEventsSqlite,
)
from OriginAgent.agent.domain_pack_events_sqlite import DomainPackEventsSqlite
from OriginAgent.agent.facts import FactEventStoreSqlite
from OriginAgent.agent.facts_sqlite import FactStoreSqlite
from OriginAgent.agent.meta_cognition_audit_sqlite import (
    MetaConfidenceTracesSqlite,
    MetaDecisionsSqlite,
    MetaEvolutionSeedsSqlite,
    MetaJournalsSqlite,
    MetaPatternsSqlite,
    MetaReflectionsSqlite,
    MetaTriggersSqlite,
)
from OriginAgent.agent.reminders_sqlite import ReminderStoreSqlite
from OriginAgent.agent.skill_lifecycle_sqlite import SkillLifecycleStoreSqlite
from OriginAgent.agent.subagent_records_sqlite import (
    SubagentLifecycleStoreSqlite,
    SubagentTaskStoreSqlite,
    SubagentToolStoreSqlite,
)

# ── Tier 1 stores (append-only) ─────────────────────────────────────
from OriginAgent.agent.tier1_stores_sqlite import (
    CausalEdgesSqlite,
    EvolutionConfigPatchesSqlite,
    EvolutionDependenciesSqlite,
    EvolutionHealthHistorySqlite,
    EvolutionOutcomesSqlite,
    EvolutionSandboxCacheSqlite,
    EvolutionTrialLogsSqlite,
    MetaProgrammingCompilationsSqlite,
    SimulationFeedbackSqlite,
    SimulationTracesSqlite,
    ThoughtFramesSqlite,
    ThoughtJournalsSqlite,
)

# ── Tier 2 stores (append-only / RMW) ───────────────────────────────
from OriginAgent.agent.tier2_stores_sqlite import (
    DeliberationCyclesSqlite,
    IdempotencyKeysSqlite,
    MemoryCandidatesSqlite,
    SchedulerRunsSqlite,
)

# ── Tier 3 stores (RMW) ─────────────────────────────────────────────
from OriginAgent.agent.tier3_stores_sqlite import (
    FactRelationStoreSqlite,
    ReviewProposalEventStoreSqlite,
    ReviewProposalStoreSqlite,
)
from OriginAgent.agent.tools.audit_sqlite import ToolCallAuditSqlite
from OriginAgent.bdi.desire_store import DesireStoreSqlite
from OriginAgent.bdi.plan_library_sqlite import PlanLibrarySqlite
from OriginAgent.storage.jsonl_migration import MigrationResult


def _migrate_one(store: object, label: str) -> None:
    """Run migrate() on *store* and log the result."""
    if not hasattr(store, "migrate"):
        return
    try:
        result: MigrationResult = store.migrate()
        if result.records_imported or result.records_skipped:
            logger.info(
                "SQLite migration [{}]: {} imported, {} skipped{}",
                label,
                result.records_imported,
                result.records_skipped,
                f" ({result.error})" if result.error else "",
            )
    except Exception:
        logger.exception("SQLite migration [{}] failed", label)


@dataclass
class SqliteStoreRegistry:
    """Container for all SQLite store instances."""

    # Tier 1
    thought_frames: ThoughtFramesSqlite
    thought_journals: ThoughtJournalsSqlite
    causal_edges: CausalEdgesSqlite
    simulation_traces: SimulationTracesSqlite
    simulation_feedback: SimulationFeedbackSqlite
    evolution_sandbox_cache: EvolutionSandboxCacheSqlite
    evolution_outcomes: EvolutionOutcomesSqlite
    evolution_dependencies: EvolutionDependenciesSqlite
    evolution_health_history: EvolutionHealthHistorySqlite
    evolution_trial_logs: EvolutionTrialLogsSqlite
    evolution_config_patches: EvolutionConfigPatchesSqlite
    meta_programming_compilations: MetaProgrammingCompilationsSqlite

    # Tier 2
    memory_candidates: MemoryCandidatesSqlite
    scheduler_runs: SchedulerRunsSqlite
    deliberation_cycles: DeliberationCyclesSqlite
    idempotency_keys: IdempotencyKeysSqlite

    # Tier 3
    fact_relations: FactRelationStoreSqlite
    review_proposals: ReviewProposalStoreSqlite
    review_proposal_events: ReviewProposalEventStoreSqlite

    # Meta-cognition audit
    meta_triggers: MetaTriggersSqlite
    meta_decisions: MetaDecisionsSqlite
    meta_journals: MetaJournalsSqlite
    meta_reflections: MetaReflectionsSqlite
    meta_confidence_traces: MetaConfidenceTracesSqlite
    meta_patterns: MetaPatternsSqlite
    meta_evolution_seeds: MetaEvolutionSeedsSqlite

    # Cognitive audit
    cognitive_events: CognitiveEventsSqlite
    cognitive_decisions: CognitiveDecisionsSqlite

    # Subagent records
    subagent_tasks: SubagentTaskStoreSqlite
    subagent_lifecycle: SubagentLifecycleStoreSqlite
    subagent_tools: SubagentToolStoreSqlite

    # Core stores
    fact_store: FactStoreSqlite
    desires: DesireStoreSqlite
    plans: PlanLibrarySqlite

    # Misc specialized
    active_intents: ActiveIntentLedgerSqlite
    tool_audit: ToolCallAuditSqlite
    domain_pack_events: DomainPackEventsSqlite
    skill_lifecycle: SkillLifecycleStoreSqlite
    reminders: ReminderStoreSqlite
    fact_events: FactEventStoreSqlite


class SqliteStoreFactory:
    """Creates all SQLite stores and runs one-time migration."""

    @staticmethod
    def create_all(workspace: Path) -> SqliteStoreRegistry:
        w = Path(workspace)

        # Tier 1
        thought_frames = ThoughtFramesSqlite(w)
        thought_journals = ThoughtJournalsSqlite(w)
        causal_edges = CausalEdgesSqlite(w)
        simulation_traces = SimulationTracesSqlite(w)
        simulation_feedback = SimulationFeedbackSqlite(w)
        evolution_sandbox_cache = EvolutionSandboxCacheSqlite(w)
        evolution_outcomes = EvolutionOutcomesSqlite(w)
        evolution_dependencies = EvolutionDependenciesSqlite(w)
        evolution_health_history = EvolutionHealthHistorySqlite(w)
        evolution_trial_logs = EvolutionTrialLogsSqlite(w)
        evolution_config_patches = EvolutionConfigPatchesSqlite(w)
        meta_programming_compilations = MetaProgrammingCompilationsSqlite(w)

        # Tier 2
        memory_candidates = MemoryCandidatesSqlite(w)
        scheduler_runs = SchedulerRunsSqlite(w)
        deliberation_cycles = DeliberationCyclesSqlite(w)
        idempotency_keys = IdempotencyKeysSqlite(w)

        # Tier 3
        fact_relations = FactRelationStoreSqlite(w)
        review_proposals = ReviewProposalStoreSqlite(w)
        review_proposal_events = ReviewProposalEventStoreSqlite(w)

        # Meta-cognition (7 tables)
        meta_triggers = MetaTriggersSqlite(w)
        meta_decisions = MetaDecisionsSqlite(w)
        meta_journals = MetaJournalsSqlite(w)
        meta_reflections = MetaReflectionsSqlite(w)
        meta_confidence_traces = MetaConfidenceTracesSqlite(w)
        meta_patterns = MetaPatternsSqlite(w)
        meta_evolution_seeds = MetaEvolutionSeedsSqlite(w)

        # Cognitive audit
        cognitive_events = CognitiveEventsSqlite(w)
        cognitive_decisions = CognitiveDecisionsSqlite(w)

        # Subagent records
        subagent_tasks = SubagentTaskStoreSqlite(w)
        subagent_lifecycle = SubagentLifecycleStoreSqlite(w)
        subagent_tools = SubagentToolStoreSqlite(w)

        # Misc specialized
        active_intents = ActiveIntentLedgerSqlite(w)
        tool_audit = ToolCallAuditSqlite(w)
        domain_pack_events = DomainPackEventsSqlite(w)
        skill_lifecycle = SkillLifecycleStoreSqlite(w)
        reminders = ReminderStoreSqlite(w)
        fact_events = FactEventStoreSqlite(w)
        fact_store = FactStoreSqlite(w)
        desires = DesireStoreSqlite(w)
        plans = PlanLibrarySqlite(w)

        registry = SqliteStoreRegistry(
            thought_frames=thought_frames,
            thought_journals=thought_journals,
            causal_edges=causal_edges,
            simulation_traces=simulation_traces,
            simulation_feedback=simulation_feedback,
            evolution_sandbox_cache=evolution_sandbox_cache,
            evolution_outcomes=evolution_outcomes,
            evolution_dependencies=evolution_dependencies,
            evolution_health_history=evolution_health_history,
            evolution_trial_logs=evolution_trial_logs,
            evolution_config_patches=evolution_config_patches,
            meta_programming_compilations=meta_programming_compilations,
            memory_candidates=memory_candidates,
            scheduler_runs=scheduler_runs,
            deliberation_cycles=deliberation_cycles,
            idempotency_keys=idempotency_keys,
            fact_relations=fact_relations,
            review_proposals=review_proposals,
            review_proposal_events=review_proposal_events,
            meta_triggers=meta_triggers,
            meta_decisions=meta_decisions,
            meta_journals=meta_journals,
            meta_reflections=meta_reflections,
            meta_confidence_traces=meta_confidence_traces,
            meta_patterns=meta_patterns,
            meta_evolution_seeds=meta_evolution_seeds,
            cognitive_events=cognitive_events,
            cognitive_decisions=cognitive_decisions,
            subagent_tasks=subagent_tasks,
            subagent_lifecycle=subagent_lifecycle,
            subagent_tools=subagent_tools,
            active_intents=active_intents,
            tool_audit=tool_audit,
            domain_pack_events=domain_pack_events,
            skill_lifecycle=skill_lifecycle,
            reminders=reminders,
            fact_events=fact_events,
            fact_store=fact_store,
            desires=desires,
            plans=plans,
        )

        # Run all migrations (idempotent — safe on every startup)
        _migrate_one(fact_store, "fact_store")
        _migrate_one(desires, "desires")
        _migrate_one(plans, "plans")
        _migrate_one(thought_frames, "thought_frames")
        _migrate_one(thought_journals, "thought_journals")
        _migrate_one(causal_edges, "causal_edges")
        _migrate_one(simulation_traces, "simulation_traces")
        _migrate_one(simulation_feedback, "simulation_feedback")
        _migrate_one(evolution_sandbox_cache, "evolution_sandbox_cache")
        _migrate_one(evolution_outcomes, "evolution_outcomes")
        _migrate_one(evolution_dependencies, "evolution_dependencies")
        _migrate_one(evolution_health_history, "evolution_health_history")
        _migrate_one(evolution_trial_logs, "evolution_trial_logs")
        _migrate_one(evolution_config_patches, "evolution_config_patches")
        _migrate_one(meta_programming_compilations, "meta_programming_compilations")
        _migrate_one(memory_candidates, "memory_candidates")
        _migrate_one(scheduler_runs, "scheduler_runs")
        _migrate_one(deliberation_cycles, "deliberation_cycles")
        _migrate_one(idempotency_keys, "idempotency_keys")
        _migrate_one(fact_relations, "fact_relations")
        _migrate_one(review_proposals, "review_proposals")
        _migrate_one(review_proposal_events, "review_proposal_events")
        _migrate_one(meta_triggers, "meta_triggers")
        _migrate_one(meta_decisions, "meta_decisions")
        _migrate_one(meta_journals, "meta_journals")
        _migrate_one(meta_reflections, "meta_reflections")
        _migrate_one(meta_confidence_traces, "meta_confidence_traces")
        _migrate_one(meta_patterns, "meta_patterns")
        _migrate_one(meta_evolution_seeds, "meta_evolution_seeds")
        _migrate_one(cognitive_events, "cognitive_events")
        _migrate_one(cognitive_decisions, "cognitive_decisions")
        _migrate_one(subagent_tasks, "subagent_tasks")
        _migrate_one(subagent_lifecycle, "subagent_lifecycle")
        _migrate_one(subagent_tools, "subagent_tools")
        _migrate_one(active_intents, "active_intents")
        _migrate_one(tool_audit, "tool_audit")
        _migrate_one(domain_pack_events, "domain_pack_events")
        _migrate_one(skill_lifecycle, "skill_lifecycle")
        _migrate_one(reminders, "reminders")
        _migrate_one(fact_events, "fact_events")

        return registry


__all__ = ["SqliteStoreFactory", "SqliteStoreRegistry"]
