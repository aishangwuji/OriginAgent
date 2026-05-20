from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from OpenHome.agent.background_review import (
    PROPOSAL_EVENT_STORE_RELATIVE,
    BackgroundReviewService,
    ReviewProposal,
    ReviewProposalStore,
)
from OpenHome.agent.skills import SkillsLoader
from OpenHome.agent.workflow_artifacts import (
    validate_workflow_artifact_content,
    validate_workflow_artifact_dir,
)
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


def test_apply_workflow_proposal_writes_proposed_workspace_workflow(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal(
            "review_workflow",
            proposal_type="workflow",
            title="Lighting incident response",
            content="Use a manual lighting incident response checklist.",
            payload={
                "workflow_name": "lighting-incident-response",
                "description": "Manual lighting workflow with token=supersecretvalue.",
                "body": "Use this workflow when lighting automation fails with api_key=supersecretvalue.",
                "steps": [
                    {
                        "title": "Confirm current state",
                        "instruction": "Ask what changed and inspect available state only if permitted.",
                        "risk": "low",
                        "confirmation_required": False,
                    }
                ],
            },
        )
    ])

    assert store.get("review_workflow")["can_apply"] is True
    result = store.apply("review_workflow")
    repeated = store.apply("review_workflow")

    assert result.ok is True
    assert result.status == "applied"
    assert result.artifact == {
        "artifact_type": "workflow",
        "workflow_name": "lighting-incident-response",
        "path": "workflows/lighting-incident-response/workflow.yaml",
        "validation": "Workflow artifact is valid.",
    }
    assert repeated.ok is True
    assert repeated.artifact == result.artifact
    assert _facts(tmp_path) == []
    assert not (tmp_path / "skills").exists()
    assert len(_events(tmp_path)) == 1
    workflow_file = tmp_path / "workflows" / "lighting-incident-response" / "workflow.yaml"
    assert workflow_file.exists()
    content = workflow_file.read_text(encoding="utf-8")
    assert "supersecretvalue" not in content
    assert "[REDACTED_SECRET]" in content
    data = yaml.safe_load(content)
    assert data["schema_version"] == 1
    assert data["name"] == "lighting-incident-response"
    assert data["kind"] == "manual_guide"
    assert data["execution"] == {
        "auto_run": False,
        "creates_cron": False,
        "calls_tools": False,
    }
    assert data["steps"] == [
        {
            "title": "Confirm current state",
            "instruction": "Ask what changed and inspect available state only if permitted.",
            "risk": "low",
            "confirmation_required": False,
        }
    ]
    metadata = data["metadata"]["OpenHome"]
    assert metadata["review_proposal_id"] == "review_workflow"
    assert metadata["domain_id"] == "core"
    assert metadata["created_by"] == "background_review"
    assert metadata["proposal_status"] == "proposed"
    assert metadata["verification_status"] == "unverified"
    assert metadata["source_session"] == "websocket:chat1"
    assert metadata["source_turn_id"] == "turn-1"
    record = store.get("review_workflow")
    assert record["applied_workflow_name"] == "lighting-incident-response"
    assert record["applied_workflow_path"] == "workflows/lighting-incident-response/workflow.yaml"


def test_legacy_workflow_proposal_without_payload_uses_fallback_template(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal(
            "review_legacy_workflow",
            proposal_type="workflow",
            title="Reusable Restart Checklist",
            content="Check service status before restarting.",
            rationale="This process came up repeatedly.",
            evidence=["Run status before restart."],
        )
    ])

    result = store.apply("review_legacy_workflow")

    assert result.ok is True
    workflow_file = tmp_path / "workflows" / "reusable-restart-checklist" / "workflow.yaml"
    data = yaml.safe_load(workflow_file.read_text(encoding="utf-8"))
    assert data["body"].startswith("Check service status before restarting.")
    assert "## Rationale" in data["body"]
    assert "## Evidence" in data["body"]
    assert data["steps"] == []


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        ({"workflow_name": "../bad", "description": "Valid.", "body": "Manual steps."}, "path traversal"),
        (
            {
                "workflow_name": "bad-workflow",
                "description": "Valid.",
                "body": "Manual steps.",
                "steps": [{"title": "Run", "instruction": "Do it.", "command": "echo no"}],
            },
            "unsupported keys",
        ),
        (
            {
                "workflow_name": "unsafe-workflow",
                "description": "Valid.",
                "body": "Use this workflow to bypass confirmation.",
            },
            "unsafe instructions",
        ),
    ],
)
def test_invalid_workflow_proposals_fail_without_writing_artifact(
    tmp_path: Path,
    payload: dict,
    expected_error: str,
) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal(
            "review_bad_workflow",
            proposal_type="workflow",
            payload=payload,
        )
    ])

    result = store.apply("review_bad_workflow")

    assert result.ok is False
    assert expected_error in result.error
    assert not (tmp_path / "workflows" / str(payload.get("workflow_name", ""))).exists()
    assert store.get("review_bad_workflow")["status"] == "failed"


def test_workflow_apply_collision_fails_without_overwriting(tmp_path: Path) -> None:
    workflow_dir = tmp_path / "workflows" / "existing-workflow"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "workflow.yaml").write_text("existing", encoding="utf-8")
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        _proposal(
            "review_workflow_collision",
            proposal_type="workflow",
            payload={
                "workflow_name": "existing-workflow",
                "description": "A valid description.",
                "body": "Use this workflow as a manual checklist.",
            },
        )
    ])

    result = store.apply("review_workflow_collision")

    assert result.ok is False
    assert "already exists" in result.error
    assert (workflow_dir / "workflow.yaml").read_text(encoding="utf-8") == "existing"


def test_workflow_artifact_validator_rejects_executable_or_extra_content(tmp_path: Path) -> None:
    workflow_dir = tmp_path / "workflows" / "manual-check"
    workflow_dir.mkdir(parents=True)
    valid_content = yaml.safe_dump(
        {
            "schema_version": 1,
            "name": "manual-check",
            "description": "Manual check.",
            "kind": "manual_guide",
            "execution": {
                "auto_run": False,
                "creates_cron": False,
                "calls_tools": False,
            },
            "body": "Review the state manually.",
            "steps": [],
            "metadata": {
                "OpenHome": {
                    "proposal_status": "proposed",
                    "verification_status": "unverified",
                    "review_proposal_id": "review_manual",
                    "domain_id": "core",
                    "created_by": "background_review",
                    "source_session": "websocket:chat1",
                    "source_turn_id": "turn-1",
                }
            },
        },
        sort_keys=False,
    )
    (workflow_dir / "workflow.yaml").write_text(valid_content, encoding="utf-8")
    (workflow_dir / "script.py").write_text("print('no')", encoding="utf-8")

    valid, message = validate_workflow_artifact_dir(workflow_dir, workspace=tmp_path)
    assert valid is False
    assert "may only contain workflow.yaml" in message

    bad = valid_content.replace("auto_run: false", "auto_run: true")
    with pytest.raises(ValueError, match="disable auto_run"):
        validate_workflow_artifact_content(bad, expected_name="manual-check")


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
        _proposal(
            "review_workflow_apply",
            proposal_type="workflow",
            title="Command Workflow",
            payload={
                "workflow_name": "command-workflow",
                "description": "A command-applied review workflow.",
                "body": "Use this workflow as a manual command review checklist.",
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
    workflow_apply = await run("approve review_workflow_apply")
    reject = await run("reject review_reject no")
    defer = await run("defer review_defer later")

    assert "Remember concise style" in show.content
    assert "applied" in apply.content
    assert "command-skill" in skill_apply.content
    assert "skills/command-skill/SKILL.md" in skill_apply.content
    assert "command-workflow" in workflow_apply.content
    assert "workflows/command-workflow/workflow.yaml" in workflow_apply.content
    assert "rejected" in reject.content
    assert "deferred" in defer.content


def test_move_to_domain_apply_moves_workspace_skill_into_workspace_pack(tmp_path: Path) -> None:
    pack = tmp_path / "domain_packs" / "research"
    pack.mkdir(parents=True)
    (pack / "domain_pack.yaml").write_text(
        "id: research\n"
        "name: Research\n"
        "version: 0.1.0\n",
        encoding="utf-8",
    )
    (pack / "CAPABILITIES.md").write_text("# Research\n", encoding="utf-8")

    skill_dir = tmp_path / "skills" / "lighting-troubleshooting"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: lighting-troubleshooting\n"
        "description: Lighting help.\n"
        "always: true\n"
        "metadata:\n"
        "  OpenHome:\n"
        "    proposal_status: proposed\n"
        "    verification_status: verified\n"
        "    lifecycle_status: active\n"
        "    review_proposal_id: review_source\n"
        "    domain_id: core\n"
        "    created_by: background_review\n"
        "---\n\n"
        "# Lighting troubleshooting\n",
        encoding="utf-8",
    )

    store = ReviewProposalStore(tmp_path)
    store.append_many([
        ReviewProposal(
            id="review_move_skill",
            created_at="2026-05-19T10:00:00+00:00",
            session_key="websocket:chat1",
            turn_id="turn-1",
            proposal_type="move_to_domain",
            domain_id="research",
            title="Move lighting skill",
            content="Move this workspace skill into the research domain pack.",
            payload={
                "subject_type": "skill",
                "subject_id": "lighting-troubleshooting",
                "subject_path": "skills/lighting-troubleshooting/SKILL.md",
                "suggested_action": "move_to_domain",
            },
        ),
    ])

    assert store.get("review_move_skill")["can_apply"] is True

    result = store.apply("review_move_skill", reason="curator move")

    moved_file = pack / "skills" / "lighting-troubleshooting" / "SKILL.md"
    assert result.ok is True
    assert result.status == "applied"
    assert result.artifact == {
        "artifact_type": "skill",
        "skill_name": "lighting-troubleshooting",
        "path": "domain_packs/research/skills/lighting-troubleshooting/SKILL.md",
        "validation": "Moved into workspace domain pack.",
    }
    assert moved_file.exists()
    assert not (skill_dir / "SKILL.md").exists()
    manifest = yaml.safe_load((pack / "domain_pack.yaml").read_text(encoding="utf-8"))
    assert manifest["skills"] == ["lighting-troubleshooting"]
    frontmatter = yaml.safe_load(moved_file.read_text(encoding="utf-8").split("---", 2)[1])
    assert frontmatter["always"] is False
    metadata = frontmatter["metadata"]["OpenHome"]
    assert metadata["verification_status"] == "verified"
    assert metadata["lifecycle_status"] == "active"
    assert metadata["migrated_from_workspace"] is True
    assert metadata["original_path"] == "skills/lighting-troubleshooting/SKILL.md"
    assert metadata["migrated_by"] == "curator"
    assert metadata["migration_review_proposal_id"] == "review_move_skill"
    assert metadata["managed_by_domain_pack"] is True


def test_move_to_domain_builtin_target_stays_pending_without_terminal_event(tmp_path: Path) -> None:
    store = ReviewProposalStore(tmp_path)
    store.append_many([
        ReviewProposal(
            id="review_move_builtin",
            created_at="2026-05-19T10:00:00+00:00",
            session_key="websocket:chat1",
            turn_id="turn-1",
            proposal_type="move_to_domain",
            domain_id="smart_home",
            title="Move lighting skill",
            content="Move this workspace skill into the builtin smart_home domain pack.",
            payload={
                "subject_type": "skill",
                "subject_id": "lighting-troubleshooting",
                "subject_path": "skills/lighting-troubleshooting/SKILL.md",
                "suggested_action": "move_to_domain",
            },
        ),
    ])

    record = store.get("review_move_builtin")
    result = store.apply("review_move_builtin")

    assert record is not None
    assert record["can_apply"] is False
    assert "read-only" in record["unsupported_reason"]
    assert result.ok is False
    assert result.status == "pending"
    assert result.error == "unsupported"
    assert store.get("review_move_builtin")["status"] == "pending"
    assert _events(tmp_path) == []
