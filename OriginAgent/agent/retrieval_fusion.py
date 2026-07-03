"""Minimal multi-source retrieval fusion for continuity P3A."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from OriginAgent.agent.facts import FactRetrievalBundle
from OriginAgent.agent.memory import MemoryStore
from OriginAgent.agent.scope import ScopeResolver
from OriginAgent.memory.policy import nearline_runtime_enabled
from OriginAgent.memory.retrieval import NearlineRetrievalResult
from OriginAgent.session.search import SessionSearchService
from OriginAgent.utils.helpers import truncate_text


_NORMALIZE_RE = re.compile(r"\s+")


def _normalize_text(value: str) -> str:
    return _NORMALIZE_RE.sub(" ", str(value or "").strip()).casefold()


def _parse_timestamp(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _string_list(values: Any, *, limit: int = 8) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


@dataclass(frozen=True)
class RetrievalHit:
    source: str
    title: str
    text: str
    scope: str | None = None
    owner_id: str | None = None
    timestamp: str | None = None
    confidence: float = 0.0
    score: float = 0.0
    dedupe_key: str | None = None
    locator: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def to_audit(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "title": self.title,
            "scope": self.scope,
            "owner_id": self.owner_id,
            "timestamp": self.timestamp,
            "confidence": round(self.confidence, 4),
            "score": round(self.score, 4),
            "locator": dict(self.locator),
            "text_preview": truncate_text(self.text, 220),
            "details": dict(self.details),
            "dedupe_key": self.dedupe_key,
        }


@dataclass(frozen=True)
class RetrievalBlock:
    source: str
    text: str
    hits: list[RetrievalHit] = field(default_factory=list)

    def to_audit(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "text_preview": truncate_text(self.text, 220),
            "hit_count": len(self.hits),
            "hits": [hit.to_audit() for hit in self.hits],
        }


@dataclass(frozen=True)
class RetrievalFusionResult:
    retrieved_blocks: list[RetrievalBlock] = field(default_factory=list)
    source_hits: dict[str, list[RetrievalHit]] = field(default_factory=dict)
    audit: dict[str, Any] = field(default_factory=dict)


class RetrievalFusion:
    """Read-only retrieval fusion over facts, nearline, and session search."""

    _SOURCE_ORDER = {
        "fact_store": 0,
        "prewarm_seed": 1,
        "nearline_retrieval": 2,
        "memory_candidates": 3,
        "session_search": 4,
    }
    _BLOCK_SOURCE_NAME = {
        "fact_store": "memory_retrieval",
        "prewarm_seed": "retrieval_prewarm_seed",
        "nearline_retrieval": "layered_memory",
        "memory_candidates": "memory_candidates",
        "session_search": "retrieval_session_search",
    }
    _SESSION_SEARCH_SOURCES = ("sessions", "history", "webui")
    _MEMORY_BLOCK_SOURCES = ("memory_candidates",)

    def __init__(
        self,
        workspace: Path,
        *,
        memory: MemoryStore,
        nearline_memory: Any,
        session_search: SessionSearchService,
        context_config: Any,
        nearline_memory_config: Any | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.memory = memory
        self.nearline_memory = nearline_memory
        self.session_search = session_search
        self.context_config = context_config
        self.nearline_memory_config = nearline_memory_config

    def retrieve(
        self,
        *,
        query: str | None,
        session_key: str | None,
        runtime_context: Any | None,
        current_message: str | None,
        recent_history: Iterable[dict[str, Any]] | None,
        session_summary: str | None,
        prewarm_seed: list[dict[str, Any]] | None = None,
        working_memory_hints: list[str] | None = None,
        world_summary_hints: list[str] | None = None,
    ) -> RetrievalFusionResult:
        del query, session_summary
        hint_segments = [
            str(current_message or "").strip(),
            *[str(item or "").strip() for item in (working_memory_hints or [])],
            *[str(item or "").strip() for item in (world_summary_hints or [])],
        ]
        message = "\n".join(segment for segment in hint_segments if segment)
        current_scope = str(getattr(runtime_context, "default_scope", "session") or "session")
        current_owner_id = str(getattr(runtime_context, "user_id", "") or "") or None
        current_device_id = str(getattr(runtime_context, "device_id", "") or "") or None
        scope_prefix = "user" if current_scope == "user" else None

        fact_bundle = self.memory.fact_store.retrieve_context_bundle(
            scope_prefix=scope_prefix,
        )
        fallback_bundle = self.memory.get_memory_context_bundle(scope_prefix=scope_prefix)
        nearline_result = (
            self.nearline_memory.retrieve(
                query=message or None,
                recent_history=recent_history,
            )
            if nearline_runtime_enabled(self.nearline_memory_config)
            else NearlineRetrievalResult()
        )
        search_result = (
            self.session_search.search(
                query=message,
                sources=self._SESSION_SEARCH_SOURCES,
                session_key=session_key,
                limit=max(1, int(getattr(self.context_config, "max_retrieval_hits_per_source", 4) or 4)),
                mode="hybrid",
            )
            if message
            else {"results": [], "searched_sources": [], "mode": "hybrid"}
        )
        memory_block_result = (
            self.session_search.search(
                query=message,
                sources=self._MEMORY_BLOCK_SOURCES,
                session_key=session_key,
                limit=max(1, int(getattr(self.context_config, "max_retrieval_hits_per_source", 4) or 4)),
                mode="hybrid",
                result_shape="memory_blocks",
            )
            if message
            else {"results": [], "searched_sources": [], "mode": "hybrid"}
        )

        source_hits: dict[str, list[RetrievalHit]] = {
            "fact_store": self._fact_hits(fact_bundle, fallback_bundle=fallback_bundle),
            "prewarm_seed": self._prewarm_hits(prewarm_seed),
            "nearline_retrieval": self._nearline_hits(nearline_result),
            "memory_candidates": self._memory_block_hits(memory_block_result),
            "session_search": self._session_search_hits(search_result),
        }
        raw_source_counts = {source: len(hits) for source, hits in source_hits.items()}

        visible_hits: dict[str, list[RetrievalHit]] = {}
        scope_filtered: list[dict[str, Any]] = []
        for source, hits in source_hits.items():
            kept: list[RetrievalHit] = []
            for hit in hits:
                hidden_reason = self._scope_hidden_reason(
                    hit,
                    current_scope=current_scope,
                    current_owner_id=current_owner_id,
                    current_device_id=current_device_id,
                )
                if hidden_reason is not None:
                    scope_filtered.append({
                        "source": source,
                        "title": hit.title,
                        "reason": hidden_reason,
                    })
                    continue
                kept.append(hit)
            visible_hits[source] = kept

        trimmed_by_source = self._trim_by_source(visible_hits)
        deduped_hits, deduped_count = self._dedupe(trimmed_by_source)
        blocks, trimmed_global = self._build_blocks(deduped_hits)

        audit = {
            "enabled": True,
            "query": message,
            "sources_used": [source for source, hits in source_hits.items() if hits],
            "source_counts": {source: len(hits) for source, hits in deduped_hits.items()},
            "raw_source_counts": raw_source_counts,
            "trimmed_by_source": trimmed_by_source["trimmed"],
            "deduped_count": deduped_count,
            "trimmed_count": len(trimmed_by_source["trimmed"]) + trimmed_global,
            "trimmed_global_count": trimmed_global,
            "scope_filtered": scope_filtered,
            "hits": {
                source: [hit.to_audit() for hit in hits]
                for source, hits in deduped_hits.items()
            },
            "blocks": [block.to_audit() for block in blocks],
            "session_search": {
                "mode": search_result.get("mode"),
                "searched_sources": list(search_result.get("searched_sources") or []),
                "performance_note": search_result.get("performance_note"),
                "index_stale": bool(search_result.get("index_stale")),
                "index_refresh_running": bool(search_result.get("index_refresh_running")),
            },
            "memory_candidate_search": {
                "mode": memory_block_result.get("mode"),
                "searched_sources": list(memory_block_result.get("searched_sources") or []),
                "performance_note": memory_block_result.get("performance_note"),
            },
        }
        return RetrievalFusionResult(
            retrieved_blocks=blocks,
            source_hits=deduped_hits,
            audit=audit,
        )

    def _fact_hits(
        self,
        bundle: FactRetrievalBundle,
        *,
        fallback_bundle: FactRetrievalBundle,
    ) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        if bundle.rendered_text.strip():
            hits.append(
                RetrievalHit(
                    source="fact_store",
                    title="facts",
                    text=bundle.rendered_text,
                    scope="user",
                    owner_id=None,
                    timestamp=None,
                    confidence=0.8,
                    score=0.8,
                    dedupe_key=_normalize_text("\n".join(fact.content for fact in bundle.facts)),
                    locator={"kind": "fact_bundle"},
                    details={"bundle_kind": "semantic"},
                )
            )
            return hits
        if fallback_bundle.rendered_text.strip():
            hits.append(
                RetrievalHit(
                    source="fact_store",
                    title="facts",
                    text=fallback_bundle.rendered_text,
                    scope="user",
                    owner_id=None,
                    timestamp=None,
                    confidence=0.5,
                    score=0.5,
                    dedupe_key=_normalize_text(fallback_bundle.rendered_text),
                    locator={"kind": "memory_md"},
                    details={"bundle_kind": "fallback"},
                )
            )
            return hits
        for item in bundle.retrievals:
            fact = item.fact
            hits.append(
                RetrievalHit(
                    source="fact_store",
                    title=fact.category or "fact",
                    text=fact.content,
                    scope=fact.scope,
                    owner_id=fact.owner,
                    timestamp=fact.updated_at,
                    confidence=float(fact.confidence or 0.0),
                    score=float(item.score or 0.0),
                    dedupe_key=_normalize_text(fact.content),
                    locator={"fact_id": fact.fact_id},
                    details={"status": fact.status, "retrieval_source": item.source},
                )
            )
        return hits

    def _nearline_hits(self, result: NearlineRetrievalResult) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        if result.has_primary_content and result.rendered_text.strip():
            hits.append(
                RetrievalHit(
                    source="nearline_retrieval",
                    title="layered_memory",
                    text=result.rendered_text,
                    scope="session",
                    owner_id=getattr(result.profile, "owner_id", None) if result.profile is not None else None,
                    timestamp=getattr(result.profile, "updated_at", None) if result.profile is not None else None,
                    confidence=0.7,
                    score=0.7,
                    dedupe_key=_normalize_text(
                        "\n".join(
                            line for line in [
                                *(episode.summary or episode.content for episode in result.episodes[:3]),
                                *(foresight.content for foresight in result.foresights[:2]),
                                *((case.task_intent or case.outcome_summary or case.approach) for case in result.agent_cases[:2]),
                                result.profile.summary if result.profile is not None else "",
                            ] if line
                        )
                    ),
                    locator={"kind": "layered_memory"},
                )
            )
            return hits
        for episode in result.episodes:
            hits.append(
                RetrievalHit(
                    source="nearline_retrieval",
                    title="episode",
                    text=episode.summary or episode.content,
                    scope="session",
                    owner_id=getattr(episode, "owner_id", None),
                    timestamp=getattr(episode, "timestamp", None),
                    confidence=0.6,
                    score=0.6 + (_parse_timestamp(getattr(episode, "timestamp", None)) / 10_000_000_000),
                    dedupe_key=_normalize_text(episode.summary or episode.content),
                    locator={"episode_id": episode.episode_id},
                )
            )
        for foresight in result.foresights:
            text = str(foresight.content or "").strip()
            if foresight.start_at or foresight.end_at:
                text = f"{text} ({foresight.start_at or '?'} -> {foresight.end_at or '?'})"
            hits.append(
                RetrievalHit(
                    source="nearline_retrieval",
                    title="foresight",
                    text=text,
                    scope="session",
                    owner_id=getattr(foresight, "owner_id", None),
                    timestamp=getattr(foresight, "timestamp", None),
                    confidence=0.55,
                    score=0.55 + (_parse_timestamp(getattr(foresight, "timestamp", None)) / 10_000_000_000),
                    dedupe_key=_normalize_text(foresight.content),
                    locator={"foresight_id": foresight.foresight_id},
                )
            )
        for case in result.agent_cases:
            summary = " | ".join(
                part for part in (case.task_intent, case.outcome_summary or case.approach) if part
            )
            hits.append(
                RetrievalHit(
                    source="nearline_retrieval",
                    title="agent_case",
                    text=summary,
                    scope="session",
                    owner_id=None,
                    timestamp=getattr(case, "timestamp", None),
                    confidence=float(min(max(case.quality_score, 0.0), 1.0)),
                    score=0.5 + float(min(max(case.quality_score, 0.0), 1.0)),
                    dedupe_key=_normalize_text(summary),
                    locator={"case_id": case.case_id},
                )
            )
        if result.profile is not None:
            profile_lines = [result.profile.summary, *_string_list(result.profile.explicit_traits, limit=3)]
            hits.append(
                RetrievalHit(
                    source="nearline_retrieval",
                    title="profile",
                    text="\n".join(line for line in profile_lines if line),
                    scope="user",
                    owner_id=getattr(result.profile, "owner_id", None),
                    timestamp=getattr(result.profile, "updated_at", None),
                    confidence=0.7,
                    score=0.7 + (_parse_timestamp(getattr(result.profile, "updated_at", None)) / 10_000_000_000),
                    dedupe_key=_normalize_text(result.profile.summary),
                    locator={"profile_id": result.profile.profile_id},
                )
            )
        return hits

    def _session_search_hits(self, result: dict[str, Any]) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for row in result.get("results", []) or []:
            if not isinstance(row, dict):
                continue
            hits.append(
                RetrievalHit(
                    source="session_search",
                    title=str(row.get("source") or "session_search"),
                    text=str(row.get("snippet") or "").strip(),
                    scope="session",
                    owner_id=None,
                    timestamp=str(row.get("timestamp") or "").strip() or None,
                    confidence=float(row.get("score") or 0.0),
                    score=float(row.get("score") or 0.0),
                    dedupe_key=_normalize_text(str(row.get("snippet") or "").strip()),
                    locator=dict(row.get("locator") or {}),
                    details={"match_type": row.get("match_type"), "source_name": row.get("source")},
                )
            )
        return hits

    def _memory_block_hits(self, result: dict[str, Any]) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for row in result.get("results", []) or []:
            if not isinstance(row, dict):
                continue
            summary = str(row.get("summary") or "").strip()
            if not summary:
                continue
            title = str(row.get("title") or row.get("block_type") or "memory_candidate").strip()
            confidence = float(row.get("confidence") or 0.75)
            hits.append(
                RetrievalHit(
                    source="memory_candidates",
                    title=title,
                    text=summary,
                    scope="session",
                    owner_id=None,
                    timestamp=str(row.get("updated_at") or "").strip() or None,
                    confidence=confidence,
                    score=confidence,
                    dedupe_key=_normalize_text(summary),
                    locator={
                        "source_kind": row.get("source_kind"),
                        "supporting_refs": list(row.get("supporting_refs") or []),
                    },
                    details={
                        "block_type": row.get("block_type"),
                        "session_key": row.get("session_key"),
                        "staleness": row.get("staleness"),
                    },
                )
            )
        return hits

    def _prewarm_hits(self, rows: list[dict[str, Any]] | None) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            hits.append(
                RetrievalHit(
                    source="prewarm_seed",
                    title=str(row.get("title") or "prewarm"),
                    text=text,
                    scope=str(row.get("scope") or "session").strip() or "session",
                    owner_id=str(row.get("owner_id")).strip() if row.get("owner_id") else None,
                    timestamp=str(row.get("timestamp") or "").strip() or None,
                    confidence=float(row.get("confidence") or 0.65),
                    score=float(row.get("score") or 0.65),
                    dedupe_key=_normalize_text(text),
                    locator=dict(row.get("locator") or {}),
                    details=dict(row.get("details") or {}),
                )
            )
        return hits

    def _scope_hidden_reason(
        self,
        hit: RetrievalHit,
        *,
        current_scope: str,
        current_owner_id: str | None,
        current_device_id: str | None,
    ) -> str | None:
        resolver = ScopeResolver()
        normalized_scope = resolver.normalize_scope(hit.scope)
        hit_device_id = str(hit.details.get("device_id") or "").strip() or None
        if not resolver.is_visible(
            scope=hit.scope,
            current_scope=current_scope,
            owner_id=hit.owner_id,
            current_owner_id=current_owner_id,
            device_id=hit_device_id,
            current_device_id=current_device_id,
        ):
            if normalized_scope == "user" and hit.owner_id and current_owner_id and hit.owner_id != current_owner_id:
                return "owner_hidden"
            if normalized_scope == "device" and hit_device_id and current_device_id and hit_device_id != current_device_id:
                return "device_hidden"
            return "scope_hidden"
        return None

    def _trim_by_source(self, source_hits: dict[str, list[RetrievalHit]]) -> dict[str, Any]:
        per_source_limit = max(1, int(getattr(self.context_config, "max_retrieval_hits_per_source", 4) or 4))
        kept: dict[str, list[RetrievalHit]] = {}
        trimmed: list[dict[str, Any]] = []
        for source, hits in source_hits.items():
            ordered = sorted(hits, key=self._sort_key)
            kept[source] = ordered[:per_source_limit]
            for dropped in ordered[per_source_limit:]:
                trimmed.append({"source": source, "title": dropped.title, "reason": "source_budget"})
        return {"kept": kept, "trimmed": trimmed}

    def _dedupe(self, trimmed_by_source: dict[str, Any]) -> tuple[dict[str, list[RetrievalHit]], int]:
        kept = trimmed_by_source["kept"]
        chosen: dict[str, RetrievalHit] = {}
        count = 0
        for source in sorted(kept.keys(), key=lambda item: self._SOURCE_ORDER.get(item, 99)):
            for hit in kept[source]:
                key = _normalize_text(hit.text)
                if hit.dedupe_key:
                    key = hit.dedupe_key
                if not key:
                    continue
                if key in chosen:
                    count += 1
                    continue
                chosen[key] = hit
        grouped: dict[str, list[RetrievalHit]] = {source: [] for source in kept.keys()}
        for hit in chosen.values():
            grouped.setdefault(hit.source, []).append(hit)
        return grouped, count

    def _build_blocks(self, source_hits: dict[str, list[RetrievalHit]]) -> tuple[list[RetrievalBlock], int]:
        max_blocks = max(1, int(getattr(self.context_config, "max_retrieval_blocks", 4) or 4))
        max_chars = max(200, int(getattr(self.context_config, "max_retrieval_chars", 8_000) or 8_000))
        blocks: list[RetrievalBlock] = []
        total_chars = 0
        trimmed = 0
        for source in sorted(source_hits.keys(), key=lambda item: self._SOURCE_ORDER.get(item, 99)):
            hits = source_hits.get(source, [])
            if not hits:
                continue
            if len(hits) == 1 and hits[0].title in {"facts", "layered_memory"}:
                text = hits[0].text
            else:
                lines = [self._block_heading(source)]
                for hit in hits:
                    lines.append(f"- {truncate_text(hit.text, 240)}")
                text = "\n".join(lines)
            projected = total_chars + len(text)
            if blocks and (len(blocks) >= max_blocks or projected > max_chars):
                trimmed += 1
                continue
            if not blocks and projected > max_chars:
                text = truncate_text(text, max_chars)
            blocks.append(
                RetrievalBlock(
                    source=self._BLOCK_SOURCE_NAME.get(source, source),
                    text=text,
                    hits=hits,
                )
            )
            total_chars += len(text)
        return blocks, trimmed

    def _sort_key(self, hit: RetrievalHit) -> tuple[float, float, float, int]:
        return (
            -float(hit.score or 0.0),
            -_parse_timestamp(hit.timestamp),
            -float(hit.confidence or 0.0),
            self._SOURCE_ORDER.get(hit.source, 99),
        )

    @staticmethod
    def _block_heading(source: str) -> str:
        if source == "fact_store":
            return "## Relevant Facts"
        if source == "prewarm_seed":
            return "## Relevant Context (from your recent sessions)"
        if source == "nearline_retrieval":
            return "## Relevant Nearline Memory"
        if source == "memory_candidates":
            return "## Queued Memory Candidates"
        return "## Relevant Session Recall"
