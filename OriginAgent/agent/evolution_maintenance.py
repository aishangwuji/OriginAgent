"""Maintenance helpers for governed self-evolution stores."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from OriginAgent.agent.evolution_dependencies import EvolutionDependencyStore
from OriginAgent.agent.evolution_outcomes import EvolutionOutcomeStore


def run_evolution_maintenance(
    workspace: Path,
    config: Any | None = None,
    *,
    force_cleanup: bool = False,
) -> dict[str, Any]:
    """Run bounded cleanup for append-only evolution stores."""

    outcome_retention_days = max(
        1,
        int(getattr(config, "outcome_retention_days", 90) if config is not None else 90),
    )
    outcome_archive_enabled = bool(
        getattr(config, "outcome_archive_enabled", True) if config is not None else True
    )
    dependency_cleanup_enabled = bool(
        getattr(config, "dependency_stale_cleanup_enabled", True) if config is not None else True
    )
    outcomes = EvolutionOutcomeStore(Path(workspace))
    dependencies = EvolutionDependencyStore(Path(workspace))
    maintenance: dict[str, Any] = {
        "outcome_retention_days": outcome_retention_days,
        "outcome_archive_enabled": outcome_archive_enabled,
        "dependency_stale_cleanup_enabled": dependency_cleanup_enabled,
    }
    try:
        maintenance["outcome_retention"] = outcomes.enforce_retention(
            retention_days=outcome_retention_days,
            archive=outcome_archive_enabled,
        )
    except Exception:
        logger.exception("Evolution outcome retention failed")
    if force_cleanup or dependency_cleanup_enabled:
        try:
            maintenance["dependency_cleanup"] = dependencies.prune_stale_references()
        except Exception:
            logger.exception("Evolution dependency cleanup failed")
    return maintenance
