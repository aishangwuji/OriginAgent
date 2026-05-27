from __future__ import annotations

import json
from datetime import datetime, timezone
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
    }
    assert result["evolution"]["promotion_gate_decision_counts"] == {}
    assert result["evolution"]["static_gate_issue_counts"] == {}
    assert result["evolution"]["sandbox"] == {
        "enabled": True,
        "passed_workflow_proposals": 0,
        "failed_workflow_proposals": 0,
        "blocked_workflow_proposals": 0,
    }
    assert result["evolution"]["skill_candidates_enabled"] is False
    assert result["evolution"]["eligible_workflow_signals"] == 0
    assert result["evolution"]["eligible_skill_signals"] == 0
    assert result["evolution"]["high_score_signals"] == []


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
    assert blocked["status"] == "blocked"
    assert blocked["issues"][0]["code"] == "sandbox_tool_blocked"
    assert failed["status"] == "failed"
    assert failed["issues"][0]["code"] == "sandbox_path_outside_root"


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
