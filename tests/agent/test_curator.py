from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from OriginAgent.agent.background_review import ReviewProposal, ReviewProposalStore
from OriginAgent.agent.curator import CURATOR_ORIGIN, CuratorService
from OriginAgent.agent.evolution import (
    AUTO_EVOLUTION_ORIGIN,
    SIGNAL_KIND_SKILL,
    SIGNAL_KIND_WORKFLOW,
    OpportunitySignalCandidate,
    OpportunitySignalStore,
)
from OriginAgent.agent.evolution_dependencies import EvolutionDependencyStore
from OriginAgent.agent.evolution_feedback import EvolutionFeedbackCalibrator
from OriginAgent.agent.evolution_outcomes import EvolutionOutcomeStore
from OriginAgent.agent.evolution_snapshots import EvolutionRollbackService, EvolutionSnapshotStore
from OriginAgent.agent.skills import SkillsLoader
from OriginAgent.agent.tools.runtime_status import RuntimeStatusTool
from OriginAgent.config.schema import EvolutionConfig


def _review_proposal(proposal_id: str, *, origin: str = "background_review", proposal_type: str = "skill") -> ReviewProposal:
    return ReviewProposal(
        id=proposal_id,
        created_at="2026-05-20T10:00:00+00:00",
        session_key="websocket:chat-a",
        turn_id="turn-1",
        origin=origin,
        proposal_type=proposal_type,
        domain_id="core",
        title="Review proposal",
        content="Proposal content.",
        rationale="Proposal rationale.",
    )


def _seed_workflow_signal(
    workspace: Path,
    *,
    target: str = "deploy backend checks",
    cursors: tuple[int, ...] = (1, 2, 3),
) -> OpportunitySignalStore:
    store = OpportunitySignalStore(workspace)
    store.upsert_candidates([
        OpportunitySignalCandidate(
            kind=SIGNAL_KIND_WORKFLOW,
            target_key=target,
            title=f"Workflow candidate: {target}",
            summary=f"Repeated workflow-like request pattern: {target}",
            evidence_sources=[
                {
                    "cursor": cursor,
                    "session_key": f"websocket:chat-{cursor}",
                    "timestamp": f"2026-05-2{cursor}T10:00:00+00:00",
                    "preview": "Every time we deploy backend, run tests and check logs.",
                }
                for cursor in cursors
            ],
        )
    ])
    return store


def _seed_skill_signal(
    workspace: Path,
    *,
    target: str = "log review troubleshooting skill",
    cursors: tuple[int, ...] = (1, 2, 3, 4, 5),
) -> OpportunitySignalStore:
    store = OpportunitySignalStore(workspace)
    store.upsert_candidates([
        OpportunitySignalCandidate(
            kind=SIGNAL_KIND_SKILL,
            target_key=target,
            title=f"Skill candidate: {target}",
            summary=f"Repeated read-only skill-like request pattern: {target}",
            evidence_sources=[
                {
                    "cursor": cursor,
                    "session_key": f"websocket:skill-{cursor}",
                    "timestamp": f"2026-05-2{min(cursor, 9)}T10:00:00+00:00",
                    "preview": "Please turn this troubleshooting analysis into a reusable skill for log review.",
                }
                for cursor in cursors
            ],
            risk_level="medium",
        )
    ])
    return store


def _write_skill(
    workspace: Path,
    skill_dir: str,
    *,
    frontmatter_name: str,
    description: str,
    body: str,
    verification_status: str,
    lifecycle_status: str,
    review_proposal_id: str,
    created_by: str = "background_review",
) -> Path:
    path = workspace / "skills" / skill_dir
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        "---\n"
        f"name: {frontmatter_name}\n"
        f"description: {description}\n"
        "always: false\n"
        "metadata:\n"
        "  OriginAgent:\n"
        "    proposal_status: proposed\n"
        f"    verification_status: {verification_status}\n"
        f"    lifecycle_status: {lifecycle_status}\n"
        f"    review_proposal_id: {review_proposal_id}\n"
        "    domain_id: core\n"
        f"    created_by: {created_by}\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return path / "SKILL.md"


def _write_workflow_artifact(
    workspace: Path,
    name: str,
    *,
    body: str,
    review_proposal_id: str,
) -> Path:
    path = workspace / "workflows" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "name": name,
                "description": f"{name} workflow.",
                "kind": "manual_guide",
                "execution": {
                    "auto_run": False,
                    "creates_cron": False,
                    "calls_tools": False,
                },
                "body": body,
                "steps": [
                    {
                        "title": "Review",
                        "instruction": "Read available context only.",
                        "risk": "low",
                        "confirmation_required": False,
                    }
                ],
                "metadata": {
                    "OriginAgent": {
                        "proposal_status": "proposed",
                        "verification_status": "verified",
                        "review_proposal_id": review_proposal_id,
                        "domain_id": "core",
                        "created_by": AUTO_EVOLUTION_ORIGIN,
                    }
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path / "workflow.yaml"


def _proposal_events(workspace: Path) -> list[dict]:
    event_file = workspace / "memory" / "review_proposal_events.jsonl"
    if not event_file.exists():
        return []
    return [
        json.loads(line)
        for line in event_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _evolution_events(workspace: Path) -> list[dict]:
    event_file = workspace / "memory" / "evolution_events.jsonl"
    if not event_file.exists():
        return []
    return [
        json.loads(line)
        for line in event_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_legacy_review_proposal_without_origin_defaults_to_background_review(tmp_path: Path) -> None:
    proposal_file = tmp_path / "memory" / "review_proposals.jsonl"
    proposal_file.parent.mkdir(parents=True, exist_ok=True)
    proposal_file.write_text(
        json.dumps({
            "id": "review_legacy",
            "created_at": "2026-05-20T10:00:00+00:00",
            "session_key": "websocket:chat-a",
            "turn_id": "turn-1",
            "proposal_type": "memory",
            "domain_id": "core",
            "title": "Legacy review",
            "content": "Remember this.",
        }) + "\n",
        encoding="utf-8",
    )

    record = ReviewProposalStore(tmp_path).get("review_legacy")

    assert record is not None
    assert record["origin"] == "background_review"


@pytest.mark.asyncio
async def test_curator_generates_deduped_skill_proposals(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _review_proposal("review_verified_unique"),
        _review_proposal("review_duplicate_canonical"),
        _review_proposal("review_duplicate_weaker"),
    ])
    _write_skill(
        tmp_path,
        "verified-unique",
        frontmatter_name="verified-unique",
        description="Unique verified skill.",
        body="Use this verified skill.",
        verification_status="verified",
        lifecycle_status="proposed",
        review_proposal_id="review_verified_unique",
    )
    duplicate_body = "Use this shared troubleshooting skill."
    _write_skill(
        tmp_path,
        "duplicate-alpha",
        frontmatter_name="lighting-troubleshooting",
        description="Shared troubleshooting skill.",
        body=duplicate_body,
        verification_status="verified",
        lifecycle_status="active",
        review_proposal_id="review_duplicate_canonical",
    )
    _write_skill(
        tmp_path,
        "duplicate-beta",
        frontmatter_name="lighting-troubleshooting",
        description="Shared troubleshooting skill.",
        body=duplicate_body,
        verification_status="verified",
        lifecycle_status="proposed",
        review_proposal_id="review_duplicate_weaker",
    )

    service = CuratorService(
        workspace=tmp_path,
        config=type("Cfg", (), {"enabled": True, "max_proposals_per_run": 12})(),
        store=store,
    )

    first = await service.review_workspace(session_key="websocket:chat-a", turn_id="turn-2")
    second = await service.review_workspace(session_key="websocket:chat-a", turn_id="turn-3")
    records = store.list_records(origin=CURATOR_ORIGIN, limit=20)
    proposal_types = sorted(record["proposal_type"] for record in records)

    assert first.status == "ok"
    assert first.proposals_written == 2
    assert second.proposals_written == 0
    assert proposal_types == ["deprecate_skill", "promote_skill"]
    assert all(record["origin"] == CURATOR_ORIGIN for record in records)
    assert all(record["payload"]["curator_key"] for record in records)
    assert all(record["payload"]["target_state_hash"] for record in records)


@pytest.mark.asyncio
async def test_curator_writes_proposals_off_event_loop_thread(tmp_path: Path) -> None:
    class ThreadRecordingStore:
        def __init__(self) -> None:
            self.thread_id: int | None = None

        def append_many(self, proposals):
            self.thread_id = threading.get_ident()
            return len(proposals)

    store = ThreadRecordingStore()
    loop_thread_id = threading.get_ident()
    service = CuratorService(
        workspace=tmp_path,
        config=SimpleNamespace(enabled=True),
        store=store,
    )
    service._build_proposals = lambda **_: [_review_proposal("review_curator", origin=CURATOR_ORIGIN)]

    result = await service.review_workspace(session_key="websocket:chat1", turn_id="turn-1")

    assert result.status == "ok"
    assert result.proposals_written == 1
    assert store.thread_id is not None
    assert store.thread_id != loop_thread_id


@pytest.mark.asyncio
async def test_curator_default_evolution_dry_run_does_not_write_workflow_proposals(tmp_path: Path) -> None:
    review_store = ReviewProposalStore(tmp_path)
    signal_store = _seed_workflow_signal(tmp_path)
    service = CuratorService(
        workspace=tmp_path,
        config=SimpleNamespace(enabled=True, max_proposals_per_run=12),
        store=review_store,
    )

    result = await service.review_workspace(session_key="websocket:chat-a", turn_id="turn-1")

    assert result.status == "ok"
    assert result.proposals_written == 0
    assert result.evolution_candidates == 1
    assert result.evolution_proposals_prepared == 0
    assert result.evolution_dry_run is True
    assert result.evolution_mode == "conservative"
    assert review_store.list_records(origin=AUTO_EVOLUTION_ORIGIN, limit=10) == []
    signals = signal_store.read_all()
    assert len(signals) == 1
    assert signals[0].status == "open"
    assert signals[0].converted_proposal_id is None


@pytest.mark.asyncio
async def test_curator_curated_evolution_writes_workflow_proposal_and_marks_signal_converted(
    tmp_path: Path,
) -> None:
    review_store = ReviewProposalStore(tmp_path)
    signal_store = _seed_workflow_signal(tmp_path)
    service = CuratorService(
        workspace=tmp_path,
        config=SimpleNamespace(enabled=True, max_proposals_per_run=12),
        evolution_config=EvolutionConfig(mode="curated", dry_run=False),
        store=review_store,
    )

    first = await service.review_workspace(session_key="websocket:chat-a", turn_id="turn-1")
    second = await service.review_workspace(session_key="websocket:chat-a", turn_id="turn-2")
    records = review_store.list_records(origin=AUTO_EVOLUTION_ORIGIN, limit=10)

    assert first.status == "ok"
    assert first.proposals_written == 1
    assert first.evolution_candidates == 1
    assert first.evolution_proposals_prepared == 1
    assert first.evolution_dry_run is False
    assert first.evolution_mode == "curated"
    assert second.proposals_written == 0
    assert len(records) == 1
    record = records[0]
    assert record["proposal_type"] == "workflow"
    assert record["origin"] == AUTO_EVOLUTION_ORIGIN
    assert record["status"] == "pending"
    assert record["can_apply"] is True
    payload = record["payload"]
    assert payload["evolution"]["origin"] == AUTO_EVOLUTION_ORIGIN
    assert payload["evolution"]["opportunity_id"]
    assert payload["evolution"]["seen_count"] == 3
    assert payload["static_gate"] == {
        "decision": "pass",
        "issues": [],
        "issue_counts": {},
    }
    assert payload["sandbox"]["status"] == "passed"
    assert payload["promotion_gate"] == {
        "decision": "pass",
        "suggested_action": "review_required",
        "risk_level": "low",
        "reasons": ["Workflow passed the gate but still requires review by current policy."],
        "static_gate_decision": "pass",
        "sandbox_status": "passed",
        "auto_verify_eligible": False,
        "issue_counts": {},
    }
    outcome_stats = EvolutionOutcomeStore(tmp_path).stats()
    assert outcome_stats["outcome_type_counts"]["gate_evaluated"] == 1
    assert outcome_stats["gate_decision_counts"] == {"pass": 2}
    signals = signal_store.read_all()
    assert len(signals) == 1
    assert signals[0].status == "converted"
    assert signals[0].converted_proposal_id == record["id"]


@pytest.mark.asyncio
async def test_curator_auto_verify_disabled_leaves_workflow_proposal_pending(tmp_path: Path) -> None:
    review_store = ReviewProposalStore(tmp_path)
    _seed_workflow_signal(tmp_path, cursors=(1, 2, 3, 4, 5))
    service = CuratorService(
        workspace=tmp_path,
        config=SimpleNamespace(enabled=True, max_proposals_per_run=12),
        evolution_config=EvolutionConfig(
            mode="curated",
            dry_run=False,
            workflow_min_seen_count=5,
            auto_verify_workflows=False,
        ),
        store=review_store,
    )

    result = await service.review_workspace(session_key="websocket:chat-a", turn_id="turn-1")
    records = review_store.list_records(origin=AUTO_EVOLUTION_ORIGIN, limit=10)

    assert result.status == "ok"
    assert result.proposals_written == 1
    assert len(records) == 1
    assert records[0]["status"] == "pending"
    assert not (tmp_path / "workflows").exists()
    assert _proposal_events(tmp_path) == []


@pytest.mark.asyncio
async def test_curator_auto_verifies_low_risk_workflow_without_activating(tmp_path: Path) -> None:
    review_store = ReviewProposalStore(tmp_path)
    signal_store = _seed_workflow_signal(tmp_path, cursors=(1, 2, 3, 4, 5))
    service = CuratorService(
        workspace=tmp_path,
        config=SimpleNamespace(enabled=True, max_proposals_per_run=12),
        evolution_config=EvolutionConfig(
            mode="curated",
            dry_run=False,
            workflow_min_seen_count=5,
            auto_verify_workflows=True,
        ),
        store=review_store,
    )

    result = await service.review_workspace(session_key="websocket:chat-a", turn_id="turn-1")
    records = review_store.list_records(origin=AUTO_EVOLUTION_ORIGIN, limit=10)
    signals = signal_store.read_all()

    assert result.status == "ok"
    assert result.proposals_written == 1
    assert len(records) == 1
    record = records[0]
    assert record["status"] == "applied"
    assert record["proposal_type"] == "workflow"
    assert record["payload"]["promotion_gate"]["auto_verify_eligible"] is True
    assert record["payload"]["promotion_gate"]["suggested_action"] == "auto_apply"
    assert record["applied_workflow_path"] == "workflows/deploy-backend-checks/workflow.yaml"
    workflow_file = tmp_path / "workflows" / "deploy-backend-checks" / "workflow.yaml"
    data = yaml.safe_load(workflow_file.read_text(encoding="utf-8"))
    metadata = data["metadata"]["OriginAgent"]
    assert metadata["proposal_status"] == "proposed"
    assert metadata["verification_status"] == "verified"
    assert metadata["created_by"] == AUTO_EVOLUTION_ORIGIN
    assert metadata["verified_by"] == AUTO_EVOLUTION_ORIGIN
    assert metadata["previous_version"] is None
    assert metadata["opportunity_id"] == signals[0].opportunity_id
    assert signals[0].status == "converted"
    assert signals[0].verification_status == "verified"
    snapshots = EvolutionSnapshotStore(tmp_path).list_snapshots(
        artifact_type="workflow",
        artifact_name="deploy-backend-checks",
    )
    assert len(snapshots) == 1
    assert snapshots[0]["proposal_id"] == record["id"]
    assert snapshots[0]["opportunity_id"] == signals[0].opportunity_id
    original_content = workflow_file.read_text(encoding="utf-8")
    assert any(
        event.get("result", {}).get("event_name") == "evolution_auto_promotion"
        for event in _evolution_events(tmp_path)
    )
    status = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=["originagent_runtime_status"]),
        sessions=object(),
        pending_queues={},
        evolution_config=service.evolution_config,
    ).execute()
    assert status["evolution"]["auto_verified_workflows_count"] == 1
    assert status["evolution"]["promotion_gate_decision_counts"] == {"pass": 1}
    assert status["evolution"]["outcomes"]["promotion_status_counts"] == {
        "proposed": 1,
        "verified": 1,
    }
    assert status["evolution"]["snapshots"]["snapshot_type_counts"] == {"workflow": 1}
    assert status["evolution"]["sandbox"]["passed_workflow_proposals"] == 1

    data["body"] = "Temporary local edit before rollback."
    workflow_file.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    rollback = EvolutionRollbackService(tmp_path).rollback(
        artifact_type="workflow",
        artifact_name="deploy-backend-checks",
        reason="test rollback",
        actor="tester",
    )

    assert rollback.ok is True
    assert rollback.status == "rolled_back"
    assert workflow_file.read_text(encoding="utf-8") == original_content
    assert any(
        event.get("event_type") == "module_rollback_succeeded"
        for event in _evolution_events(tmp_path)
    )
    status_after = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=["originagent_runtime_status"]),
        sessions=object(),
        pending_queues={},
        evolution_config=service.evolution_config,
    ).execute()
    assert status_after["evolution"]["outcomes"]["rollback_status_counts"] == {"succeeded": 1}
    assert status_after["evolution"]["snapshots"]["snapshot_type_counts"] == {"workflow": 2}

    feedback = EvolutionFeedbackCalibrator(tmp_path, service.evolution_config).run()
    calibrated = signal_store.read_all()[0]

    assert feedback.feedback_applied >= 1
    assert feedback.negative_feedback_applied == 1
    assert feedback.suppressed_signals == 1
    assert calibrated.status == "suppressed"
    assert calibrated.risk_level == "high"
    assert calibrated.verification_status == "rolled_back"
    assert calibrated.feedback_negative_count == 1
    status_feedback = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=["originagent_runtime_status"]),
        sessions=object(),
        pending_queues={},
        evolution_config=service.evolution_config,
    ).execute()
    assert status_feedback["evolution"]["suppressed_signals_count"] == 1
    assert status_feedback["evolution"]["feedback_calibration"]["feedback_polarity_counts"] == {
        "negative": 1
    }


def test_evolution_rollback_blocks_when_artifact_has_dependents(tmp_path: Path) -> None:
    base_file = _write_workflow_artifact(
        tmp_path,
        "base-workflow",
        body="Base workflow body.",
        review_proposal_id="review_base",
    )
    dependent_file = _write_workflow_artifact(
        tmp_path,
        "dependent-workflow",
        body="Run workflow:base-workflow before summarizing.",
        review_proposal_id="review_dependent",
    )
    snapshot = EvolutionSnapshotStore(tmp_path).create_snapshot(
        {
            "artifact_type": "workflow",
            "workflow_name": "base-workflow",
            "path": "workflows/base-workflow/workflow.yaml",
        },
        proposal_id="review_base",
        opportunity_id="opp_base",
        reason="test baseline",
    )
    dependency_store = EvolutionDependencyStore(tmp_path)
    dependency_store.update_artifact(
        artifact_type="workflow",
        artifact_name="base-workflow",
        artifact_path="workflows/base-workflow/workflow.yaml",
    )
    dependency_store.update_artifact(
        artifact_type="workflow",
        artifact_name="dependent-workflow",
        artifact_path="workflows/dependent-workflow/workflow.yaml",
    )
    assert snapshot is not None
    assert dependency_store.stats()["rollback_blocked_artifacts"] == 1

    data = yaml.safe_load(base_file.read_text(encoding="utf-8"))
    data["body"] = "Edited body that should remain when rollback is blocked."
    base_file.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    rollback = EvolutionRollbackService(tmp_path).rollback(
        artifact_type="workflow",
        artifact_name="base-workflow",
        reason="blocked dependency test",
        actor="tester",
    )

    assert rollback.ok is False
    assert rollback.status == "blocked_by_dependencies"
    assert rollback.error == "dependency_blocked"
    assert rollback.dependency_blockers == [
        {
            "type": "workflow",
            "name": "dependent-workflow",
            "source": "static",
            "evidence": "Run workflow:base-workflow before summarizing.",
        }
    ]
    assert "Edited body" in base_file.read_text(encoding="utf-8")
    assert dependent_file.exists()
    outcome_stats = EvolutionOutcomeStore(tmp_path).stats()
    assert outcome_stats["rollback_status_counts"] == {"blocked": 1}


def test_evolution_dependency_cleanup_prunes_stale_artifacts_and_edges(tmp_path: Path) -> None:
    base_file = _write_workflow_artifact(
        tmp_path,
        "base-workflow",
        body="Base workflow body.",
        review_proposal_id="review_base",
    )
    _write_workflow_artifact(
        tmp_path,
        "dependent-workflow",
        body="Run workflow:base-workflow and workflow:missing-workflow before summarizing.",
        review_proposal_id="review_dependent",
    )
    dependency_store = EvolutionDependencyStore(tmp_path)
    dependency_store.update_artifact(
        artifact_type="workflow",
        artifact_name="base-workflow",
        artifact_path="workflows/base-workflow/workflow.yaml",
    )
    dependency_store.update_artifact(
        artifact_type="workflow",
        artifact_name="dependent-workflow",
        artifact_path="workflows/dependent-workflow/workflow.yaml",
    )
    data = yaml.safe_load(base_file.read_text(encoding="utf-8"))
    data["metadata"]["OriginAgent"]["proposal_status"] = "deprecated"
    base_file.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    result = dependency_store.prune_stale_references()
    records = dependency_store.read_all()

    assert result == {
        "removed_artifacts": 1,
        "removed_edges": 2,
        "remaining_artifacts": 1,
        "stale_reference_count": 0,
    }
    assert records[0]["artifact_name"] == "dependent-workflow"
    assert records[0]["depends_on"] == []
    assert records[0]["referenced_by"] == []


@pytest.mark.asyncio
async def test_curator_runs_evolution_retention_and_dependency_cleanup(tmp_path: Path) -> None:
    old = datetime(2000, 1, 1, 0, 0, tzinfo=timezone.utc)
    outcomes = EvolutionOutcomeStore(tmp_path)
    old_event = outcomes.append_event("signal_created", opportunity_id="opp_old", timestamp=old)
    outcomes.append_event("promoted", opportunity_id="opp_keep", timestamp=old)
    _write_workflow_artifact(
        tmp_path,
        "dependent-workflow",
        body="Run workflow:missing-workflow before summarizing.",
        review_proposal_id="review_dependent",
    )
    dependency_store = EvolutionDependencyStore(tmp_path)
    dependency_store.update_artifact(
        artifact_type="workflow",
        artifact_name="dependent-workflow",
        artifact_path="workflows/dependent-workflow/workflow.yaml",
    )
    service = CuratorService(
        workspace=tmp_path,
        config=SimpleNamespace(enabled=True, max_proposals_per_run=12),
        evolution_config=EvolutionConfig(
            outcome_retention_days=90,
            outcome_archive_enabled=True,
            dependency_stale_cleanup_enabled=True,
        ),
        store=ReviewProposalStore(tmp_path),
    )

    result = await service.review_workspace(session_key="websocket:chat-a", turn_id="turn-1")
    maintenance = service._last_evolution_scan["maintenance"]
    archive_lines = [
        json.loads(line)
        for line in (tmp_path / "memory" / "archive" / "evolution_outcomes_archive.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert result.status == "ok"
    assert maintenance["outcome_retention"]["archived_count"] == 1
    assert maintenance["dependency_cleanup"]["removed_edges"] == 1
    assert outcomes.stats()["outcome_type_counts"] == {"promoted": 1}
    assert outcomes.stats()["archive"]["archived_outcome_count"] == 1
    assert archive_lines[0]["record"]["event_id"] == old_event["event_id"]
    assert dependency_store.read_all()[0]["depends_on"] == []


@pytest.mark.asyncio
async def test_curator_skill_candidates_default_disabled(tmp_path: Path) -> None:
    review_store = ReviewProposalStore(tmp_path)
    signal_store = _seed_skill_signal(tmp_path)
    service = CuratorService(
        workspace=tmp_path,
        config=SimpleNamespace(enabled=True, max_proposals_per_run=12),
        evolution_config=EvolutionConfig(mode="curated", dry_run=False),
        store=review_store,
    )

    result = await service.review_workspace(session_key="websocket:chat-a", turn_id="turn-1")

    assert result.status == "ok"
    assert result.proposals_written == 0
    assert result.evolution_candidates == 1
    assert review_store.list_records(origin=AUTO_EVOLUTION_ORIGIN, limit=10) == []
    signals = signal_store.read_all()
    assert signals[0].status == "open"


@pytest.mark.asyncio
async def test_curator_generates_read_only_skill_proposal_when_enabled(tmp_path: Path) -> None:
    review_store = ReviewProposalStore(tmp_path)
    signal_store = _seed_skill_signal(tmp_path)
    service = CuratorService(
        workspace=tmp_path,
        config=SimpleNamespace(enabled=True, max_proposals_per_run=12),
        evolution_config=EvolutionConfig(
            mode="curated",
            dry_run=False,
            skill_candidates_enabled=True,
        ),
        store=review_store,
    )

    result = await service.review_workspace(session_key="websocket:chat-a", turn_id="turn-1")
    records = review_store.list_records(origin=AUTO_EVOLUTION_ORIGIN, limit=10)

    assert result.status == "ok"
    assert result.proposals_written == 1
    assert result.evolution_candidates == 1
    assert result.evolution_proposals_prepared == 1
    assert len(records) == 1
    record = records[0]
    assert record["proposal_type"] == "skill"
    assert record["status"] == "pending"
    assert record["payload"]["evolution"]["kind"] == SIGNAL_KIND_SKILL
    assert record["payload"]["static_gate"] == {
        "decision": "pass",
        "issues": [],
        "issue_counts": {},
    }
    assert record["payload"]["promotion_gate"]["decision"] == "pass"
    assert record["payload"]["promotion_gate"]["suggested_action"] == "review_required"
    assert record["payload"]["promotion_gate"]["sandbox_status"] == "not_applicable"

    applied = review_store.apply(record["id"], reason="reviewed")
    assert applied.ok is True
    skill_file = tmp_path / "skills" / "log-review-troubleshooting-skill" / "SKILL.md"
    frontmatter = yaml.safe_load(skill_file.read_text(encoding="utf-8").split("---", 2)[1])
    assert frontmatter["always"] is False
    assert frontmatter["metadata"]["OriginAgent"]["proposal_status"] == "proposed"
    assert frontmatter["metadata"]["OriginAgent"]["verification_status"] == "unverified"
    assert EvolutionSnapshotStore(tmp_path).stats()["snapshot_type_counts"] == {"skill": 1}
    signals = signal_store.read_all()
    assert signals[0].status == "converted"
    assert signals[0].converted_proposal_id == record["id"]


def test_curator_promote_apply_verifies_and_activates_workspace_skill(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "lighting-troubleshooting",
        frontmatter_name="lighting-troubleshooting",
        description="Lighting help.",
        body="Use this skill for lighting issues.",
        verification_status="unverified",
        lifecycle_status="proposed",
        review_proposal_id="review_source",
    )
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _review_proposal(
            "review_promote",
            origin=CURATOR_ORIGIN,
            proposal_type="promote_skill",
        )
    ])
    proposal_file = tmp_path / "memory" / "review_proposals.jsonl"
    raw = json.loads(proposal_file.read_text(encoding="utf-8").splitlines()[0])
    raw["payload"] = {
        "skill_name": "lighting-troubleshooting",
        "subject_id": "lighting-troubleshooting",
        "subject_type": "skill",
        "subject_path": "skills/lighting-troubleshooting/SKILL.md",
        "curator_key": "promote-skill:lighting-troubleshooting",
        "target_state_hash": "state-1",
        "suggested_action": "promote_skill",
        "impact_summary": "Promote skill.",
    }
    proposal_file.write_text(json.dumps(raw) + "\n", encoding="utf-8")

    result = store.apply("review_promote", reason="curator approved")
    record = SkillsLoader(tmp_path).get_skill_record("lighting-troubleshooting")

    assert result.ok is True
    assert result.status == "applied"
    assert record is not None
    assert record["verification_status"] == "verified"
    assert record["lifecycle_status"] == "active"
    assert store.get("review_promote")["applied_skill_path"] == "skills/lighting-troubleshooting/SKILL.md"


def test_curator_deprecate_apply_marks_workspace_skill_deprecated(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "duplicate-skill",
        frontmatter_name="duplicate-skill",
        description="Duplicate skill.",
        body="Use this skill for duplicated work.",
        verification_status="verified",
        lifecycle_status="active",
        review_proposal_id="review_source",
    )
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _review_proposal(
            "review_deprecate",
            origin=CURATOR_ORIGIN,
            proposal_type="deprecate_skill",
        )
    ])
    proposal_file = tmp_path / "memory" / "review_proposals.jsonl"
    raw = json.loads(proposal_file.read_text(encoding="utf-8").splitlines()[0])
    raw["payload"] = {
        "skill_name": "duplicate-skill",
        "subject_id": "duplicate-skill",
        "subject_type": "skill",
        "subject_path": "skills/duplicate-skill/SKILL.md",
        "curator_key": "deprecate-skill:duplicate-skill",
        "target_state_hash": "state-2",
        "suggested_action": "deprecate_skill",
        "impact_summary": "Deprecate skill.",
    }
    proposal_file.write_text(json.dumps(raw) + "\n", encoding="utf-8")

    result = store.apply("review_deprecate", reason="curator approved")
    record = SkillsLoader(tmp_path).get_skill_record("duplicate-skill")

    assert result.ok is True
    assert record is not None
    assert record["lifecycle_status"] == "deprecated"


def test_review_only_curator_apply_stays_pending_without_terminal_event(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        ReviewProposal(
            id="review_merge",
            created_at="2026-05-20T10:00:00+00:00",
            session_key="curator:system",
            turn_id="turn-4",
            origin=CURATOR_ORIGIN,
            proposal_type="merge_skill",
            domain_id="core",
            title="Review duplicate skills",
            content="Manual merge needed.",
            payload={
                "curator_key": "merge-skill:alpha",
                "target_state_hash": "hash-1",
                "subject_type": "skill_group",
                "subject_id": "alpha,beta",
                "suggested_action": "merge_skill",
            },
        )
    ])

    result = store.apply("review_merge")

    assert result.ok is False
    assert result.status == "pending"
    assert store.get("review_merge")["status"] == "pending"
    assert _proposal_events(tmp_path) == []
