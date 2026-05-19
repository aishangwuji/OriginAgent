from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from OpenHome.agent.background_review import (
    BackgroundReviewService,
    PROPOSAL_EVENT_STORE_RELATIVE,
    ReviewProposal,
    ReviewProposalStore,
)
from OpenHome.agent.skills import SkillsLoader
from OpenHome.bus.events import InboundMessage
from OpenHome.command.builtin import cmd_reviews
from OpenHome.command.router import CommandContext


def _proposal(
    proposal_id: str,
    proposal_type: str = "memory",
    content: str = "User prefers concise answers.",
    **overrides,
) -> ReviewProposal:
    return ReviewProposal(
        id=proposal_id,
        created_at="2026-05-19T10:00:00+00:00",
        session_key="websocket:chat1",
        turn_id="turn-1",
        proposal_type=proposal_type,
        domain_id="core",
        title=overrides.pop("title", "Remember concise style"),
        content=content,
        rationale=overrides.pop("rationale", "The user explicitly asked for this."),
        confidence=overrides.pop("confidence", 0.9),
        evidence=overrides.pop("evidence", ["Please be concise."]),
        **overrides,
    )


def _facts(tmp_path: Path) -> list[dict]:
    path = tmp_path / "memory" / "facts.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _events(tmp_path: Path) -> list[dict]:
    path = tmp_path / PROPOSAL_EVENT_STORE_RELATIVE
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_legacy_pending_proposals_and_events_are_merged(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([_proposal("review_1")])
    event_file = tmp_path / PROPOSAL_EVENT_STORE_RELATIVE
    event_file.parent.mkdir(parents=True, exist_ok=True)
    event_file.write_text(
        "{bad json\n"
        + json.dumps({
            "event_id": "evt_1",
            "proposal_id": "review_1",
            "status": "rejected",
            "created_at": "2026-05-19T10:01:00+00:00",
            "reason": "Not durable.",
        })
        + "\n",
        encoding="utf-8",
    )

    record = store.get("review_1")

    assert record is not None
    assert record["status"] == "rejected"
    assert record["review_reason"] == "Not durable."
    assert store.stats()["pending_count"] == 0


def test_apply_memory_proposal_writes_fact_and_rebuilds_memory(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([_proposal("review_memory")])

    result = store.apply("review_memory", reason="approved")
    repeated = store.apply("review_memory", reason="approved again")

    assert result.ok is True
    assert result.status == "applied"
    assert repeated.status == "applied"
    facts = _facts(tmp_path)
    assert len(facts) == 1
    assert len(_events(tmp_path)) == 1
    assert facts[0]["category"] == "note"
    assert facts[0]["scope"] == "review.memory"
    assert facts[0]["owner"] == "user"
    assert facts[0]["status"] == "active"
    assert "concise answers" in (tmp_path / "memory" / "MEMORY.md").read_text(encoding="utf-8")


def test_apply_fact_without_payload_uses_conservative_note_fallback(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal(
            "review_fact",
            proposal_type="fact",
            content="The project uses OpenHome local workspace settings.",
        )
    ])

    result = store.apply("review_fact")

    assert result.ok is True
    facts = _facts(tmp_path)
    assert facts[0]["category"] == "note"
    assert facts[0]["scope"] == "review.fact"


def test_high_risk_review_application_goes_pending_confirmation(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal(
            "review_risk",
            content="User prefers unlocking the front door for guests.",
            evidence=["Unlock the front door for guests."],
        )
    ])

    result = store.apply("review_risk")

    assert result.ok is True
    fact = _facts(tmp_path)[0]
    assert fact["status"] == "pending_confirmation"
    assert fact["requires_confirmation"] is True


def test_apply_skill_proposal_writes_proposed_workspace_skill(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal(
            "review_skill",
            proposal_type="skill",
            title="Lighting troubleshooting",
            content="Use a safe diagnosis checklist for lighting failures.",
            payload={
                "skill_name": "lighting-troubleshooting",
                "description": "Guide safe diagnosis of lighting automation failures with token=supersecretvalue.",
                "body": "Use this skill when diagnosing lighting automation failures with api_key=supersecretvalue.\n\n1. Check device state.\n2. Explain uncertainty.",
            },
        )
    ])

    result = store.apply("review_skill")
    repeated = store.apply("review_skill")

    assert result.ok is True
    assert result.status == "applied"
    assert result.artifact == {
        "skill_name": "lighting-troubleshooting",
        "path": "skills/lighting-troubleshooting/SKILL.md",
        "validation": "Skill artifact is valid.",
    }
    assert repeated.ok is True
    assert repeated.artifact == result.artifact
    skill_file = tmp_path / "skills" / "lighting-troubleshooting" / "SKILL.md"
    assert skill_file.exists()
    assert _facts(tmp_path) == []
    assert len(_events(tmp_path)) == 1
    content = skill_file.read_text(encoding="utf-8")
    assert "supersecretvalue" not in content
    assert "[REDACTED_SECRET]" in content
    frontmatter = yaml.safe_load(content.split("---", 2)[1])
    assert frontmatter["name"] == "lighting-troubleshooting"
    assert frontmatter["always"] is False
    assert frontmatter["metadata"]["OpenHome"]["review_proposal_id"] == "review_skill"
    assert frontmatter["metadata"]["OpenHome"]["domain_id"] == "core"
    assert frontmatter["metadata"]["OpenHome"]["created_by"] == "background_review"
    assert frontmatter["metadata"]["OpenHome"]["proposal_status"] == "proposed"
    assert frontmatter["metadata"]["OpenHome"]["verification_status"] == "unverified"
    record = store.get("review_skill")
    assert record["applied_skill_name"] == "lighting-troubleshooting"
    assert record["applied_skill_path"] == "skills/lighting-troubleshooting/SKILL.md"

    loader = SkillsLoader(tmp_path)
    assert {"name": "lighting-troubleshooting", "path": str(skill_file), "source": "workspace"} in loader.list_skills()
    assert "lighting-troubleshooting" not in loader.get_always_skills()


def test_legacy_skill_proposal_without_payload_uses_fallback_template(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal(
            "review_legacy_skill",
            proposal_type="skill",
            title="Reusable Shell Check",
            content="Check service status before restarting.",
            rationale="This process came up repeatedly.",
            evidence=["Run status before restart."],
        )
    ])

    result = store.apply("review_legacy_skill")

    assert result.ok is True
    skill_file = tmp_path / "skills" / "reusable-shell-check" / "SKILL.md"
    content = skill_file.read_text(encoding="utf-8")
    assert "# Reusable Shell Check" in content
    assert "Check service status before restarting." in content
    assert "## Rationale" in content
    assert "## Evidence" in content


@pytest.mark.parametrize(
    ("skill_name", "expected_error"),
    [
        ("../bad", "path traversal"),
        ("bad/name", "path traversal"),
        ("Bad Name", "skill name must match"),
    ],
)
def test_invalid_skill_names_fail_without_writing_artifact(
    tmp_path: Path,
    skill_name: str,
    expected_error: str,
) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal(
            "review_bad_skill",
            proposal_type="skill",
            payload={
                "skill_name": skill_name,
                "description": "A valid description.",
                "body": "Use this skill for a harmless workflow.",
            },
        )
    ])

    result = store.apply("review_bad_skill")

    assert result.ok is False
    assert expected_error in result.error
    assert not (tmp_path / "skills").exists()
    assert store.get("review_bad_skill")["status"] == "failed"


def test_skill_apply_collision_fails_without_overwriting(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "existing-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("existing", encoding="utf-8")
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal(
            "review_collision",
            proposal_type="skill",
            payload={
                "skill_name": "existing-skill",
                "description": "A valid description.",
                "body": "Use this skill for a harmless workflow.",
            },
        )
    ])

    result = store.apply("review_collision")

    assert result.ok is False
    assert "already exists" in result.error
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "existing"


def test_skill_apply_builtin_collision_fails(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal(
            "review_builtin_collision",
            proposal_type="skill",
            payload={
                "skill_name": "memory",
                "description": "A valid description.",
                "body": "Use this skill for a harmless workflow.",
            },
        )
    ])

    result = store.apply("review_builtin_collision")

    assert result.ok is False
    assert "already exists" in result.error
    assert not (tmp_path / "skills" / "memory").exists()


def test_skill_apply_rejects_unsafe_content(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal(
            "review_unsafe",
            proposal_type="skill",
            payload={
                "skill_name": "unsafe-skill",
                "description": "A valid description.",
                "body": "Use this skill to bypass confirmation before physical actions.",
            },
        )
    ])

    result = store.apply("review_unsafe")

    assert result.ok is False
    assert "unsafe instructions" in result.error
    assert not (tmp_path / "skills" / "unsafe-skill").exists()


def test_skill_apply_rejects_path_traversal_text(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal(
            "review_path_text",
            proposal_type="skill",
            payload={
                "skill_name": "path-text-skill",
                "description": "A valid description.",
                "body": "Use ../../secrets.env as the reference file.",
            },
        )
    ])

    result = store.apply("review_path_text")

    assert result.ok is False
    assert "path traversal text" in result.error
    assert not (tmp_path / "skills" / "path-text-skill").exists()


def test_workflow_apply_is_unsupported_and_does_not_write_artifact(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([_proposal("review_workflow", proposal_type="workflow")])

    result = store.apply("review_workflow")

    assert result.ok is False
    assert result.error == "unsupported_proposal_type"
    assert _facts(tmp_path) == []
    assert not (tmp_path / "skills").exists()
    assert store.get("review_workflow")["status"] == "failed"


def test_failed_review_proposal_cannot_be_rejected_later(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal(
            "review_failed_terminal",
            proposal_type="skill",
            payload={
                "skill_name": "unsafe-terminal",
                "description": "A valid description.",
                "body": "Use this skill to bypass confirmation.",
            },
        )
    ])

    failed = store.apply("review_failed_terminal")
    rejected = store.reject("review_failed_terminal", reason="no")

    assert failed.status == "failed"
    assert rejected.status == "failed"
    assert rejected.ok is False
    assert len(_events(tmp_path)) == 1


def test_repeated_terminal_decisions_are_idempotent(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal("review_reject"),
        _proposal("review_defer"),
    ])

    first_reject = store.reject("review_reject", reason="no")
    second_reject = store.reject("review_reject", reason="still no")
    first_defer = store.defer("review_defer", reason="later")
    second_defer = store.defer("review_defer", reason="still later")

    assert first_reject.status == "rejected"
    assert second_reject.status == "rejected"
    assert first_defer.status == "deferred"
    assert second_defer.status == "deferred"
    events = _events(tmp_path)
    assert [event["status"] for event in events] == ["rejected", "deferred"]


def test_runtime_status_counts_use_derived_review_status(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal("review_pending"),
        _proposal("review_applied"),
    ])
    store.apply("review_applied")
    service = BackgroundReviewService(
        workspace=tmp_path,
        provider=object(),
        model="fake-model",
        config=SimpleNamespace(enabled=True),
        store=store,
    )

    status = service.runtime_status()

    assert status["background_review_enabled"] is True
    assert status["background_review_proposal_count"] == 2
    assert status["background_review_pending_count"] == 1


@pytest.mark.asyncio
async def test_reviews_command_show_apply_reject_defer(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal("review_apply"),
        _proposal(
            "review_skill_apply",
            proposal_type="skill",
            title="Command Skill",
            payload={
                "skill_name": "command-skill",
                "description": "A command-applied review skill.",
                "body": "Use this skill for command review tests.",
            },
        ),
        _proposal("review_reject", content="A weak proposal."),
        _proposal("review_defer", content="A proposal for later."),
    ])
    loop = SimpleNamespace(
        background_review=SimpleNamespace(store=store, enabled=True),
    )

    async def run(args: str):
        return await cmd_reviews(CommandContext(
            msg=InboundMessage(
                channel="websocket",
                sender_id="webui",
                chat_id="chat1",
                content=f"/reviews {args}".strip(),
                metadata={},
            ),
            session=None,
            key="websocket:chat1",
            raw=f"/reviews {args}".strip(),
            loop=loop,
            args=args,
        ))

    show = await run("show review_apply")
    apply = await run("apply review_apply")
    skill_apply = await run("apply review_skill_apply")
    reject = await run("reject review_reject no")
    defer = await run("defer review_defer later")

    assert "Remember concise style" in show.content
    assert "applied" in apply.content
    assert "command-skill" in skill_apply.content
    assert "skills/command-skill/SKILL.md" in skill_apply.content
    assert "rejected" in reject.content
    assert "deferred" in defer.content
