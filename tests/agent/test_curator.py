from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from OriginAgent.agent.background_review import ReviewProposal, ReviewProposalStore
from OriginAgent.agent.curator import CURATOR_ORIGIN, CuratorService
from OriginAgent.agent.skills import SkillsLoader


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


def _proposal_events(workspace: Path) -> list[dict]:
    event_file = workspace / "memory" / "review_proposal_events.jsonl"
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
