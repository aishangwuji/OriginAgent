"""Tests for the manual-approval gate in evolution activation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from OriginAgent.evolution.activation import (
    EvolutionActivationResult,
    EvolutionModuleActivator,
)
from OriginAgent.evolution.events import EventType, EvolutionEvent


def _make_staged_artifact(workspace: Path, *, module_id: str = "test-mod") -> str:
    """Create a minimal staged artifact and return its digest."""
    import hashlib

    from OriginAgent.evolution.package import compute_artifact_digest

    artifact_dir = workspace / "memory" / "evolution_staging" / "test-digest" / "artifact"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "evolution_manifest.yaml").write_text(
        f"schema_version: originagent.evolution.module.v1\n"
        f"module_id: {module_id}\n"
        f"module_type: skill\n"
        f"version: 1.0.0\n"
    )
    (artifact_dir / "SKILL.md").write_text("# Test\n")
    digest = compute_artifact_digest(artifact_dir)

    # Stage it under the hash-based directory
    real_staging = workspace / "memory" / "evolution_staging" / digest
    if real_staging != artifact_dir:
        import shutil
        if real_staging.exists():
            shutil.rmtree(real_staging)
        shutil.copytree(artifact_dir.parent, real_staging)

    return digest


class TestManualApprovalGate:
    def test_activation_rejected_when_approval_required_and_no_approver(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        digest = _make_staged_artifact(workspace)

        # Config loader that requires manual approval
        config_loader = MagicMock(return_value=MagicMock(
            model_dump=MagicMock(return_value={"require_manual_approval": True})
        ))

        activator = EvolutionModuleActivator(
            workspace=workspace,
            config_loader=config_loader,
        )

        result = activator.activate(digest, actor="system")

        assert result.ok is False
        assert result.status == "rejected"
        assert "Manual approval required" in result.error

    def test_activation_accepted_with_approver_provided(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        digest = _make_staged_artifact(workspace)

        config_loader = MagicMock(return_value=MagicMock(
            model_dump=MagicMock(return_value={"require_manual_approval": True})
        ))

        activator = EvolutionModuleActivator(
            workspace=workspace,
            config_loader=config_loader,
        )

        result = activator.activate(digest, actor="system", approved_by="admin@example.com")

        # With an approver, should proceed past the gate (may fail on
        # later checks like staging metadata — that's fine, the gate itself passed)
        assert result.status != "rejected"

    def test_approval_not_required_when_config_disabled(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        digest = _make_staged_artifact(workspace)

        config_loader = MagicMock(return_value=MagicMock(
            model_dump=MagicMock(return_value={"require_manual_approval": False})
        ))

        activator = EvolutionModuleActivator(
            workspace=workspace,
            config_loader=config_loader,
        )

        result = activator.activate(digest, actor="system")

        # Gate should let it through (may fail on staging metadata — fine)
        assert result.status != "rejected"
