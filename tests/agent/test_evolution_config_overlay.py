from __future__ import annotations

from pathlib import Path

import pytest

from OriginAgent.agent.curator import CuratorService
from OriginAgent.agent.evolution_config_overlay import (
    CONFIG_PATCH_APPLIED,
    CONFIG_PATCH_REJECTED,
    ConfigMutationGate,
    ConfigPatch,
    EvolutionConfigOverlayStore,
    apply_config_overlay,
)
from OriginAgent.agent.evolution_maintenance import run_evolution_maintenance
from OriginAgent.agent.evolution_outcomes import EvolutionOutcomeStore
from OriginAgent.config.schema import EvolutionConfig


def test_config_mutation_gate_rejects_safety_relaxing_patches() -> None:
    config = EvolutionConfig(
        mode="curated",
        dry_run=True,
        allow_manual_override=False,
        workflow_priority_threshold=0.8,
        max_proposals_per_cycle=2,
    )

    assert ConfigMutationGate.validate(config, ConfigPatch("allow_manual_override", True, "bad"))
    assert ConfigMutationGate.validate(config, ConfigPatch("dry_run", False, "bad"))
    assert ConfigMutationGate.validate(config, ConfigPatch("mode", "aggressive", "bad"))
    assert ConfigMutationGate.validate(config, ConfigPatch("workflow_priority_threshold", 0.7, "bad"))
    assert ConfigMutationGate.validate(config, ConfigPatch("max_proposals_per_cycle", 3, "bad"))
    assert ConfigMutationGate.validate(config, ConfigPatch("trial.read_only_tools_only", False, "bad"))
    assert ConfigMutationGate.validate(config, ConfigPatch("sandbox.read_only_tools", ["read_file", "web_fetch"], "bad"))


def test_overlay_store_applies_only_governed_safety_tightening_patches(tmp_path: Path) -> None:
    config = EvolutionConfig(
        mode="curated",
        dry_run=False,
        workflow_priority_threshold=0.7,
        max_proposals_per_cycle=3,
    )
    store = EvolutionConfigOverlayStore(tmp_path)

    result = store.apply_patches(
        config,
        [
            ConfigPatch("dry_run", True, "preview first"),
            ConfigPatch("mode", "conservative", "tighten mode"),
            ConfigPatch("workflow_priority_threshold", 0.75, "raise threshold"),
            ConfigPatch("max_proposals_per_cycle", 2, "reduce rate"),
            ConfigPatch("allow_manual_override", True, "should reject"),
        ],
        actor="unit-test",
        source="test",
    )
    effective = store.effective_config(config)
    outcome_counts = EvolutionOutcomeStore(tmp_path).stats()["outcome_type_counts"]

    assert result.ok is True
    assert len(result.applied) == 4
    assert len(result.rejected) == 1
    assert config.mode == "curated"
    assert config.dry_run is False
    assert effective.mode == "conservative"
    assert effective.dry_run is True
    assert effective.workflow_priority_threshold == pytest.approx(0.75)
    assert effective.max_proposals_per_cycle == 2
    assert store.status()["active"] is True
    assert store.status()["override_count"] == 4
    assert outcome_counts[CONFIG_PATCH_APPLIED] == 4
    assert outcome_counts[CONFIG_PATCH_REJECTED] == 1


def test_maintenance_self_tunes_unhealthy_evolution_into_overlay(tmp_path: Path) -> None:
    config = EvolutionConfig(
        mode="curated",
        dry_run=False,
        workflow_priority_threshold=0.7,
        max_proposals_per_cycle=3,
        auto_verify_workflows=True,
        skill_candidates_enabled=True,
    )
    EvolutionOutcomeStore(tmp_path).append_event("rolled_back", rollback_status="succeeded")

    maintenance = run_evolution_maintenance(tmp_path, config)
    overlay = EvolutionConfigOverlayStore(tmp_path).status()
    effective = apply_config_overlay(tmp_path, config)

    assert maintenance["config_self_tuning"]["applied_count"] >= 4
    assert overlay["active"] is True
    assert effective.mode == "conservative"
    assert effective.dry_run is True
    assert effective.workflow_priority_threshold > 0.7
    assert effective.max_proposals_per_cycle < 3
    assert config.mode == "curated"
    assert config.dry_run is False


def test_curator_reads_effective_overlay_config(tmp_path: Path) -> None:
    config = EvolutionConfig(mode="curated", dry_run=False)
    EvolutionConfigOverlayStore(tmp_path).apply_patches(
        config,
        [ConfigPatch("dry_run", True, "preview first")],
        actor="unit-test",
        source="test",
    )

    service = CuratorService(workspace=tmp_path, evolution_config=config)

    assert service.evolution_config.dry_run is True
    assert config.dry_run is False


def test_overlay_store_rejects_candidate_batch_when_canary_health_drops(tmp_path: Path, monkeypatch) -> None:
    config = EvolutionConfig(mode="curated", dry_run=False, workflow_priority_threshold=0.7)
    store = EvolutionConfigOverlayStore(tmp_path)
    scores = iter([
        {"score": 90.0, "level": "healthy"},
        {"score": 80.0, "level": "warning"},
    ])
    monkeypatch.setattr(store, "_compute_health", lambda _cfg: next(scores))

    result = store.apply_patches(
        config,
        [ConfigPatch("dry_run", True, "preview first")],
        actor="unit-test",
        source="test",
    )

    assert result.ok is False
    assert result.applied == []
    assert result.rejected[0]["status"] == "canary_rejected"
    assert result.rejected[0]["error"] == "canary_health_drop"
    assert store.status()["active"] is False


def test_overlay_store_marks_canary_unavailable_when_health_errors(tmp_path: Path, monkeypatch) -> None:
    config = EvolutionConfig(mode="curated", dry_run=False)
    store = EvolutionConfigOverlayStore(tmp_path)
    monkeypatch.setattr(store, "_compute_health", lambda _cfg: (_ for _ in ()).throw(RuntimeError("health unavailable")))

    result = store.apply_patches(
        config,
        [ConfigPatch("dry_run", True, "preview first")],
        actor="unit-test",
        source="test",
    )

    assert result.ok is False
    assert result.rejected[0]["status"] == "canary_rejected"
    assert result.rejected[0]["error"] == "canary_unavailable"
    assert "health unavailable" in result.rejected[0]["detail"]
