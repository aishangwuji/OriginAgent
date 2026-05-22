from __future__ import annotations

import json
from pathlib import Path

from OriginAgent.evolution import EvolutionModuleManager
from OriginAgent.evolution.manifest import MODULE_SCHEMA_VERSION


def _write_evolution_manifest(root: Path, *, module_type: str = "skill", module_id: str = "calendar-helper") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "evolution_manifest.yaml").write_text(
        "\n".join(
            [
                f"schema_version: {MODULE_SCHEMA_VERSION}",
                f"module_id: {module_id}",
                f"module_type: {module_type}",
                "version: 1.0.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_skill_package(root: Path) -> Path:
    _write_evolution_manifest(root)
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
    path = workspace / "memory" / "evolution_events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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

    event_text = (workspace / "memory" / "evolution_events.jsonl").read_text(encoding="utf-8")
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
