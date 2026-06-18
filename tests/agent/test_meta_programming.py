from pathlib import Path
from types import SimpleNamespace

from OriginAgent.agent.evolution import OpportunitySignal, SIGNAL_KIND_WORKFLOW
from OriginAgent.agent.meta_cognition_audit import JsonlMetaCognitionAuditLedger
from OriginAgent.agent.meta_cognition_models import ErrorPattern, ReflectionRecord
from OriginAgent.agent.meta_programming import (
    COMPILER_VERSION,
    CompiledProposalBundle,
    MetaProgrammingCompilationStore,
    MetaProgrammingEngine,
)
from OriginAgent.config.schema import EvolutionConfig


def _workflow_signal(*, pattern_id: str) -> OpportunitySignal:
    return OpportunitySignal(
        opportunity_id="workflow:signal-1",
        kind=SIGNAL_KIND_WORKFLOW,
        target_key="deploy backend checks",
        title="Workflow candidate: deploy backend checks",
        summary="Repeated workflow-like request pattern: deploy backend checks",
        source_pattern_id=pattern_id,
        evidence_sources=[
            {
                "cursor": 1,
                "timestamp": "2026-05-20T10:00:00+00:00",
                "preview": "Run tests and inspect logs after backend deploys.",
            }
        ],
        seen_count=3,
        priority_score=0.92,
        risk_level="high",
    )


def _append_pattern(ledger: JsonlMetaCognitionAuditLedger, *, pattern_id: str) -> None:
    ledger.append_reflection(
        ReflectionRecord(
            reflection_id="reflection-1",
            session_key="websocket:chat-a",
            reflection_kind="failure_analysis",
            outcome_class="tool_failure",
            what_failed=["Backend deploy checks were skipped."],
            summary="Repeated backend deploy failures due to skipped checks.",
            retention_hint="candidate",
        )
    )
    ledger.append_pattern(
        ErrorPattern(
            pattern_id=pattern_id,
            pattern_key="meta.workflow.general_reasoning.tool_failure.deploy-backend-checks",
            owner_id="websocket:chat-a",
            source_reflection_ids=["reflection-1"],
            capability_domain="general_reasoning",
            severity="high",
            frequency=5,
            distinct_turn_count=3,
            candidate_target_type=SIGNAL_KIND_WORKFLOW,
            summary="Repeated backend deploy failures due to skipped checks.",
        )
    )


def test_compilation_store_dedupes_same_pattern_target_hash(tmp_path: Path) -> None:
    store = MetaProgrammingCompilationStore(tmp_path)
    bundle = CompiledProposalBundle(
        bundle_id="meta_bundle_workflow_hash-1",
        source_pattern_id="pattern-1",
        source_signal_id="workflow:signal-1",
        target_type="workflow",
        target_key="deploy backend checks",
        input_summary_hash="hash-1",
        compiler_version=COMPILER_VERSION,
        payload={"curator_key": "workflow:deploy", "target_state_hash": "hash-1"},
    )

    store.upsert_many([bundle])
    store.upsert_many([bundle])

    assert len(store.read_all()) == 1
    assert store.status()["compiled_bundle_count"] == 1


def test_compile_for_signals_adds_missing_primary_bundle_when_only_secondary_exists(tmp_path: Path) -> None:
    pattern_id = "pattern-1"
    signal = _workflow_signal(pattern_id=pattern_id)
    ledger = JsonlMetaCognitionAuditLedger(tmp_path)
    _append_pattern(ledger, pattern_id=pattern_id)
    store = MetaProgrammingCompilationStore(tmp_path)
    store.upsert_many([
        CompiledProposalBundle(
            bundle_id="meta_bundle_prompt_policy_hash-1",
            source_pattern_id=pattern_id,
            source_signal_id=signal.opportunity_id,
            target_type="prompt_policy",
            target_key=signal.target_key,
            input_summary_hash="hash-prompt",
            compiler_version=COMPILER_VERSION,
            payload={
                "curator_key": "meta-prompt-policy:workflow:signal-1",
                "target_state_hash": "hash-prompt",
            },
            review_only=True,
        )
    ])
    engine = MetaProgrammingEngine(
        tmp_path,
        meta_cognition_config=SimpleNamespace(evolution_bridge_enabled=True),
        evolution_config=EvolutionConfig(mode="curated", dry_run=False),
        audit=ledger,
        store=store,
    )

    lookup = engine.compile_for_signals(signals=[signal], limit_patterns=1)

    assert "prompt_policy" in lookup[signal.opportunity_id]
    assert "workflow" in lookup[signal.opportunity_id]
    assert lookup[signal.opportunity_id]["workflow"].payload["meta_programming"]["source_pattern_id"] == pattern_id
