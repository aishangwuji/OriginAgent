"""Structured long-term memory facts.

`facts.jsonl` is a current-state store, not an append-only event log. Bad JSON
lines are ignored on reads and dropped the next time the store rewrites the
file. Fact `content` is the remembered human-readable fact and is not redacted;
`source_excerpt` is supporting evidence and is redacted before persistence.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
import hashlib
import json
import os
import re
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock
from loguru import logger

from OriginAgent.utils.helpers import ensure_dir


@dataclass(frozen=True)
class FactStoreConfig:
    category_order: tuple[str, ...]
    legacy_categories: tuple[str, ...] = ()
    valid_owners: tuple[str, ...] = ("user", "assistant", "system", "unknown")
    high_risk_categories: tuple[str, ...] = ("policy", "safety")
    conflict_categories: tuple[str, ...] = ("preference", "routine", "policy", "safety", "temporary")
    high_risk_keywords: tuple[str, ...] = ()
    temporary_language: tuple[str, ...] = ()
    uncertain_language: tuple[str, ...] = ()
    confidence_decay_factor: float = 0.98
    min_confidence: float = 0.3
    decay_start_days: int = 30
    auto_active_confidence_threshold: float = 0.8
    auto_active_budget: float = 5.0
    reject_confidence_multiplier: float = 0.7
    calibration_min_count: int = 10


DEFAULT_FACT_STORE_CONFIG = FactStoreConfig(
    category_order=(
        "preference",
        "routine",
        "policy",
        "safety",
        "temporary",
        "note",
    ),
    legacy_categories=("household", "device"),
    valid_owners=("user", "assistant", "system", "unknown", "household"),
    high_risk_keywords=(
        "security",
        "camera",
        "gas",
        "medication",
        "payment",
        "password",
        "key",
        "permission",
        "token",
    ),
    temporary_language=(
        "today",
        "tomorrow",
        "this week",
        "temporary",
        "for now",
        "just this time",
        "今天",
        "明天",
        "这周",
        "本周",
        "临时",
        "暂时",
        "先",
        "这次",
    ),
    uncertain_language=(
        "maybe",
        "usually",
        "sometimes",
        "probably",
        "around",
        "roughly",
        "可能",
        "一般",
        "有时",
        "大概",
        "差不多",
        "左右",
        "偶尔",
    ),
)

CATEGORY_ORDER = DEFAULT_FACT_STORE_CONFIG.category_order + DEFAULT_FACT_STORE_CONFIG.legacy_categories
VALID_CATEGORIES = set(CATEGORY_ORDER)
VALID_OWNERS = set(DEFAULT_FACT_STORE_CONFIG.valid_owners)
VALID_STATUSES = {
    "active",
    "deprecated",
    "contradicted",
    "pending_confirmation",
}
VALID_CONSISTENCY_STATES = {
    "consistent",
    "contested",
    "contradicted",
}
VALID_CONFIDENCE_VERSIONS = {"v1", "v2"}
VALID_RELATION_TYPES = {
    "equivalent",
    "supersedes",
    "contradicts",
    "generalizes",
    "narrows",
    "implies",
    "related_to",
}
VALID_RELATION_STATUSES = {"active", "inactive"}
DEFAULT_CONFIDENCE_VERSION = "v2"
DEFAULT_CONSISTENCY_STATE = "consistent"
DEFAULT_CONTEXT_TOP_K = 12
FEATURE_FLAG_NAMES = (
    "semantic_retrieval_enabled",
    "semantic_merge_enabled",
    "fact_graph_enabled",
    "confidence_v2_enabled",
    "contradiction_auto_flip_enabled",
    "fact_audit_enabled",
)
HIGH_RISK_CATEGORIES = set(DEFAULT_FACT_STORE_CONFIG.high_risk_categories)
CONFLICT_CATEGORIES = set(DEFAULT_FACT_STORE_CONFIG.conflict_categories)
MAX_DEPRECATIONS_PER_BATCH = 3
HIGH_RISK_KEYWORDS = DEFAULT_FACT_STORE_CONFIG.high_risk_keywords
TEMPORARY_LANGUAGE = DEFAULT_FACT_STORE_CONFIG.temporary_language
UNCERTAIN_LANGUAGE = DEFAULT_FACT_STORE_CONFIG.uncertain_language
HIGH_RISK_DEVICE_DOMAINS = {"lock", "security", "camera", "gas", "presence"}


@dataclass
class ParseRejectedProposal:
    section: str
    index: int | None
    raw: Any
    error: str


@dataclass
class FactProposal:
    content: str
    category: str
    scope: str
    owner: str
    source_cursors: list[int]
    source_excerpt: str
    confidence: float = 0.7
    expires_at: str | None = None
    supersedes_fact_id: str | None = None
    requires_confirmation: bool | None = None
    status: str | None = None
    reason: str = ""
    change_kind: str | None = None
    target_fact_id: str | None = None
    relation_candidates: list[dict[str, Any]] = field(default_factory=list)
    semantic_scope_hint: str | None = None


@dataclass
class FactDeprecationProposal:
    fact_id: str
    reason: str
    source_cursors: list[int] = field(default_factory=list)


@dataclass
class DreamFactProposalBatch:
    facts_to_upsert: list[FactProposal] = field(default_factory=list)
    facts_to_deprecate: list[FactDeprecationProposal] = field(default_factory=list)
    memory_render_hints: list[Any] = field(default_factory=list)
    parse_rejected: list[ParseRejectedProposal] = field(default_factory=list)


@dataclass
class ValidationIssue:
    code: str
    severity: str
    message: str


@dataclass
class ValidationResult:
    decision: str
    confidence: float
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass
class DreamFactApplyResult:
    accepted: list[FactRecord] = field(default_factory=list)
    pending: list[FactRecord] = field(default_factory=list)
    rejected: list[tuple[FactProposal | FactDeprecationProposal, list[ValidationIssue]]] = (
        field(default_factory=list)
    )
    deprecated: list[str] = field(default_factory=list)
    parse_rejected: list[ParseRejectedProposal] = field(default_factory=list)


@dataclass
class FactRecord:
    fact_id: str
    content: str
    canonical_key: str
    category: str
    scope: str
    owner: str
    source_cursors: list[int]
    source_excerpt: str
    confidence: float
    status: str
    created_at: str
    updated_at: str
    last_seen_at: str
    expires_at: str | None
    supersedes_fact_id: str | None
    requires_confirmation: bool
    base_confidence: float = 1.0
    confidence_version: str = DEFAULT_CONFIDENCE_VERSION
    consistency_state: str = DEFAULT_CONSISTENCY_STATE
    support_count: int = 1
    contradiction_count: int = 0
    retrieval_count: int = 0
    injection_count: int = 0
    last_supported_at: str | None = None
    last_contested_at: str | None = None
    last_retrieved_at: str | None = None
    semantic_text_hash: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FactRecord":
        required_strings = (
            "fact_id",
            "content",
            "canonical_key",
            "category",
            "scope",
            "owner",
            "source_excerpt",
            "status",
            "created_at",
            "updated_at",
            "last_seen_at",
        )
        if any(not isinstance(raw.get(key), str) for key in required_strings):
            raise ValueError("fact record missing required string fields")
        source_cursors = _normalize_source_cursors(raw.get("source_cursors"))
        confidence = _normalize_confidence(raw.get("confidence", 1.0))
        expires_at = raw.get("expires_at")
        supersedes_fact_id = raw.get("supersedes_fact_id")
        requires_confirmation = raw.get("requires_confirmation", False)
        last_supported_at = _optional_record_string(raw.get("last_supported_at"))
        last_contested_at = _optional_record_string(raw.get("last_contested_at"))
        last_retrieved_at = _optional_record_string(raw.get("last_retrieved_at"))
        if expires_at is not None and not isinstance(expires_at, str):
            raise ValueError("expires_at must be a string or null")
        if supersedes_fact_id is not None and not isinstance(supersedes_fact_id, str):
            raise ValueError("supersedes_fact_id must be a string or null")
        if not isinstance(requires_confirmation, bool):
            raise ValueError("requires_confirmation must be bool")
        base_confidence = _normalize_confidence(raw.get("base_confidence", confidence))
        confidence_version = _normalize_confidence_version(
            raw.get("confidence_version", DEFAULT_CONFIDENCE_VERSION)
        )
        consistency_state = _normalize_consistency_state(
            raw.get("consistency_state", DEFAULT_CONSISTENCY_STATE)
        )
        support_count = max(1, _normalize_nonnegative_int(raw.get("support_count", 1)))
        contradiction_count = _normalize_nonnegative_int(raw.get("contradiction_count", 0))
        retrieval_count = _normalize_nonnegative_int(raw.get("retrieval_count", 0))
        injection_count = _normalize_nonnegative_int(raw.get("injection_count", 0))
        semantic_text_hash = _normalize_semantic_text_hash(
            raw.get("semantic_text_hash")
            or semantic_text_hash_for_fact(raw["content"], raw.get("owner"), raw.get("scope"))
        )
        if not last_supported_at:
            last_supported_at = raw["last_seen_at"]
        return cls(
            fact_id=raw["fact_id"],
            content=raw["content"],
            canonical_key=raw["canonical_key"],
            category=_normalize_category(raw["category"]),
            scope=_normalize_scope(raw["scope"]),
            owner=_normalize_owner(raw["owner"]),
            source_cursors=source_cursors,
            source_excerpt=raw["source_excerpt"],
            confidence=confidence,
            status=_normalize_status(raw["status"]),
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            last_seen_at=raw["last_seen_at"],
            expires_at=expires_at,
            supersedes_fact_id=supersedes_fact_id,
            requires_confirmation=requires_confirmation,
            base_confidence=base_confidence,
            confidence_version=confidence_version,
            consistency_state=consistency_state,
            support_count=support_count,
            contradiction_count=contradiction_count,
            retrieval_count=retrieval_count,
            injection_count=injection_count,
            last_supported_at=last_supported_at,
            last_contested_at=last_contested_at,
            last_retrieved_at=last_retrieved_at,
            semantic_text_hash=semantic_text_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not isinstance(data.get("last_seen_at"), str) or not str(data.get("last_seen_at")).strip():
            data["last_seen_at"] = self.effective_last_seen_at()
        return data

    def effective_last_seen_at(self) -> str:
        candidates = [
            ts
            for ts in (
                self.last_seen_at,
                self.last_supported_at,
                self.last_retrieved_at,
            )
            if isinstance(ts, str) and ts.strip()
        ]
        if not candidates:
            return self.last_seen_at
        return max(
            candidates,
            key=lambda item: _parse_datetime(item) or datetime.min.replace(tzinfo=timezone.utc),
        )


@dataclass(frozen=True)
class FactRelationRecord:
    relation_id: str
    source_fact_id: str
    target_fact_id: str
    relation_type: str
    confidence: float
    origin: str
    created_at: str
    updated_at: str
    status: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FactRelationRecord":
        required_strings = (
            "relation_id",
            "source_fact_id",
            "target_fact_id",
            "relation_type",
            "origin",
            "created_at",
            "updated_at",
            "status",
        )
        if any(not isinstance(raw.get(key), str) or not str(raw.get(key)).strip() for key in required_strings):
            raise ValueError("relation record missing required string fields")
        evidence = raw.get("evidence", {})
        if not isinstance(evidence, dict):
            raise ValueError("relation evidence must be an object")
        return cls(
            relation_id=str(raw["relation_id"]).strip(),
            source_fact_id=str(raw["source_fact_id"]).strip(),
            target_fact_id=str(raw["target_fact_id"]).strip(),
            relation_type=_normalize_relation_type(raw["relation_type"]),
            confidence=_normalize_confidence(raw.get("confidence", 0.7)),
            origin=str(raw["origin"]).strip(),
            created_at=str(raw["created_at"]).strip(),
            updated_at=str(raw["updated_at"]).strip(),
            status=_normalize_relation_status(raw["status"]),
            evidence=evidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactEventRecord:
    event_id: str
    fact_id: str
    event_type: str
    actor: str
    origin: str
    batch_id: str
    proposal_id: str | None
    review_event_id: str | None
    created_at: str
    before: dict[str, Any]
    after: dict[str, Any]
    reason: str
    evidence: dict[str, Any]
    model_info: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FactEventRecord":
        required_strings = (
            "event_id",
            "fact_id",
            "event_type",
            "actor",
            "origin",
            "batch_id",
            "created_at",
        )
        if any(not isinstance(raw.get(key), str) for key in required_strings):
            raise ValueError("event record missing required string fields")
        before = raw.get("before", {})
        after = raw.get("after", {})
        evidence = raw.get("evidence", {})
        model_info = raw.get("model_info", {})
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise ValueError("event before/after must be objects")
        if not isinstance(evidence, dict) or not isinstance(model_info, dict):
            raise ValueError("event evidence/model_info must be objects")
        return cls(
            event_id=str(raw["event_id"]).strip(),
            fact_id=str(raw["fact_id"]).strip(),
            event_type=str(raw["event_type"]).strip(),
            actor=str(raw["actor"]).strip(),
            origin=str(raw["origin"]).strip(),
            batch_id=str(raw["batch_id"]).strip(),
            proposal_id=_optional_record_string(raw.get("proposal_id")),
            review_event_id=_optional_record_string(raw.get("review_event_id")),
            created_at=str(raw["created_at"]).strip(),
            before=before,
            after=after,
            reason=str(raw.get("reason") or ""),
            evidence=evidence,
            model_info=model_info,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactRetrievalResult:
    fact: FactRecord
    score: float
    source: str
    summary: str = ""
    contested_summary: str = ""


@dataclass(frozen=True)
class FactRetrievalBundle:
    facts: list[FactRecord]
    rendered_text: str
    fallback_used: bool = False
    retrievals: list[FactRetrievalResult] = field(default_factory=list)


@dataclass(frozen=True)
class FactResolution:
    action: str
    existing_fact_id: str | None = None
    candidate_fact_id: str | None = None
    relation_type: str | None = None
    similarity: float = 0.0
    contested: bool = False


class FactRelationStore:
    def __init__(self, workspace: Path, *, path: Path | None = None) -> None:
        self.workspace = Path(workspace)
        self.path = path or (self.workspace / "memory" / "fact_relations.jsonl")
        self._lock_path = self.path.parent / ".fact_relations.lock"

    def _locked(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self._lock_path))

    def read_all(self) -> list[FactRelationRecord]:
        with self._locked():
            return self.read_all_unlocked()

    def read_all_unlocked(self) -> list[FactRelationRecord]:
        records: list[FactRelationRecord] = []
        with suppress(FileNotFoundError):
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        parsed = json.loads(raw)
                        if not isinstance(parsed, dict):
                            raise ValueError("relation line is not an object")
                        records.append(FactRelationRecord.from_dict(parsed))
                    except (json.JSONDecodeError, ValueError, TypeError):
                        logger.warning("Skipping invalid fact relation line in {}", self.path)
        return records

    def upsert(self, relation: FactRelationRecord) -> FactRelationRecord:
        with self._locked():
            records = self.read_all_unlocked()
            updated = self.upsert_unlocked(records, relation)
            self._write_unlocked(records)
            return updated

    def upsert_unlocked(
        self,
        records: list[FactRelationRecord],
        relation: FactRelationRecord,
    ) -> FactRelationRecord:
        for index, existing in enumerate(records):
            same_edge = (
                existing.source_fact_id == relation.source_fact_id
                and existing.target_fact_id == relation.target_fact_id
                and existing.relation_type == relation.relation_type
            )
            if not same_edge:
                continue
            records[index] = relation
            return relation
        records.append(relation)
        return relation

    def reverse_supersedes_index(self) -> dict[str, list[str]]:
        reverse: dict[str, list[str]] = {}
        for relation in self.read_all():
            if relation.relation_type != "supersedes" or relation.status != "active":
                continue
            reverse.setdefault(relation.target_fact_id, []).append(relation.source_fact_id)
        return reverse

    def related_fact_ids(
        self,
        fact_id: str,
        *,
        relation_types: Iterable[str] | None = None,
        depth: int = 1,
        bidirectional: bool = False,
    ) -> set[str]:
        allowed = {
            _normalize_relation_type(item)
            for item in (relation_types or VALID_RELATION_TYPES)
        }
        adjacency: dict[str, set[str]] = {}
        for relation in self.read_all():
            if relation.status != "active" or relation.relation_type not in allowed:
                continue
            adjacency.setdefault(relation.source_fact_id, set()).add(relation.target_fact_id)
            if bidirectional:
                adjacency.setdefault(relation.target_fact_id, set()).add(relation.source_fact_id)
        frontier = {fact_id}
        visited = {fact_id}
        for _ in range(max(1, depth)):
            next_frontier: set[str] = set()
            for current in frontier:
                for target in adjacency.get(current, set()):
                    if target in visited:
                        continue
                    visited.add(target)
                    next_frontier.add(target)
            frontier = next_frontier
            if not frontier:
                break
        visited.discard(fact_id)
        return visited

    def _write_unlocked(self, records: list[FactRelationRecord]) -> None:
        text = "".join(
            json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        )
        _write_text_atomic(self.path, text)


class FactEventStore:
    def __init__(self, workspace: Path, *, path: Path | None = None) -> None:
        self.workspace = Path(workspace)
        self.path = path or (self.workspace / "memory" / "audit" / "fact_events.jsonl")
        self._lock_path = self.path.parent / ".fact_events.lock"

    def _locked(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self._lock_path))

    def append(self, event: FactEventRecord) -> FactEventRecord:
        with self._locked():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return event

    def read_all(self) -> list[FactEventRecord]:
        records: list[FactEventRecord] = []
        with suppress(FileNotFoundError):
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        parsed = json.loads(raw)
                        if not isinstance(parsed, dict):
                            raise ValueError("event line is not an object")
                        records.append(FactEventRecord.from_dict(parsed))
                    except (json.JSONDecodeError, ValueError, TypeError):
                        logger.warning("Skipping invalid fact event line in {}", self.path)
        return records


class FactSemanticResolver:
    _TOKEN_RE = re.compile(r"[\w\u3400-\u4dbf\u4e00-\u9fff]+", re.UNICODE)

    def __init__(self, workspace: Path, *, path: Path | None = None) -> None:
        self.workspace = Path(workspace)
        self.path = path or (self.workspace / "memory" / "semantic_index.json")
        self._lock_path = self.path.parent / ".semantic_index.lock"

    def _locked(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self._lock_path))

    def candidate_search(
        self,
        proposal: FactProposal,
        records: list[FactRecord],
    ) -> list[tuple[FactRecord, float]]:
        owner = _normalize_owner(proposal.owner)
        category = _normalize_category(proposal.category)
        scope = _normalize_scope(proposal.scope)
        candidates: list[tuple[FactRecord, float]] = []
        for record in records:
            if record.owner != owner:
                continue
            if record.category != category:
                continue
            if not _scope_family_match(scope, record.scope):
                continue
            score = self.similarity(proposal.content, record.content)
            if score <= 0.0:
                continue
            candidates.append((record, score))
        candidates.sort(key=lambda item: item[1], reverse=True)
        return candidates

    def classify_relation(
        self,
        proposal: FactProposal,
        record: FactRecord,
        similarity: float,
    ) -> str:
        left = normalize_fact_content(proposal.content)
        right = normalize_fact_content(record.content)
        left_tokens = set(self._TOKEN_RE.findall(left))
        right_tokens = set(self._TOKEN_RE.findall(right))
        if similarity >= 0.92:
            return "equivalent"
        if left_tokens and right_tokens and left_tokens.issuperset(right_tokens):
            return "narrows"
        if left_tokens and right_tokens and left_tokens.issubset(right_tokens):
            return "generalizes"
        if similarity >= 0.80:
            return "related_to"
        return "related_to"

    def similarity(self, left: str, right: str) -> float:
        left_tokens = set(self._TOKEN_RE.findall(normalize_fact_content(left)))
        right_tokens = set(self._TOKEN_RE.findall(normalize_fact_content(right)))
        if not left_tokens or not right_tokens:
            return 0.0
        shared = len(left_tokens & right_tokens)
        union = len(left_tokens | right_tokens)
        token_score = shared / union if union else 0.0
        prefix_bonus = 0.1 if normalize_fact_content(left) == normalize_fact_content(right) else 0.0
        return max(0.0, min(1.0, token_score + prefix_bonus))

    def record_pending_index(self, record: FactRecord) -> None:
        with self._locked():
            payload = self._read_unlocked()
            payload["pending_hashes"] = sorted(set(payload.get("pending_hashes", [])) | {record.semantic_text_hash})
            self._write_unlocked(payload)

    def runtime_status(self) -> dict[str, Any]:
        with self._locked():
            payload = self._read_unlocked()
        embeddings = payload.get("embeddings", {})
        pending = payload.get("pending_hashes", [])
        return {
            "semantic_index_available": bool(payload),
            "semantic_embedding_count": len(embeddings) if isinstance(embeddings, dict) else 0,
            "semantic_pending_count": len(pending) if isinstance(pending, list) else 0,
        }

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"embeddings": {}, "pending_hashes": []}
        return parsed if isinstance(parsed, dict) else {"embeddings": {}, "pending_hashes": []}

    def _write_unlocked(self, payload: dict[str, Any]) -> None:
        _write_text_atomic(self.path, json.dumps(payload, ensure_ascii=False, sort_keys=True))


def normalize_fact_content(content: str) -> str:
    text = content.strip().lower()
    return re.sub(r"\s+", " ", text)


def semantic_text_hash_for_fact(
    content: str,
    owner: str | None = None,
    scope: str | None = None,
) -> str:
    payload = "\0".join([
        normalize_fact_content(content),
        _normalize_owner(owner) if owner is not None else "unknown",
        _normalize_scope(scope) if scope is not None else "general",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_key_for_fact(
    content: str,
    owner: str,
    category: str,
    scope: str,
) -> str:
    payload = "\0".join([
        normalize_fact_content(content),
        owner.strip().lower(),
        category.strip().lower(),
        scope.strip().lower(),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def domain_id_for_fact(scope: str, content: str) -> str:
    return _infer_domain_key(scope, content)


def parse_fact_proposal_response(text: str) -> DreamFactProposalBatch:
    raw_text = _extract_json_text(text)
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid fact proposal JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("fact proposal response must be a JSON object")

    batch = DreamFactProposalBatch(
        memory_render_hints=(
            parsed.get("memory_render_hints")
            if isinstance(parsed.get("memory_render_hints"), list)
            else []
        ),
    )
    upserts = parsed.get("facts_to_upsert", [])
    deprecations = parsed.get("facts_to_deprecate", [])
    if not isinstance(upserts, list):
        batch.parse_rejected.append(ParseRejectedProposal(
            section="facts_to_upsert",
            index=None,
            raw=upserts,
            error="facts_to_upsert must be a list",
        ))
        upserts = []
    if not isinstance(deprecations, list):
        batch.parse_rejected.append(ParseRejectedProposal(
            section="facts_to_deprecate",
            index=None,
            raw=deprecations,
            error="facts_to_deprecate must be a list",
        ))
        deprecations = []

    for index, raw in enumerate(upserts):
        try:
            batch.facts_to_upsert.append(_parse_fact_proposal(raw))
        except ValueError as exc:
            batch.parse_rejected.append(ParseRejectedProposal(
                section="facts_to_upsert",
                index=index,
                raw=raw,
                error=str(exc),
            ))

    for index, raw in enumerate(deprecations):
        try:
            batch.facts_to_deprecate.append(_parse_deprecation_proposal(raw))
        except ValueError as exc:
            batch.parse_rejected.append(ParseRejectedProposal(
                section="facts_to_deprecate",
                index=index,
                raw=raw,
                error=str(exc),
            ))

    return batch


def validate_fact_proposal(
    proposal: FactProposal,
    *,
    existing_facts: list[FactRecord],
    history_entries: list[dict[str, Any]],
    batch_cursor_min: int,
    batch_cursor_max: int,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    try:
        category = _normalize_category(proposal.category)
        scope = _normalize_scope(proposal.scope)
        _normalize_owner(proposal.owner)
        if proposal.status is not None:
            _normalize_status(proposal.status)
        if (
            proposal.requires_confirmation is not None
            and not isinstance(proposal.requires_confirmation, bool)
        ):
            raise ValueError("requires_confirmation must be bool")
    except ValueError as exc:
        issues.append(_issue("invalid_field", "reject", str(exc)))
        return ValidationResult(
            decision="reject",
            confidence=_normalize_confidence(proposal.confidence),
            issues=issues,
        )

    confidence = _normalize_confidence(proposal.confidence)
    history_cursors = _history_cursor_set(history_entries)
    if not proposal.source_cursors:
        issues.append(_issue(
            "missing_source_cursors",
            "reject",
            "Fact proposals must cite at least one source cursor.",
        ))
    elif any(
        cursor < batch_cursor_min
        or cursor > batch_cursor_max
        or cursor not in history_cursors
        for cursor in proposal.source_cursors
    ):
        issues.append(_issue(
            "source_cursor_out_of_batch",
            "reject",
            "All source cursors must be in the current Dream batch.",
        ))

    if not proposal.source_excerpt.strip():
        issues.append(_issue(
            "missing_source_excerpt",
            "reject",
            "Fact proposals must include a supporting source excerpt.",
        ))
    elif not _source_excerpt_matches_history(proposal, history_entries):
        issues.append(_issue(
            "source_excerpt_not_found",
            "reject",
            "source_excerpt must be present in the cited history cursor content.",
        ))

    combined_text = " ".join([
        proposal.content,
        proposal.source_excerpt,
        proposal.scope,
        proposal.reason,
    ])
    if (
        category in HIGH_RISK_CATEGORIES
        or _contains_any(combined_text, HIGH_RISK_KEYWORDS)
        or _requires_high_risk_confirmation(scope=scope, content=proposal.content)
    ):
        issues.append(_issue(
            "high_risk_memory",
            "pending",
            "High-risk security facts require confirmation.",
        ))

    if category == "temporary" and not proposal.expires_at:
        issues.append(_issue(
            "temporary_missing_expires_at",
            "pending",
            "Temporary facts without an expiration require confirmation.",
        ))

    if category != "temporary" and _contains_any(combined_text, TEMPORARY_LANGUAGE):
        issues.append(_issue(
            "temporary_language_non_temporary",
            "pending",
            "Temporary language in a non-temporary fact requires confirmation.",
        ))

    if _contains_any(combined_text, UNCERTAIN_LANGUAGE):
        confidence = min(confidence, 0.7)
        issues.append(_issue(
            "uncertain_language",
            "warn",
            "Uncertain language capped confidence at 0.7.",
        ))

    if category in CONFLICT_CATEGORIES:
        for record in existing_facts:
            if (
                record.status == "active"
                and record.category == category
                and record.scope == scope
                and normalize_fact_content(record.content)
                != normalize_fact_content(proposal.content)
            ):
                severity = "pending"
                if str(proposal.change_kind or "").strip().lower() in {"replace", "contradict"}:
                    severity = "warn"
                issues.append(_issue(
                    "possible_conflict",
                    severity,
                    "Existing active fact in the same category/scope differs.",
                ))
                break

    if any(issue.severity == "reject" for issue in issues):
        decision = "reject"
    elif any(issue.severity == "pending" for issue in issues):
        decision = "pending_confirmation"
    else:
        decision = "active"
    return ValidationResult(decision=decision, confidence=confidence, issues=issues)


def validate_deprecation_proposal(
    proposal: FactDeprecationProposal,
    *,
    existing_facts: list[FactRecord],
    history_entries: list[dict[str, Any]],
    batch_cursor_min: int,
    batch_cursor_max: int,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    target = next((fact for fact in existing_facts if fact.fact_id == proposal.fact_id), None)
    if target is None:
        issues.append(_issue(
            "unknown_fact_id",
            "reject",
            "Deprecation fact_id does not exist.",
        ))
    if not proposal.reason.strip():
        issues.append(_issue(
            "missing_deprecation_reason",
            "reject",
            "Deprecation proposals must include a non-empty reason.",
        ))

    history_cursors = _history_cursor_set(history_entries)
    if not proposal.source_cursors or any(
        cursor < batch_cursor_min
        or cursor > batch_cursor_max
        or cursor not in history_cursors
        for cursor in proposal.source_cursors
    ):
        issues.append(_issue(
            "missing_current_source_for_deprecation",
            "reject",
            "Deprecations must include source_cursors in the current Dream batch.",
        ))

    if (
        target is not None
        and target.category in HIGH_RISK_CATEGORIES
        and target.status == "active"
    ):
        issues.append(_issue(
            "active_high_risk_deprecation",
            "reject",
            "Active policy/safety facts cannot be deprecated automatically.",
        ))

    decision = "reject" if any(issue.severity == "reject" for issue in issues) else "active"
    return ValidationResult(decision=decision, confidence=1.0, issues=issues)


def render_memory_md(records: list[FactRecord]) -> str:
    active_by_category: dict[str, list[FactRecord]] = {
        category: [] for category in CATEGORY_ORDER
    }
    pending: list[FactRecord] = []
    for record in records:
        if record.status == "pending_confirmation":
            pending.append(record)
            continue
        if record.status != "active":
            continue
        active_by_category.setdefault(record.category, []).append(record)

    lines = [
        "# Long-term Memory",
        "",
        "> Generated from memory/facts.jsonl. Do not edit directly.",
    ]

    for category in CATEGORY_ORDER:
        facts = sorted(
            active_by_category.get(category, []),
            key=lambda fact: (
                fact.scope.casefold(),
                fact.content.casefold(),
                fact.fact_id,
            ),
        )
        if not facts:
            continue
        lines.extend(["", f"## {_section_title(category)}"])
        for fact in facts:
            lines.extend(_render_fact_lines(fact))

    pending = sorted(
        pending,
        key=lambda fact: (
            CATEGORY_ORDER.index(fact.category)
            if fact.category in VALID_CATEGORIES
            else len(CATEGORY_ORDER),
            fact.scope.casefold(),
            fact.content.casefold(),
            fact.fact_id,
        ),
    )
    if pending:
        lines.extend(["", "## Pending Confirmation"])
        for fact in pending:
            lines.extend(_render_fact_lines(fact, include_category=True))

    return "\n".join(lines).rstrip() + "\n"


def summarize_facts(
    workspace: Path,
    *,
    fact_store: FactStore | None = None,
) -> dict[str, Any]:
    """Return redacted fact counts without exposing raw content."""

    store = fact_store or FactStore(Path(workspace))
    with store._locked():
        records = store.read_all_unlocked()

    category_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()
    active_count = 0
    pending_confirmation_count = 0
    contested_count = 0
    contradicted_count = 0
    for record in records:
        if record.status == "active":
            active_count += 1
            category_counts[record.category] += 1
            domain_counts[_fact_domain_key(record)] += 1
            if record.consistency_state == "contested":
                contested_count += 1
        elif record.status == "pending_confirmation":
            pending_confirmation_count += 1
            if record.consistency_state == "contested":
                contested_count += 1
        elif record.status == "contradicted":
            contradicted_count += 1
    return {
        "active_count": active_count,
        "pending_confirmation_count": pending_confirmation_count,
        "contested_count": contested_count,
        "contradicted_count": contradicted_count,
        "category_counts": dict(category_counts),
        "domain_counts": dict(domain_counts),
        "relation_count": len(store.read_relations()) if store.flag_enabled("fact_graph_enabled") else 0,
        "event_count": len(store.read_events()) if store.flag_enabled("fact_audit_enabled") else 0,
    }


class FactStore:
    def __init__(
        self,
        workspace: Path,
        *,
        facts_file: Path | None = None,
        lock_factory: Callable[[], FileLock] | None = None,
        redactor: Callable[[str], str] | None = None,
        config: FactStoreConfig | None = None,
        feature_flags: dict[str, bool] | None = None,
    ):
        self.workspace = workspace
        self.memory_dir = ensure_dir(workspace / "memory")
        self.facts_file = facts_file or self.memory_dir / "facts.jsonl"
        self.calibration_file = self.memory_dir / "confidence_calibration.json"
        self.relations_file = self.memory_dir / "fact_relations.jsonl"
        self.semantic_index_file = self.memory_dir / "semantic_index.json"
        self.fact_events_file = self.memory_dir / "audit" / "fact_events.jsonl"
        self._lock_file = self.memory_dir / ".lock"
        self._lock_factory = lock_factory
        self._redactor = redactor or _default_redactor
        self.config = config or DEFAULT_FACT_STORE_CONFIG
        self.feature_flags = self._normalize_feature_flags(feature_flags)
        self.relation_store = FactRelationStore(workspace, path=self.relations_file)
        self.event_store = FactEventStore(workspace, path=self.fact_events_file)
        self.semantic_resolver = FactSemanticResolver(workspace, path=self.semantic_index_file)
        self._cached_signature: tuple[int, int] | None = None
        self._cached_raw_records: list[dict[str, Any]] = []

    @staticmethod
    def _normalize_feature_flags(feature_flags: dict[str, bool] | None) -> dict[str, bool]:
        normalized = {name: False for name in FEATURE_FLAG_NAMES}
        if not isinstance(feature_flags, dict):
            return normalized
        for name in FEATURE_FLAG_NAMES:
            if name in feature_flags:
                normalized[name] = bool(feature_flags[name])
        return normalized

    def flag_enabled(self, name: str) -> bool:
        return bool(self.feature_flags.get(str(name or "").strip(), False))

    def _locked(self) -> FileLock:
        if self._lock_factory is not None:
            return self._lock_factory()
        return FileLock(str(self._lock_file))

    def read_all(self) -> list[FactRecord]:
        with self._locked():
            return self.read_all_unlocked()

    def read_all_unlocked(self) -> list[FactRecord]:
        raw_records = self._load_raw_records_unlocked()
        records: list[FactRecord] = []
        for parsed in raw_records:
            with suppress(ValueError, TypeError):
                records.append(FactRecord.from_dict(parsed))
        return records

    def _load_raw_records_unlocked(self) -> list[dict[str, Any]]:
        signature = self._file_signature()
        if signature is not None and signature == self._cached_signature:
            return [dict(item) for item in self._cached_raw_records]
        records: list[dict[str, Any]] = []
        with suppress(FileNotFoundError):
            with open(self.facts_file, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    raw_line = line.strip()
                    if not raw_line:
                        continue
                    try:
                        parsed = json.loads(raw_line)
                        if not isinstance(parsed, dict):
                            raise ValueError("fact line is not an object")
                        records.append(parsed)
                    except (json.JSONDecodeError, ValueError, TypeError):
                        logger.warning(
                            "Skipping invalid facts.jsonl line {} in {}",
                            line_no,
                            self.facts_file,
                        )
                        continue
        self._refresh_cache_from_raw(records)
        return [dict(item) for item in records]

    def _refresh_cache_from_raw(self, records: list[dict[str, Any]]) -> None:
        self._cached_raw_records = [dict(item) for item in records]
        self._cached_signature = self._file_signature()

    def _file_signature(self) -> tuple[int, int] | None:
        try:
            stat = self.facts_file.stat()
        except FileNotFoundError:
            return None
        except OSError:
            return None
        return (int(stat.st_mtime_ns), int(stat.st_size))

    def runtime_status(self) -> dict[str, Any]:
        with self._locked():
            records = self.read_all_unlocked()
        counts = Counter(record.status for record in records)
        consistency = Counter(record.consistency_state for record in records)
        events = self.event_store.read_all() if self.flag_enabled("fact_audit_enabled") else []
        relations = self.relation_store.read_all() if self.flag_enabled("fact_graph_enabled") else []
        semantic = (
            self.semantic_resolver.runtime_status()
            if self.flag_enabled("semantic_retrieval_enabled") or self.flag_enabled("semantic_merge_enabled")
            else {
                "semantic_index_available": False,
                "semantic_embedding_count": 0,
                "semantic_pending_count": 0,
            }
        )
        event_types = Counter(event.event_type for event in events)
        return {
            "fact_total_count": len(records),
            "fact_status_counts": dict(counts),
            "fact_consistency_counts": dict(consistency),
            "fact_relation_count": len(relations),
            "fact_event_count": len(events),
            "fact_event_type_counts": dict(event_types),
            **semantic,
        }

    def read_relations(self) -> list[FactRelationRecord]:
        return self.relation_store.read_all()

    def persist_relation_candidates(
        self,
        relation_candidates: list[dict[str, Any]] | None,
        *,
        default_source_fact_id: str | None = None,
        default_target_fact_id: str | None = None,
        origin: str = "fact_store",
        content_for_hash: str = "",
        default_confidence: float = 0.7,
    ) -> list[FactRelationRecord]:
        with self._locked():
            return self._persist_relation_candidates_unlocked(
                relation_candidates or [],
                default_source_fact_id=default_source_fact_id,
                default_target_fact_id=default_target_fact_id,
                origin=origin,
                content_for_hash=content_for_hash,
                default_confidence=default_confidence,
            )

    def related_facts(
        self,
        fact_id: str,
        *,
        relation_types: Iterable[str] | None = None,
        depth: int = 1,
        include_pending: bool = False,
        bidirectional: bool = False,
    ) -> list[FactRecord]:
        statuses = {"active", "pending_confirmation"} if include_pending else {"active"}
        related_ids = self.relation_store.related_fact_ids(
            fact_id,
            relation_types=relation_types,
            depth=depth,
            bidirectional=bidirectional,
        )
        if not related_ids:
            return []
        records_by_id = {
            record.fact_id: record
            for record in self.read_all()
            if record.status in statuses
        }
        return [records_by_id[related_id] for related_id in sorted(related_ids) if related_id in records_by_id]

    def read_events(self) -> list[FactEventRecord]:
        return self.event_store.read_all()

    def retrieve_context_bundle(
        self,
        *,
        scope_prefix: str | None = None,
        category: str | None = None,
        include_pending: bool = False,
        top_k: int = DEFAULT_CONTEXT_TOP_K,
    ) -> FactRetrievalBundle:
        with self._locked():
            all_records = self.read_all_unlocked()
            records = self._list_active_from_records(
                all_records,
                category=category,
                scope_prefix=scope_prefix,
                include_pending=include_pending,
            )
            if self.flag_enabled("semantic_retrieval_enabled"):
                ranked = self._build_retrieval_rankings(records, top_k=top_k)
                if ranked and self.flag_enabled("fact_graph_enabled"):
                    ranked = self._expand_relation_rankings(
                        ranked,
                        all_records=all_records,
                        include_pending=include_pending,
                        top_k=top_k,
                    )
                now = datetime.now(timezone.utc).isoformat()
                for item in ranked:
                    item.fact.retrieval_count += 1
                    item.fact.injection_count += 1
                    item.fact.last_retrieved_at = now
                    item.fact.last_seen_at = item.fact.effective_last_seen_at()
                    if self.flag_enabled("confidence_v2_enabled"):
                        item.fact.confidence = self._derive_confidence(
                            item.fact,
                            now=_parse_datetime(now) or datetime.now(timezone.utc),
                        )
                if ranked:
                    self._write_records_unlocked(all_records)
            else:
                ranked = [
                    FactRetrievalResult(
                        fact=record,
                        score=record.confidence,
                        source="memory_md_fallback",
                    )
                    for record in records[: max(1, top_k)]
                ]
        return FactRetrievalBundle(
            facts=[item.fact for item in ranked],
            rendered_text=render_memory_md([item.fact for item in ranked]) if ranked else "",
            fallback_used=not self.flag_enabled("semantic_retrieval_enabled"),
            retrievals=ranked,
        )

    def list_active(
        self,
        *,
        category: str | None = None,
        scope_prefix: str | None = None,
        include_pending: bool = False,
    ) -> list[FactRecord]:
        with self._locked():
            return self.list_active_unlocked(
                category=category,
                scope_prefix=scope_prefix,
                include_pending=include_pending,
            )

    def list_active_unlocked(
        self,
        *,
        category: str | None = None,
        scope_prefix: str | None = None,
        include_pending: bool = False,
    ) -> list[FactRecord]:
        return self._list_active_from_records(
            self.read_all_unlocked(),
            category=category,
            scope_prefix=scope_prefix,
            include_pending=include_pending,
        )

    def _list_active_from_records(
        self,
        records: list[FactRecord],
        *,
        category: str | None = None,
        scope_prefix: str | None = None,
        include_pending: bool = False,
    ) -> list[FactRecord]:
        target_category = _normalize_category(category) if category else None
        target_scope = _normalize_scope(scope_prefix) if scope_prefix else None
        statuses = {"active", "pending_confirmation"} if include_pending else {"active"}
        facts = [
            record
            for record in records
            if record.status in statuses
        ]
        if target_category:
            facts = [record for record in facts if record.category == target_category]
        if target_scope:
            facts = [
                record
                for record in facts
                if record.scope == target_scope or record.scope.startswith(f"{target_scope}.")
            ]
        return sorted(
            facts,
            key=lambda fact: (
                CATEGORY_ORDER.index(fact.category)
                if fact.category in VALID_CATEGORIES
                else len(CATEGORY_ORDER),
                fact.scope.casefold(),
                fact.content.casefold(),
                fact.fact_id,
            ),
        )

    def find_by_canonical_key(
        self,
        canonical_key: str,
        *,
        status: str | None = None,
    ) -> FactRecord | None:
        with self._locked():
            return self.find_by_canonical_key_unlocked(canonical_key, status=status)

    def find_by_canonical_key_unlocked(
        self,
        canonical_key: str,
        *,
        status: str | None = None,
    ) -> FactRecord | None:
        canonical_key = str(canonical_key or "").strip()
        if not canonical_key:
            return None
        target_status = _normalize_status(status) if status else None
        for record in self.read_all_unlocked():
            if record.canonical_key != canonical_key:
                continue
            if target_status and record.status != target_status:
                continue
            return record
        return None

    def update_confidence(self, fact_id: str, confidence: float) -> FactRecord | None:
        with self._locked():
            records = self.read_all_unlocked()
            updated = self.update_confidence_in_records_unlocked(
                records,
                fact_id,
                confidence,
            )
            if updated is not None:
                self._write_records_unlocked(records)
            return updated

    def update_confidence_in_records_unlocked(
        self,
        records: list[FactRecord],
        fact_id: str,
        confidence: float,
    ) -> FactRecord | None:
        fact_id = str(fact_id or "").strip()
        if not fact_id:
            return None
        new_confidence = _normalize_confidence(confidence)
        for record in records:
            if record.fact_id != fact_id:
                continue
            if record.confidence == new_confidence:
                return record
            record.confidence = new_confidence
            record.updated_at = datetime.now(timezone.utc).isoformat()
            return record
        return None

    def decay_confidence(
        self,
        *,
        factor: float | None = None,
        min_confidence: float | None = None,
        decay_start_days: int | None = None,
        now: datetime | None = None,
    ) -> int:
        with self._locked():
            records = self.read_all_unlocked()
            changed = self.decay_confidence_in_records_unlocked(
                records,
                factor=factor,
                min_confidence=min_confidence,
                decay_start_days=decay_start_days,
                now=now,
            )
            if changed:
                self._write_records_unlocked(records)
            return changed

    def decay_confidence_in_records_unlocked(
        self,
        records: list[FactRecord],
        *,
        factor: float | None = None,
        min_confidence: float | None = None,
        decay_start_days: int | None = None,
        now: datetime | None = None,
    ) -> int:
        changed = 0
        for record in records:
            if record.status != "active":
                continue
            now_dt = now or datetime.now(timezone.utc)
            usage_decay = _usage_decay_multiplier(record, now=now_dt)
            if self.flag_enabled("confidence_v2_enabled"):
                new_confidence = self._derive_confidence(
                    record,
                    now=now_dt,
                    factor=factor,
                    min_confidence=min_confidence,
                    decay_start_days=decay_start_days,
                )
                if new_confidence < record.confidence and usage_decay < 1.0:
                    new_confidence = record.confidence - (
                        (record.confidence - new_confidence) * usage_decay
                    )
            else:
                last_seen = _parse_datetime(record.last_seen_at) or now_dt
                days_since_seen = _days_between(last_seen, now_dt)
                effective_factor = (
                    self.config.confidence_decay_factor
                    if factor is None
                    else _normalize_decay_factor(factor)
                )
                effective_min = self.config.min_confidence if min_confidence is None else min_confidence
                grace = self.config.decay_start_days if decay_start_days is None else max(0, int(decay_start_days))
                if days_since_seen <= grace:
                    new_confidence = record.confidence
                else:
                    decay_steps = max(0, days_since_seen - grace) * usage_decay
                    new_confidence = max(
                        _normalize_confidence(effective_min),
                        record.confidence * (effective_factor ** decay_steps),
                    )
            if new_confidence == record.confidence:
                continue
            record.confidence = new_confidence
            record.updated_at = now_dt.isoformat()
            changed += 1
        return changed

    def calibrate_confidence(
        self,
        proposal_type: str,
        domain_id: str,
        raw_confidence: float,
        *,
        source: str = "dream",
        category: str = "note",
    ) -> float:
        confidence = _normalize_confidence(raw_confidence)
        calibration = self._read_confidence_calibration()
        for key in _calibration_keys(source, category, proposal_type, domain_id):
            raw_entry = calibration.get(key)
            if not isinstance(raw_entry, dict):
                continue
            sample_count = max(
                _int_value(raw_entry.get("count_30d"), default=0),
                _int_value(raw_entry.get("count_90d"), default=0),
                _int_value(raw_entry.get("count"), default=0),
            )
            if sample_count <= self.config.calibration_min_count:
                continue
            bias = _float_value(
                raw_entry.get("bias_30d", raw_entry.get("bias_90d", raw_entry.get("bias"))),
                default=0.0,
            )
            adjusted = confidence + bias
            if _normalize_category(category) in HIGH_RISK_CATEGORIES and bias > 0:
                adjusted = confidence
            return max(0.1, min(0.99, adjusted))
        return confidence

    def _read_confidence_calibration(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self.calibration_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def upsert_fact(
        self,
        content: str,
        *,
        category: str = "note",
        scope: str = "general",
        owner: str = "unknown",
        source_cursors: list[int] | None = None,
        source_excerpt: str = "",
        confidence: float = 1.0,
        expires_at: str | None = None,
        requires_confirmation: bool | None = None,
        status: str | None = None,
        supersedes_fact_id: str | None = None,
        change_kind: str | None = None,
        target_fact_id: str | None = None,
        relation_candidates: list[dict[str, Any]] | None = None,
        semantic_scope_hint: str | None = None,
        proposal_id: str | None = None,
        review_event_id: str | None = None,
        batch_id: str = "runtime",
        actor: str = "system",
        origin: str = "fact_store",
        model_info: dict[str, Any] | None = None,
    ) -> FactRecord:
        with self._locked():
            return self.upsert_fact_unlocked(
                content,
                category=category,
                scope=scope,
                owner=owner,
                source_cursors=source_cursors,
                source_excerpt=source_excerpt,
                confidence=confidence,
                expires_at=expires_at,
                requires_confirmation=requires_confirmation,
                status=status,
                supersedes_fact_id=supersedes_fact_id,
                change_kind=change_kind,
                target_fact_id=target_fact_id,
                relation_candidates=relation_candidates,
                semantic_scope_hint=semantic_scope_hint,
                proposal_id=proposal_id,
                review_event_id=review_event_id,
                batch_id=batch_id,
                actor=actor,
                origin=origin,
                model_info=model_info,
            )

    def upsert_fact_unlocked(
        self,
        content: str,
        *,
        category: str = "note",
        scope: str = "general",
        owner: str = "unknown",
        source_cursors: list[int] | None = None,
        source_excerpt: str = "",
        confidence: float = 1.0,
        expires_at: str | None = None,
        requires_confirmation: bool | None = None,
        status: str | None = None,
        supersedes_fact_id: str | None = None,
        change_kind: str | None = None,
        target_fact_id: str | None = None,
        relation_candidates: list[dict[str, Any]] | None = None,
        semantic_scope_hint: str | None = None,
        proposal_id: str | None = None,
        review_event_id: str | None = None,
        batch_id: str = "runtime",
        actor: str = "system",
        origin: str = "fact_store",
        model_info: dict[str, Any] | None = None,
    ) -> FactRecord:
        records = self.read_all_unlocked()
        fact = self.upsert_fact_in_records_unlocked(
            records,
            content,
            category=category,
            scope=scope,
            owner=owner,
            source_cursors=source_cursors,
            source_excerpt=source_excerpt,
            confidence=confidence,
            expires_at=expires_at,
            requires_confirmation=requires_confirmation,
            status=status,
            supersedes_fact_id=supersedes_fact_id,
            change_kind=change_kind,
            target_fact_id=target_fact_id,
            relation_candidates=relation_candidates,
            semantic_scope_hint=semantic_scope_hint,
            proposal_id=proposal_id,
            review_event_id=review_event_id,
            batch_id=batch_id,
            actor=actor,
            origin=origin,
            model_info=model_info,
        )
        self._write_records_unlocked(records)
        return fact

    def upsert_fact_in_records_unlocked(
        self,
        records: list[FactRecord],
        content: str,
        *,
        category: str = "note",
        scope: str = "general",
        owner: str = "unknown",
        source_cursors: list[int] | None = None,
        source_excerpt: str = "",
        confidence: float = 1.0,
        expires_at: str | None = None,
        requires_confirmation: bool | None = None,
        status: str | None = None,
        supersedes_fact_id: str | None = None,
        change_kind: str | None = None,
        target_fact_id: str | None = None,
        relation_candidates: list[dict[str, Any]] | None = None,
        semantic_scope_hint: str | None = None,
        proposal_id: str | None = None,
        review_event_id: str | None = None,
        batch_id: str = "runtime",
        actor: str = "system",
        origin: str = "fact_store",
        model_info: dict[str, Any] | None = None,
    ) -> FactRecord:
        content = content.strip()
        if not content:
            raise ValueError("fact content cannot be empty")
        explicit_status = status
        explicit_requires_confirmation = requires_confirmation
        category = _normalize_category(category)
        scope = _normalize_scope(scope)
        owner = _normalize_owner(owner)
        source_cursors = _normalize_source_cursors(source_cursors)
        confidence = _normalize_confidence(confidence)
        status, requires_confirmation = _resolve_status_and_confirmation(
            category=category,
            status=status,
            requires_confirmation=requires_confirmation,
            config=self.config,
        )
        redacted_excerpt = self._redactor(source_excerpt.strip()) if source_excerpt else ""
        now = datetime.now(timezone.utc).isoformat()
        canonical_key = canonical_key_for_fact(content, owner, category, scope)
        semantic_text_hash = semantic_text_hash_for_fact(content, owner, scope)

        existing = next(
            (
                record
                for record in records
                if record.canonical_key == canonical_key
                and record.status in {"active", "pending_confirmation"}
            ),
            None,
        )
        if existing is not None:
            before = existing.to_dict()
            self._reinforce_record(
                existing,
                source_cursors=source_cursors,
                source_excerpt=redacted_excerpt,
                base_confidence=confidence,
                updated_at=now,
                expires_at=expires_at,
                supersedes_fact_id=supersedes_fact_id,
                explicit_status=status if explicit_status is not None else None,
                explicit_requires_confirmation=(
                    requires_confirmation if explicit_requires_confirmation is not None else None
                ),
            )
            self._append_fact_event(
                existing,
                event_type="reinforce",
                before=before,
                after=existing.to_dict(),
                reason="exact canonical match",
                evidence={"source_cursors": source_cursors},
                proposal_id=proposal_id,
                review_event_id=review_event_id,
                batch_id=batch_id,
                actor=actor,
                origin=origin,
                model_info=model_info,
            )
            self.semantic_resolver.record_pending_index(existing)
            return existing

        resolution = self._semantic_resolution_for_new_fact(
            records,
            content=content,
            category=category,
            scope=scope,
            owner=owner,
            source_cursors=source_cursors,
            source_excerpt=redacted_excerpt,
            confidence=confidence,
            status=status,
            requires_confirmation=requires_confirmation,
            supersedes_fact_id=supersedes_fact_id,
            now=now,
            change_kind=change_kind,
            target_fact_id=target_fact_id,
            relation_candidates=relation_candidates or [],
            semantic_scope_hint=semantic_scope_hint,
            proposal_id=proposal_id,
            review_event_id=review_event_id,
            batch_id=batch_id,
            actor=actor,
            origin=origin,
            model_info=model_info,
        )
        if resolution is not None:
            return resolution

        incumbent = self._find_conflicting_incumbent(
            records,
            category=category,
            scope=scope,
            owner=owner,
            content=content,
        )
        if supersedes_fact_id:
            self._deprecate_fact_in_records(
                records,
                supersedes_fact_id,
                updated_at=now,
                status="deprecated",
            )
        record = FactRecord(
            fact_id=self._new_fact_id(records),
            content=content,
            canonical_key=canonical_key,
            category=category,
            scope=scope,
            owner=owner,
            source_cursors=source_cursors,
            source_excerpt=redacted_excerpt,
            confidence=confidence,
            status=status,
            created_at=now,
            updated_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            supersedes_fact_id=supersedes_fact_id,
            requires_confirmation=requires_confirmation,
            base_confidence=confidence,
            confidence_version=DEFAULT_CONFIDENCE_VERSION,
            consistency_state=DEFAULT_CONSISTENCY_STATE,
            support_count=1,
            contradiction_count=0,
            retrieval_count=0,
            injection_count=0,
            last_supported_at=now,
            last_contested_at=None,
            last_retrieved_at=None,
            semantic_text_hash=semantic_text_hash,
        )
        if self.flag_enabled("confidence_v2_enabled"):
            record.confidence = self._derive_confidence(
                record,
                now=_parse_datetime(now) or datetime.now(timezone.utc),
            )
        records.append(record)
        if supersedes_fact_id:
            self._upsert_relation(
                source_fact_id=record.fact_id,
                target_fact_id=supersedes_fact_id,
                relation_type="supersedes",
                confidence=confidence,
                origin=origin,
                evidence={"content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()},
            )
        if relation_candidates:
            self._persist_explicit_relations(
                record=record,
                relation_candidates=relation_candidates,
                target_fact_id=target_fact_id,
                origin=origin,
            )
        if incumbent is not None:
            self._mark_contested_pair(
                incumbent=incumbent,
                challenger=record,
                now=now,
                reason=f"change_kind={change_kind or 'new'}",
                auto_flip=bool(
                    self.flag_enabled("contradiction_auto_flip_enabled")
                    and str(change_kind or "").strip().lower() in {"replace", "contradict"}
                ),
            )
        self.semantic_resolver.record_pending_index(record)
        self._append_fact_event(
            record,
            event_type="create",
            before={},
            after=record.to_dict(),
            reason="new fact",
            evidence={"source_cursors": source_cursors},
            proposal_id=proposal_id,
            review_event_id=review_event_id,
            batch_id=batch_id,
            actor=actor,
            origin=origin,
            model_info=model_info,
        )
        return record

    def deprecate_fact(
        self,
        fact_id: str,
        *,
        reason: str | None = None,
        superseded_by: str | None = None,
    ) -> bool:
        with self._locked():
            return self.deprecate_fact_unlocked(
                fact_id,
                reason=reason,
                superseded_by=superseded_by,
            )

    def deprecate_fact_unlocked(
        self,
        fact_id: str,
        *,
        reason: str | None = None,
        superseded_by: str | None = None,
    ) -> bool:
        records = self.read_all_unlocked()
        changed = self._deprecate_fact_in_records(
            records,
            fact_id,
            updated_at=datetime.now().isoformat(),
        )
        if changed:
            self._write_records_unlocked(records)
        return changed

    def render_memory_md(self) -> str:
        with self._locked():
            return self.render_memory_md_unlocked()

    def render_memory_md_unlocked(self) -> str:
        return render_memory_md(self.read_all_unlocked())

    def _write_records_unlocked(self, records: list[FactRecord]) -> None:
        text = "".join(
            json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        )
        _write_text_atomic(self.facts_file, text)
        self._refresh_cache_from_raw([record.to_dict() for record in records])

    def _new_fact_id(self, records: list[FactRecord]) -> str:
        existing = {record.fact_id for record in records}
        while True:
            fact_id = f"fact_{uuid.uuid4().hex[:12]}"
            if fact_id not in existing:
                return fact_id

    @staticmethod
    def _deprecate_fact_in_records(
        records: list[FactRecord],
        fact_id: str,
        *,
        updated_at: str,
        status: str = "deprecated",
    ) -> bool:
        normalized_status = _normalize_status(status)
        for record in records:
            if record.fact_id != fact_id:
                continue
            record.status = normalized_status
            if normalized_status == "contradicted":
                record.consistency_state = "contradicted"
            record.updated_at = updated_at
            return True
        return False

    def _find_conflicting_incumbent(
        self,
        records: list[FactRecord],
        *,
        category: str,
        scope: str,
        owner: str,
        content: str,
    ) -> FactRecord | None:
        normalized = normalize_fact_content(content)
        candidates = [
            record
            for record in records
            if record.owner == owner
            and record.category == category
            and record.scope == scope
            and record.status == "active"
            and normalize_fact_content(record.content) != normalized
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                item.confidence,
                item.support_count,
                item.updated_at,
            ),
            reverse=True,
        )
        return candidates[0]

    def _new_pending_challenger(
        self,
        records: list[FactRecord],
        *,
        content: str,
        category: str,
        scope: str,
        owner: str,
        source_cursors: list[int],
        source_excerpt: str,
        confidence: float,
        expires_at: str | None,
        requires_confirmation: bool,
        status: str,
        supersedes_fact_id: str | None,
        now: str,
    ) -> FactRecord:
        semantic_text_hash = semantic_text_hash_for_fact(content, owner, scope)
        record = FactRecord(
            fact_id=self._new_fact_id(records),
            content=content,
            canonical_key=canonical_key_for_fact(content, owner, category, scope),
            category=category,
            scope=scope,
            owner=owner,
            source_cursors=source_cursors,
            source_excerpt=source_excerpt,
            confidence=confidence,
            status=status,
            created_at=now,
            updated_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            supersedes_fact_id=supersedes_fact_id,
            requires_confirmation=requires_confirmation,
            base_confidence=confidence,
            confidence_version=DEFAULT_CONFIDENCE_VERSION,
            consistency_state="contested",
            support_count=1,
            contradiction_count=0,
            retrieval_count=0,
            injection_count=0,
            last_supported_at=now,
            last_contested_at=now,
            last_retrieved_at=None,
            semantic_text_hash=semantic_text_hash,
        )
        if self.flag_enabled("confidence_v2_enabled"):
            record.confidence = self._derive_confidence(
                record,
                now=_parse_datetime(now) or datetime.now(timezone.utc),
            )
        records.append(record)
        return record

    def _mark_contested_pair(
        self,
        *,
        incumbent: FactRecord,
        challenger: FactRecord,
        now: str,
        reason: str,
        auto_flip: bool,
    ) -> None:
        incumbent_before = incumbent.to_dict()
        challenger_before = challenger.to_dict()
        incumbent.consistency_state = "contested"
        challenger.consistency_state = "contested"
        incumbent.contradiction_count += 1
        challenger.contradiction_count += 1
        incumbent.last_contested_at = now
        challenger.last_contested_at = now
        if self.flag_enabled("confidence_v2_enabled"):
            now_dt = _parse_datetime(now) or datetime.now(timezone.utc)
            incumbent.confidence = self._derive_confidence(incumbent, now=now_dt)
            challenger.confidence = self._derive_confidence(challenger, now=now_dt)
        self._append_fact_event(
            incumbent,
            event_type="contest",
            before=incumbent_before,
            after=incumbent.to_dict(),
            reason=reason,
            evidence={"challenger_fact_id": challenger.fact_id},
        )
        if challenger_before != challenger.to_dict():
            self._append_fact_event(
                challenger,
                event_type="contest",
                before=challenger_before,
                after=challenger.to_dict(),
                reason=reason,
                evidence={"incumbent_fact_id": incumbent.fact_id},
            )
        if auto_flip and incumbent.category not in HIGH_RISK_CATEGORIES:
            confidence_gap = challenger.confidence - incumbent.confidence
            challenger_batches = max(1, len(challenger.source_cursors))
            if challenger_batches >= 2 or confidence_gap >= 0.15:
                incumbent_before = incumbent.to_dict()
                incumbent.status = "contradicted"
                incumbent.consistency_state = "contradicted"
                incumbent.updated_at = now
                self._append_fact_event(
                    incumbent,
                    event_type="contradict",
                    before=incumbent_before,
                    after=incumbent.to_dict(),
                    reason="automatic replace flip",
                    evidence={"challenger_fact_id": challenger.fact_id, "confidence_gap": confidence_gap},
                )

    def _build_retrieval_rankings(
        self,
        records: list[FactRecord],
        *,
        top_k: int,
    ) -> list[FactRetrievalResult]:
        ranked: list[FactRetrievalResult] = []
        for record in records:
            score = record.confidence
            if record.consistency_state == "contested":
                score -= 0.1
            ranked.append(FactRetrievalResult(
                fact=record,
                score=max(0.0, min(1.0, score)),
                source="exact_scope",
                contested_summary=self._contested_summary(record, records),
            ))
        ranked.sort(key=lambda item: (item.score, item.fact.support_count), reverse=True)
        return ranked[:max(1, top_k)]

    def _expand_relation_rankings(
        self,
        ranked: list[FactRetrievalResult],
        *,
        all_records: list[FactRecord],
        include_pending: bool,
        top_k: int,
    ) -> list[FactRetrievalResult]:
        if not ranked:
            return ranked
        statuses = {"active", "pending_confirmation"} if include_pending else {"active"}
        records_by_id = {
            record.fact_id: record
            for record in all_records
            if record.status in statuses
        }
        seen = {item.fact.fact_id for item in ranked}
        expanded = list(ranked)
        for item in ranked:
            related_ids = self.relation_store.related_fact_ids(
                item.fact.fact_id,
                relation_types=("generalizes", "narrows", "implies"),
                depth=1,
                bidirectional=True,
            )
            for related_id in sorted(related_ids):
                related = records_by_id.get(related_id)
                if related is None or related.fact_id in seen:
                    continue
                seen.add(related.fact_id)
                expanded.append(FactRetrievalResult(
                    fact=related,
                    score=max(0.0, min(1.0, related.confidence - 0.05)),
                    source="relation_expansion",
                    summary="related support fact",
                    contested_summary=self._contested_summary(related, all_records),
                ))
                if len(expanded) >= max(1, top_k):
                    break
            if len(expanded) >= max(1, top_k):
                break
        source_rank = {"exact_scope": 2, "relation_expansion": 1}
        expanded.sort(
            key=lambda result: (
                source_rank.get(result.source, 0),
                result.score,
                result.fact.support_count,
            ),
            reverse=True,
        )
        return expanded[:max(1, top_k)]

    def _derive_confidence(
        self,
        record: FactRecord,
        *,
        now: datetime,
        factor: float | None = None,
        min_confidence: float | None = None,
        decay_start_days: int | None = None,
    ) -> float:
        base_confidence = _normalize_confidence(record.base_confidence)
        last_seen = _parse_datetime(record.effective_last_seen_at()) or now
        grace = self.config.decay_start_days if decay_start_days is None else max(0, int(decay_start_days))
        idle_days = _days_between(last_seen, now)
        stale_penalty = 0.0
        if idle_days > grace:
            stale_penalty = (idle_days - grace) / 45.0
        alpha = (
            1
            + 4 * base_confidence
            + 1.0 * max(1, record.support_count)
            + min(record.injection_count, 8) * 0.25
        )
        beta = (
            1
            + 4 * (1 - base_confidence)
            + 1.25 * record.contradiction_count
            + stale_penalty
        )
        confidence = alpha / (alpha + beta)
        min_value = self.config.min_confidence if min_confidence is None else min_confidence
        return max(_normalize_confidence(min_value), min(0.995, confidence))

    def _reinforce_record(
        self,
        record: FactRecord,
        *,
        source_cursors: list[int],
        source_excerpt: str,
        base_confidence: float,
        updated_at: str,
        expires_at: str | None,
        supersedes_fact_id: str | None,
        explicit_status: str | None,
        explicit_requires_confirmation: bool | None,
    ) -> None:
        record.updated_at = updated_at
        record.last_seen_at = updated_at
        record.last_supported_at = updated_at
        record.source_cursors = _merge_source_cursors(record.source_cursors, source_cursors)
        if source_excerpt:
            record.source_excerpt = source_excerpt
        record.base_confidence = max(record.base_confidence, base_confidence)
        record.support_count += 1
        if expires_at is not None:
            record.expires_at = expires_at
        if supersedes_fact_id is not None:
            record.supersedes_fact_id = supersedes_fact_id
        if explicit_status is not None:
            record.status = explicit_status
        if explicit_requires_confirmation is not None:
            record.requires_confirmation = explicit_requires_confirmation
        if self.flag_enabled("confidence_v2_enabled"):
            record.confidence = self._derive_confidence(
                record,
                now=_parse_datetime(updated_at) or datetime.now(timezone.utc),
            )
        else:
            record.confidence = max(record.confidence, _normalize_confidence(base_confidence))

    def _semantic_resolution_for_new_fact(
        self,
        records: list[FactRecord],
        *,
        content: str,
        category: str,
        scope: str,
        owner: str,
        source_cursors: list[int],
        source_excerpt: str,
        confidence: float,
        status: str,
        requires_confirmation: bool,
        supersedes_fact_id: str | None,
        now: str,
        change_kind: str | None = None,
        target_fact_id: str | None = None,
        relation_candidates: list[dict[str, Any]] | None = None,
        semantic_scope_hint: str | None = None,
        proposal_id: str | None = None,
        review_event_id: str | None = None,
        batch_id: str = "runtime",
        actor: str = "system",
        origin: str = "fact_store",
        model_info: dict[str, Any] | None = None,
    ) -> FactRecord | None:
        proposal = FactProposal(
            content=content,
            category=category,
            scope=scope,
            owner=owner,
            source_cursors=source_cursors,
            source_excerpt=source_excerpt,
            confidence=confidence,
            supersedes_fact_id=supersedes_fact_id,
            requires_confirmation=requires_confirmation,
            status=status,
            reason="",
            change_kind=change_kind,
            target_fact_id=target_fact_id,
            relation_candidates=relation_candidates or [],
            semantic_scope_hint=semantic_scope_hint,
        )
        if not self.flag_enabled("semantic_merge_enabled") and not self.flag_enabled("fact_graph_enabled"):
            return None
        candidates = self.semantic_resolver.candidate_search(proposal, records)
        if not candidates:
            return None
        candidate, similarity = candidates[0]
        relation_type = self.semantic_resolver.classify_relation(proposal, candidate, similarity)
        if (
            similarity >= 0.92
            and relation_type == "equivalent"
            and category not in HIGH_RISK_CATEGORIES
        ):
            before = candidate.to_dict()
            self._reinforce_record(
                candidate,
                source_cursors=source_cursors,
                source_excerpt=source_excerpt,
                base_confidence=confidence,
                updated_at=now,
                expires_at=None,
                supersedes_fact_id=supersedes_fact_id,
                explicit_status=None,
                explicit_requires_confirmation=None,
            )
            self._upsert_relation(
                source_fact_id=candidate.fact_id,
                target_fact_id=candidate.fact_id,
                relation_type="equivalent",
                confidence=similarity,
                origin="semantic_resolver",
                evidence={"semantic_text_hash": candidate.semantic_text_hash},
            )
            self._append_fact_event(
                candidate,
                event_type="merge",
                before=before,
                after=candidate.to_dict(),
                reason="semantic equivalent merge",
                evidence={"similarity": similarity},
                proposal_id=proposal_id,
                review_event_id=review_event_id,
                batch_id=batch_id,
                actor=actor,
                origin=origin,
                model_info=model_info,
            )
            self.semantic_resolver.record_pending_index(candidate)
            return candidate
        if similarity >= 0.80 and self.flag_enabled("fact_graph_enabled"):
            self._upsert_relation(
                source_fact_id=target_fact_id or candidate.fact_id,
                target_fact_id=candidate.fact_id,
                relation_type=relation_type,
                confidence=similarity,
                origin=origin,
                evidence={
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "semantic_scope_hint": semantic_scope_hint or "",
                },
            )
        if (
            category in CONFLICT_CATEGORIES
            and candidate.status == "active"
            and relation_type in {"related_to", "generalizes", "narrows"}
            and normalize_fact_content(candidate.content) != normalize_fact_content(content)
        ):
            challenger = self._new_pending_challenger(
                records,
                content=content,
                category=category,
                scope=scope,
                owner=owner,
                source_cursors=source_cursors,
                source_excerpt=source_excerpt,
                confidence=confidence,
                expires_at=None,
                requires_confirmation=True,
                status="pending_confirmation",
                supersedes_fact_id=supersedes_fact_id,
                now=now,
            )
            self._mark_contested_pair(
                incumbent=candidate,
                challenger=challenger,
                now=now,
                reason=f"semantic contradiction candidate ({relation_type})",
                auto_flip=bool(
                    self.flag_enabled("contradiction_auto_flip_enabled")
                    and str(change_kind or "").strip().lower() in {"replace", "contradict"}
                ),
            )
            self._append_fact_event(
                challenger,
                event_type="create",
                before={},
                after=challenger.to_dict(),
                reason="challenger fact created",
                evidence={"similarity": similarity, "relation_type": relation_type},
                proposal_id=proposal_id,
                review_event_id=review_event_id,
                batch_id=batch_id,
                actor=actor,
                origin=origin,
                model_info=model_info,
            )
            if self.flag_enabled("fact_graph_enabled"):
                self._upsert_relation(
                    source_fact_id=challenger.fact_id,
                    target_fact_id=candidate.fact_id,
                    relation_type="contradicts",
                    confidence=similarity,
                    origin=origin,
                    evidence={"semantic_scope_hint": semantic_scope_hint or ""},
                )
            if relation_candidates:
                self._persist_explicit_relations(
                    record=challenger,
                    relation_candidates=relation_candidates,
                    target_fact_id=target_fact_id or candidate.fact_id,
                    origin=origin,
                )
            self.semantic_resolver.record_pending_index(challenger)
            return challenger
        return None

    def _upsert_relation(
        self,
        *,
        source_fact_id: str,
        target_fact_id: str,
        relation_type: str,
        confidence: float,
        origin: str,
        evidence: dict[str, Any],
    ) -> FactRelationRecord | None:
        if not self.flag_enabled("fact_graph_enabled"):
            return None
        if not source_fact_id or not target_fact_id:
            return None
        if source_fact_id == target_fact_id and relation_type != "equivalent":
            return None
        now = datetime.now(timezone.utc).isoformat()
        relation = FactRelationRecord(
            relation_id=f"relation_{uuid.uuid4().hex[:12]}",
            source_fact_id=source_fact_id,
            target_fact_id=target_fact_id,
            relation_type=relation_type,
            confidence=_normalize_confidence(confidence),
            origin=origin,
            created_at=now,
            updated_at=now,
            status="active",
            evidence=evidence,
        )
        return self.relation_store.upsert(relation)

    def _persist_relation_candidates_unlocked(
        self,
        relation_candidates: list[dict[str, Any]],
        *,
        default_source_fact_id: str | None = None,
        default_target_fact_id: str | None = None,
        origin: str,
        content_for_hash: str = "",
        default_confidence: float = 0.7,
        new_fact_id: str | None = None,
    ) -> list[FactRelationRecord]:
        persisted: list[FactRelationRecord] = []
        if not self.flag_enabled("fact_graph_enabled") or not relation_candidates:
            return persisted
        for item in relation_candidates:
            if not isinstance(item, dict):
                continue
            relation_type_raw = item.get("relation_type", item.get("relation_kind"))
            try:
                relation_type = _normalize_relation_type(relation_type_raw)
            except ValueError:
                continue
            if relation_type not in {"contradicts", "generalizes", "narrows"}:
                continue
            source_fact_id = _optional_record_string(item.get("source_fact_id"))
            candidate_target_fact_id = _optional_record_string(item.get("target_fact_id"))
            pair = item.get("fact_pair")
            if (
                (not source_fact_id or not candidate_target_fact_id)
                and isinstance(pair, list)
                and len(pair) == 2
                and all(isinstance(value, str) and value.strip() for value in pair)
            ):
                source_fact_id = source_fact_id or str(pair[0]).strip()
                candidate_target_fact_id = candidate_target_fact_id or str(pair[1]).strip()
            if not source_fact_id:
                source_fact_id = default_source_fact_id
            if source_fact_id == "__new_fact__":
                source_fact_id = new_fact_id or default_source_fact_id
            if not candidate_target_fact_id:
                candidate_target_fact_id = default_target_fact_id
            if candidate_target_fact_id == "__new_fact__":
                candidate_target_fact_id = new_fact_id or default_target_fact_id
            if not source_fact_id or not candidate_target_fact_id:
                continue
            confidence = _normalize_confidence(
                item.get("confidence", item.get("relation_confidence", default_confidence))
            )
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            if content_for_hash and "content_hash" not in evidence:
                evidence = {
                    **evidence,
                    "content_hash": hashlib.sha256(content_for_hash.encode("utf-8")).hexdigest(),
                }
            relation = self._upsert_relation(
                source_fact_id=source_fact_id,
                target_fact_id=candidate_target_fact_id,
                relation_type=relation_type,
                confidence=confidence,
                origin=str(item.get("origin") or origin or "fact_store").strip() or "fact_store",
                evidence=evidence,
            )
            if relation is not None:
                persisted.append(relation)
        return persisted

    def _persist_explicit_relations(
        self,
        *,
        record: FactRecord,
        relation_candidates: list[dict[str, Any]],
        target_fact_id: str | None,
        origin: str,
    ) -> list[FactRelationRecord]:
        return self._persist_relation_candidates_unlocked(
            relation_candidates,
            default_source_fact_id=record.fact_id,
            default_target_fact_id=target_fact_id,
            origin=origin,
            content_for_hash=record.content,
            default_confidence=record.confidence,
            new_fact_id=record.fact_id,
        )

    def _append_fact_event(
        self,
        record: FactRecord,
        *,
        event_type: str,
        before: dict[str, Any],
        after: dict[str, Any],
        reason: str,
        evidence: dict[str, Any],
        proposal_id: str | None = None,
        review_event_id: str | None = None,
        batch_id: str = "runtime",
        actor: str = "system",
        origin: str = "fact_store",
        model_info: dict[str, Any] | None = None,
    ) -> None:
        if not self.flag_enabled("fact_audit_enabled"):
            return
        before_summary = _redacted_fact_snapshot(before)
        after_summary = _redacted_fact_snapshot(after)
        self.event_store.append(FactEventRecord(
            event_id=f"fact_event_{uuid.uuid4().hex}",
            fact_id=record.fact_id,
            event_type=event_type,
            actor=actor,
            origin=origin,
            batch_id=batch_id,
            proposal_id=proposal_id,
            review_event_id=review_event_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            before=before_summary,
            after=after_summary,
            reason=reason,
            evidence=evidence,
            model_info=model_info or {},
        ))

    def _contested_summary(
        self,
        record: FactRecord,
        records: list[FactRecord],
    ) -> str:
        if record.consistency_state != "contested":
            return ""
        conflicts = [
            other.content
            for other in records
            if other.fact_id != record.fact_id
            and other.category == record.category
            and other.scope == record.scope
            and normalize_fact_content(other.content) != normalize_fact_content(record.content)
        ]
        if not conflicts:
            return ""
        return f"contested by {len(conflicts)} alternative fact(s)"


def _default_redactor(text: str) -> str:
    from OriginAgent.agent.memory import redact_memory_text

    return redact_memory_text(text)


def _extract_json_text(text: str) -> str:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    return stripped


def _parse_fact_proposal(raw: Any) -> FactProposal:
    if not isinstance(raw, dict):
        raise ValueError("fact proposal must be an object")
    content = _required_str(raw, "content")
    category = _required_str(raw, "category")
    scope = _required_str(raw, "scope")
    owner = _required_str(raw, "owner")
    source_cursors = _parse_cursor_list(raw.get("source_cursors", []), "source_cursors")
    source_excerpt = _optional_str(raw, "source_excerpt", "")
    confidence = _optional_float(raw, "confidence", 0.7)
    expires_at = _optional_nullable_str(raw, "expires_at")
    supersedes_fact_id = _optional_nullable_str(raw, "supersedes_fact_id")
    status = _optional_nullable_str(raw, "status")
    requires_confirmation = raw.get("requires_confirmation")
    if requires_confirmation is not None and not isinstance(requires_confirmation, bool):
        raise ValueError("requires_confirmation must be bool or null")
    reason = _optional_str(raw, "reason", "")
    change_kind = _optional_nullable_str(raw, "change_kind")
    target_fact_id = _optional_nullable_str(raw, "target_fact_id")
    semantic_scope_hint = _optional_nullable_str(raw, "semantic_scope_hint")
    relation_candidates = raw.get("relation_candidates", [])
    if not isinstance(relation_candidates, list):
        raise ValueError("relation_candidates must be a list")
    normalized_relation_candidates: list[dict[str, Any]] = []
    for item in relation_candidates:
        if not isinstance(item, dict):
            raise ValueError("relation_candidates entries must be objects")
        normalized_relation_candidates.append(dict(item))
    return FactProposal(
        content=content,
        category=category,
        scope=scope,
        owner=owner,
        source_cursors=source_cursors,
        source_excerpt=source_excerpt,
        confidence=confidence,
        expires_at=expires_at,
        supersedes_fact_id=supersedes_fact_id,
        requires_confirmation=requires_confirmation,
        status=status,
        reason=reason,
        change_kind=change_kind,
        target_fact_id=target_fact_id,
        relation_candidates=normalized_relation_candidates,
        semantic_scope_hint=semantic_scope_hint,
    )


def _parse_deprecation_proposal(raw: Any) -> FactDeprecationProposal:
    if not isinstance(raw, dict):
        raise ValueError("deprecation proposal must be an object")
    return FactDeprecationProposal(
        fact_id=_required_str(raw, "fact_id"),
        reason=_optional_str(raw, "reason", ""),
        source_cursors=_parse_cursor_list(raw.get("source_cursors", []), "source_cursors"),
    )


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_str(raw: dict[str, Any], key: str, default: str) -> str:
    value = raw.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value.strip()


def _optional_nullable_str(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value.strip() or None


def _optional_float(raw: dict[str, Any], key: str, default: float) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be numeric")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be numeric") from exc


def _parse_cursor_list(value: Any, key: str) -> list[int]:
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    cursors: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"{key} must contain integer cursors")
        if item > 0:
            cursors.append(item)
    return sorted(set(cursors))


def _history_cursor_set(history_entries: list[dict[str, Any]]) -> set[int]:
    cursors: set[int] = set()
    for entry in history_entries:
        raw = entry.get("cursor")
        if isinstance(raw, bool) or not isinstance(raw, int):
            continue
        cursors.add(raw)
    return cursors


def _source_excerpt_matches_history(
    proposal: FactProposal,
    history_entries: list[dict[str, Any]],
) -> bool:
    by_cursor = {
        entry.get("cursor"): str(entry.get("content", ""))
        for entry in history_entries
    }
    excerpt = _normalize_excerpt_for_match(proposal.source_excerpt)
    if not excerpt:
        return False
    for cursor in proposal.source_cursors:
        content = _normalize_excerpt_for_match(by_cursor.get(cursor, ""))
        if excerpt in content:
            return True
    return False


def _normalize_excerpt_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().casefold())


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lower = text.casefold()
    return any(needle.casefold() in lower for needle in needles)


def _requires_high_risk_confirmation(*, scope: str, content: str) -> bool:
    return _infer_domain_key(scope, content) in HIGH_RISK_DEVICE_DOMAINS


def _fact_domain_key(record: FactRecord) -> str:
    domain = _infer_domain_key(record.scope, record.content)
    if domain and domain != "general":
        return domain
    scope_head = record.scope.split(".", 1)[0].strip().lower()
    return scope_head or "general"


def _infer_domain_key(scope: str, content: str) -> str:
    text = f"{scope or ''} {content or ''}".casefold()
    for domain in HIGH_RISK_DEVICE_DOMAINS:
        if domain in text:
            return domain
    parts = [part for part in str(scope or "").strip().lower().split(".") if part]
    return parts[0] if parts else "general"


def _issue(code: str, severity: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, message=message)


def _normalize_category(category: str | None) -> str:
    normalized = (category or "note").strip().lower()
    if normalized not in VALID_CATEGORIES:
        raise ValueError(f"invalid fact category: {category!r}")
    return normalized


def _normalize_owner(owner: str | None) -> str:
    normalized = (owner or "unknown").strip().lower()
    if normalized not in VALID_OWNERS:
        raise ValueError(f"invalid fact owner: {owner!r}")
    return normalized


def _normalize_scope(scope: str | None) -> str:
    normalized = (scope or "general").strip().lower()
    if not normalized:
        normalized = "general"
    return re.sub(r"\s+", ".", normalized)


def _normalize_status(status: str | None) -> str:
    normalized = (status or "active").strip().lower()
    if normalized not in VALID_STATUSES:
        raise ValueError(f"invalid fact status: {status!r}")
    return normalized


def _normalize_consistency_state(value: Any) -> str:
    normalized = str(value or DEFAULT_CONSISTENCY_STATE).strip().lower()
    if normalized not in VALID_CONSISTENCY_STATES:
        raise ValueError(f"invalid consistency_state: {value!r}")
    return normalized


def _normalize_confidence_version(value: Any) -> str:
    normalized = str(value or DEFAULT_CONFIDENCE_VERSION).strip().lower()
    if normalized not in VALID_CONFIDENCE_VERSIONS:
        raise ValueError(f"invalid confidence_version: {value!r}")
    return normalized


def _normalize_relation_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in VALID_RELATION_TYPES:
        raise ValueError(f"invalid relation_type: {value!r}")
    return normalized


def _normalize_relation_status(value: Any) -> str:
    normalized = str(value or "active").strip().lower()
    if normalized not in VALID_RELATION_STATUSES:
        raise ValueError(f"invalid relation status: {value!r}")
    return normalized


def _normalize_semantic_text_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        raise ValueError("semantic_text_hash cannot be empty")
    return text


def _normalize_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be numeric") from exc
    return max(0.0, min(1.0, confidence))


def _normalize_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("expected nonnegative integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("expected nonnegative integer") from exc
    return max(0, number)


def _normalize_decay_factor(value: Any) -> float:
    try:
        factor = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence decay factor must be numeric") from exc
    return max(0.0, min(1.0, factor))


def _usage_decay_multiplier(record: FactRecord, *, now: datetime) -> float:
    retrieval_count = max(0, int(record.retrieval_count))
    injection_count = max(0, int(record.injection_count))
    if retrieval_count < 3 and injection_count < 2:
        return 1.0

    retrieval_weight = min(retrieval_count, 12) / 12.0
    injection_weight = min(injection_count, 8) / 8.0
    usage_score = (retrieval_weight * 0.6) + (injection_weight * 0.4)

    last_retrieved = _parse_datetime(record.last_retrieved_at or "")
    if last_retrieved is None:
        recency_multiplier = 1.0
    else:
        retrieval_idle_days = _days_between(last_retrieved, now)
        if retrieval_idle_days <= 7:
            recency_multiplier = 0.7
        elif retrieval_idle_days <= 30:
            recency_multiplier = 0.85
        else:
            recency_multiplier = 1.0
    return max(0.5, 1.0 - (usage_score * 0.35)) * recency_multiplier


def _parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _optional_record_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expected string or null")
    text = value.strip()
    return text or None


def _scope_family_match(left: str | None, right: str | None) -> bool:
    left_scope = _normalize_scope(left)
    right_scope = _normalize_scope(right)
    return (
        left_scope == right_scope
        or left_scope.startswith(f"{right_scope}.")
        or right_scope.startswith(f"{left_scope}.")
    )


def _days_between(start: datetime, end: datetime) -> int:
    if start.tzinfo is not None and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    elif start.tzinfo is None and end.tzinfo is not None:
        start = start.replace(tzinfo=end.tzinfo)
    delta = end - start
    return max(0, delta.days)


def _calibration_key(proposal_type: str, domain_id: str) -> str:
    proposal = str(proposal_type or "").strip().lower()
    domain = str(domain_id or "").strip().lower()
    if not proposal or not domain:
        return ""
    return f"{proposal}:{domain}"


def _calibration_keys(
    source: str,
    category: str,
    proposal_type: str,
    domain_id: str,
) -> list[str]:
    normalized_source = str(source or "dream").strip().lower() or "dream"
    normalized_category = _normalize_category(category)
    normalized_proposal = str(proposal_type or "fact").strip().lower() or "fact"
    normalized_domain = str(domain_id or "general").strip().lower() or "general"
    keys = [
        f"{normalized_source}:{normalized_category}:{normalized_domain}",
        f"{normalized_source}:{normalized_category}:{normalized_proposal}:{normalized_domain}",
        f"{normalized_source}:{normalized_domain}",
        f"{normalized_source}",
        _calibration_key(normalized_proposal, normalized_domain),
    ]
    deduped: list[str] = []
    for key in keys:
        if key and key not in deduped:
            deduped.append(key)
    return deduped


def _int_value(value: Any, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_value(value: Any, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _redacted_fact_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    snapshot: dict[str, Any] = {}
    for key in (
        "fact_id",
        "canonical_key",
        "category",
        "scope",
        "owner",
        "status",
        "consistency_state",
        "confidence",
        "base_confidence",
        "confidence_version",
        "support_count",
        "contradiction_count",
        "retrieval_count",
        "injection_count",
        "created_at",
        "updated_at",
        "last_seen_at",
        "last_supported_at",
        "last_contested_at",
        "last_retrieved_at",
        "expires_at",
        "supersedes_fact_id",
        "requires_confirmation",
        "semantic_text_hash",
    ):
        if key in raw:
            snapshot[key] = raw[key]
    if isinstance(raw.get("content"), str):
        redacted_content = _default_redactor(raw["content"])
        snapshot["content_hash"] = hashlib.sha256(
            normalize_fact_content(redacted_content).encode("utf-8")
        ).hexdigest()
        snapshot["content_preview"] = redact_memory_preview(redacted_content)
    if isinstance(raw.get("source_excerpt"), str) and raw["source_excerpt"].strip():
        redacted_excerpt = _default_redactor(raw["source_excerpt"])
        snapshot["source_excerpt_hash"] = hashlib.sha256(
            redacted_excerpt.encode("utf-8")
        ).hexdigest()
        snapshot["source_excerpt_preview"] = redact_memory_preview(redacted_excerpt)
    return snapshot


def redact_memory_preview(text: str, *, limit: int = 120) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _normalize_source_cursors(value: list[int] | Any | None) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("source_cursors must be a list")
    cursors: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            continue
        if item > 0:
            cursors.add(item)
    return sorted(cursors)


def _merge_source_cursors(existing: list[int], incoming: list[int]) -> list[int]:
    return sorted(set(existing) | set(incoming))


def _resolve_status_and_confirmation(
    *,
    category: str,
    status: str | None,
    requires_confirmation: bool | None,
    config: FactStoreConfig = DEFAULT_FACT_STORE_CONFIG,
) -> tuple[str, bool]:
    if requires_confirmation is not None and not isinstance(requires_confirmation, bool):
        raise ValueError("requires_confirmation must be bool")
    requested_status = _normalize_status(status) if status is not None else None
    if category in set(config.high_risk_categories):
        if requires_confirmation is False and requested_status == "active":
            return "active", False
        if requested_status in {"deprecated", "contradicted"}:
            return requested_status, True
        return "pending_confirmation", True
    return requested_status or "active", bool(requires_confirmation)


def _section_title(category: str) -> str:
    return {
        "preference": "Preferences",
        "routine": "Routines",
        "household": "Household",
        "device": "Devices",
        "policy": "Policies",
        "safety": "Safety",
        "temporary": "Temporary",
        "note": "Notes",
    }[category]


def _render_fact_lines(
    fact: FactRecord,
    *,
    include_category: bool = False,
) -> list[str]:
    content_lines = fact.content.splitlines() or [""]
    lines = [f"- {content_lines[0]}"]
    for line in content_lines[1:]:
        lines.append(f"  {line}")
    if include_category:
        lines.append(f"  - category: {fact.category}")
    lines.append(f"  - scope: {fact.scope}")
    lines.append(f"  - confidence: {_format_confidence(fact.confidence)}")
    if fact.expires_at:
        lines.append(f"  - expires_at: {fact.expires_at}")
    if fact.consistency_state == "contested":
        lines.append("  - contested: this fact has active competing evidence")
    source = _format_source(fact.source_cursors)
    if source:
        lines.append(f"  - source: {source}")
    return lines


def _format_confidence(value: float) -> str:
    if value == int(value):
        return f"{value:.1f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _format_source(source_cursors: list[int]) -> str:
    recent = source_cursors[-3:]
    if not recent:
        return ""
    if len(recent) == 1:
        return f"cursor {recent[0]}"
    return "cursors " + ", ".join(str(cursor) for cursor in recent)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        _fsync_parent(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _fsync_parent(path: Path) -> None:
    with suppress(PermissionError, OSError):
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
