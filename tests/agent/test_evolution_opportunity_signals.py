from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from OriginAgent.agent.evolution import (
    AUTO_EVOLUTION_ORIGIN,
    SIGNAL_KIND_SKILL,
    SIGNAL_KIND_WORKFLOW,
    build_skill_payload_from_signal,
    build_workflow_payload_from_signal,
    OpportunitySignalCandidate,
    OpportunitySignalStore,
    detect_skill_opportunity_candidates,
    detect_workflow_opportunity_candidates,
    static_gate_skill_payload,
    static_gate_workflow_payload,
)
from OriginAgent.agent.evolution_sandbox import SandboxEvaluator
from OriginAgent.agent.evolution_feedback import EvolutionFeedbackCalibrator
from OriginAgent.agent.evolution_outcomes import EvolutionOutcomeStore
from OriginAgent.agent.background_review import ReviewProposal, ReviewProposalStore
from OriginAgent.agent.tools.runtime_status import RuntimeStatusTool
from OriginAgent.config.schema import EvolutionConfig


def _candidate(*, target: str = "deploy backend checks", cursors: tuple[int, ...] = (1, 2)):
    return OpportunitySignalCandidate(
        kind=SIGNAL_KIND_WORKFLOW,
        target_key=target,
        title=f"Workflow candidate: {target}",
        summary=f"Repeated workflow-like request pattern: {target}",
        evidence_sources=[
            {
                "cursor": cursor,
                "timestamp": f"2026-05-2{cursor} 10:00",
                "preview": "Every time we deploy backend, run tests and check logs.",
            }
            for cursor in cursors
        ],
    )


def test_opportunity_signal_store_upserts_and_dedupes_evidence(tmp_path) -> None:
    store = OpportunitySignalStore(tmp_path)
    first_seen = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    last_seen = datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc)

    store.upsert_candidates([_candidate(cursors=(1, 2))], now=first_seen)
    store.upsert_candidates([_candidate(cursors=(2, 3))], now=last_seen)

    records = store.read_all()
    assert len(records) == 1
    signal = records[0]
    assert signal.kind == SIGNAL_KIND_WORKFLOW
    assert signal.first_seen_at == first_seen.isoformat()
    assert signal.last_seen_at == last_seen.isoformat()
    assert signal.seen_count == 3
    assert [item["cursor"] for item in signal.evidence_sources] == [1, 2, 3]
    assert signal.priority_score >= 0.7
    outcomes = EvolutionOutcomeStore(tmp_path).read_all()
    assert [event["type"] for event in outcomes] == ["signal_created", "signal_updated"]
    assert outcomes[0]["opportunity_id"] == signal.opportunity_id
    assert outcomes[1]["feedback_score"] == pytest.approx(signal.priority_score)


def test_feedback_calibrator_lowers_rejected_evolution_signal_once(tmp_path) -> None:
    signal_store = OpportunitySignalStore(tmp_path)
    signal = signal_store.upsert_candidates([_candidate(cursors=(1, 2, 3))])[0]
    review_store = ReviewProposalStore(tmp_path)
    review_store.append_many([
        ReviewProposal(
            id="review_auto_workflow_feedback",
            created_at="2026-05-20T10:00:00+00:00",
            session_key="curator:system",
            turn_id="turn-1",
            origin=AUTO_EVOLUTION_ORIGIN,
            proposal_type="workflow",
            domain_id="core",
            title="Create workflow",
            content="Create a reviewed workflow.",
            payload=build_workflow_payload_from_signal(signal, config=EvolutionConfig()),
            confidence=signal.priority_score,
        )
    ])
    review_store.reject("review_auto_workflow_feedback", reason="not useful")

    first = EvolutionFeedbackCalibrator(tmp_path, EvolutionConfig()).run()
    second = EvolutionFeedbackCalibrator(tmp_path, EvolutionConfig()).run()

    updated = signal_store.read_all()[0]
    assert first.processed_events == 1
    assert first.feedback_applied == 1
    assert first.negative_feedback_applied == 1
    assert second.feedback_applied == 0
    assert updated.feedback_negative_count == 1
    assert updated.feedback_multiplier == pytest.approx(0.8)
    assert updated.priority_score == pytest.approx(signal.priority_score * 0.8)
    assert updated.status == "open"
    assert updated.verification_status == "feedback_negative"
    outcome_stats = EvolutionOutcomeStore(tmp_path).stats()
    assert outcome_stats["outcome_type_counts"]["feedback_applied"] == 1


def test_feedback_calibrator_skips_positive_reinforcement_during_cooldown(tmp_path) -> None:
    signal_store = OpportunitySignalStore(tmp_path)
    signal = signal_store.upsert_candidates([_candidate(cursors=(1, 2, 3))])[0]
    review_store = ReviewProposalStore(tmp_path)
    payload = build_workflow_payload_from_signal(signal, config=EvolutionConfig())
    review_store.append_many([
        ReviewProposal(
            id="review_auto_workflow_rejected",
            created_at="2026-05-20T10:00:00+00:00",
            session_key="curator:system",
            turn_id="turn-1",
            origin=AUTO_EVOLUTION_ORIGIN,
            proposal_type="workflow",
            domain_id="core",
            title="Reject workflow",
            content="Create a reviewed workflow.",
            payload=payload,
            confidence=signal.priority_score,
        ),
        ReviewProposal(
            id="review_auto_workflow_approved",
            created_at="2026-05-20T10:01:00+00:00",
            session_key="curator:system",
            turn_id="turn-2",
            origin=AUTO_EVOLUTION_ORIGIN,
            proposal_type="workflow",
            domain_id="core",
            title="Approve workflow",
            content="Create a reviewed workflow.",
            payload=payload,
            confidence=signal.priority_score,
        ),
    ])
    review_store.reject("review_auto_workflow_rejected", reason="not useful")
    review_store.apply("review_auto_workflow_approved", reason="manual follow-up")

    result = EvolutionFeedbackCalibrator(
        tmp_path,
        EvolutionConfig(feedback_cooldown_days=30, feedback_trend_window_days=30),
    ).run()
    updated = signal_store.read_all()[0]
    status = EvolutionFeedbackCalibrator(
        tmp_path,
        EvolutionConfig(feedback_cooldown_days=30, feedback_trend_window_days=30),
    ).status()
    feedback_events = [
        event for event in EvolutionOutcomeStore(tmp_path).read_all()
        if event.get("type") == "feedback_applied"
    ]

    assert result.processed_events == 2
    assert result.feedback_applied == 1
    assert result.negative_feedback_applied == 1
    assert result.positive_feedback_applied == 0
    assert result.cooldown_skipped_events == 1
    assert updated.feedback_negative_count == 1
    assert updated.feedback_positive_count == 0
    assert updated.feedback_multiplier == pytest.approx(0.8)
    assert updated.priority_score == pytest.approx(signal.priority_score * 0.8)
    assert status["cooldown_count"] == 1
    assert status["next_cooldown_expires_at"] is not None
    assert status["feedback_event_count"] == 2
    assert status["feedback_polarity_counts"] == {"negative": 1, "positive": 1}
    assert status["feedback_trend_counts"] == {
        "negative": 1,
        "skipped_positive": 1,
        "net": -1,
    }
    assert status["feedback_trends"][signal.opportunity_id]["negative"] == 1
    assert status["feedback_trends"][signal.opportunity_id]["skipped_positive"] == 1
    assert status["last_result"]["cooldown_skipped_events"] == 1
    assert feedback_events[1]["calibration_result"]["skipped_by_cooldown"] is True


def test_workflow_detector_requires_repeated_evidence() -> None:
    entries = [
        {
            "cursor": 1,
            "timestamp": "2026-05-20 10:00",
            "content": "每次部署后端时请运行测试并检查日志",
        },
        {
            "cursor": 2,
            "timestamp": "2026-05-20 11:00",
            "content": "每次部署后端时请运行测试并检查日志",
        },
    ]

    candidates = detect_workflow_opportunity_candidates(entries, min_evidence_sources=2)

    assert len(candidates) == 1
    assert candidates[0].kind == SIGNAL_KIND_WORKFLOW
    assert candidates[0].target_key == "每次部署后端时请运行测试并检查日志"
    assert len(candidates[0].evidence_sources) == 2


def test_skill_detector_and_payload_keep_candidates_read_only(tmp_path) -> None:
    entries = [
        {
            "cursor": cursor,
            "timestamp": f"2026-05-2{cursor} 10:00",
            "content": "Please turn this troubleshooting analysis into a reusable skill for log review.",
        }
        for cursor in range(1, 6)
    ]

    candidates = detect_skill_opportunity_candidates(entries, min_evidence_sources=5)
    store = OpportunitySignalStore(tmp_path)
    signal = store.upsert_candidates(candidates)[0]
    payload = build_skill_payload_from_signal(signal, config=EvolutionConfig())

    assert signal.kind == SIGNAL_KIND_SKILL
    assert signal.seen_count == 5
    assert signal.priority_score >= 0.85
    assert payload["subject_type"] == "skill"
    assert payload["evolution"]["origin"] == AUTO_EVOLUTION_ORIGIN
    assert payload["evolution"]["kind"] == SIGNAL_KIND_SKILL
    assert payload["static_gate"] == {
        "decision": "pass",
        "issues": [],
        "issue_counts": {},
    }
    assert len(payload["body"]) <= 5000


@pytest.mark.asyncio
async def test_runtime_status_reports_evolution_defaults(tmp_path) -> None:
    result = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=["a"]),
        sessions=object(),
        pending_queues={},
    ).execute()

    assert result["evolution"]["mode"] == "conservative"
    assert result["evolution"]["dry_run"] is True
    assert result["evolution"]["opportunity_signals_count"] == 0
    assert result["evolution"]["converted_signals_count"] == 0
    assert result["evolution"]["suppressed_signals_count"] == 0
    assert result["evolution"]["feedback_adjusted_signals_count"] == 0
    assert result["evolution"]["feedback_negative_signals_count"] == 0
    assert result["evolution"]["feedback_positive_signals_count"] == 0
    assert result["evolution"]["pending_proposals_from_evolution"] == 0
    assert result["evolution"]["proposal_count_from_evolution"] == 0
    assert result["evolution"]["auto_verified_workflows_count"] == 0
    assert result["evolution"]["outcomes"] == {
        "outcome_event_count": 0,
        "outcome_type_counts": {},
        "gate_decision_counts": {},
        "sandbox_status_counts": {},
        "review_status_counts": {},
        "promotion_status_counts": {},
        "rollback_status_counts": {},
        "last_outcome_at": None,
        "archive": {
            "archived_outcome_count": 0,
            "last_archived_at": None,
        },
    }
    assert result["evolution"]["maintenance"] == {
        "outcome_retention_days": 90,
        "outcome_archive_enabled": True,
        "dependency_stale_cleanup_enabled": True,
    }
    assert result["evolution"]["snapshots"] == {
        "snapshot_count": 0,
        "snapshot_type_counts": {},
        "last_snapshot_at": None,
    }
    assert result["evolution"]["dependencies"] == {
        "tracked_artifacts": 0,
        "dependency_edges": 0,
        "rollback_blocked_artifacts": 0,
        "stale_reference_count": 0,
    }
    assert result["evolution"]["feedback_calibration"] == {
        "enabled": True,
        "processed_event_count": 0,
        "feedback_event_count": 0,
        "feedback_polarity_counts": {},
        "cooldown_count": 0,
        "next_cooldown_expires_at": None,
        "feedback_trend_window_days": 14,
        "feedback_trend_counts": {},
        "feedback_trends": {},
        "last_calibrated_at": None,
        "last_result": None,
    }
    assert result["evolution"]["promotion_gate_decision_counts"] == {}
    assert result["evolution"]["static_gate_issue_counts"] == {}
    assert result["evolution"]["sandbox"] == {
        "enabled": True,
        "passed_workflow_proposals": 0,
        "failed_workflow_proposals": 0,
        "blocked_workflow_proposals": 0,
    }
    assert result["evolution"]["trial"] == {
        "enabled": True,
        "isolated_workspace": True,
        "read_only_tools_only": True,
        "allowed_tools": ["glob", "grep", "read_file"],
        "blocked_tools": ["cron", "edit_file", "exec", "message", "spawn", "write_file"],
        "temp_dir_configured": False,
    }
    assert result["evolution"]["skill_candidates_enabled"] is False
    assert result["evolution"]["eligible_workflow_signals"] == 0
    assert result["evolution"]["eligible_skill_signals"] == 0
    assert result["evolution"]["high_score_signals"] == []


def test_outcome_store_archives_old_noncritical_events(tmp_path) -> None:
    store = EvolutionOutcomeStore(tmp_path)
    now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(days=120)
    recent = now - timedelta(days=5)

    old_signal = store.append_event("signal_created", opportunity_id="opp_old", timestamp=old)
    old_promoted = store.append_event("promoted", opportunity_id="opp_keep", timestamp=old)
    recent_signal = store.append_event("signal_updated", opportunity_id="opp_recent", timestamp=recent)

    result = store.enforce_retention(retention_days=90, archive=True, now=now)
    records = store.read_all()
    archive = store.archive_stats()
    archived_path = tmp_path / "memory" / "archive" / "evolution_outcomes_archive.jsonl"
    archived_lines = [
        json.loads(line)
        for line in archived_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert result["archived_count"] == 1
    assert result["retained_count"] == 2
    assert result["archive_path"] == "memory/archive/evolution_outcomes_archive.jsonl"
    assert [record["event_id"] for record in records] == [
        old_promoted["event_id"],
        recent_signal["event_id"],
    ]
    assert archive["archived_outcome_count"] == 1
    assert archive["last_archived_at"] is not None
    assert archived_lines[0]["record"]["event_id"] == old_signal["event_id"]


@pytest.mark.asyncio
async def test_runtime_status_reports_high_score_evolution_signals(tmp_path) -> None:
    store = OpportunitySignalStore(tmp_path)
    store.upsert_candidates([_candidate(cursors=(1, 2, 3))])

    result = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=["a"]),
        sessions=object(),
        pending_queues={},
        evolution_config=EvolutionConfig(),
    ).execute()

    evolution = result["evolution"]
    assert evolution["mode"] == "conservative"
    assert evolution["dry_run"] is True
    assert evolution["opportunity_signals_count"] == 1
    assert evolution["eligible_workflow_signals"] == 1
    assert evolution["pending_proposals_from_evolution"] == 0
    assert evolution["outcomes"]["outcome_type_counts"] == {"signal_created": 1}
    assert evolution["high_score_signals"] == [
        {
            "kind": SIGNAL_KIND_WORKFLOW,
            "target": "deploy backend checks",
            "priority_score": pytest.approx(0.735),
        }
    ]


def test_workflow_payload_includes_redacted_evidence_and_static_gate(tmp_path) -> None:
    store = OpportunitySignalStore(tmp_path)
    signal = store.upsert_candidates([
        _candidate(
            target="deploy backend checks with token=abcdefghijk",
            cursors=(1, 2, 3),
        )
    ])[0]

    payload = build_workflow_payload_from_signal(signal, config=EvolutionConfig())

    evolution = payload["evolution"]
    assert evolution["origin"] == AUTO_EVOLUTION_ORIGIN
    assert evolution["opportunity_id"] == signal.opportunity_id
    assert evolution["evidence_sources"][0] == {
        "cursor": 1,
        "session_key": None,
        "timestamp": "2026-05-21 10:00",
        "preview": "Every time we deploy backend, run tests and check logs.",
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "abcdefghijk" not in serialized
    assert "[REDACTED_SECRET]" in serialized
    assert payload["static_gate"] == {
        "decision": "pass",
        "issues": [],
        "issue_counts": {},
    }


def test_static_gate_uses_validation_issue_shape_for_risky_workflows() -> None:
    payload = {
        "steps": [
            {
                "title": "Run deployment script",
                "instruction": "Use powershell command to deploy.",
                "risk": "medium",
                "tool": "exec",
            }
        ],
        "body": "Manual review for a side-effecting workflow.",
    }

    result = static_gate_workflow_payload(payload, config=EvolutionConfig())

    assert result["decision"] == "requires_manual_review"
    assert result["issue_counts"] == {"pending": 2}
    assert all(set(issue) == {"code", "severity", "message"} for issue in result["issues"])
    assert {issue["code"] for issue in result["issues"]} == {
        "workflow_tool_not_allowed",
        "workflow_dangerous_instruction",
    }


def test_static_gate_flags_risky_skill_drafts() -> None:
    payload = {
        "body": (
            "# Command: install helper\n\n"
            "Use exec to run curl, then npm install a package and continue."
        ),
        "evolution": {"body_truncated": True},
    }

    result = static_gate_skill_payload(payload, config=EvolutionConfig())

    assert result["decision"] == "requires_manual_review"
    assert all(set(issue) == {"code", "severity", "message"} for issue in result["issues"])
    assert {issue["code"] for issue in result["issues"]} == {
        "skill_tool_not_allowed",
        "skill_install_command",
        "skill_command_heading",
        "skill_sensitive_action",
        "skill_body_truncated",
    }
    assert result["issue_counts"] == {"pending": 4, "warning": 1}


def test_sandbox_evaluator_passes_read_only_and_blocks_side_effects(tmp_path) -> None:
    evaluator = SandboxEvaluator(tmp_path, EvolutionConfig())
    base_payload = {
        "target_state_hash": "sandbox-state",
        "evolution": {
            "opportunity_id": "opportunity-1",
            "evidence_sources": [{"cursor": 1}, {"cursor": 2}],
        },
    }

    passed = evaluator.evaluate_workflow_payload({
        **base_payload,
        "steps": [{"title": "Read", "tool": "read_file", "path": "notes.txt"}],
    })
    blocked = evaluator.evaluate_workflow_payload({
        **base_payload,
        "target_state_hash": "sandbox-state-2",
        "steps": [{"title": "Write", "tool": "write_file", "path": "notes.txt"}],
    })
    failed = evaluator.evaluate_workflow_payload({
        **base_payload,
        "target_state_hash": "sandbox-state-3",
        "steps": [{"title": "Escape", "tool": "read_file", "path": "..\\secrets.txt"}],
    })

    assert passed["status"] == "passed"
    assert passed["replay_summary"] == {
        "steps_checked": 1,
        "blocked_steps": 0,
        "sample_count": 2,
    }
    assert passed["policy"]["executes_tools"] is False
    assert passed["policy"]["workspace_visible"] is False
    assert passed["step_results"] == [
        {
            "index": 1,
            "title": "Read",
            "tool": "read_file",
            "status": "passed",
            "issues": [],
            "path_keys_checked": ["path"],
            "executed": False,
            "simulated": True,
        }
    ]
    assert blocked["status"] == "blocked"
    assert blocked["issues"][0]["code"] == "sandbox_tool_blocked"
    assert blocked["step_results"][0]["status"] == "blocked"
    assert blocked["step_results"][0]["issues"][0]["code"] == "sandbox_tool_blocked"
    assert failed["status"] == "failed"
    assert failed["issues"][0]["code"] == "sandbox_path_outside_root"
    assert failed["step_results"][0]["status"] == "failed"
    assert failed["step_results"][0]["issues"][0]["code"] == "sandbox_path_outside_root"


def test_trial_evaluator_enforces_isolated_read_only_policy(tmp_path) -> None:
    evaluator = SandboxEvaluator(tmp_path, EvolutionConfig())
    base_payload = {
        "target_state_hash": "trial-state",
        "evolution": {
            "opportunity_id": "opportunity-trial",
            "evidence_sources": [{"cursor": 1}, {"cursor": 2}],
        },
    }

    passed = evaluator.evaluate_trial_workflow_payload({
        **base_payload,
        "steps": [{"title": "Read", "tool": "read_file", "path": "notes.txt"}],
    })
    blocked = evaluator.evaluate_trial_workflow_payload({
        **base_payload,
        "steps": [{"title": "Write", "tool": "write_file", "path": "notes.txt"}],
    })
    failed = evaluator.evaluate_trial_workflow_payload({
        **base_payload,
        "steps": [{"title": "Escape", "tool": "read_file", "path": "..\\secrets.txt"}],
    })

    assert passed["status"] == "passed"
    assert passed["mode"] == "trial"
    assert passed["read_only"] is True
    assert passed["isolated_workspace"] is True
    assert passed["policy"]["blocked_tools"] == [
        "cron",
        "edit_file",
        "exec",
        "message",
        "spawn",
        "write_file",
    ]
    assert blocked["status"] == "blocked"
    assert blocked["issues"][0]["code"] == "trial_tool_blocked"
    assert blocked["step_results"][0]["status"] == "blocked"
    assert blocked["step_results"][0]["executed"] is False
    assert blocked["step_results"][0]["simulated"] is True
    assert failed["status"] == "failed"
    assert failed["issues"][0]["code"] == "trial_path_outside_root"
    assert failed["step_results"][0]["status"] == "failed"


def test_sandbox_evaluator_reports_step_level_failures_without_execution(tmp_path) -> None:
    evaluator = SandboxEvaluator(tmp_path, EvolutionConfig())

    result = evaluator.evaluate_workflow_payload({
        "target_state_hash": "step-level-sandbox",
        "evolution": {
            "opportunity_id": "opportunity-step-level",
            "evidence_sources": [{"cursor": 1}],
        },
        "steps": [
            {"title": "Read", "tool": "read_file", "path": "notes.txt"},
            {"title": "Write", "tool": "write_file", "path": "notes.txt"},
            {"title": "Escape", "tool": "grep", "pattern": "..\\secret"},
            "bad-step",
        ],
    })

    assert result["status"] == "failed"
    assert result["replay_summary"] == {
        "steps_checked": 3,
        "blocked_steps": 3,
        "sample_count": 1,
    }
    assert [step["status"] for step in result["step_results"]] == [
        "passed",
        "blocked",
        "failed",
        "invalid",
    ]
    assert all(step["executed"] is False for step in result["step_results"])
    assert all(step["simulated"] is True for step in result["step_results"])
    assert result["step_results"][1]["issues"][0]["code"] == "sandbox_tool_blocked"
    assert result["step_results"][2]["issues"][0]["code"] == "sandbox_path_outside_root"
    assert result["step_results"][3]["issues"][0]["code"] == "sandbox_step_not_mapping"


@pytest.mark.asyncio
async def test_runtime_status_counts_pending_auto_evolution_proposals(tmp_path) -> None:
    ReviewProposalStore(tmp_path).append_many([
        ReviewProposal(
            id="review_auto_workflow",
            created_at="2026-05-20T10:00:00+00:00",
            session_key="curator:system",
            turn_id="turn-1",
            origin=AUTO_EVOLUTION_ORIGIN,
            proposal_type="workflow",
            domain_id="core",
            title="Create workflow",
            content="Create a reviewed workflow.",
            payload={
                "static_gate": {
                    "decision": "requires_manual_review",
                    "issues": [
                        {
                            "code": "workflow_tool_not_allowed",
                            "severity": "pending",
                            "message": "Needs review.",
                        }
                    ],
                    "issue_counts": {"pending": 1},
                },
                "promotion_gate": {
                    "decision": "manual_review",
                    "suggested_action": "review_required",
                    "risk_level": "low",
                    "reasons": ["Sandbox blocked one or more workflow steps."],
                    "static_gate_decision": "requires_manual_review",
                    "sandbox_status": "blocked",
                    "auto_verify_eligible": False,
                    "issue_counts": {"pending": 1},
                },
                "sandbox": {"status": "blocked"},
            },
        )
    ])

    result = await RuntimeStatusTool(
        workspace=tmp_path,
        registry=SimpleNamespace(tool_names=["a"]),
        sessions=object(),
        pending_queues={},
        evolution_config=EvolutionConfig(mode="curated", dry_run=False),
    ).execute()

    evolution = result["evolution"]
    assert evolution["mode"] == "curated"
    assert evolution["dry_run"] is False
    assert evolution["pending_proposals_from_evolution"] == 1
    assert evolution["proposal_count_from_evolution"] == 1
    assert evolution["promotion_gate_decision_counts"] == {"manual_review": 1}
    assert evolution["static_gate_issue_counts"] == {"pending": 1}
    assert evolution["sandbox"]["blocked_workflow_proposals"] == 1
