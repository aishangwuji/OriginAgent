from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from OpenHome.agent.background_review import ReviewProposal, ReviewProposalStore
from OpenHome.agent.confirmation import ConfirmationRequest, PendingConfirmationStore
from OpenHome.agent.facts import FactStore
from OpenHome.agent.self_model import SelfModelService

RAW_SECRET = "sk-proj-secretsecretsecretsecret"


def test_self_model_builds_empty_workspace_snapshot(tmp_path) -> None:
    self_model = SelfModelService(tmp_path).build()

    assert self_model["schema_version"] == 1
    assert self_model["identity"]["workspace_name"] == tmp_path.name
    assert self_model["domains"]["stats"]["builtin_domain_pack_count"] >= 1
    assert self_model["skills"]["stats"]["workspace_skills_count"] == 0
    assert self_model["workflows"]["items"] == []
    assert self_model["facts"]["active_count"] == 0
    assert self_model["reviews"]["pending_count"] == 0
    assert self_model["confirmations"]["pending_count"] == 0
    assert self_model["limitations"] == []


def test_self_model_derives_limitations_and_redacts_sensitive_content(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "lighting-troubleshooting"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: lighting-troubleshooting\n"
        "description: Manual lighting recovery.\n"
        "always: false\n"
        "metadata:\n"
        "  OpenHome:\n"
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
