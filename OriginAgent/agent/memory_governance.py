"""Minimal continuity governance for Phase 3 memory promotion and forgetting."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from OriginAgent.agent.facts import HIGH_RISK_KEYWORDS, TEMPORARY_LANGUAGE, canonical_key_for_fact
from OriginAgent.utils.helpers import ensure_dir


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _trim_text(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(needle.casefold() in lowered for needle in needles)


@dataclass(frozen=True)
class PromotionCandidate:
    candidate_key: str
    content: str
    category: str
    scope: str
    owner: str
    confidence: float
    source: str
    reason: str
    sensitive: bool = False
    requires_user_confirmation: bool = False
    source_excerpt: str = ""
    turn_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "content": self.content,
            "category": self.category,
            "scope": self.scope,
            "owner": self.owner,
            "confidence": self.confidence,
            "source": self.source,
            "reason": self.reason,
            "sensitive": self.sensitive,
            "requires_user_confirmation": self.requires_user_confirmation,
            "source_excerpt": self.source_excerpt,
            "turn_id": self.turn_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class GovernanceDecision:
    promotion_candidates: list[PromotionCandidate] = field(default_factory=list)
    forgetting_actions: list[dict[str, Any]] = field(default_factory=list)


class PromotionCandidateStore:
    """Single-process-optimized candidate counter using atomic rewrite.

    This store intentionally follows the rest of the codebase's temp-file +
    os.replace write pattern and assumes a single OriginAgent process is the
    normal deployment shape. In multi-process deployments, last-writer-wins can
    delay a promotion by losing one increment, but the file remains valid.
    """

    def __init__(self, workspace: Path) -> None:
        self._path = ensure_dir(Path(workspace) / "memory" / "governance") / "promotion_candidates.json"

    def load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            if isinstance(key, str) and isinstance(value, dict):
                result[key] = dict(value)
        return result

    def increment(self, candidate: PromotionCandidate, *, turn_id: str) -> dict[str, Any]:
        state = self.load()
        record = dict(state.get(candidate.candidate_key) or {})
        seen_turn_ids = {
            str(item).strip()
            for item in (record.get("seen_turn_ids") or [])
            if str(item).strip()
        }
        if turn_id not in seen_turn_ids:
            seen_turn_ids.add(turn_id)
            record["confirmation_count"] = int(record.get("confirmation_count", 0) or 0) + 1
        record.update(candidate.to_json())
        record["seen_turn_ids"] = sorted(seen_turn_ids)
        record["updated_at"] = _utcnow_iso()
        record.setdefault("created_at", record["updated_at"])
        state[candidate.candidate_key] = record
        self._write_atomic(state)
        return record

    def prune_stale(self, *, max_age_days: int = 30) -> int:
        state = self.load()
        if not state:
            return 0
        cutoff = _utcnow() - timedelta(days=max(1, int(max_age_days or 30)))
        kept: dict[str, dict[str, Any]] = {}
        removed = 0
        for key, record in state.items():
            updated_at = _parse_dt(record.get("updated_at"))
            if updated_at is not None and updated_at < cutoff:
                removed += 1
                continue
            kept[key] = record
        if removed:
            self._write_atomic(kept)
        return removed

    def _write_atomic(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(f".{self._path.name}.tmp")
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self._path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise


class MemoryGovernance:
    """Minimal promotion and forgetting loop for continuity Phase 3."""

    def __init__(
        self,
        *,
        workspace: Path,
        memory: Any,
        context_config: Any,
        working_memory: Any,
        world_state: Any,
    ) -> None:
        self._workspace = Path(workspace)
        self._memory = memory
        self._context_config = context_config
        self._working_memory = working_memory
        self._world_state = world_state
        self._candidate_store = PromotionCandidateStore(self._workspace)
        self._last_status: dict[str, Any] = {
            "governance_enabled": bool(getattr(context_config, "governance_enabled", False)),
            "promotion_candidates": [],
            "promotion_applied_count": 0,
            "promotion_conflict_count": 0,
            "forgetting_actions": [],
            "candidate_store_path": str(self._candidate_store._path),
        }

    def runtime_status(self) -> dict[str, Any]:
        return dict(self._last_status)

    def evaluate_turn(
        self,
        session: Any,
        *,
        runtime_context: Any | None,
        turn_id: str,
        current_message: str | None,
    ) -> GovernanceDecision:
        if not bool(getattr(self._context_config, "governance_enabled", False)):
            return GovernanceDecision()
        candidates: list[PromotionCandidate] = []
        owner = "user"
        scope = "user"
        working = self._working_memory.load(
            session,
            identity=getattr(runtime_context, "identity", None) if runtime_context is not None else None,
        )
        if working.current_goal and self._is_long_term_preference(working.current_goal):
            candidates.append(
                self._candidate_from_text(
                    content=working.current_goal,
                    category="preference",
                    scope=scope,
                    owner=owner,
                    confidence=0.86,
                    source="working_memory",
                    reason="explicit_long_term_goal",
                    turn_id=turn_id,
                )
            )
        for item in list(working.priority_facts or []):
            text = str(item or "").strip()
            if not text or text.startswith("goal_summary:"):
                continue
            if self._is_sensitive(text):
                continue
            candidates.append(
                self._candidate_from_text(
                    content=text,
                    category="note",
                    scope=scope,
                    owner=owner,
                    confidence=0.82,
                    source="working_memory",
                    reason="stable_priority_fact",
                    turn_id=turn_id,
                )
            )
        world_snapshot = self._world_state.load(
            session,
            identity=runtime_context if runtime_context is not None else None,
        )
        if world_snapshot.world_summary is not None:
            for item in list(world_snapshot.world_summary.focus or []):
                text = str(item or "").strip()
                if not text or self._looks_transient(text) or self._is_sensitive(text):
                    continue
                candidates.append(
                    self._candidate_from_text(
                        content=text,
                        category="note",
                        scope=scope,
                        owner=owner,
                        confidence=0.84,
                        source="world_state",
                        reason="confirmed_world_conclusion",
                        turn_id=turn_id,
                    )
                )
        forgetting_actions = self._collect_forgetting_actions(session, current_message=current_message)
        # Deduplicate per turn by candidate key.
        by_key: dict[str, PromotionCandidate] = {}
        for candidate in candidates:
            by_key.setdefault(candidate.candidate_key, candidate)
        return GovernanceDecision(
            promotion_candidates=list(by_key.values()),
            forgetting_actions=forgetting_actions,
        )

    def apply_turn(
        self,
        session: Any,
        decision: GovernanceDecision,
    ) -> dict[str, Any]:
        promotions: list[dict[str, Any]] = []
        promotion_applied_count = 0
        promotion_conflict_count = 0
        threshold = max(1, int(getattr(self._context_config, "promotion_min_confirmations", 2) or 2))
        confidence_threshold = float(
            getattr(self._context_config, "promotion_confidence_threshold", 0.8) or 0.8
        )
        for candidate in decision.promotion_candidates:
            record = self._candidate_store.increment(
                candidate,
                turn_id=str(candidate.turn_id or candidate.metadata.get("turn_id") or candidate.reason),
            )
            applied = False
            conflict = False
            if (
                int(record.get("confirmation_count", 0) or 0) >= threshold
                and float(candidate.confidence) >= confidence_threshold
                and not candidate.requires_user_confirmation
            ):
                applied = True
                before = len(self._memory.fact_store.read_all())
                self._memory.upsert_fact_and_rebuild_memory(
                    candidate.content,
                    category=candidate.category,
                    scope=candidate.scope,
                    owner=candidate.owner,
                    confidence=candidate.confidence,
                    source_excerpt=candidate.source_excerpt or candidate.reason,
                    batch_id="continuity_governance",
                    actor="system",
                    origin="continuity_governance",
                )
                after = self._memory.fact_store.read_all()
                latest = after[-1] if after else None
                conflict = bool(latest is not None and getattr(latest, "consistency_state", "") == "contested")
                promotion_applied_count += 1
                if conflict:
                    promotion_conflict_count += 1
            promotions.append({
                **candidate.to_json(),
                "confirmation_count": int(record.get("confirmation_count", 0) or 0),
                "applied": applied,
                "conflict": conflict,
            })
        stale_candidates = self._candidate_store.prune_stale()
        self._last_status = {
            "governance_enabled": bool(getattr(self._context_config, "governance_enabled", False)),
            "promotion_candidates": promotions,
            "promotion_applied_count": promotion_applied_count,
            "promotion_conflict_count": promotion_conflict_count,
            "forgetting_actions": list(decision.forgetting_actions),
            "stale_candidate_pruned_count": stale_candidates,
            "candidate_store_path": str(self._candidate_store._path),
        }
        return dict(self._last_status)

    def _collect_forgetting_actions(self, session: Any, *, current_message: str | None) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        snapshot = self._working_memory.load(session)
        expires_at = _parse_dt(snapshot.expires_at) if snapshot.expires_at else None
        if expires_at is not None and expires_at < _utcnow():
            actions.append({"kind": "working_memory_expired", "session_key": snapshot.session_key})
        if not str(current_message or "").strip():
            actions.append({"kind": "empty_turn", "retained": True})
        return actions

    def _candidate_from_text(
        self,
        *,
        content: str,
        category: str,
        scope: str,
        owner: str,
        confidence: float,
        source: str,
        reason: str,
        turn_id: str,
    ) -> PromotionCandidate:
        normalized = _trim_text(content, max_chars=240)
        sensitive = self._is_sensitive(normalized)
        requires_confirmation = bool(
            sensitive and getattr(self._context_config, "promotion_require_user_confirmation_for_sensitive", True)
        )
        return PromotionCandidate(
            candidate_key=canonical_key_for_fact(normalized, owner, category, scope),
            content=normalized,
            category=category,
            scope=scope,
            owner=owner,
            confidence=max(0.0, min(1.0, confidence)),
            source=source,
            reason=reason,
            sensitive=sensitive,
            requires_user_confirmation=requires_confirmation,
            source_excerpt=reason,
            turn_id=turn_id,
            metadata={},
        )

    @staticmethod
    def _is_long_term_preference(text: str) -> bool:
        lowered = str(text or "").casefold()
        return any(token in lowered for token in ("prefer", "always", "default", "习惯", "偏好", "默认"))

    @staticmethod
    def _looks_transient(text: str) -> bool:
        return _contains_any(str(text or ""), TEMPORARY_LANGUAGE)

    @staticmethod
    def _is_sensitive(text: str) -> bool:
        return _contains_any(str(text or ""), HIGH_RISK_KEYWORDS)
