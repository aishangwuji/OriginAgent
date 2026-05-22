"""Local OriginAgent evolution runtime primitives."""

from OriginAgent.evolution.activation import EvolutionActivationResult, EvolutionModuleActivator
from OriginAgent.evolution.events import EventType, EvolutionEvent
from OriginAgent.evolution.ledger import EvolutionLedger
from OriginAgent.evolution.manager import (
    EvolutionModuleManager,
    EvolutionStageResult,
    EvolutionVerificationResult,
)
from OriginAgent.evolution.manifest import EvolutionManifest, validate_manifest
from OriginAgent.evolution.state_branch import (
    EvolutionMergeConflict,
    EvolutionMergePreview,
    EvolutionStateBranchResult,
    EvolutionStateBranchStore,
)
from OriginAgent.evolution.verifier import EvolutionModuleVerifier, EvolutionVerificationReport

__all__ = [
    "EventType",
    "EvolutionActivationResult",
    "EvolutionEvent",
    "EvolutionLedger",
    "EvolutionModuleActivator",
    "EvolutionModuleManager",
    "EvolutionModuleVerifier",
    "EvolutionManifest",
    "EvolutionMergeConflict",
    "EvolutionMergePreview",
    "EvolutionStageResult",
    "EvolutionStateBranchResult",
    "EvolutionStateBranchStore",
    "EvolutionVerificationReport",
    "EvolutionVerificationResult",
    "validate_manifest",
]
