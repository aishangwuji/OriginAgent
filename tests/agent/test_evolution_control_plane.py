from __future__ import annotations

from pathlib import Path

import yaml

from OriginAgent.agent.background_review import ReviewProposal, ReviewProposalStore
from OriginAgent.agent.evolution import (
    AUTO_EVOLUTION_ORIGIN,
    SIGNAL_KIND_WORKFLOW,
    OpportunitySignalCandidate,
    OpportunitySignalStore,
    build_workflow_payload_from_signal,
)
from OriginAgent.agent.evolution_control_plane import (
    ACTION_RESULT_SCHEMA_VERSION,
    ACTION_SCHEMA_VERSION,
    CONTROL_EVENT_DENIED,
    CONTROL_EVENT_EXECUTED,
    EVOLUTION_MANUAL_OVERRIDE_DISABLED,
    EvolutionControlPlane,
)
from OriginAgent.agent.evolution_outcomes import EvolutionOutcomeStore
from OriginAgent.agent.evolution_snapshots import EvolutionSnapshotStore
from OriginAgent.agent.evolution_trial_logs import EvolutionTrialLogStore
from OriginAgent.config.schema import EvolutionConfig


def _candidate(*, target: str = "deploy backend checks") -> OpportunitySignalCandidate:
    return OpportunitySignalCandidate(
        kind=SIGNAL_KIND_WORKFLOW,
        target_key=target,
        title=f"Workflow candidate: {target}",
        summary=f"Repeated workflow-like request pattern: {target}",
        evidence_sources=[
            {
                "cursor": cursor,
                "timestamp": f"2026-05-2{cursor}T10:00:00+00:00",
                "preview": "Every time we deploy backend, run tests and check logs.",
            }
            for cursor in (1, 2, 3)
        ],
    )


def _append_retryable_proposal(tmp_path: Path) -> tuple[str, str]:
    signal = OpportunitySignalStore(tmp_path).upsert_candidates([_candidate()])[0]
    payload = build_workflow_payload_from_signal(signal, config=EvolutionConfig())
    payload["steps"] = [{"title": "Read notes", "tool": "read_file", "path": "notes.txt"}]
    proposal_id = "review_auto_workflow_control_plane"
    ReviewProposalStore(tmp_path).append_many([
        ReviewProposal(
            id=proposal_id,
            created_at="2026-05-20T10:00:00+00:00",
            session_key="curator:system",
            turn_id="turn-1",
            origin=AUTO_EVOLUTION_ORIGIN,
            proposal_type="workflow",
            domain_id="core",
            title="Create workflow",
            content="Create a reviewed workflow.",
            payload=payload,
            confidence=signal.priority_score,
        )
    ])
    return proposal_id, signal.opportunity_id


def test_control_plane_status_and_read_model(tmp_path: Path) -> None:
    proposal_id, signal_id = _append_retryable_proposal(tmp_path)
    plane = EvolutionControlPlane(tmp_path, EvolutionConfig())

    status = plane.status()
    actions = plane.list_actions()
    signals = plane.list_signals()
    proposals = plane.list_proposals()

    assert status["control_plane"]["version"] == "originagent.evolution.control_plane.v2"
    assert status["policy"]["permissions"]["read"] is True
    assert status["policy"]["permissions"]["apply"] is False
    assert status["read_model"]["signals"]["open"] == 1
    assert status["read_model"]["proposals"]["total"] == 1
    assert status["schema_validation"]["record_counts"]["opportunity_signals"] == 1
    assert signals["signals"][0]["opportunity_id"] == signal_id
    assert proposals["proposals"][0]["proposal_id"] == proposal_id
    assert actions["schema_version"] == ACTION_SCHEMA_VERSION
    action_map = {item["action_kind"]: item for item in actions["actions"]}
    assert len(action_map) == len(actions["actions"])
    assert action_map["status"]["permission"] == "read"
    assert action_map["status"]["action_id"] == "status"
    assert action_map["status"]["schema_version"] == ACTION_SCHEMA_VERSION
    assert action_map["status"]["previewable"] is True
    assert action_map["status"]["executable"] is True
    assert action_map["status"]["suggested_my_action"] == "my action=evolution_status"
    assert action_map["validate_schema"]["suggested_my_action"] == "my action=validate_evolution_schema"
    assert action_map["suppress_signal"]["requires_manual_override"] is True
    assert action_map["suppress_signal"]["parameters_schema"]["required"] == ["target_id"]
    assert action_map["rollback_artifact"]["permission"] == "rollback"
    assert action_map["rollback_artifact"]["risk_level"] == "high"


def test_control_plane_recommendations_include_action_descriptor(tmp_path: Path) -> None:
    proposal_id, _ = _append_retryable_proposal(tmp_path)
    plane = EvolutionControlPlane(tmp_path, EvolutionConfig())

    recommendations = plane.list_recommendations()

    pending = next(item for item in recommendations if item["code"] == "pending_evolution_proposal")
    descriptor = pending["action_descriptor"]
    assert pending["target_id"] == proposal_id
    assert pending["previewable"] is True
    assert pending["executable"] is True
    assert pending["suggested_next_step"] == "execute_or_inspect"
    assert descriptor["schema_version"] == ACTION_SCHEMA_VERSION
    assert descriptor["action_id"] == f"inspect_evolution_proposal:{proposal_id}"
    assert descriptor["target_type"] == "proposal"
    assert descriptor["target_id"] == proposal_id
    assert descriptor["permission"] == "read"
    assert descriptor["suggested_my_action"] == f"my action=inspect_evolution_proposal key={proposal_id}"


def test_control_plane_previews_do_not_write_governed_state(tmp_path: Path) -> None:
    proposal_id, signal_id = _append_retryable_proposal(tmp_path)
    plane = EvolutionControlPlane(tmp_path, EvolutionConfig())

    retry_preview = plane.preview_action(
        "retry_trial",
        target_id=proposal_id,
        fixtures={"notes.txt": "Trial fixture."},
    )
    suppress_preview = plane.preview_action(
        "suppress_signal",
        target_id=signal_id,
        reason="too noisy",
    )

    record = ReviewProposalStore(tmp_path).get(proposal_id)
    signal = OpportunitySignalStore(tmp_path).read_all()[0]
    outcomes = EvolutionOutcomeStore(tmp_path).stats()
    assert retry_preview["ok"] is True
    assert retry_preview["schema_version"] == ACTION_RESULT_SCHEMA_VERSION
    assert retry_preview["will_write"] is False
    assert retry_preview["policy"]["permission"] == "override"
    assert retry_preview["action"]["action_id"] == f"retry_trial:{proposal_id}"
    assert suppress_preview["preview"]["next_status"] == "suppressed"
    assert record is not None
    assert "trial" not in record["payload"]
    assert signal.status == "open"
    assert EvolutionTrialLogStore(tmp_path).read_all() == []
    assert not any(name.startswith("control_action_") for name in outcomes["outcome_type_counts"])
    assert "trial_retried" not in outcomes["outcome_type_counts"]


def test_control_plane_write_requires_manual_override(tmp_path: Path) -> None:
    _, signal_id = _append_retryable_proposal(tmp_path)
    plane = EvolutionControlPlane(tmp_path, EvolutionConfig())

    result = plane.execute_action("suppress_signal", target_id=signal_id, reason="test")

    signal = OpportunitySignalStore(tmp_path).read_all()[0]
    outcome_stats = EvolutionOutcomeStore(tmp_path).stats()
    assert result["ok"] is False
    assert result["allowed"] is False
    assert result["error"] == "manual_override_disabled"
    assert result["message"] == EVOLUTION_MANUAL_OVERRIDE_DISABLED
    assert result["will_write"] is False
    assert result["action"]["requires_manual_override"] is True
    assert signal.status == "open"
    assert "signal_suppressed" not in outcome_stats["outcome_type_counts"]
    assert outcome_stats["outcome_type_counts"][CONTROL_EVENT_DENIED] == 1
    event = EvolutionOutcomeStore(tmp_path).read_all()[-1]
    assert event["type"] == CONTROL_EVENT_DENIED
    assert event["opportunity_id"] == signal_id
    assert event["metadata"]["action_kind"] == "suppress_signal"
    assert event["metadata"]["policy_decision"] == "denied"


def test_control_plane_executes_write_when_manual_override_enabled(tmp_path: Path) -> None:
    _, signal_id = _append_retryable_proposal(tmp_path)
    plane = EvolutionControlPlane(tmp_path, EvolutionConfig(allow_manual_override=True))

    suppressed = plane.execute_action(
        "suppress_signal",
        target_id=signal_id,
        reason="not useful",
        actor="operator-a",
        source="unit-test",
    )
    resumed = plane.execute_action(
        "resume_signal",
        target_id=signal_id,
        reason="reconsider",
        actor="operator-a",
        source="unit-test",
    )

    signal = OpportunitySignalStore(tmp_path).read_all()[0]
    outcome_stats = EvolutionOutcomeStore(tmp_path).stats()
    assert suppressed["ok"] is True
    assert suppressed["will_write"] is True
    assert suppressed["result"]["status"] == "suppressed"
    assert resumed["ok"] is True
    assert resumed["result"]["status"] == "open"
    assert signal.status == "open"
    assert outcome_stats["outcome_type_counts"]["signal_suppressed"] == 1
    assert outcome_stats["outcome_type_counts"]["signal_resumed"] == 1
    assert outcome_stats["outcome_type_counts"][CONTROL_EVENT_EXECUTED] == 2
    control_events = [
        event for event in EvolutionOutcomeStore(tmp_path).read_all()
        if event.get("type") == CONTROL_EVENT_EXECUTED
    ]
    assert control_events[-1]["metadata"]["actor"] == "operator-a"
    assert control_events[-1]["metadata"]["source"] == "unit-test"
    assert control_events[-1]["metadata"]["policy_decision"] == "allowed"


def test_control_plane_read_execute_and_unknown_policy(tmp_path: Path) -> None:
    plane = EvolutionControlPlane(tmp_path, EvolutionConfig())

    read_result = plane.execute_action("status")
    unknown = plane.execute_action("not_a_real_action")

    assert read_result["ok"] is True
    assert read_result["schema_version"] == ACTION_RESULT_SCHEMA_VERSION
    assert read_result["allowed"] is True
    assert read_result["policy"]["permission"] == "read"
    assert read_result["will_write"] is False
    assert read_result["action"]["action_kind"] == "status"
    assert unknown["ok"] is False
    assert unknown["allowed"] is False
    assert unknown["error"] == "unsupported_action"
    assert unknown["policy"]["permission"] == "unknown"
    assert EvolutionOutcomeStore(tmp_path).stats()["outcome_type_counts"][CONTROL_EVENT_DENIED] == 1


def test_control_plane_rollback_preview_uses_snapshot_without_writing(tmp_path: Path) -> None:
    workflow_dir = tmp_path / "workflows" / "deploy-backend-checks"
    workflow_dir.mkdir(parents=True)
    workflow_file = workflow_dir / "workflow.yaml"
    workflow_file.write_text(
        yaml.safe_dump(
            {
                "name": "deploy-backend-checks",
                "description": "Demo workflow",
                "body": "Original body.",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    snapshot = EvolutionSnapshotStore(tmp_path).create_snapshot({
        "artifact_type": "workflow",
        "workflow_name": "deploy-backend-checks",
        "path": "workflows/deploy-backend-checks/workflow.yaml",
    })
    plane = EvolutionControlPlane(tmp_path, EvolutionConfig())

    preview = plane.preview_action(
        "rollback_artifact",
        artifact_type="workflow",
        artifact_name="deploy-backend-checks",
        reason="operator preview",
    )

    outcome_stats = EvolutionOutcomeStore(tmp_path).stats()
    assert snapshot is not None
    assert preview["ok"] is True
    assert preview["will_write"] is False
    assert preview["requires_manual_override"] is True
    assert preview["policy"]["permission"] == "rollback"
    assert preview["preview"]["snapshot_id"] == snapshot["snapshot_id"]
    assert preview["preview"]["would_write_artifact"] is True
    assert "rolled_back" not in outcome_stats["outcome_type_counts"]
