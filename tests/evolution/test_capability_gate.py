from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from OriginAgent.evolution import EvolutionCapabilityGate, EvolutionModuleManager
from OriginAgent.evolution.activation import ACTIVATION_SCHEMA_VERSION
from OriginAgent.evolution.manifest import MODULE_SCHEMA_VERSION
from OriginAgent.security.capabilities import CapabilitySnapshot


def _write_skill_package(root: Path, manifest_updates: dict[str, Any] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": MODULE_SCHEMA_VERSION,
        "module_id": "calendar-helper",
        "module_type": "skill",
        "version": "1.0.0",
    }
    manifest.update(manifest_updates or {})
    (root / "evolution_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    frontmatter = {
        "name": "calendar-helper",
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
    (root / "SKILL.md").write_text(
        "---\n" + yaml.safe_dump(frontmatter, sort_keys=False) + "---\n\n# Calendar Helper\n",
        encoding="utf-8",
    )
    return root


def _stage_verify_activate(
    workspace: Path,
    source: Path,
) -> tuple[EvolutionModuleManager, str]:
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(source)
    assert staged.ok, staged.error
    verified = manager.verify(staged.artifact_digest)
    assert verified.ok, verified.error
    activated = manager.activate_module(staged.artifact_digest)
    assert activated.ok, activated.error
    return manager, staged.artifact_digest


def test_active_verified_artifact_generates_capability_snapshot(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_verify_activate(
        workspace,
        _write_skill_package(tmp_path / "source", {"permissions": {"read_files": True}}),
    )

    result = manager.capability_snapshot(digest)

    assert result.ok is True
    assert result.status == "ready"
    assert result.module_id == "calendar-helper"
    assert result.snapshot is not None
    assert result.snapshot.version == 1
    assert result.snapshot.can_read_files is True
    assert result.snapshot.can_write_files is False


def test_undeclared_permissions_are_false_and_empty(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _manager, digest = _stage_verify_activate(workspace, _write_skill_package(tmp_path / "source"))

    result = EvolutionCapabilityGate(workspace).snapshot_for_artifact(digest)

    assert result.ok is True
    assert result.snapshot is not None
    assert result.snapshot.can_read_files is False
    assert result.snapshot.can_exec is False
    assert result.snapshot.allowed_mcp_scopes == ()


def test_mcp_read_scope_is_preserved(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _manager, digest = _stage_verify_activate(
        workspace,
        _write_skill_package(tmp_path / "source", {"permissions": {"mcp_scopes": ["read"]}}),
    )

    result = EvolutionCapabilityGate(workspace).snapshot_for_artifact(digest)

    assert result.ok is True
    assert result.snapshot is not None
    assert result.snapshot.allowed_mcp_scopes == ("read",)


def test_base_snapshot_intersection_cannot_expand_scheduled_permissions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _manager, digest = _stage_verify_activate(
        workspace,
        _write_skill_package(tmp_path / "source", {"permissions": {"read_files": True}}),
    )

    result = EvolutionCapabilityGate(workspace).snapshot_for_artifact(
        digest,
        base_snapshot=CapabilitySnapshot.scheduled_default(),
    )

    assert result.ok is True
    assert result.snapshot is not None
    assert result.snapshot.source == "cron"
    assert result.snapshot.trigger == "scheduled"
    assert result.snapshot.can_read_files is False


def test_snapshot_requires_active_activation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(_write_skill_package(tmp_path / "source"))
    assert staged.ok
    assert manager.verify(staged.artifact_digest).ok

    result = EvolutionCapabilityGate(workspace).snapshot_for_artifact(staged.artifact_digest)

    assert result.ok is False
    assert result.status == "not_active"


def test_snapshot_rejects_rolled_back_activation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager, digest = _stage_verify_activate(workspace, _write_skill_package(tmp_path / "source"))
    assert manager.rollback_module(digest).ok

    result = EvolutionCapabilityGate(workspace).snapshot_for_artifact(digest)

    assert result.ok is False
    assert result.status == "not_active"


def test_snapshot_rejects_active_but_unverified_artifact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(_write_skill_package(tmp_path / "source"))
    assert staged.ok
    activation_dir = workspace / "memory" / "evolution_activations" / staged.artifact_digest
    activation_dir.mkdir(parents=True)
    (activation_dir / "activation.json").write_text(
        json.dumps(
            {
                "schema_version": ACTIVATION_SCHEMA_VERSION,
                "activation_id": "activation_test",
                "artifact_digest": staged.artifact_digest,
                "module_id": "calendar-helper",
                "module_type": "skill",
                "version": "1.0.0",
                "status": "active",
                "activated_at": "2026-05-22T00:00:00+00:00",
                "rolled_back_at": "",
                "target_paths": ["skills/calendar-helper"],
                "activation_event_id": "",
                "rollback_event_id": "",
            }
        ),
        encoding="utf-8",
    )

    result = EvolutionCapabilityGate(workspace).snapshot_for_artifact(staged.artifact_digest)

    assert result.ok is False
    assert result.status == "unverified"


def test_snapshot_rejects_corrupted_staged_artifact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _manager, digest = _stage_verify_activate(workspace, _write_skill_package(tmp_path / "source"))
    artifact_skill = workspace / "memory" / "evolution_staging" / digest / "artifact" / "SKILL.md"
    artifact_skill.write_text("# Changed\n", encoding="utf-8")

    result = EvolutionCapabilityGate(workspace).snapshot_for_artifact(digest)

    assert result.ok is False
    assert result.status == "verification_failed"


def test_capability_errors_do_not_include_absolute_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_skill_package(tmp_path / "source")
    _manager, digest = _stage_verify_activate(workspace, source)
    manifest_path = workspace / "memory" / "evolution_staging" / digest / "artifact" / "evolution_manifest.yaml"
    manifest_path.unlink()

    result = EvolutionCapabilityGate(workspace).snapshot_for_artifact(digest)

    serialized = f"{result.error} {result.status}"
    assert str(workspace.resolve()) not in serialized
    assert str(source.resolve()) not in serialized
