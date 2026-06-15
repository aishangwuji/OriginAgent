from __future__ import annotations

import json
from importlib.resources import files as pkg_files
from datetime import datetime, timedelta, timezone

from OriginAgent.agent.background_review import ReviewProposal, ReviewProposalStore
from OriginAgent.agent.confirmation import ConfirmationRequest, PendingConfirmationStore
from OriginAgent.agent.facts import FactStore
from OriginAgent.agent.self_model import SelfModelService
from OriginAgent.config.schema import NearlineMemoryConfig
from OriginAgent.memory.candidates import GovernedMemoryWriter, MemoryCandidate
from OriginAgent.memory.models import ProfileSnapshot
from OriginAgent.memory.profile import NearlineProfileService
from OriginAgent.memory.store import NearlineMemoryStore

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
    assert self_model["memory"]["user_profile_file"]["status"] == "missing"
    assert self_model["memory"]["memory_candidate_queue"]["status"] == "lazy_not_created"
    assert self_model["reviews"]["pending_count"] == 0
    assert self_model["confirmations"]["pending_count"] == 0
    limitation_codes = {item["code"] for item in self_model["limitations"]}
    assert limitation_codes <= {"domain_invalid"}


def test_self_model_reports_workspace_initialization_states(tmp_path) -> None:
    user_file = tmp_path / "USER.md"
    template_text = (pkg_files("OriginAgent") / "templates" / "USER.md").read_text(encoding="utf-8")

    self_model = SelfModelService(tmp_path).build()
    assert self_model["memory"]["user_profile_file"]["status"] == "missing"
    assert self_model["memory"]["user_profile_file"]["exists"] is False
    assert self_model["memory"]["user_profile_file"]["managed_profile_shadow_present"] is False
    assert self_model["memory"]["user_profile_file"]["managed_profile_shadow_last_synced_at"] is None

    user_file.write_text(template_text, encoding="utf-8")

    self_model = SelfModelService(tmp_path).build()
    assert self_model["memory"]["user_profile_file"]["status"] == "template_only"
    assert self_model["memory"]["user_profile_file"]["exists"] is True
    assert self_model["memory"]["user_profile_file"]["managed_profile_shadow_present"] is False

    user_file.write_text("# User Profile\n\n- Name or preferred address: Ada\n", encoding="utf-8")
    self_model = SelfModelService(tmp_path).build()
    assert self_model["memory"]["user_profile_file"]["status"] == "initialized"
    assert self_model["memory"]["user_profile_file"]["exists"] is True
    assert self_model["memory"]["user_profile_file"]["managed_profile_shadow_present"] is False


def test_self_model_reports_managed_profile_shadow_presence(tmp_path) -> None:
    snapshot = ProfileSnapshot(
        profile_id="profile_1",
        owner_id="user",
        summary="Prefers concise updates.",
        explicit_traits=["Prefers concise updates"],
        implicit_traits=["Will send draft tomorrow"],
        source_memcell_ids=["mem_1"],
        updated_at="2026-06-05T10:03:00+00:00",
    )
    NearlineMemoryStore(tmp_path).append_profiles([snapshot])
    NearlineProfileService(tmp_path).write_profile_shadow(snapshot)

    self_model = SelfModelService(tmp_path).build()
    user_profile = self_model["memory"]["user_profile_file"]

    assert user_profile["status"] == "initialized"
    assert user_profile["managed_profile_shadow_present"] is True
    assert user_profile["managed_profile_shadow_last_synced_at"] == snapshot.updated_at


def test_self_model_reports_memory_candidate_queue_states(tmp_path) -> None:
    self_model = SelfModelService(tmp_path).build()
    queue = self_model["memory"]["memory_candidate_queue"]

    assert queue["status"] == "lazy_not_created"
    assert queue["exists"] is False
    assert queue["pending_count"] == 0
    assert queue["last_candidate_at"] is None
    assert queue["by_kind"] == {}
    assert queue["by_consumer_pending"] == {}
    assert queue["oldest_pending_at"] is None

    queue_file = tmp_path / "memory" / "memory_candidates.jsonl"
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text("", encoding="utf-8")

    self_model = SelfModelService(tmp_path).build()
    queue = self_model["memory"]["memory_candidate_queue"]
    assert queue["status"] == "empty"
    assert queue["exists"] is True
    assert queue["pending_count"] == 0
    assert queue["last_candidate_at"] is None
    assert queue["by_kind"] == {}
    assert queue["by_consumer_pending"] == {"dream": 0, "nearline_profile": 0}
    assert queue["oldest_pending_at"] is None

    writer = GovernedMemoryWriter(tmp_path)
    writer.append(
        MemoryCandidate(
            candidate_id="memcand_1",
            kind="preference",
            summary="Prefers concise release updates",
            source_session_key="cli:test",
            source_refs=["turn-1"],
            source_excerpt="I prefer concise release updates",
            confidence=0.95,
            sensitivity="low",
            scope="user",
            owner_id="user",
            created_at="2026-06-05T10:00:00+00:00",
        )
    )
    writer.append(
        MemoryCandidate(
            candidate_id="memcand_2",
            kind="task_pattern",
            summary="Often asks for release checklists",
            source_session_key="cli:test",
            source_refs=["turn-2"],
            source_excerpt="I usually want a release checklist",
            confidence=0.9,
            sensitivity="low",
            scope="user",
            owner_id="user",
            created_at="2026-06-05T10:01:00+00:00",
        )
    )

    self_model = SelfModelService(tmp_path).build()
    queue = self_model["memory"]["memory_candidate_queue"]

    assert queue["status"] == "active"
    assert queue["exists"] is True
    assert queue["pending_count"] == 2
    assert queue["last_candidate_at"] == "2026-06-05T10:01:00+00:00"
    assert queue["oldest_pending_at"] == "2026-06-05T10:00:00+00:00"
    assert queue["by_kind"] == {"preference": 1, "task_pattern": 1}
    assert queue["by_consumer_pending"] == {"dream": 0, "nearline_profile": 2}


def test_self_model_keeps_workspace_state_when_runtime_snapshot_memory_exists(tmp_path) -> None:
    self_model = SelfModelService(
        tmp_path,
        runtime_snapshot={
            "memory_summary": {
                "has_memory_context": True,
                "recent_history_pending_count": 3,
                "nearline": {"status": "enabled"},
            }
        },
    ).build()

    assert self_model["memory"]["has_memory_context"] is True
    assert self_model["memory"]["recent_history_pending_count"] == 3
    assert self_model["memory"]["user_profile_file"]["status"] == "missing"
    assert self_model["memory"]["memory_candidate_queue"]["status"] == "lazy_not_created"


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


def test_self_model_uses_runtime_nearline_config_without_snapshot(tmp_path) -> None:
    self_model = SelfModelService(
        tmp_path,
        nearline_memory_config=NearlineMemoryConfig(
            enabled=True,
            pipeline_enabled=False,
            profile_shadow_write_enabled=True,
        ),
    ).build()

    assert self_model["memory"]["nearline"]["status"] == "idle"
    assert self_model["memory"]["nearline"]["nearline_enabled"] is True
    assert self_model["memory"]["nearline"]["pipeline_enabled"] is False
    assert self_model["memory"]["nearline"]["profile_shadow_write_enabled"] is True
    assert self_model["memory"]["nearline"]["memcell_count"] == 0
    assert self_model["memory"]["nearline"]["user_shadow_last_synced_at"] is None


def test_self_model_does_not_read_stale_nearline_files_when_pipeline_is_disabled(tmp_path) -> None:
    nearline = tmp_path / "memory" / "nearline"
    nearline.mkdir(parents=True, exist_ok=True)
    (nearline / "episodes.jsonl").write_text(
        json.dumps(
            {
                "episode_id": "ep_1",
                "memcell_id": "mem_1",
                "session_key": "cli:test",
                "owner_id": "user",
                "summary": "Stale episode",
                "content": "Need a release checklist.",
                "timestamp": "2026-06-05T10:00:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    self_model = SelfModelService(
        tmp_path,
        nearline_memory_config=NearlineMemoryConfig(
            enabled=True,
            pipeline_enabled=False,
            profile_shadow_write_enabled=True,
        ),
    ).build()

    nearline_summary = self_model["memory"]["nearline"]
    assert nearline_summary["status"] == "idle"
    assert nearline_summary["episode_count"] == 0
    assert nearline_summary["last_synced_at"] is None


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

    self_model = SelfModelService(
        tmp_path,
        nearline_memory_config=NearlineMemoryConfig(
            enabled=True,
            pipeline_enabled=True,
            profile_shadow_write_enabled=True,
        ),
    ).build()
    nearline_summary = self_model["memory"]["nearline"]

    assert nearline_summary["episode_count"] == 1
    assert nearline_summary["foresight_count"] == 1
    assert nearline_summary["agent_case_count"] == 1
    assert nearline_summary["profile_count"] == 1
    assert nearline_summary["last_synced_at"] == "2026-06-05T10:03:00+00:00"
