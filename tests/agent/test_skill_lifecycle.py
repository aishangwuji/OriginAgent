from __future__ import annotations

import json
from pathlib import Path

import yaml

from OriginAgent.agent.skill_lifecycle import SkillLifecycleStore
from OriginAgent.agent.skills import SkillsLoader


def _write_workspace_skill(
    workspace: Path,
    name: str,
    *,
    verification_status: str = "unverified",
    lifecycle_status: str | None = None,
    always: bool = False,
) -> Path:
    skill_dir = workspace / "skills" / name
    skill_dir.mkdir(parents=True)
    originagent = {
        "proposal_status": "proposed",
        "verification_status": verification_status,
        "review_proposal_id": f"review_{name}",
        "domain_id": "core",
        "created_by": "background_review",
    }
    if lifecycle_status is not None:
        originagent["lifecycle_status"] = lifecycle_status
    content = {
        "name": name,
        "description": f"{name} description",
        "always": always,
        "metadata": {"OriginAgent": originagent},
    }
    path = skill_dir / "SKILL.md"
    path.write_text(
        "---\n"
        + yaml.safe_dump(content, sort_keys=False)
        + "---\n\n"
        + f"# {name}\n\nUse this skill carefully.\n",
        encoding="utf-8",
    )
    return path


def _frontmatter(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8").split("---", 2)[1]
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return parsed


def _event_count(store: SkillLifecycleStore) -> int:
    if not store.event_path.exists():
        return 0
    return sum(1 for line in store.event_path.read_text(encoding="utf-8").splitlines() if line.strip())


def test_p7_skill_is_derived_as_proposed_without_migration(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_path = _write_workspace_skill(workspace, "lighting-troubleshooting")

    loader = SkillsLoader(workspace, builtin_skills_dir=tmp_path / "builtin")
    record = loader.get_skill_record("lighting-troubleshooting")

    assert record is not None
    assert record["path"] == str(skill_path)
    assert record["lifecycle_status"] == "proposed"
    assert record["verification_status"] == "unverified"
    assert "lighting-troubleshooting" not in loader.build_skills_summary()


def test_verify_activate_and_always_write_events_and_frontmatter(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_path = _write_workspace_skill(workspace, "lighting-troubleshooting")
    store = SkillLifecycleStore(workspace)

    verified = store.transition("lighting-troubleshooting", action="verify", reason="looks right")
    assert verified.ok is True
    assert verified.status == "proposed"
    metadata = _frontmatter(skill_path)["metadata"]["OriginAgent"]
    assert metadata["verification_status"] == "verified"
    assert metadata["lifecycle_status"] == "proposed"
    assert metadata["reviewed_by"] == "user"
    assert metadata["reviewed_at"]
    assert metadata["last_lifecycle_event_id"]
    assert _event_count(store) == 1

    repeat = store.transition("lighting-troubleshooting", action="verify")
    assert repeat.ok is True
    assert _event_count(store) == 1

    activated = store.transition("lighting-troubleshooting", action="activate")
    assert activated.ok is True
    assert activated.status == "active"
    always = store.transition("lighting-troubleshooting", action="always", enabled=True)
    assert always.ok is True
    assert always.skill is not None
    assert always.skill["effective_always"] is True

    loader = SkillsLoader(workspace, builtin_skills_dir=tmp_path / "builtin")
    assert "lighting-troubleshooting" in loader.build_skills_summary()
    assert loader.get_always_skills() == ["lighting-troubleshooting"]
    assert _event_count(store) == 3


def test_invalid_transitions_do_not_write_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_workspace_skill(workspace, "draft")
    store = SkillLifecycleStore(workspace)

    activation = store.transition("draft", action="activate")
    always = store.transition("draft", action="always", enabled=True)

    assert activation.ok is False
    assert activation.error == "invalid_transition"
    assert always.ok is False
    assert always.error == "invalid_transition"
    assert _event_count(store) == 0


def test_always_off_can_revoke_unverified_workspace_skill(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_path = _write_workspace_skill(workspace, "draft", always=True)
    store = SkillLifecycleStore(workspace)
    loader = SkillsLoader(workspace, builtin_skills_dir=tmp_path / "builtin")

    record = loader.get_skill_record("draft")
    assert record is not None
    assert record["always"] is True
    assert record["effective_always"] is False
    assert record["can_toggle_always"] is True
    assert loader.get_always_skills() == []

    result = store.transition("draft", action="always", enabled=False)

    assert result.ok is True
    assert result.skill is not None
    assert result.skill["always"] is False
    assert _frontmatter(skill_path)["always"] is False
    assert _event_count(store) == 1


def test_rejected_and_deprecated_skills_are_not_loaded_by_default(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_workspace_skill(workspace, "reject-me")
    _write_workspace_skill(workspace, "old-skill", verification_status="verified", lifecycle_status="active", always=True)
    store = SkillLifecycleStore(workspace)

    rejected = store.transition("reject-me", action="reject")
    deprecated = store.transition("old-skill", action="deprecate")

    assert rejected.ok is True
    assert deprecated.ok is True
    loader = SkillsLoader(workspace, builtin_skills_dir=tmp_path / "builtin")
    assert loader.load_skill("reject-me") == (
        "Rejected skill: this workspace skill is marked rejected and cannot be loaded."
    )
    assert loader.load_skills_for_context(["reject-me"]) == ""
    deprecated_text = loader.load_skill("old-skill") or ""
    assert deprecated_text.startswith("Deprecated skill:")
    assert "old-skill" not in loader.build_skills_summary()
    assert loader.get_always_skills() == []


def test_terminal_statuses_do_not_reopen_or_duplicate_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_workspace_skill(workspace, "reject-me")
    _write_workspace_skill(workspace, "old-skill", verification_status="verified", lifecycle_status="active")
    store = SkillLifecycleStore(workspace)

    rejected = store.transition("reject-me", action="reject")
    repeated_reject = store.transition("reject-me", action="reject")
    activate_rejected = store.transition("reject-me", action="activate")
    deprecated = store.transition("old-skill", action="deprecate")
    repeated_deprecate = store.transition("old-skill", action="deprecate")
    activate_deprecated = store.transition("old-skill", action="activate")

    assert rejected.ok is True
    assert repeated_reject.ok is True
    assert activate_rejected.ok is False
    assert activate_rejected.error == "terminal_status"
    assert deprecated.ok is True
    assert repeated_deprecate.ok is True
    assert activate_deprecated.ok is False
    assert activate_deprecated.error == "terminal_status"
    assert _event_count(store) == 2


def test_bad_lifecycle_event_json_is_skipped(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_workspace_skill(workspace, "alpha")
    store = SkillLifecycleStore(workspace)
    store.event_path.parent.mkdir(parents=True)
    store.event_path.write_text("{bad json\n", encoding="utf-8")

    records = store.list_records([{"name": "alpha", "path": str(workspace / "skills" / "alpha" / "SKILL.md"), "source": "workspace"}])

    assert records[0]["lifecycle_status"] == "proposed"
    assert records[0]["verification_status"] == "unverified"


def test_latest_event_overrides_stale_frontmatter(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_path = _write_workspace_skill(workspace, "alpha")
    store = SkillLifecycleStore(workspace)
    store.event_path.parent.mkdir(parents=True)
    store.event_path.write_text(
        json.dumps(
            {
                "event_id": "event_1",
                "skill_name": "alpha",
                "action": "activate",
                "created_at": "2026-05-19T00:00:00+00:00",
                "reason": "api_key=sk-proj-" + "A" * 40,
                "next": {
                    "lifecycle_status": "active",
                    "verification_status": "verified",
                    "always": False,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    record = store.list_records([{"name": "alpha", "path": str(skill_path), "source": "workspace"}])[0]

    assert record["lifecycle_status"] == "active"
    assert record["verification_status"] == "verified"
    assert "[REDACTED_SECRET]" in record["last_event"]["reason"]
    assert "sk-proj" not in record["last_event"]["reason"]
