from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from OriginAgent.agent.domain_pack_governance import DomainPackGovernanceService
from OriginAgent.agent.skills import SkillsLoader
from OriginAgent.config.schema import Config
from OriginAgent.evolution import EvolutionModuleManager
from OriginAgent.evolution.manifest import MODULE_SCHEMA_VERSION


def _write_evolution_manifest(
    root: Path,
    *,
    module_id: str = "calendar-helper",
    module_type: str = "skill",
    version: str = "1.0.0",
    manifest_updates: dict[str, Any] | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": MODULE_SCHEMA_VERSION,
        "module_id": module_id,
        "module_type": module_type,
        "version": version,
    }
    manifest.update(manifest_updates or {})
    (root / "evolution_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )


def _write_skill_package(
    root: Path,
    *,
    module_id: str = "calendar-helper",
    proposed: bool = True,
    manifest_updates: dict[str, Any] | None = None,
) -> Path:
    _write_evolution_manifest(root, module_id=module_id, manifest_updates=manifest_updates)
    if proposed:
        frontmatter = {
            "name": module_id,
            "description": "Calendar helper",
            "metadata": {
                "OriginAgent": {
                    "proposal_status": "proposed",
                    "verification_status": "unverified",
                    "lifecycle_status": "proposed",
                    "created_by": "background_review",
                }
            },
        }
        body = "---\n" + yaml.safe_dump(frontmatter, sort_keys=False) + "---\n\n# Calendar Helper\n"
    else:
        body = "# Calendar Helper\n"
    (root / "SKILL.md").write_text(body, encoding="utf-8")
    return root


def _write_domain_pack_package(root: Path, *, module_id: str = "research") -> Path:
    _write_evolution_manifest(root, module_id=module_id, module_type="domain_pack")
    (root / "domain_pack.yaml").write_text(
        f"id: {module_id}\nname: Research\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    (root / "CAPABILITIES.md").write_text("# Research\n", encoding="utf-8")
    return root


def _write_unsupported_package(root: Path, *, module_type: str) -> Path:
    _write_evolution_manifest(root, module_id=f"{module_type}-module", module_type=module_type)
    (root / "README.md").write_text("# Unsupported\n", encoding="utf-8")
    return root


def _config_pair() -> tuple[Config, Any, Any]:
    config = Config()
    return config, lambda: config, lambda _config: None


def _stage_and_verify(manager: EvolutionModuleManager, source: Path):
    staged = manager.stage(source)
    assert staged.ok, staged.error
    verified = manager.verify(staged.artifact_digest)
    assert verified.ok, verified.error
    return staged


def _event_rows(workspace: Path, filename: str = "evolution_events.jsonl") -> list[dict[str, Any]]:
    import sqlite3

    path = workspace / "memory" / filename
    if path.exists():
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # Only fall back to SQLite when the default event filename is requested
    if filename != "evolution_events.jsonl":
        return []
    db_path = workspace / "memory" / "evolution_ledger.sqlite3"
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM evolution_events ORDER BY rowid").fetchall()
        conn.close()
        result = []
        for row in rows:
            d = dict(row)
            payload = json.loads(d.pop("payload_json", "{}"))
            merged = {**payload, **d}
            result.append(merged)
        return result
    return []


def test_unverified_digest_activation_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(_write_skill_package(tmp_path / "source"))
    assert staged.ok

    result = manager.activate_module(staged.artifact_digest, approved_by="test")

    assert result.ok is False
    assert result.status == "unverified"
    assert _event_rows(workspace)[-1]["event_type"] == "module_failed"


def test_verified_skill_activation_creates_active_skill(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager = EvolutionModuleManager(workspace)
    staged = _stage_and_verify(manager, _write_skill_package(tmp_path / "source"))

    result = manager.activate_module(staged.artifact_digest, approved_by="test")

    assert result.ok is True
    assert result.status == "active"
    skill_dir = workspace / "skills" / "calendar-helper"
    assert (skill_dir / "SKILL.md").exists()
    assert not (skill_dir / "evolution_manifest.yaml").exists()
    activation_json = workspace / "memory" / "evolution_activations" / staged.artifact_digest / "activation.json"
    metadata = json.loads(activation_json.read_text(encoding="utf-8"))
    assert metadata["status"] == "active"
    assert metadata["activation_event_id"] == result.events[-1].event_id
    assert result.events[-1].event_type == "module_activated"
    loader = SkillsLoader(workspace, builtin_skills_dir=tmp_path / "empty")
    assert "calendar-helper" in loader.build_skills_summary()


def test_invalid_skill_module_id_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager = EvolutionModuleManager(workspace)
    staged = _stage_and_verify(manager, _write_skill_package(tmp_path / "source", module_id="Bad_Name"))

    result = manager.activate_module(staged.artifact_digest, approved_by="test")

    assert result.ok is False
    assert result.status == "invalid_skill_name"
    assert not (workspace / "skills" / "Bad_Name").exists()


def test_skill_target_conflict_does_not_overwrite(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    existing = workspace / "skills" / "calendar-helper"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("# Existing\n", encoding="utf-8")
    manager = EvolutionModuleManager(workspace)
    staged = _stage_and_verify(manager, _write_skill_package(tmp_path / "source"))

    result = manager.activate_module(staged.artifact_digest, approved_by="test")

    assert result.ok is False
    assert result.status == "skill_exists"
    assert (existing / "SKILL.md").read_text(encoding="utf-8") == "# Existing\n"


def test_skill_verify_transition_failure_deletes_copied_skill(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager = EvolutionModuleManager(workspace)
    staged = _stage_and_verify(manager, _write_skill_package(tmp_path / "source", proposed=False))

    result = manager.activate_module(staged.artifact_digest, approved_by="test")

    assert result.ok is False
    assert result.status == "skill_verify_failed"
    assert not (workspace / "skills" / "calendar-helper").exists()


def test_skill_activate_transition_failure_attempts_deprecate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    manager = EvolutionModuleManager(workspace)
    staged = _stage_and_verify(manager, _write_skill_package(tmp_path / "source"))
    actions: list[str] = []

    class FakeLifecycle:
        def __init__(self, _workspace: Path) -> None:
            pass

        def transition(self, _name: str, *, action: str, **_kwargs: Any):
            actions.append(action)
            if action == "activate":
                return _Transition(False, "invalid_transition")
            return _Transition(True, "")

    monkeypatch.setattr("OriginAgent.evolution.activation.SkillLifecycleStore", FakeLifecycle)

    result = manager.activate_module(staged.artifact_digest, approved_by="test")

    assert result.ok is False
    assert result.status == "skill_activate_failed"
    assert actions == ["verify", "activate", "deprecate"]
    assert (workspace / "skills" / "calendar-helper").exists()


def test_skill_rollback_deprecates_without_deleting_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager = EvolutionModuleManager(workspace)
    staged = _stage_and_verify(manager, _write_skill_package(tmp_path / "source"))
    assert manager.activate_module(staged.artifact_digest, approved_by="test").ok

    result = manager.rollback_module(staged.artifact_digest)

    assert result.ok is True
    assert result.status == "rolled_back"
    assert (workspace / "skills" / "calendar-helper" / "SKILL.md").exists()
    loader = SkillsLoader(workspace, builtin_skills_dir=tmp_path / "empty")
    assert "calendar-helper" not in loader.build_skills_summary()
    assert [event.event_type for event in result.events] == [
        "module_rollback_started",
        "module_rollback_succeeded",
    ]


def test_domain_pack_activation_and_rollback_use_governance(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config, config_loader, config_saver = _config_pair()
    manager = EvolutionModuleManager(
        workspace,
        config_loader=config_loader,
        config_saver=config_saver,
    )
    staged = _stage_and_verify(manager, _write_domain_pack_package(tmp_path / "domain-source"))

    activated = manager.activate_module(staged.artifact_digest, approved_by="test")

    assert activated.ok is True
    assert (workspace / "domain_packs" / "research" / "domain_pack.yaml").exists()
    service = DomainPackGovernanceService(
        workspace,
        config_loader=lambda: config,
        config_saver=lambda _config: None,
    )
    record = service.get_record("research")
    assert record is not None
    assert record["active_requested"] is True

    rolled_back = manager.rollback_module(staged.artifact_digest)

    assert rolled_back.ok is True
    assert not (workspace / "domain_packs" / "research").exists()
    assert [row["action"] for row in _event_rows(workspace, "domain_pack_events.jsonl")] == [
        "install",
        "activate",
        "deactivate",
        "uninstall",
    ]


def test_domain_pack_existing_target_fails_without_upgrade(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    existing = workspace / "domain_packs" / "research"
    existing.mkdir(parents=True)
    (existing / "domain_pack.yaml").write_text("id: research\nname: Existing\nversion: 0.1.0\n")
    config, config_loader, config_saver = _config_pair()
    manager = EvolutionModuleManager(workspace, config_loader=config_loader, config_saver=config_saver)
    staged = _stage_and_verify(manager, _write_domain_pack_package(tmp_path / "domain-source"))

    result = manager.activate_module(staged.artifact_digest, approved_by="test")

    assert result.ok is False
    assert result.status == "domain_pack_exists"
    assert (existing / "domain_pack.yaml").read_text(encoding="utf-8").startswith("id: research")
    assert _event_rows(workspace, "domain_pack_events.jsonl") == []


def test_workflow_and_tool_activation_are_unsupported(tmp_path: Path) -> None:
    for module_type in ("workflow", "tool"):
        workspace = tmp_path / module_type / "workspace"
        manager = EvolutionModuleManager(workspace)
        staged = _stage_and_verify(
            manager,
            _write_unsupported_package(tmp_path / module_type / "source", module_type=module_type),
        )

        result = manager.activate_module(staged.artifact_digest, approved_by="test")

        assert result.ok is False
        assert result.status == "unsupported"
        assert not (workspace / "skills").exists()
        assert not (workspace / "domain_packs").exists()


def test_active_state_branch_blocks_activation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager = EvolutionModuleManager(workspace)
    staged = _stage_and_verify(manager, _write_skill_package(tmp_path / "source"))
    branch = manager.create_state_branch(staged.artifact_digest)
    assert branch.ok

    result = manager.activate_module(staged.artifact_digest, approved_by="test")

    assert result.ok is False
    assert result.status == "active_state_branch"


def test_repeated_activate_and_rollback_are_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager = EvolutionModuleManager(workspace)
    staged = _stage_and_verify(manager, _write_skill_package(tmp_path / "source"))
    assert manager.activate_module(staged.artifact_digest, approved_by="test").ok

    repeated_activate = manager.activate_module(staged.artifact_digest, approved_by="test")
    rolled_back = manager.rollback_module(staged.artifact_digest)
    repeated_rollback = manager.rollback_module(staged.artifact_digest)

    assert repeated_activate.ok is True
    assert repeated_activate.status == "already_active"
    assert rolled_back.ok is True
    assert repeated_rollback.ok is True
    assert repeated_rollback.status == "already_rolled_back"


def test_activation_events_do_not_record_absolute_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source")
    manager = EvolutionModuleManager(workspace)
    staged = _stage_and_verify(manager, source)

    manager.activate_module(staged.artifact_digest, approved_by="test")
    manager.rollback_module(staged.artifact_digest)

    event_text = "\n".join(json.dumps(row, sort_keys=True) for row in _event_rows(workspace))
    assert str(source.resolve()) not in event_text
    assert str((workspace / "skills" / "calendar-helper").resolve()) not in event_text
    assert str((workspace / staged.staging_path).resolve()) not in event_text
    assert "skills/calendar-helper" in event_text


@dataclass(frozen=True)
class _Transition:
    ok: bool
    error: str
    message: str = ""
