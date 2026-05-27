"""Maintenance helpers for governed self-evolution stores."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from OriginAgent.agent.evolution_dependencies import EvolutionDependencyStore
from OriginAgent.agent.evolution_outcomes import EvolutionOutcomeStore
from OriginAgent.agent.evolution_trial_logs import EvolutionTrialLogStore


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
    trial_config = getattr(config, "trial", None) if config is not None else None
    trial_log_retention_days = max(
        1,
        int(getattr(trial_config, "trial_log_retention_days", 30) if trial_config is not None else 30),
    )
    max_retained_trial_logs = max(
        0,
        int(getattr(trial_config, "max_retained_trial_logs", 10) if trial_config is not None else 10),
    )
    outcomes = EvolutionOutcomeStore(Path(workspace))
    dependencies = EvolutionDependencyStore(Path(workspace))
    trial_logs = EvolutionTrialLogStore(Path(workspace))
    maintenance: dict[str, Any] = {
        "outcome_retention_days": outcome_retention_days,
        "outcome_archive_enabled": outcome_archive_enabled,
        "dependency_stale_cleanup_enabled": dependency_cleanup_enabled,
        "trial_log_retention_days": trial_log_retention_days,
        "max_retained_trial_logs": max_retained_trial_logs,
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
    try:
        maintenance["trial_log_retention"] = trial_logs.enforce_retention(
            max_records=max_retained_trial_logs,
            retention_days=trial_log_retention_days,
        )
    except Exception:
        logger.exception("Evolution trial log retention failed")
    return maintenance
