from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from OriginAgent.evolution import EvolutionModuleManager, EvolutionModuleVerifier
from OriginAgent.evolution.manifest import MODULE_SCHEMA_VERSION
from OriginAgent.evolution.package import compute_artifact_digest


def _write_package(root: Path, manifest_updates: dict[str, Any] | None = None) -> Path:
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
    (root / "SKILL.md").write_text("# Calendar Helper\n", encoding="utf-8")
    return root


def _write_domain_pack_package(
    root: Path,
    *,
    include_capabilities: bool = True,
    with_missing_skill: bool = False,
) -> Path:
    _write_package(root, {"module_id": "research", "module_type": "domain_pack"})
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


def _stage_package(tmp_path: Path, manifest_updates: dict[str, Any] | None = None):
    workspace = tmp_path / "workspace"
    source = _write_package(tmp_path / "source", manifest_updates)
    manager = EvolutionModuleManager(workspace)
    staged = manager.stage(source)
    assert staged.ok
    return workspace, staged


def _check(report, code: str) -> dict[str, Any]:
    matches = [check for check in report.checks if check["code"] == code]
    assert matches, f"missing check code: {code}"
    return matches[-1]


def test_staged_skill_verifies_successfully(tmp_path: Path) -> None:
    workspace, staged = _stage_package(tmp_path)
    verifier = EvolutionModuleVerifier(workspace)

    report = verifier.verify(staged.artifact_digest)

    assert report.ok is True
    assert report.status == "verified"
    assert report.module_id == "calendar-helper"
    assert report.module_type == "skill"
    assert report.module_version == "1.0.0"
    assert report.artifact_digest == staged.artifact_digest
    assert report.staging_path == staged.staging_path
    assert _check(report, "digest_match")["ok"] is True
    assert _check(report, "manifest_staging_alignment")["ok"] is True


def test_successful_report_includes_phase_1c_check_codes(tmp_path: Path) -> None:
    workspace, staged = _stage_package(tmp_path, {"permissions": {"read_files": True}})

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert {check["code"] for check in report.checks} >= {
        "staging_metadata_integrity",
        "digest_match",
        "manifest_staging_alignment",
        "permission_read_files",
        "permission_write_files",
        "permission_exec",
        "permission_send_cross_target",
        "permission_create_cron",
        "permission_spawn",
        "permission_device_domains",
        "permission_mcp_scopes",
        "permission_unknown_keys",
        "external_endpoints",
        "external_writes_state",
        "context_token_budget",
        "contains_python_files",
    }


def test_missing_staging_directory_fails(tmp_path: Path) -> None:
    report = EvolutionModuleVerifier(tmp_path / "workspace").verify("missing")

    assert report.ok is False
    assert _check(report, "staging_metadata_integrity")["ok"] is False


def test_missing_artifact_directory_fails(tmp_path: Path) -> None:
    workspace, staged = _stage_package(tmp_path)
    artifact_dir = workspace / staged.staging_path / "artifact"
    for child in artifact_dir.iterdir():
        child.unlink()
    artifact_dir.rmdir()

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is False
    assert _check(report, "digest_match")["ok"] is False


def test_broken_staging_json_fails(tmp_path: Path) -> None:
    workspace, staged = _stage_package(tmp_path)
    (workspace / staged.staging_path / "staging.json").write_text("{broken", encoding="utf-8")

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is False
    assert _check(report, "staging_metadata_integrity")["ok"] is False


def test_digest_mismatch_fails(tmp_path: Path) -> None:
    workspace, staged = _stage_package(tmp_path)
    (workspace / staged.staging_path / "artifact" / "SKILL.md").write_text(
        "# Changed\n",
        encoding="utf-8",
    )

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is False
    assert _check(report, "digest_match")["ok"] is False


def test_manifest_metadata_mismatch_fails(tmp_path: Path) -> None:
    workspace, staged = _stage_package(tmp_path)
    metadata_path = workspace / staged.staging_path / "staging.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["module_id"] = "other"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is False
    assert _check(report, "manifest_staging_alignment")["ok"] is False


def test_read_files_permission_is_allowed(tmp_path: Path) -> None:
    workspace, staged = _stage_package(tmp_path, {"permissions": {"read_files": True}})

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is True
    assert report.permissions_evaluated == {"read_files": True}
    assert _check(report, "permission_read_files")["ok"] is True


def test_unknown_permission_key_fails(tmp_path: Path) -> None:
    workspace, staged = _stage_package(tmp_path, {"permissions": {"unknown": True}})

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is False
    assert report.unknown_keys_rejected == ("unknown",)
    assert _check(report, "permission_unknown_keys")["ok"] is False


def test_denied_boolean_permissions_fail(tmp_path: Path) -> None:
    denied_keys = ["write_files", "exec", "send_cross_target", "create_cron", "spawn"]
    for key in denied_keys:
        workspace, staged = _stage_package(tmp_path / key, {"permissions": {key: True}})

        report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

        assert report.ok is False
        assert f"permission_{key}" in report.permissions_denied
        assert _check(report, f"permission_{key}")["ok"] is False


def test_device_domains_are_denied(tmp_path: Path) -> None:
    workspace, staged = _stage_package(tmp_path, {"permissions": {"device_domains": ["lighting"]}})

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is False
    assert "permission_device_domains" in report.permissions_denied


def test_mcp_read_scope_is_allowed_and_other_scopes_fail(tmp_path: Path) -> None:
    workspace, staged = _stage_package(tmp_path / "allowed", {"permissions": {"mcp_scopes": ["read"]}})
    allowed = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)
    assert allowed.ok is True

    workspace, staged = _stage_package(tmp_path / "denied", {"permissions": {"mcp_scopes": ["write"]}})
    denied = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)
    assert denied.ok is False
    assert "permission_mcp_scopes" in denied.permissions_denied


def test_external_endpoints_fail(tmp_path: Path) -> None:
    workspace, staged = _stage_package(tmp_path, {"external_endpoints": ["https://api.example.com"]})

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is False
    assert _check(report, "external_endpoints")["ok"] is False


def test_external_state_writes_fail(tmp_path: Path) -> None:
    workspace, staged = _stage_package(
        tmp_path,
        {"external_side_effects": {"writes_external_state": True}},
    )

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is False
    assert _check(report, "external_writes_state")["ok"] is False


def test_non_positive_token_budget_fails(tmp_path: Path) -> None:
    workspace, staged = _stage_package(tmp_path, {"context_budget": {"token_budget": 0}})

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is False
    assert _check(report, "context_token_budget")["ok"] is False


def test_invalid_domain_pack_declarations_fail_verification(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_domain_pack_package(tmp_path / "source", with_missing_skill=True)
    staged = EvolutionModuleManager(workspace).stage(source)
    assert staged.ok is False

    # Manually create an invalid staged artifact to prove verifier enforces the same boundary.
    source = _write_domain_pack_package(tmp_path / "manual-source", with_missing_skill=True)
    artifact = tmp_path / "manual-artifact"
    artifact.mkdir(parents=True)
    for item in source.iterdir():
        if item.is_file():
            (artifact / item.name).write_bytes(item.read_bytes())
    digest = compute_artifact_digest(artifact)
    stage_root = workspace / "memory" / "evolution_staging" / digest
    staged_artifact = stage_root / "artifact"
    staged_artifact.mkdir(parents=True)
    for item in artifact.iterdir():
        if item.is_file():
            (staged_artifact / item.name).write_bytes(item.read_bytes())
    (stage_root / "staging.json").write_text(
        json.dumps(
            {
                "schema_version": "originagent.evolution.staging.v1",
                "module_id": "research",
                "module_type": "domain_pack",
                "version": "1.0.0",
                "artifact_digest": digest,
            }
        ),
        encoding="utf-8",
    )

    report = EvolutionModuleVerifier(workspace).verify(digest)

    assert report.ok is False
    assert "missing SKILL.md" in report.error


def test_unavailable_domain_pack_still_verifies(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = _write_domain_pack_package(tmp_path / "source", include_capabilities=False)
    staged = EvolutionModuleManager(workspace).stage(source)
    assert staged.ok is True

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is True
    assert report.module_type == "domain_pack"


def test_python_files_are_not_imported_and_are_reported(tmp_path: Path) -> None:
    source = _write_package(tmp_path / "source")
    marker = tmp_path / "imported.txt"
    (source / "side_effect.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    staged = EvolutionModuleManager(workspace).stage(source)
    assert staged.ok

    report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)

    assert report.ok is True
    assert marker.exists() is False
    python_check = _check(report, "contains_python_files")
    assert python_check["ok"] is True
    assert "1 Python file" in python_check["message"]
