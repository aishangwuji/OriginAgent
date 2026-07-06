"""UtilityRewardBridge — extract reward signals for ACT-R utility learning.

Bridges two reward sources:
1. Desire status transitions (SATISFIED/CANCELLED/stagnant)
2. ReflectionRecord outcome_class + confidence from MetaCognitionReflector
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from OriginAgent.bdi.models import Desire, DesireStatus


# Reward signal weights for status transitions
_REWARD_SATISFIED = 1.0
_REWARD_CANCELLED = -0.5
_REWARD_STAGNANT = -0.1  # Active but evaluated >3 times without progress

# Stagnation threshold: if a desire has been evaluated more than this and is still ACTIVE
_STAGNATION_EVAL_THRESHOLD = 3

# Merge weights for combining status + reflection rewards
_STATUS_WEIGHT = 0.7
_REFLECTION_WEIGHT = 0.3


class UtilityRewardBridge:
    """Extract reward signals for ACT-R utility learning.

    Combines two reward sources:
    - Desire status transitions (immediate, from DesireStore)
    - ReflectionRecord feedback (deferred, from MetaCognitionReflector audit)
    """

    def __init__(
        self,
        *,
        audit_ledger: Any | None = None,
        desire_store: Any | None = None,
    ) -> None:
        self._audit_ledger = audit_ledger
        self._desire_store = desire_store

    def extract_status_rewards(
        self,
        desires: list[Desire],
    ) -> dict[str, float]:
        """Extract rewards from Desire status transitions.

        Args:
            desires: List of Desires to evaluate (typically those updated in the current cycle)

        Returns:
            Dict mapping desire_id -> reward signal in [-1.0, 1.0]
        """
        rewards: dict[str, float] = {}
        for desire in desires:
            if desire.status == DesireStatus.SATISFIED:
                rewards[desire.desire_id] = _REWARD_SATISFIED
            elif desire.status == DesireStatus.CANCELLED:
                rewards[desire.desire_id] = _REWARD_CANCELLED
            elif desire.status == DesireStatus.ACTIVE and desire.evaluation_count > _STAGNATION_EVAL_THRESHOLD:
                rewards[desire.desire_id] = _REWARD_STAGNANT
        return rewards

    def extract_reflection_rewards(
        self,
        *,
        since: str,
        session_keys: list[str],
        desire_id_lookup: set[str] | None = None,
    ) -> dict[str, float]:
        """Extract rewards from ReflectionRecord outcomes.

        Args:
            since: ISO timestamp; only reflections created after this are considered
            session_keys: Filter reflections by these session keys
            desire_id_lookup: Optional set of desire_ids to match against reflection evidence_refs

        Returns:
            Dict mapping desire_id -> reward signal in [-1.0, 1.0]
        """
        if self._audit_ledger is None:
            return {}

        try:
            recent = self._audit_ledger.recent_reflections(limit=200)
        except Exception:
            logger.debug("UtilityRewardBridge: audit_ledger.recent_reflections failed")
            return {}

        rewards: dict[str, float] = {}
        session_set = set(session_keys) if session_keys else set()

        for row in recent:
            # Parse the reflection record
            try:
                from OriginAgent.agent.meta_cognition_models import ReflectionRecord
                reflection = ReflectionRecord.from_json(row)
            except Exception:
                continue

            # Time filter: only reflections after 'since'
            if since and reflection.created_at <= since:
                continue

            # Session filter
            if session_set and reflection.session_key not in session_set:
                continue

            # Determine reward sign from outcome_class
            outcome = (reflection.outcome_class or "").strip().lower()
            if outcome == "success":
                reward = float(reflection.confidence) * 1.0
            elif outcome == "failure":
                reward = float(reflection.confidence) * -1.0
            else:
                continue  # neutral or unknown — no reward

            # Map to desire_id via payload evidence_refs / trigger_contexts
            payload = reflection.payload if isinstance(reflection.payload, dict) else {}
            evidence_refs = list(payload.get("evidence_refs") or [])
            trigger_contexts = list(payload.get("trigger_contexts") or [])

            # Extract any desire_id references from evidence_refs
            matched_desire_ids: list[str] = []
            for ref in evidence_refs:
                ref_str = str(ref or "").strip()
                # desire_ids typically look like "desire_xxx" or "desire_fs_xxx"
                if desire_id_lookup and ref_str in desire_id_lookup:
                    matched_desire_ids.append(ref_str)
                # Also check if ref starts with "desire"
                elif ref_str.startswith("desire_") and desire_id_lookup and ref_str in desire_id_lookup:
                    matched_desire_ids.append(ref_str)

            # If no direct desire_id match, try trigger_contexts
            if not matched_desire_ids:
                for ctx in trigger_contexts:
                    if not isinstance(ctx, dict):
                        continue
                    # Check if trigger_context has desire_id field
                    did = str(ctx.get("desire_id") or "").strip()
                    if did and (desire_id_lookup is None or did in desire_id_lookup):
                        matched_desire_ids.append(did)

            # Apply reward to matched desires
            for did in matched_desire_ids:
                if did in rewards:
                    # If multiple reflections match same desire, take the average
                    rewards[did] = (rewards[did] + reward) / 2.0
                else:
                    rewards[did] = reward

        return rewards

    @staticmethod
    def merge_rewards(
        status_rewards: dict[str, float],
        reflection_rewards: dict[str, float],
    ) -> dict[str, float]:
        """Merge status and reflection rewards using weighted average.

        Weight: status=0.7, reflection=0.3
        If only one source has a reward for a desire, use that source's weight.
        """
        all_desire_ids = set(status_rewards.keys()) | set(reflection_rewards.keys())
        merged: dict[str, float] = {}
        for did in all_desire_ids:
            s_reward = status_rewards.get(did)
            r_reward = reflection_rewards.get(did)
            if s_reward is not None and r_reward is not None:
                merged[did] = _STATUS_WEIGHT * s_reward + _REFLECTION_WEIGHT * r_reward
            elif s_reward is not None:
                merged[did] = s_reward
            elif r_reward is not None:
                merged[did] = r_reward
        return merged
