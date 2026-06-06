from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from OriginAgent.agent.background_review import ReviewProposal, ReviewProposalStore
from OriginAgent.agent.confirmation import ConfirmationRequest, PendingConfirmationStore
from OriginAgent.agent.facts import FactStore
from OriginAgent.agent.self_model import SelfModelService

RAW_SECRET = "sk-proj-secretsecretsecretsecret"


def test_self_model_builds_empty_workspace_snapshot(tmp_path) -> None:
    self_model = SelfModelService(tmp_path).build()

    assert self_model["schema_version"] == 1
    assert self_model["identity"]["workspace_name"] == tmp_path.name
    assert self_model["domains"]["stats"]["builtin_domain_pack_count"] >= 1
    assert self_model["skills"]["stats"]["workspace_skills_count"] == 0
    assert self_model["workflows"]["items"] == []
    assert self_model["facts"]["active_count"] == 0
    assert self_model["memory"]["nearline"]["status"] == "disabled"
    assert self_model["memory"]["nearline"]["memcell_count"] == 0
    assert self_model["reviews"]["pending_count"] == 0
    assert self_model["confirmations"]["pending_count"] == 0
    limitation_codes = {item["code"] for item in self_model["limitations"]}
    assert limitation_codes <= {"domain_invalid"}


def test_self_model_derives_limitations_and_redacts_sensitive_content(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "lighting-troubleshooting"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: lighting-troubleshooting\n"
        "description: Manual lighting recovery.\n"
        "always: false\n"
        "metadata:\n"
        "  OriginAgent:\n"
        "    proposal_status: proposed\n"
        "    verification_status: unverified\n"
        "    lifecycle_status: proposed\n"
        "    review_proposal_id: review_skill\n"
        "    created_by: background_review\n"
        "---\n\n# Lighting\n",
        encoding="utf-8",
    )

    broken_workflow = tmp_path / "workflows" / "broken-workflow"
    broken_workflow.mkdir(parents=True)
    (broken_workflow / "notes.txt").write_text("not a workflow", encoding="utf-8")

    ReviewProposalStore(tmp_path).append_many([
        ReviewProposal(
            id="review_memory",
            created_at="2026-05-20T10:00:00+00:00",
            session_key="websocket:chat1",
            turn_id="turn-1",
            proposal_type="memory",
            domain_id="core",
            title="Remember private token",
            content=f"Remember this secret {RAW_SECRET}",
        )
    ])

    now = datetime.now(timezone.utc)
    PendingConfirmationStore(tmp_path).write_all([
        ConfirmationRequest(
            confirmation_id="confirmation_secret",
            kind="action_confirmation",
            status="pending",
            prompt=f"Need confirmation for token {RAW_SECRET}",
            action="test_action",
            scope="general",
            trigger="user_initiated",
            risk="high",
            requested_by="user",
            decision_reason="",
            presence_status="unknown",
            related_fact_ids=[],
            created_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=5)).isoformat(),
        )
    ])

    FactStore(tmp_path).upsert_fact(
        f"Secret should stay hidden {RAW_SECRET}",
        category="note",
        scope="general",
        owner="user",
    )

    self_model = SelfModelService(tmp_path).build()
    serialized = json.dumps(self_model, ensure_ascii=False, sort_keys=True)
    limitation_codes = {item["code"] for item in self_model["limitations"]}

    assert self_model["facts"]["active_count"] == 1
    assert self_model["reviews"]["pending_count"] == 1
    assert self_model["confirmations"]["pending_count"] == 1
    assert self_model["workflows"]["items"][0]["status"] == "invalid"
    assert limitation_codes >= {
        "skill_unverified",
        "workflow_invalid",
        "review_pending",
        "confirmation_pending",
    }
    assert RAW_SECRET not in serialized
    assert "[REDACTED_SECRET]" in serialized


def test_self_model_uses_nearline_runtime_snapshot_when_present(tmp_path) -> None:
    self_model = SelfModelService(
        tmp_path,
        runtime_snapshot={
            "memory_summary": {
                "has_memory_context": False,
                "recent_history_pending_count": 0,
                "nearline": {
                    "status": "enabled",
                    "memcell_count": 3,
                    "episode_count": 2,
                },
            },
            "nearline_memory_summary": {
                "status": "enabled",
                "memcell_count": 3,
                "episode_count": 2,
            },
        },
    ).build()

    assert self_model["memory"]["nearline"]["status"] == "enabled"
    assert self_model["memory"]["nearline"]["memcell_count"] == 3


def test_self_model_reports_nearline_counts_and_last_sync(tmp_path) -> None:
    nearline = tmp_path / "memory" / "nearline"
    nearline.mkdir(parents=True, exist_ok=True)
    (nearline / "episodes.jsonl").write_text(
        json.dumps(
            {
                "episode_id": "ep_1",
                "memcell_id": "mem_1",
                "session_key": "cli:test",
                "owner_id": "user",
                "summary": "User asked for release checklist",
                "content": "Need a release checklist.",
                "timestamp": "2026-06-05T10:00:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (nearline / "foresights.jsonl").write_text(
        json.dumps(
            {
                "foresight_id": "fo_1",
                "memcell_id": "mem_1",
                "session_key": "cli:test",
                "owner_id": "user",
                "content": "Will send draft tomorrow",
                "evidence": "Will send tomorrow",
                "start_at": "2026-06-06T09:00:00+00:00",
                "timestamp": "2026-06-05T10:01:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (nearline / "agent_cases.jsonl").write_text(
        json.dumps(
            {
                "case_id": "case_1",
                "memcell_id": "mem_2",
                "session_key": "cli:test",
                "agent_id": "origin",
                "task_intent": "Implement validation",
                "approach": "Added smoke test",
                "outcome_summary": "Validation passed",
                "quality_score": 1.0,
                "timestamp": "2026-06-05T10:02:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (nearline / "profiles.jsonl").write_text(
        json.dumps(
            {
                "profile_id": "profile_1",
                "owner_id": "user",
                "summary": "Prefers concise updates.",
                "explicit_traits": ["Prefers concise updates"],
                "implicit_traits": ["Will send draft tomorrow"],
                "source_memcell_ids": ["mem_1"],
                "updated_at": "2026-06-05T10:03:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    self_model = SelfModelService(tmp_path).build()
    nearline_summary = self_model["memory"]["nearline"]

    assert nearline_summary["episode_count"] == 1
    assert nearline_summary["foresight_count"] == 1
    assert nearline_summary["agent_case_count"] == 1
    assert nearline_summary["profile_count"] == 1
    assert nearline_summary["last_synced_at"] == "2026-06-05T10:03:00+00:00"
