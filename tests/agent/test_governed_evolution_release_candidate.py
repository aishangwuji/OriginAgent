from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from OriginAgent.agent.background_review import ReviewProposalStore
from OriginAgent.agent.curator import CuratorService
from OriginAgent.agent.evolution import (
    AUTO_EVOLUTION_ORIGIN,
    OpportunitySignalStore,
    detect_workflow_opportunity_candidates,
)
from OriginAgent.agent.evolution_maintenance import run_evolution_maintenance
from OriginAgent.agent.evolution_operator import EvolutionOperator
from OriginAgent.agent.evolution_outcomes import EvolutionOutcomeStore
from OriginAgent.agent.evolution_schema import validate_evolution_stores, validate_review_proposal_payload
from OriginAgent.agent.evolution_trial_logs import EvolutionTrialLogStore
from OriginAgent.agent.memory import MemoryStore
from OriginAgent.config.schema import EvolutionConfig


@pytest.mark.asyncio
async def test_governed_evolution_release_candidate_workflow_loop(tmp_path) -> None:
    memory = MemoryStore(tmp_path)
    entries = [
        "Every time we deploy backend, run tests and check logs before release.",
        "Every time we deploy backend, run tests and check logs before release.",
        "Every time we deploy backend, run tests and check logs before release.",
    ]
    cursors = [memory.append_history(entry) for entry in entries]
    history = memory.read_unprocessed_history(since_cursor=0)
    candidates = detect_workflow_opportunity_candidates(history, min_evidence_sources=2)
    signal_store = OpportunitySignalStore(tmp_path)
    signals = signal_store.upsert_candidates(candidates)
    config = EvolutionConfig(mode="curated", dry_run=False)
    review_store = ReviewProposalStore(tmp_path)
    curator = CuratorService(
        workspace=tmp_path,
        config=SimpleNamespace(enabled=True, max_proposals_per_run=12),
        evolution_config=config,
        store=review_store,
    )

    curator_result = await curator.review_workspace(session_key="curator:system", turn_id="turn-rc")
    records = review_store.list_records(origin=AUTO_EVOLUTION_ORIGIN, status="pending", limit=10)
    assert curator_result.status == "ok"
    assert curator_result.proposals_written == 1
    assert len(signals) == 1
    assert signals[0].seen_count == 3
    assert [item.get("cursor") for item in signals[0].evidence_sources] == cursors
    assert len(records) == 1

    proposal = records[0]
    proposal_id = str(proposal["id"])
    payload = proposal["payload"]
    assert validate_review_proposal_payload(payload) == []
    assert payload["evolution"]["origin"] == AUTO_EVOLUTION_ORIGIN
    assert payload["sandbox"]["status"] == "passed"
    assert payload["promotion_gate"]["decision"] == "pass"
    assert payload["operator_insights"]["recommended_action"] == "review_required"

    operator = EvolutionOperator(tmp_path, config)
    preview = operator.preview_action(
        "retry_trial",
        target_id=proposal_id,
        fixtures={"notes.txt": "Release candidate fixture."},
    )
    assert preview["ok"] is True
    assert preview["will_write"] is False
    assert preview["preview"]["trial_gate_status"] == "passed"
    assert EvolutionTrialLogStore(tmp_path).read_all() == []
    assert "trial" not in (review_store.get(proposal_id) or {})["payload"]

    recommendations = operator.list_recommendations()
    recommendation = next(item for item in recommendations if item["code"] == "pending_evolution_proposal")
    assert recommendation["action_kind"] == "inspect_evolution_proposal"
    assert recommendation["requires_manual_override"] is False
    assert recommendation["target_id"] == proposal_id

    applied = review_store.apply(proposal_id, reason="release candidate manual apply")
    assert applied.ok is True
    assert applied.status == "applied"
    workflow_path = tmp_path / str(payload["subject_path"])
    assert workflow_path.is_file()
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    metadata = workflow["metadata"]["OriginAgent"]
    assert metadata["proposal_status"] == "proposed"
    assert metadata["verification_status"] == "unverified"
    assert workflow["execution"] == {
        "auto_run": False,
        "creates_cron": False,
        "calls_tools": False,
    }

    outcome_stats = EvolutionOutcomeStore(tmp_path).stats()
    assert outcome_stats["outcome_type_counts"]["signal_created"] == 1
    assert outcome_stats["outcome_type_counts"]["gate_evaluated"] == 1
    assert outcome_stats["outcome_type_counts"]["proposal_generated"] == 1
    assert outcome_stats["outcome_type_counts"]["review_approved"] == 1
    assert "trial_retried" not in outcome_stats["outcome_type_counts"]

    maintenance = run_evolution_maintenance(tmp_path, config)
    schema = validate_evolution_stores(tmp_path)
    report = operator.generate_report(period_days=7)
    assert maintenance["schema_validation"]["ok"] is True
    assert schema["ok"] is True
    assert schema["record_counts"]["opportunity_signals"] == 1
    assert schema["record_counts"]["review_proposals"] == 1
    assert schema["record_counts"]["outcomes"] >= 4
    assert "Schema validation: ok" in report
    assert "No recommendation automatically activates artifacts." in report
