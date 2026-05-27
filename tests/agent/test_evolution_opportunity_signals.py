from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from OriginAgent.agent.evolution import (
    AUTO_EVOLUTION_ORIGIN,
    SIGNAL_KIND_WORKFLOW,
    build_workflow_payload_from_signal,
    OpportunitySignalCandidate,
    OpportunitySignalStore,
    detect_workflow_opportunity_candidates,
    static_gate_workflow_payload,
)
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
    assert result["evolution"]["static_gate_issue_counts"] == {}
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
    assert evolution["pending_proposals_from_evolution"] == 0
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
                }
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
    assert evolution["static_gate_issue_counts"] == {"pending": 1}
