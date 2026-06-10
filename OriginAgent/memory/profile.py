"""Profile synthesis and managed USER.md shadow rendering for nearline memory."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from OriginAgent.memory.candidates import GovernedMemoryWriter, MemoryCandidate
from OriginAgent.memory.models import EpisodeRecord, ForesightRecord, MemCell, ProfileSnapshot
from OriginAgent.memory.store import NearlineMemoryStore
from OriginAgent.utils.helpers import truncate_text

if TYPE_CHECKING:
    from OriginAgent.agent.memory import MemoryStore

_MANAGED_START = "<!-- ORIGINAGENT_MANAGED_PROFILE_START -->"
_MANAGED_END = "<!-- ORIGINAGENT_MANAGED_PROFILE_END -->"
_DEFAULT_USER_TEMPLATE = "# User Profile\n\n"


class NearlineProfileService:
    """Aggregate nearline objects into profile snapshots and managed USER.md shadow content."""

    def __init__(
        self,
        workspace: Path,
        *,
        store: NearlineMemoryStore | None = None,
        memory_store: "MemoryStore | None" = None,
    ) -> None:
        from OriginAgent.agent.memory import MemoryStore

        self.workspace = Path(workspace)
        self.store = store or NearlineMemoryStore(self.workspace)
        self.memory_store = memory_store or MemoryStore(self.workspace)
        self._candidate_writer = GovernedMemoryWriter(self.workspace)

    def synthesize_snapshot(
        self,
        *,
        owner_id: str,
        memcells: list[MemCell],
        episodes: list[EpisodeRecord],
        foresights: list[ForesightRecord],
        candidates: list[MemoryCandidate] | None = None,
    ) -> ProfileSnapshot | None:
        if not memcells:
            memcells = []
        recent_explicit = _dedupe_preserve_order(
            _compact_line(episode.summary, 160)
            for episode in reversed(episodes[-8:])
            if _compact_line(episode.summary, 160)
        )
        recent_implicit = _dedupe_preserve_order(
            _compact_line(foresight.content, 160)
            for foresight in reversed(foresights[-8:])
            if _compact_line(foresight.content, 160)
        )

        text_traits = self._infer_traits_from_memcells(memcells)
        candidate_traits = self._infer_traits_from_candidates(candidates or [])
        explicit_traits = _dedupe_preserve_order(
            [*candidate_traits["explicit"], *recent_explicit, *text_traits["explicit"]]
        )[:6]
        implicit_traits = _dedupe_preserve_order(
            [*candidate_traits["implicit"], *recent_implicit, *text_traits["implicit"]]
        )[:6]
        if not explicit_traits and not implicit_traits:
            return None

        summary_lines = []
        if explicit_traits:
            summary_lines.append(f"Preferences: {', '.join(explicit_traits[:2])}")
        if implicit_traits:
            summary_lines.append(f"Likely ongoing context: {', '.join(implicit_traits[:2])}")
        summary = " | ".join(summary_lines)[:320].strip()
        if not summary:
            summary = " | ".join([*(explicit_traits[:1]), *(implicit_traits[:1])]).strip()
        content_hash = hashlib.sha1(
            self._normalized_managed_payload(
                summary=summary,
                explicit_traits=explicit_traits,
                implicit_traits=implicit_traits,
                source_memcell_ids=[memcell.memcell_id for memcell in memcells],
            ).encode("utf-8")
        ).hexdigest()
        identity_payload = "|".join(
            [
                owner_id or "user",
                (memcells[-1].ended_at if memcells else datetime.utcnow().isoformat()),
                ",".join(memcell.memcell_id for memcell in memcells),
                ",".join(candidate.candidate_id for candidate in (candidates or [])[:8]),
            ]
        )
        digest = hashlib.sha1(identity_payload.encode("utf-8")).hexdigest()[:12]
        return ProfileSnapshot(
            profile_id=f"profile_{digest}",
            owner_id=owner_id or "user",
            summary=summary,
            explicit_traits=explicit_traits,
            implicit_traits=implicit_traits,
            source_memcell_ids=[memcell.memcell_id for memcell in memcells],
            updated_at=memcells[-1].ended_at if memcells else datetime.utcnow().isoformat(),
            metadata={
                "source": "nearline_profile_service",
                "episode_count": len(episodes),
                "foresight_count": len(foresights),
                "content_hash": content_hash,
                "governed_candidate_ids": [candidate.candidate_id for candidate in (candidates or [])[:8]],
            },
        )

    def refresh_profile(
        self,
        *,
        owner_id: str = "user",
        limit: int = 80,
        write_user_shadow: bool = False,
    ) -> ProfileSnapshot | None:
        memcells = self.store.read_memcells(limit=limit)
        episodes = [
            item for item in self.store.read_episodes(limit=limit)
            if not owner_id or item.owner_id == owner_id
        ]
        foresights = [
            item for item in self.store.read_foresights(limit=limit)
            if not owner_id or item.owner_id == owner_id
        ]
        candidates, end_cursor = self._candidate_writer.read_pending_for_consumer(
            "nearline_profile",
            kinds=("preference", "task_pattern"),
            owner_id=owner_id or None,
            limit=limit,
        )
        if owner_id:
            memcell_ids = {
                *[item.memcell_id for item in episodes],
                *[item.memcell_id for item in foresights],
            }
            memcells = [item for item in memcells if item.memcell_id in memcell_ids] or memcells
        snapshot = self.synthesize_snapshot(
            owner_id=owner_id or "user",
            memcells=memcells,
            episodes=episodes,
            foresights=foresights,
            candidates=candidates,
        )
        if snapshot is None:
            return None
        self.store.append_profiles([snapshot])
        if candidates:
            self._candidate_writer.advance_consumer_cursor("nearline_profile", end_cursor)
        if write_user_shadow:
            self.write_profile_shadow(snapshot)
        return snapshot

    def write_profile_shadow(self, snapshot: ProfileSnapshot) -> str:
        current = self.memory_store.read_user()
        updated = self.render_user_with_managed_profile(current, snapshot)
        if self._managed_content_unchanged(current, snapshot):
            return current
        self.memory_store.write_user(updated)
        return updated

    def render_user_with_managed_profile(
        self,
        current_text: str,
        snapshot: ProfileSnapshot,
    ) -> str:
        base = current_text if current_text.strip() else _DEFAULT_USER_TEMPLATE
        managed = self._render_managed_block(snapshot)
        if _MANAGED_START in base and _MANAGED_END in base:
            prefix, tail = base.split(_MANAGED_START, 1)
            _old, suffix = tail.split(_MANAGED_END, 1)
            return prefix.rstrip() + "\n\n" + managed + suffix
        separator = "" if base.endswith("\n") else "\n"
        return base + separator + ("\n" if base.strip() else "") + managed + "\n"

    def _render_managed_block(self, snapshot: ProfileSnapshot) -> str:
        lines = [
            _MANAGED_START,
            "## Managed Profile Snapshot",
            "",
            f"Updated: {snapshot.updated_at}",
            f"Content hash: {snapshot.metadata.get('content_hash', '')}",
            "",
            "Summary:",
            snapshot.summary or "No synthesized summary yet.",
            "",
            "Explicit traits:",
        ]
        if snapshot.explicit_traits:
            lines.extend(f"- {item}" for item in snapshot.explicit_traits[:6])
        else:
            lines.append("- None")
        lines.extend(["", "Implicit traits:"])
        if snapshot.implicit_traits:
            lines.extend(f"- {item}" for item in snapshot.implicit_traits[:6])
        else:
            lines.append("- None")
        lines.extend([
            "",
            "Source memcells:",
            f"- {', '.join(snapshot.source_memcell_ids[:8]) or 'None'}",
            _MANAGED_END,
        ])
        return "\n".join(lines)

    def _managed_content_unchanged(self, current_text: str, snapshot: ProfileSnapshot) -> bool:
        current_hash = self._extract_managed_content_hash(current_text)
        next_hash = str(snapshot.metadata.get("content_hash") or "").strip()
        return bool(current_hash and next_hash and current_hash == next_hash)

    @staticmethod
    def _extract_managed_content_hash(current_text: str) -> str | None:
        if _MANAGED_START not in current_text or _MANAGED_END not in current_text:
            return None
        marker = "Content hash:"
        for line in current_text.splitlines():
            if line.startswith(marker):
                value = line.split(marker, 1)[1].strip()
                return value or None
        return None

    @staticmethod
    def _normalized_managed_payload(
        *,
        summary: str,
        explicit_traits: list[str],
        implicit_traits: list[str],
        source_memcell_ids: list[str],
    ) -> str:
        payload = {
            "summary": summary,
            "explicit_traits": list(explicit_traits[:6]),
            "implicit_traits": list(implicit_traits[:6]),
            "source_memcell_ids": list(source_memcell_ids[:8]),
        }
        return str(payload)

    @staticmethod
    def managed_markers() -> tuple[str, str]:
        return _MANAGED_START, _MANAGED_END

    @staticmethod
    def _infer_traits_from_memcells(memcells: list[MemCell]) -> dict[str, list[str]]:
        explicit: list[str] = []
        implicit: list[str] = []
        for memcell in memcells[-12:]:
            for message in memcell.messages:
                if message.role != "user":
                    continue
                text = _compact_line(message.content, 180)
                lowered = text.casefold()
                if not text:
                    continue
                if any(needle in lowered for needle in ("prefer", "like", "usually", "希望", "喜欢", "偏好")):
                    explicit.append(text)
                if any(needle in lowered for needle in ("will ", "plan", "going to", "明天", "下周", "会在", "准备")):
                    implicit.append(text)
        return {
            "explicit": _dedupe_preserve_order(explicit)[:6],
            "implicit": _dedupe_preserve_order(implicit)[:6],
        }

    @staticmethod
    def _infer_traits_from_candidates(candidates: list[MemoryCandidate]) -> dict[str, list[str]]:
        explicit: list[str] = []
        implicit: list[str] = []
        for candidate in candidates:
            text = _compact_line(candidate.summary, 180)
            if not text:
                continue
            if candidate.kind == "preference":
                explicit.append(text)
                continue
            if candidate.kind == "task_pattern":
                implicit.append(text)
                continue
            metadata = dict(candidate.metadata or {})
            for value in metadata.get("traits", []) if isinstance(metadata.get("traits"), list) else []:
                compact = _compact_line(value, 180)
                if compact:
                    explicit.append(compact)
            source_excerpt = _compact_line(candidate.source_excerpt, 180)
            if source_excerpt and source_excerpt != text:
                implicit.append(source_excerpt)
        return {
            "explicit": _dedupe_preserve_order(explicit)[:6],
            "implicit": _dedupe_preserve_order(implicit)[:6],
        }

    @staticmethod
    def last_sync_time(workspace: Path) -> str | None:
        profiles = NearlineMemoryStore(workspace).read_profiles(limit=1)
        if not profiles:
            return None
        return profiles[-1].updated_at or None


def _compact_line(text: str, limit: int) -> str:
    compact = " ".join(str(text or "").split()).strip()
    if not compact:
        return ""
    return truncate_text(compact, limit).replace("\n... (truncated)", " ...")


def _dedupe_preserve_order(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out
