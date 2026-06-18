"""Deterministic meta-programming compilation for governed evolution proposals."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from OriginAgent.agent.evolution import (
    AUTO_EVOLUTION_ORIGIN,
    OpportunitySignal,
    build_skill_payload_from_signal,
    build_workflow_payload_from_signal,
)
from OriginAgent.agent.evolution_config_overlay import ConfigMutationGate, ConfigPatch
from OriginAgent.agent.meta_cognition_models import ErrorPattern, ReflectionRecord
from OriginAgent.agent.meta_cognition_redact import redact_meta_list, redact_meta_text
from OriginAgent.agent.meta_cognition_audit import JsonlMetaCognitionAuditLedger
from OriginAgent.utils.helpers import ensure_dir, truncate_text

COMPILATION_STORE_RELATIVE = Path("memory") / "meta_programming_compilations.jsonl"
COMPILER_VERSION = "originagent.meta_programming.v1"
_MAX_REASON_CHARS = 240
_MAX_SUMMARY_CHARS = 320
_MAX_FIXTURE_TEXT_CHARS = 2000
_MAX_ARTIFACT_PREVIEW_CHARS = 12000
_MAX_EVIDENCE = 8
_REVIEW_ONLY_TARGETS = {"prompt_policy", "architecture_patch"}
_PRIMARY_TARGETS = {"workflow", "skill"}
_DEFAULT_TARGET_ORDER = ("workflow", "skill", "config_overlay", "prompt_policy", "architecture_patch")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    import hashlib

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _clean_text(value: Any, max_chars: int) -> str:
    return truncate_text(redact_meta_text(value, max_chars=max_chars), max_chars)


def _clean_summary(value: Any) -> str:
    return _clean_text(value, _MAX_SUMMARY_CHARS)


@dataclass(frozen=True)
class ConfigOverlayPatchCandidate:
    path: str
    value: Any
    reason: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromptPolicyCandidate:
    target_area: str
    suggested_changes: list[str]
    rationale: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArchitecturePatchCandidate:
    target_subsystem: str
    expected_effect: str
    rollback_notes: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompiledProposalBundle:
    bundle_id: str
    source_pattern_id: str
    source_signal_id: str
    target_type: str
    target_key: str
    input_summary_hash: str
    compiler_version: str = COMPILER_VERSION
    created_at: str = field(default_factory=_utcnow_iso)
    summary: str = ""
    hypothesis: str = ""
    risk_level: str = "medium"
    review_mode: str = "review_required"
    evidence_sources: list[dict[str, Any]] = field(default_factory=list)
    source_reflection_ids: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    artifact_preview: str = ""
    trial_fixtures: dict[str, str] = field(default_factory=dict)
    rollback_notes: str = ""
    review_only: bool = False
    rejected: bool = False
    reject_reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "CompiledProposalBundle":
        if not isinstance(raw, dict):
            raise ValueError("compiled bundle must be an object")
        return cls(
            bundle_id=str(raw.get("bundle_id") or ""),
            source_pattern_id=str(raw.get("source_pattern_id") or ""),
            source_signal_id=str(raw.get("source_signal_id") or ""),
            target_type=str(raw.get("target_type") or ""),
            target_key=str(raw.get("target_key") or ""),
            input_summary_hash=str(raw.get("input_summary_hash") or ""),
            compiler_version=str(raw.get("compiler_version") or COMPILER_VERSION),
            created_at=str(raw.get("created_at") or _utcnow_iso()),
            summary=str(raw.get("summary") or ""),
            hypothesis=str(raw.get("hypothesis") or ""),
            risk_level=str(raw.get("risk_level") or "medium"),
            review_mode=str(raw.get("review_mode") or "review_required"),
            evidence_sources=[
                item for item in raw.get("evidence_sources") or [] if isinstance(item, dict)
            ],
            source_reflection_ids=[
                str(item) for item in raw.get("source_reflection_ids") or [] if str(item).strip()
            ],
            payload=dict(raw.get("payload") or {}) if isinstance(raw.get("payload"), dict) else {},
            artifact_preview=str(raw.get("artifact_preview") or ""),
            trial_fixtures={
                str(key): str(value)
                for key, value in dict(raw.get("trial_fixtures") or {}).items()
                if str(key).strip()
            },
            rollback_notes=str(raw.get("rollback_notes") or ""),
            review_only=bool(raw.get("review_only")),
            rejected=bool(raw.get("rejected")),
            reject_reason=str(raw.get("reject_reason") or ""),
        )


class MetaProgrammingCompilationStore:
    """Append-only store for compiled proposal bundles with deduped lookup."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.path = self.workspace / COMPILATION_STORE_RELATIVE
        ensure_dir(self.path.parent)
        self._lock = FileLock(str(self.path.parent / ".meta_programming_compilations.lock"))

    def read_all(self) -> list[CompiledProposalBundle]:
        bundles: list[CompiledProposalBundle] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                        bundle = CompiledProposalBundle.from_json(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if bundle.bundle_id:
                        bundles.append(bundle)
        except FileNotFoundError:
            return []
        return bundles

    def upsert_many(self, bundles: list[CompiledProposalBundle]) -> list[CompiledProposalBundle]:
        if not bundles:
            return []
        with self._lock:
            existing = self.read_all()
            by_key = {
                (bundle.source_pattern_id, bundle.target_type, bundle.input_summary_hash): bundle
                for bundle in existing
            }
            changed = False
            for bundle in bundles:
                key = (bundle.source_pattern_id, bundle.target_type, bundle.input_summary_hash)
                current = by_key.get(key)
                if current == bundle:
                    continue
                by_key[key] = bundle
                changed = True
            if changed:
                records = sorted(
                    by_key.values(),
                    key=lambda item: (item.created_at, item.bundle_id),
                )
                with self.path.open("w", encoding="utf-8") as handle:
                    for record in records:
                        handle.write(json.dumps(record.to_json(), ensure_ascii=False, sort_keys=True) + "\n")
            return bundles

    def lookup_by_signal(self, signals: list[OpportunitySignal]) -> dict[str, dict[str, CompiledProposalBundle]]:
        by_pattern: dict[str, dict[str, CompiledProposalBundle]] = {}
        for bundle in self.read_all():
            if not bundle.source_pattern_id:
                continue
            by_pattern.setdefault(bundle.source_pattern_id, {})[bundle.target_type] = bundle
        matches: dict[str, dict[str, CompiledProposalBundle]] = {}
        for signal in signals:
            pattern_id = str(signal.source_pattern_id or "").strip()
            if not pattern_id:
                continue
            typed = by_pattern.get(pattern_id)
            if typed:
                matches[signal.opportunity_id] = dict(typed)
        return matches

    def status(self) -> dict[str, Any]:
        bundles = self.read_all()
        counts: dict[str, int] = {}
        review_only = 0
        rejected = 0
        for bundle in bundles:
            counts[bundle.target_type] = counts.get(bundle.target_type, 0) + 1
            if bundle.review_only:
                review_only += 1
            if bundle.rejected:
                rejected += 1
        return {
            "compiled_bundle_count": len(bundles),
            "compiled_bundle_type_counts": counts,
            "review_only_bundle_count": review_only,
            "compiler_reject_count": rejected,
        }


class MetaProgrammingEngine:
    """Compile deterministic governed proposal drafts from patterns and signals."""

    def __init__(
        self,
        workspace: Path,
        *,
        meta_cognition_config: Any | None = None,
        evolution_config: Any | None = None,
        audit: JsonlMetaCognitionAuditLedger | None = None,
        store: MetaProgrammingCompilationStore | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.meta_cognition_config = meta_cognition_config
        self.evolution_config = evolution_config
        self.audit = audit or JsonlMetaCognitionAuditLedger(self.workspace)
        self.store = store or MetaProgrammingCompilationStore(self.workspace)

    def enabled(self) -> bool:
        return bool(getattr(self.meta_cognition_config, "evolution_bridge_enabled", False))

    def compile_for_signals(
        self,
        *,
        signals: list[OpportunitySignal],
        limit_patterns: int = 1,
    ) -> dict[str, dict[str, CompiledProposalBundle]]:
        if not signals:
            return {}
        if not self.enabled():
            return {}
        lookup = self.store.lookup_by_signal(signals)
        missing = [
            signal
            for signal in signals
            if signal.source_pattern_id
            and self._primary_target(signal) not in lookup.get(signal.opportunity_id, {})
        ]
        if not missing:
            return lookup
        pattern_ids = []
        for signal in missing:
            pattern_id = str(signal.source_pattern_id or "").strip()
            if pattern_id and pattern_id not in pattern_ids:
                pattern_ids.append(pattern_id)
            if len(pattern_ids) >= max(1, limit_patterns):
                break
        if not pattern_ids:
            return lookup
        patterns = self._pattern_map(pattern_ids)
        reflections = self._reflection_map()
        compiled: list[CompiledProposalBundle] = []
        for signal in missing:
            pattern = patterns.get(str(signal.source_pattern_id or "").strip())
            if pattern is None:
                continue
            signal_reflections = [
                reflections[ref_id]
                for ref_id in pattern.source_reflection_ids
                if ref_id in reflections
            ]
            compiled.extend(self._compile_one(signal=signal, pattern=pattern, reflections=signal_reflections))
        self.store.upsert_many(compiled)
        return self.store.lookup_by_signal(signals)

    @staticmethod
    def _primary_target(signal: OpportunitySignal) -> str:
        if signal.kind == "workflow_candidate":
            return "workflow"
        if signal.kind == "skill_candidate":
            return "skill"
        return ""

    def _compile_one(
        self,
        *,
        signal: OpportunitySignal,
        pattern: ErrorPattern,
        reflections: list[ReflectionRecord],
    ) -> list[CompiledProposalBundle]:
        bundles: list[CompiledProposalBundle] = []
        bundle_targets = list(_DEFAULT_TARGET_ORDER)
        for target_type in bundle_targets:
            bundle = self._compile_target(
                target_type=target_type,
                signal=signal,
                pattern=pattern,
                reflections=reflections,
            )
            if bundle is not None:
                bundles.append(bundle)
        return bundles

    def _compile_target(
        self,
        *,
        target_type: str,
        signal: OpportunitySignal,
        pattern: ErrorPattern,
        reflections: list[ReflectionRecord],
    ) -> CompiledProposalBundle | None:
        if target_type == "workflow" and signal.kind == "workflow_candidate":
            payload = build_workflow_payload_from_signal(signal, config=self.evolution_config)
            payload["meta_programming"] = self._meta_block(signal, pattern, target_type)
            payload["trial_fixtures"] = self._trial_fixtures(pattern, reflections)
            return self._bundle(
                signal=signal,
                pattern=pattern,
                target_type=target_type,
                reflections=reflections,
                payload=payload,
                artifact_preview=_clean_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), _MAX_ARTIFACT_PREVIEW_CHARS),
                trial_fixtures=payload["trial_fixtures"],
                review_only=False,
            )
        if target_type == "skill" and signal.kind == "skill_candidate":
            payload = build_skill_payload_from_signal(signal, config=self.evolution_config)
            payload["meta_programming"] = self._meta_block(signal, pattern, target_type)
            return self._bundle(
                signal=signal,
                pattern=pattern,
                target_type=target_type,
                reflections=reflections,
                payload=payload,
                artifact_preview=_clean_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), _MAX_ARTIFACT_PREVIEW_CHARS),
                trial_fixtures={},
                review_only=False,
            )
        if target_type == "config_overlay":
            return self._compile_config_overlay(signal=signal, pattern=pattern, reflections=reflections)
        if target_type == "prompt_policy":
            return self._compile_prompt_policy(signal=signal, pattern=pattern, reflections=reflections)
        if target_type == "architecture_patch":
            return self._compile_architecture_patch(signal=signal, pattern=pattern, reflections=reflections)
        return None

    def _compile_config_overlay(
        self,
        *,
        signal: OpportunitySignal,
        pattern: ErrorPattern,
        reflections: list[ReflectionRecord],
    ) -> CompiledProposalBundle | None:
        patches = self._config_patches(pattern, reflections)
        if not patches:
            return None
        validated: list[ConfigOverlayPatchCandidate] = []
        rejected: list[str] = []
        for patch in patches:
            issue = ConfigMutationGate.validate(self.evolution_config, ConfigPatch(patch.path, patch.value, patch.reason))
            if issue:
                rejected.append(issue)
                continue
            validated.append(patch)
        payload = {
            "subject_type": "config_overlay",
            "subject_id": "learning.evolution",
            "subject_path": "memory/evolution_config_overrides.json",
            "curator_key": f"meta-config-overlay:{signal.opportunity_id}",
            "target_state_hash": self._input_hash(pattern, target_type="config_overlay"),
            "suggested_action": "config_overlay",
            "impact_summary": _clean_summary(pattern.summary),
            "patches": [patch.to_json() for patch in validated],
            "evolution": {
                "origin": AUTO_EVOLUTION_ORIGIN,
                "opportunity_id": signal.opportunity_id,
                "source_pattern_id": pattern.pattern_id,
                "kind": "config_overlay",
                "priority_score": round(signal.priority_score, 3),
                "seen_count": signal.seen_count,
                "risk_level": "medium",
                "evidence_sources": signal.evidence_sources[:_MAX_EVIDENCE],
            },
            "meta_programming": self._meta_block(signal, pattern, "config_overlay"),
        }
        bundle = self._bundle(
            signal=signal,
            pattern=pattern,
            target_type="config_overlay",
            reflections=reflections,
            payload=payload,
            artifact_preview=_clean_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), _MAX_ARTIFACT_PREVIEW_CHARS),
            trial_fixtures={},
            review_only=False,
        )
        if rejected and not validated:
            return CompiledProposalBundle(
                **{
                    **bundle.to_json(),
                    "review_only": True,
                    "rejected": True,
                    "reject_reason": _clean_text("; ".join(rejected), _MAX_REASON_CHARS),
                }
            )
        return bundle

    def _compile_prompt_policy(
        self,
        *,
        signal: OpportunitySignal,
        pattern: ErrorPattern,
        reflections: list[ReflectionRecord],
    ) -> CompiledProposalBundle:
        candidate = PromptPolicyCandidate(
            target_area=self._capability_area(pattern),
            suggested_changes=[
                _clean_text(
                    f"Prefer explicit guardrail wording for repeated issue: {pattern.summary}",
                    220,
                )
            ],
            rationale=_clean_text(
                reflections[0].summary if reflections else pattern.summary,
                _MAX_REASON_CHARS,
            ),
        )
        payload = {
            "subject_type": "prompt_policy",
            "subject_id": candidate.target_area,
            "curator_key": f"meta-prompt-policy:{signal.opportunity_id}",
            "target_state_hash": self._input_hash(pattern, target_type="prompt_policy"),
            "suggested_action": "review_only",
            "impact_summary": _clean_summary(pattern.summary),
            "prompt_policy_candidate": candidate.to_json(),
            "evolution": {
                "origin": AUTO_EVOLUTION_ORIGIN,
                "opportunity_id": signal.opportunity_id,
                "source_pattern_id": pattern.pattern_id,
                "kind": "prompt_policy",
                "priority_score": round(signal.priority_score, 3),
                "seen_count": signal.seen_count,
                "risk_level": "medium",
                "evidence_sources": signal.evidence_sources[:_MAX_EVIDENCE],
            },
            "meta_programming": self._meta_block(signal, pattern, "prompt_policy"),
        }
        return self._bundle(
            signal=signal,
            pattern=pattern,
            target_type="prompt_policy",
            reflections=reflections,
            payload=payload,
            artifact_preview=_clean_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), _MAX_ARTIFACT_PREVIEW_CHARS),
            trial_fixtures={},
            review_only=True,
        )

    def _compile_architecture_patch(
        self,
        *,
        signal: OpportunitySignal,
        pattern: ErrorPattern,
        reflections: list[ReflectionRecord],
    ) -> CompiledProposalBundle:
        candidate = ArchitecturePatchCandidate(
            target_subsystem=self._capability_area(pattern),
            expected_effect=_clean_text(
                f"Reduce recurrence of {pattern.capability_domain or 'general'} failure mode.",
                _MAX_REASON_CHARS,
            ),
            rollback_notes="Review-only in v1. No direct code or topology mutation is allowed.",
        )
        payload = {
            "subject_type": "architecture_patch",
            "subject_id": candidate.target_subsystem,
            "curator_key": f"meta-architecture-patch:{signal.opportunity_id}",
            "target_state_hash": self._input_hash(pattern, target_type="architecture_patch"),
            "suggested_action": "review_only",
            "impact_summary": _clean_summary(pattern.summary),
            "architecture_patch_candidate": candidate.to_json(),
            "evolution": {
                "origin": AUTO_EVOLUTION_ORIGIN,
                "opportunity_id": signal.opportunity_id,
                "source_pattern_id": pattern.pattern_id,
                "kind": "architecture_patch",
                "priority_score": round(signal.priority_score, 3),
                "seen_count": signal.seen_count,
                "risk_level": "high" if pattern.severity == "high" else "medium",
                "evidence_sources": signal.evidence_sources[:_MAX_EVIDENCE],
            },
            "meta_programming": self._meta_block(signal, pattern, "architecture_patch"),
        }
        return self._bundle(
            signal=signal,
            pattern=pattern,
            target_type="architecture_patch",
            reflections=reflections,
            payload=payload,
            artifact_preview=_clean_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), _MAX_ARTIFACT_PREVIEW_CHARS),
            trial_fixtures={},
            review_only=True,
        )

    def _bundle(
        self,
        *,
        signal: OpportunitySignal,
        pattern: ErrorPattern,
        target_type: str,
        reflections: list[ReflectionRecord],
        payload: dict[str, Any],
        artifact_preview: str,
        trial_fixtures: dict[str, str],
        review_only: bool,
    ) -> CompiledProposalBundle:
        input_hash = self._input_hash(pattern, target_type=target_type)
        bundle_id = f"meta_bundle_{target_type}_{input_hash[:16]}"
        return CompiledProposalBundle(
            bundle_id=bundle_id,
            source_pattern_id=pattern.pattern_id,
            source_signal_id=signal.opportunity_id,
            target_type=target_type,
            target_key=str(signal.target_key or ""),
            input_summary_hash=input_hash,
            summary=_clean_summary(pattern.summary),
            hypothesis=_clean_text(pattern.summary, _MAX_REASON_CHARS),
            risk_level="high" if pattern.severity == "high" else "medium",
            review_mode="review_only" if review_only else "review_required",
            evidence_sources=signal.evidence_sources[:_MAX_EVIDENCE],
            source_reflection_ids=[item.reflection_id for item in reflections[:8]],
            payload=payload,
            artifact_preview=artifact_preview,
            trial_fixtures=trial_fixtures,
            rollback_notes=payload.get("rollback_notes", ""),
            review_only=review_only,
            rejected=False,
            reject_reason="",
        )

    def _pattern_map(self, pattern_ids: list[str]) -> dict[str, ErrorPattern]:
        wanted = set(pattern_ids)
        out: dict[str, ErrorPattern] = {}
        for row in self.audit.recent_patterns(limit=400):
            try:
                pattern = ErrorPattern.from_json(row)
            except ValueError:
                continue
            if pattern.pattern_id in wanted:
                out[pattern.pattern_id] = pattern
        return out

    def _reflection_map(self) -> dict[str, ReflectionRecord]:
        out: dict[str, ReflectionRecord] = {}
        for row in self.audit.recent_reflections(limit=400):
            try:
                reflection = ReflectionRecord.from_json(row)
            except ValueError:
                continue
            if reflection.reflection_id:
                out[reflection.reflection_id] = reflection
        return out

    def _trial_fixtures(
        self,
        pattern: ErrorPattern,
        reflections: list[ReflectionRecord],
    ) -> dict[str, str]:
        lines: list[str] = []
        for reflection in reflections[:3]:
            if reflection.summary:
                lines.append(f"- {reflection.summary}")
            elif reflection.what_failed:
                lines.append(f"- {reflection.what_failed[0]}")
        if not lines:
            lines.append(f"- {pattern.summary}")
        notes = "\n".join(lines)
        return {"meta-pattern-notes.txt": truncate_text(notes, _MAX_FIXTURE_TEXT_CHARS)}

    def _meta_block(self, signal: OpportunitySignal, pattern: ErrorPattern, target_type: str) -> dict[str, Any]:
        return {
            "compiler_version": COMPILER_VERSION,
            "source_pattern_id": pattern.pattern_id,
            "source_signal_id": signal.opportunity_id,
            "target_type": target_type,
            "input_summary_hash": self._input_hash(pattern, target_type=target_type),
            "review_only": target_type in _REVIEW_ONLY_TARGETS,
        }

    def _input_hash(self, pattern: ErrorPattern, *, target_type: str) -> str:
        return _stable_hash([
            pattern.pattern_id,
            target_type,
            pattern.summary,
            pattern.severity,
            pattern.candidate_target_type or "",
        ])

    def _capability_area(self, pattern: ErrorPattern) -> str:
        text = str(pattern.capability_domain or "general_reasoning").strip().lower()
        return text or "general_reasoning"

    def _config_patches(
        self,
        pattern: ErrorPattern,
        reflections: list[ReflectionRecord],
    ) -> list[ConfigOverlayPatchCandidate]:
        reasons = redact_meta_list(
            [reflection.summary for reflection in reflections if reflection.summary] or [pattern.summary],
            max_items=2,
            max_chars=_MAX_REASON_CHARS,
        )
        reason = "; ".join(reasons) if reasons else _clean_text(pattern.summary, _MAX_REASON_CHARS)
        if pattern.severity == "high":
            return [
                ConfigOverlayPatchCandidate(
                    path="dry_run",
                    value=True,
                    reason=reason or "Repeated high-severity pattern requires preview-only mode.",
                ),
                ConfigOverlayPatchCandidate(
                    path="workflow_priority_threshold",
                    value=0.75,
                    reason=reason or "Raise workflow threshold after repeated high-severity pattern.",
                ),
            ]
        if pattern.frequency >= 4:
            return [
                ConfigOverlayPatchCandidate(
                    path="max_proposals_per_cycle",
                    value=1,
                    reason=reason or "Reduce proposal throughput after repeated pattern.",
                )
            ]
        return []
