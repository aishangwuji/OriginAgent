"""Minimal roaming prewarm seed preparation for continuity Phase 3."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from OriginAgent.agent.world_state import WorldStateManager


def _trim_text(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


@dataclass(frozen=True)
class PrewarmBundle:
    working_memory_seed: list[str] = field(default_factory=list)
    world_view_seed: list[str] = field(default_factory=list)
    retrieval_seed: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    reason: str = ""
    empty: bool = False

    def audit(self) -> dict[str, Any]:
        return {
            "prewarm_enabled": True,
            "prewarm_empty": self.empty,
            "prewarm_reason": self.reason,
            "prewarm_sources": list(self.sources),
            "prewarm_seeded_items": {
                "working": list(self.working_memory_seed),
                "world": list(self.world_view_seed),
            },
            "prewarm_seed_counts": {
                "working_memory_seed": len(self.working_memory_seed),
                "world_view_seed": len(self.world_view_seed),
                "retrieval_seed": len(self.retrieval_seed),
            },
        }


class RoamingPrewarmService:
    """Best-effort session roaming seed selection with a short session cache."""

    def __init__(
        self,
        *,
        workspace: Path,
        sessions: Any,
        memory: Any,
        nearline_memory: Any,
        context_config: Any,
        world_state: Any | None = None,
    ) -> None:
        self._workspace = Path(workspace)
        self._sessions = sessions
        self._memory = memory
        self._nearline_memory = nearline_memory
        self._context_config = context_config
        self._world_state = world_state
        self._session_cache: list[dict[str, Any]] | None = None
        self._session_cache_at: float = 0.0
        self._last_status: dict[str, Any] = {
            "prewarm_enabled": bool(getattr(context_config, "prewarm_enabled", False)),
            "prewarm_empty": True,
            "prewarm_reason": "not_run",
            "prewarm_sources": [],
            "prewarm_seed_counts": {},
        }

    def runtime_status(self) -> dict[str, Any]:
        return dict(self._last_status)

    def prepare(
        self,
        session_key: str,
        *,
        runtime_context: Any,
        scope_resolver: Any | None = None,
    ) -> PrewarmBundle | None:
        del scope_resolver
        if not bool(getattr(self._context_config, "prewarm_enabled", False)):
            self._last_status = {
                "prewarm_enabled": False,
                "prewarm_empty": True,
                "prewarm_reason": "disabled",
                "prewarm_sources": [],
                "prewarm_seed_counts": {},
            }
            return None
        current_user_id = str(getattr(runtime_context, "user_id", "") or "").strip()
        current_device_id = str(getattr(runtime_context, "device_id", "") or "").strip()
        session_rows = self._list_sessions_cached()
        candidates = [
            row for row in session_rows
            if row.get("key") != session_key
            and (
                (current_device_id and row.get("device_id") == current_device_id)
                or (current_user_id and row.get("user_id") == current_user_id)
            )
        ]
        candidates.sort(
            key=lambda row: (
                0 if current_device_id and row.get("device_id") == current_device_id else 1,
                -(self._updated_at_score(row.get("updated_at"))),
            ),
        )
        max_items = max(1, int(getattr(self._context_config, "prewarm_max_items", 8) or 8))
        working_seed: list[str] = []
        world_seed: list[str] = []
        retrieval_seed: list[dict[str, Any]] = []
        sources: list[str] = []
        for row in candidates[:max_items]:
            preview = _trim_text(row.get("preview"), max_chars=180)
            if preview:
                retrieval_seed.append({
                    "title": f"recent_session:{row.get('key')}",
                    "text": preview,
                    "scope": "session",
                    "owner_id": row.get("user_id"),
                    "timestamp": row.get("updated_at"),
                    "details": {
                        "source_session_key": row.get("key"),
                        "seed_kind": "session_preview",
                        "device_id": row.get("device_id"),
                    },
                })
                if "recent_sessions" not in sources:
                    sources.append("recent_sessions")
            if len(world_seed) < max_items:
                world_seed.extend(
                    self._world_seed_from_session(
                        str(row.get("key") or "").strip(),
                        runtime_context=runtime_context,
                        max_items=max_items - len(world_seed),
                    )
                )
                if world_seed and "world_summary" not in sources:
                    sources.append("world_summary")
            if len(retrieval_seed) >= max_items:
                break
        if current_user_id:
            facts_bundle = self._memory.fact_store.retrieve_context_bundle(scope_prefix="user")
            for fact in facts_bundle.facts[:max_items]:
                retrieval_seed.append({
                    "title": "fact",
                    "text": fact.content,
                    "scope": fact.scope,
                    "owner_id": fact.owner,
                    "timestamp": fact.updated_at,
                    "details": {"seed_kind": "fact_store"},
                })
                if "fact_store" not in sources:
                    sources.append("fact_store")
                if len(retrieval_seed) >= max_items:
                    break
        nearline = self._nearline_memory.retrieve(query=None, recent_history=None)
        for episode in nearline.episodes[: max(1, max_items // 2)]:
            retrieval_seed.append({
                "title": "episode",
                "text": episode.summary or episode.content,
                "scope": "session",
                "owner_id": episode.owner_id,
                "timestamp": episode.timestamp,
                "details": {"seed_kind": "episode", "source_session_key": episode.session_key},
            })
            if "nearline_episode" not in sources:
                sources.append("nearline_episode")
            if len(retrieval_seed) >= max_items:
                break
        if nearline.profile is not None:
            summary = _trim_text(nearline.profile.summary, max_chars=180)
            if summary:
                working_seed.append(f"prewarm_profile: {summary}")
                if "nearline_profile" not in sources:
                    sources.append("nearline_profile")
        if not any((working_seed, world_seed, retrieval_seed)):
            bundle = PrewarmBundle(reason="no_candidates", empty=True)
            self._last_status = bundle.audit()
            return None
        bundle = PrewarmBundle(
            working_memory_seed=working_seed[:max_items],
            world_view_seed=world_seed[:max_items],
            retrieval_seed=retrieval_seed[:max_items],
            sources=sources,
            reason="prepared",
            empty=False,
        )
        self._last_status = bundle.audit()
        return bundle

    def _world_seed_from_session(
        self,
        session_key: str,
        *,
        runtime_context: Any,
        max_items: int,
    ) -> list[str]:
        if max_items <= 0 or not session_key or not hasattr(self._sessions, "get_or_create"):
            return []
        manager = self._ensure_world_state()
        try:
            session = self._sessions.get_or_create(session_key)
            payload = manager.snapshot_prompt_payload(
                session,
                identity=runtime_context,
                current_message=None,
            )
        except Exception:
            return []
        summary = payload.get("world_summary") if isinstance(payload, dict) else None
        if not isinstance(summary, dict) or not summary:
            metadata_world = (
                session.metadata.get("world_state_v1")
                if hasattr(session, "metadata") and isinstance(session.metadata, dict)
                else None
            )
            if isinstance(metadata_world, dict):
                fallback_summary = metadata_world.get("world_summary")
                if isinstance(fallback_summary, dict):
                    summary = fallback_summary
        if not isinstance(summary, dict):
            return []
        items: list[str] = []
        raw_items = [
            *list(summary.get("contested_items") or []),
            *list(summary.get("relationships") or []),
            *list(summary.get("focus") or []),
            *list(summary.get("uncertainties") or []),
        ]
        for raw in raw_items:
            text = _trim_text(raw, max_chars=180)
            if not text:
                continue
            line = f"prewarm_world: {text}"
            if line in items:
                continue
            items.append(line)
            if len(items) >= max_items:
                break
        return items

    def _ensure_world_state(self) -> Any:
        if self._world_state is None:
            self._world_state = WorldStateManager(
                self._workspace,
                self._sessions,
                context_config=self._context_config,
            )
        return self._world_state

    def _list_sessions_cached(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._session_cache is not None and (now - self._session_cache_at) < 30:
            return list(self._session_cache)
        rows = list(self._sessions.list_sessions() or []) if hasattr(self._sessions, "list_sessions") else []
        self._session_cache = rows
        self._session_cache_at = now
        return list(rows)

    @staticmethod
    def _updated_at_score(value: Any) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            from datetime import datetime

            parsed = datetime.fromisoformat(text)
        except ValueError:
            return 0.0
        return parsed.timestamp()
