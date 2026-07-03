"""Characterization tests for the evolution activation pipeline.

These tests document the CURRENT behavior of the staging -> verify ->
activate -> capability snapshot pipeline.  They serve as regression
protection when the verifier or capability gate is modified.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from OriginAgent.evolution import (
    EvolutionCapabilityGate,
    EvolutionLedger,
    EvolutionModuleManager,
    EvolutionModuleVerifier,
)
from OriginAgent.evolution.manifest import MODULE_SCHEMA_VERSION


def _write_skill_package(root: Path, **manifest_overrides: Any) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": MODULE_SCHEMA_VERSION,
        "module_id": "test-skill",
        "module_type": "skill",
        "version": "1.0.0",
        "permissions": {"read_files": True},
    }
    manifest.update(manifest_overrides)
    (root / "evolution_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text("# Test Skill\n", encoding="utf-8")
    return root


class TestStagingToVerification:
    """Characterization: staging produces a verifiable artifact."""

    def test_staged_skill_passes_verification(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        source = _write_skill_package(tmp_path / "source")
        staged = EvolutionModuleManager(workspace).stage(source)
        assert staged.ok, f"staging failed: {staged.error}"

        report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)
        assert report.ok, f"verification failed: {report.error}"
        assert report.status == "verified"

    def test_staging_metadata_matches_manifest(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        source = _write_skill_package(tmp_path / "source")
        staged = EvolutionModuleManager(workspace).stage(source)
        assert staged.ok

        report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)
        assert report.module_id == "test-skill"
        assert report.module_type == "skill"
        assert report.module_version == "1.0.0"

    def test_digest_changes_when_artifact_content_changes(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        source = _write_skill_package(tmp_path / "source")
        staged1 = EvolutionModuleManager(workspace).stage(source)

        # Modify content
        (source / "SKILL.md").write_text("# Updated\n", encoding="utf-8")
        staged2 = EvolutionModuleManager(workspace).stage(source)

        assert staged1.artifact_digest != staged2.artifact_digest


class TestCapabilityGateIntegration:
    """Characterization: capability gate builds correct snapshots from verification."""

    def test_gate_rejects_unverified_artifact(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        gate = EvolutionCapabilityGate(workspace)
        result = gate.snapshot_for_artifact("nonexistent-digest")
        assert result.ok is False
        assert result.status == "not_active"

    def test_gate_requires_ledger_verified_event(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        source = _write_skill_package(tmp_path / "source")
        staged = EvolutionModuleManager(workspace).stage(source)
        assert staged.ok

        # Verify but do NOT activate (no ledger event recorded)
        report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)
        assert report.ok

        # Gate should reject because artifact was not activated
        gate = EvolutionCapabilityGate(workspace)
        result = gate.snapshot_for_artifact(staged.artifact_digest)
        assert result.ok is False
        assert result.status == "not_active"


class TestCodeSemanticEnforcement:
    """Characterization: AST scanner blocks modules with code-manifest mismatches."""

    def test_import_os_with_exec_false_rejected_by_verifier(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        source = _write_skill_package(
            tmp_path / "source",
            permissions={"exec": False, "read_files": True},
        )
        (source / "dangerous.py").write_text("import os\n", encoding="utf-8")
        staged = EvolutionModuleManager(workspace).stage(source)
        assert staged.ok

        report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)
        assert report.ok is False
        assert len(report.code_semantic_violations) > 0
        assert any("imports 'os'" in v for v in report.code_semantic_violations)

    def test_import_shutil_with_write_files_false_rejected(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        source = _write_skill_package(
            tmp_path / "source",
            permissions={"write_files": False, "read_files": True},
        )
        (source / "file_ops.py").write_text("import shutil\n", encoding="utf-8")
        staged = EvolutionModuleManager(workspace).stage(source)
        assert staged.ok

        report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)
        assert report.ok is False
        assert len(report.code_semantic_violations) > 0
        assert any("shutil" in v for v in report.code_semantic_violations)

    def test_clean_module_passes_full_pipeline(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        source = _write_skill_package(
            tmp_path / "source",
            permissions={"read_files": True},
        )
        (source / "helpers.py").write_text(
            "from pathlib import Path\n\ndef read(p: Path) -> str:\n    return p.read_text()\n",
            encoding="utf-8",
        )
        staged = EvolutionModuleManager(workspace).stage(source)
        assert staged.ok

        report = EvolutionModuleVerifier(workspace).verify(staged.artifact_digest)
        assert report.ok is True
        assert len(report.code_semantic_violations) == 0
