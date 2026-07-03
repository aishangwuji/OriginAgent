from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from OriginAgent.evolution import EvolutionModuleManager
from OriginAgent.evolution.manifest import MODULE_SCHEMA_VERSION


def _write_evolution_manifest(
    root: Path,
    *,
    module_type: str = "skill",
    module_id: str = "calendar-helper",
    manifest_updates: dict[str, Any] | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": MODULE_SCHEMA_VERSION,
        "module_id": module_id,
        "module_type": module_type,
        "version": "1.0.0",
    }
    manifest.update(manifest_updates or {})
    (root / "evolution_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )


def _write_skill_package(root: Path, manifest_updates: dict[str, Any] | None = None) -> Path:
    _write_evolution_manifest(root, manifest_updates=manifest_updates)
    (root / "SKILL.md").write_text("# Calendar Helper\n", encoding="utf-8")
    return root


def _write_domain_pack_package(
    root: Path,
    *,
    include_capabilities: bool = True,
    with_missing_skill: bool = False,
) -> Path:
    _write_evolution_manifest(root, module_type="domain_pack", module_id="research")
    lines = [
        "id: research",
        "name: Research",
        "version: 1.0.0",
    ]
    if with_missing_skill:
        lines.extend(["skills:", "  - missing-skill"])
    (root / "domain_pack.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if include_capabilities:
        (root / "CAPABILITIES.md").write_text("# Research\n", encoding="utf-8")
    return root


def _event_rows(workspace: Path) -> list[dict]:
    import sqlite3

    path = workspace / "memory" / "evolution_events.jsonl"
    if path.exists():
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
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


def test_stage_valid_skill_package_writes_staging_and_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source-skill")
    manager = EvolutionModuleManager(workspace)

    result = manager.stage(source)

    assert result.ok is True
    assert result.status == "staged"
    assert result.module_id == "calendar-helper"
    assert result.module_type == "skill"
    assert result.module_version == "1.0.0"
    staging_dir = workspace / result.staging_path
    assert (staging_dir / "artifact" / "evolution_manifest.yaml").exists()
    assert (staging_dir / "artifact" / "SKILL.md").exists()
    metadata = json.loads((staging_dir / "staging.json").read_text(encoding="utf-8"))
    assert metadata["schema_version"] == "originagent.evolution.staging.v1"
    assert metadata["module_id"] == "calendar-helper"
    assert metadata["module_type"] == "skill"
    assert metadata["version"] == "1.0.0"
    assert metadata["artifact_digest"] == result.artifact_digest
    assert metadata["ledger_event_id"] == result.events[-1].event_id
    assert "staging_path" not in metadata
    assert [event.event_type for event in result.events] == [
        "module_proposed",
        "module_manifest_validated",
        "module_installed_staging",
    ]
    assert [event.result["staging_path"] for event in result.events] == [result.staging_path] * 3
    assert [event["event_type"] for event in _event_rows(workspace)] == [
        "module_proposed",
        "module_manifest_validated",
        "module_installed_staging",
    ]


def test_stage_events_do_not_record_source_absolute_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source-skill")
    manager = EvolutionModuleManager(workspace)

    manager.stage(source)

    event_text = "\n".join(json.dumps(row, sort_keys=True) for row in _event_rows(workspace))
    assert str(source.resolve()) not in event_text
    assert str(workspace.resolve()) not in event_text
    assert "source-skill" in event_text


def test_stage_does_not_create_stable_module_directories(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source-skill")
    manager = EvolutionModuleManager(workspace)

    manager.stage(source)

    assert not (workspace / "skills").exists()
    assert not (workspace / "domain_packs").exists()
    assert not (workspace / "workflows").exists()


def test_stage_same_digest_is_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source-skill")
    manager = EvolutionModuleManager(workspace)

    first = manager.stage(source)
    second = manager.stage(source)

    assert first.ok is True
    assert second.ok is True
    assert second.status == "already_staged"
    assert second.artifact_digest == first.artifact_digest
    assert second.staging_path == first.staging_path
    assert second.events[-1].event_type == "module_installed_staging"
    assert second.events[-1].result["status"] == "already_staged"


def test_corrupted_staging_directory_is_replaced(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source-skill")
    manager = EvolutionModuleManager(workspace)
    first = manager.stage(source)
    staging_dir = workspace / first.staging_path
    (staging_dir / "staging.json").unlink()
    (staging_dir / "orphan.txt").write_text("stale\n", encoding="utf-8")

    second = manager.stage(source)

    assert second.ok is True
    assert second.status == "staged"
    assert (workspace / second.staging_path / "staging.json").exists()
    assert not (workspace / second.staging_path / "orphan.txt").exists()


def test_manager_initialization_cleans_stale_tmp_directories(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    stale = workspace / "memory" / "evolution_staging" / ".tmp-stale"
    stale.mkdir(parents=True)
    (stale / "partial.txt").write_text("partial\n", encoding="utf-8")

    EvolutionModuleManager(workspace)

    assert not stale.exists()


def test_stage_failure_writes_module_failed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "broken-source"
    source.mkdir()
    manager = EvolutionModuleManager(workspace)

    result = manager.stage(source)

    assert result.ok is False
    assert result.status == "failed"
    assert "evolution_manifest.yaml" in result.error
    rows = _event_rows(workspace)
    assert rows[-1]["event_type"] == "module_failed"
    assert rows[-1]["result"]["status"] == "failed"


def test_stage_valid_domain_pack_without_installing_it(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_domain_pack_package(tmp_path / "domain-source")
    manager = EvolutionModuleManager(workspace)

    result = manager.stage(source)

    assert result.ok is True
    assert result.status == "staged"
    assert result.module_id == "research"
    assert result.module_type == "domain_pack"
    assert (workspace / result.staging_path / "artifact" / "domain_pack.yaml").exists()
    assert not (workspace / "domain_packs").exists()


def test_stage_domain_pack_missing_domain_manifest_fails_clearly(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "domain-source"
    _write_evolution_manifest(source, module_type="domain_pack", module_id="research")
    manager = EvolutionModuleManager(workspace)

    result = manager.stage(source)

    assert result.ok is False
    assert "domain_pack.yaml" in result.error
    assert _event_rows(workspace)[-1]["event_type"] == "module_failed"


def test_stage_invalid_domain_pack_declarations_fail(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_domain_pack_package(tmp_path / "domain-source", with_missing_skill=True)
    manager = EvolutionModuleManager(workspace)

    result = manager.stage(source)

    assert result.ok is False
    assert "missing SKILL.md" in result.error
    assert _event_rows(workspace)[-1]["event_type"] == "module_failed"


def test_stage_unavailable_domain_pack_is_still_staged(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_domain_pack_package(tmp_path / "domain-source", include_capabilities=False)
    manager = EvolutionModuleManager(workspace)

    result = manager.stage(source)

    assert result.ok is True
    assert result.status == "staged"
    assert result.module_type == "domain_pack"
    assert not (workspace / "domain_packs").exists()


def test_verify_staged_package_writes_permission_checked_and_verified(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source-skill")
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(source)

    result = manager.verify(staged.artifact_digest)

    assert result.ok is True
    assert result.status == "verified"
    assert result.artifact_digest == staged.artifact_digest
    assert [event.event_type for event in result.events] == [
        "module_permission_checked",
        "module_verified",
    ]
    rows = _event_rows(workspace)
    assert [row["event_type"] for row in rows[-2:]] == [
        "module_permission_checked",
        "module_verified",
    ]


def test_verify_permission_failure_writes_failed_without_verified(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(
        tmp_path / "source-skill",
        manifest_updates={"permissions": {"write_files": True}},
    )
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(source)

    result = manager.verify(staged.artifact_digest)

    assert result.ok is False
    assert result.status == "failed"
    assert [event.event_type for event in result.events] == [
        "module_permission_checked",
        "module_failed",
    ]
    assert "module_verified" not in [row["event_type"] for row in _event_rows(workspace)[-2:]]


def test_permission_checked_event_records_structured_result(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(
        tmp_path / "source-skill",
        manifest_updates={"permissions": {"read_files": True, "unknown": True}},
    )
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(source)

    result = manager.verify(staged.artifact_digest)

    permission_result = result.events[0].result
    assert permission_result["status"] == "permission_check_completed"
    assert permission_result["permissions_evaluated"] == {"read_files": True}
    assert permission_result["unknown_keys_rejected"] == ["unknown"]
    assert "permissions_denied" in permission_result
    assert "checks" in permission_result


def test_verify_events_do_not_record_absolute_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source-skill")
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(source)

    manager.verify(staged.artifact_digest)

    event_text = "\n".join(json.dumps(row, sort_keys=True) for row in _event_rows(workspace))
    assert str(source.resolve()) not in event_text
    assert str((workspace / staged.staging_path).resolve()) not in event_text
    assert staged.staging_path in event_text


def test_verify_does_not_create_stable_module_directories(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source-skill")
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(source)

    manager.verify(staged.artifact_digest)

    assert not (workspace / "skills").exists()
    assert not (workspace / "domain_packs").exists()
    assert not (workspace / "workflows").exists()


def test_repeated_verify_appends_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source-skill")
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(source)

    first = manager.verify(staged.artifact_digest)
    second = manager.verify(staged.artifact_digest)

    assert first.ok is True
    assert second.ok is True
    rows = _event_rows(workspace)
    assert len(rows) == 7
    assert [row["event_type"] for row in rows[-4:]] == [
        "module_permission_checked",
        "module_verified",
        "module_permission_checked",
        "module_verified",
    ]


def test_manager_create_state_branch_after_verify(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source-skill")
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(source)
    assert staged.ok
    assert manager.verify(staged.artifact_digest).ok

    result = manager.create_state_branch(staged.artifact_digest)

    assert result.ok is True
    assert result.status == "created"
    assert result.branch_id.startswith("branch_")
    assert _event_rows(workspace)[-1]["event_type"] == "state_branch_created"


def test_manager_create_state_branch_requires_verify(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source-skill")
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(source)
    assert staged.ok

    result = manager.create_state_branch(staged.artifact_digest)

    assert result.ok is False
    assert "verified" in result.error


def test_manager_merge_and_discard_state_branch_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source-skill")
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(source)
    assert staged.ok
    assert manager.verify(staged.artifact_digest).ok

    merge_branch = manager.create_state_branch(staged.artifact_digest)
    assert merge_branch.ok
    merged = manager.merge_state_branch(merge_branch.branch_id)
    discard_branch = manager.create_state_branch(staged.artifact_digest)
    assert discard_branch.ok
    discarded = manager.discard_state_branch(discard_branch.branch_id)

    assert merged.ok is True
    assert discarded.ok is True
    rows = _event_rows(workspace)
    assert "state_branch_merged" in [row["event_type"] for row in rows]
    assert "state_branch_discarded" in [row["event_type"] for row in rows]


def test_manager_state_branch_events_do_not_record_absolute_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source-skill")
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(source)
    assert staged.ok
    assert manager.verify(staged.artifact_digest).ok

    branch = manager.create_state_branch(staged.artifact_digest)
    assert branch.ok
    manager.discard_state_branch(branch.branch_id)

    event_text = "\n".join(json.dumps(row, sort_keys=True) for row in _event_rows(workspace))
    assert str(source.resolve()) not in event_text
    assert str((workspace / "memory" / "evolution_branches" / branch.branch_id).resolve()) not in event_text
    assert branch.branch_id in event_text
