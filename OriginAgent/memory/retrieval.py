"""Layered nearline retrieval for prompt context injection."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from OriginAgent.agent.facts import FactRecord, FactStore
from OriginAgent.memory.models import AgentCaseRecord, EpisodeRecord, ForesightRecord, ProfileSnapshot
from OriginAgent.memory.store import NearlineMemoryStore
from OriginAgent.utils.helpers import truncate_text

_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-/:.]+|[\u4e00-\u9fff]{1,8}")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "for", "of", "in", "on", "at", "is",
    "are", "be", "with", "this", "that", "it", "we", "you", "i", "me", "my",
    "our", "your", "请", "帮", "一下", "一个", "我们", "你", "我", "了", "在",
}
_EXECUTION_HINTS = (
    "task", "run", "execute", "fix", "debug", "implement", "build", "tool",
    "command", "error", "issue", "workflow", "任务", "执行", "修复", "实现", "构建",
    "工具", "命令", "报错", "问题",
)


@dataclass(frozen=True)
class NearlineRetrievalResult:
    episodes: list[EpisodeRecord] = field(default_factory=list)
    support_facts: list[FactRecord] = field(default_factory=list)
    foresights: list[ForesightRecord] = field(default_factory=list)
    agent_cases: list[AgentCaseRecord] = field(default_factory=list)
    profile: ProfileSnapshot | None = None
    rendered_text: str = ""

    @property
    def has_content(self) -> bool:
        return bool(
            self.episodes
            or self.support_facts
            or self.foresights
            or self.agent_cases
            or self.profile is not None
        )

    @property
    def has_primary_content(self) -> bool:
        return bool(
            self.episodes
            or self.foresights
            or self.agent_cases
            or self.profile is not None
        )


class NearlineMemoryRetriever:
    """Episode-first retrieval over nearline stores with fact evidence support."""

    def __init__(
        self,
        workspace: Path,
        *,
        nearline_store: NearlineMemoryStore | None = None,
        fact_store: FactStore | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.store = nearline_store or NearlineMemoryStore(self.workspace)
        self.fact_store = fact_store or FactStore(self.workspace)

    def retrieve(
        self,
        *,
        query: str | None = None,
        recent_history: Iterable[dict[str, Any]] | None = None,
        episode_limit: int = 3,
        support_fact_limit: int = 4,
        foresight_limit: int = 2,
        agent_case_limit: int = 2,
    ) -> NearlineRetrievalResult:
        query_text = self._build_query_text(query=query, recent_history=recent_history)
        query_tokens = _tokens(query_text)

        episodes = self._rank_episodes(query_text, query_tokens)[:episode_limit]
        profile = self._select_profile(query_tokens)
        foresights = self._rank_foresights(query_text, query_tokens)[:foresight_limit]
        agent_cases = self._rank_agent_cases(query_text, query_tokens)[:agent_case_limit]
        support_facts = self._rank_support_facts(
            query_text,
            query_tokens,
            episodes=episodes,
        )[:support_fact_limit]

        rendered_text = self._render(
            episodes=episodes,
            support_facts=support_facts,
            foresights=foresights,
            agent_cases=agent_cases,
            profile=profile,
        )
        return NearlineRetrievalResult(
            episodes=episodes,
            support_facts=support_facts,
            foresights=foresights,
            agent_cases=agent_cases,
            profile=profile,
            rendered_text=rendered_text,
        )

    def _rank_episodes(
        self,
        query_text: str,
        query_tokens: set[str],
    ) -> list[EpisodeRecord]:
        ranked: list[tuple[float, EpisodeRecord]] = []
        for episode in self.store.read_episodes(limit=200):
            haystack = "\n".join(part for part in (episode.summary, episode.content) if part)
            score = self._lexical_score(query_tokens, haystack)
            if not query_tokens:
                score = 0.05
            if score <= 0:
                continue
            if query_text and query_text.casefold() in haystack.casefold():
                score += 0.75
            score += self._recency_bonus(episode.timestamp)
            ranked.append((score, episode))
        ranked.sort(key=lambda item: (-item[0], -self._timestamp_value(item[1].timestamp), item[1].episode_id))
        return [episode for _, episode in ranked]

    def _rank_foresights(
        self,
        query_text: str,
        query_tokens: set[str],
    ) -> list[ForesightRecord]:
        ranked: list[tuple[float, ForesightRecord]] = []
        for foresight in self.store.read_foresights(limit=120):
            haystack = "\n".join(
                part
                for part in (foresight.content, foresight.evidence, foresight.start_at or "", foresight.end_at or "")
                if part
            )
            score = self._lexical_score(query_tokens, haystack)
            if query_text and query_text.casefold() in haystack.casefold():
                score += 0.5
            if foresight.start_at and any(ch.isdigit() for ch in query_text):
                score += 0.2
            score += self._recency_bonus(foresight.timestamp)
            if score <= 0:
                continue
            ranked.append((score, foresight))
        ranked.sort(key=lambda item: (-item[0], -self._timestamp_value(item[1].timestamp), item[1].foresight_id))
        return [foresight for _, foresight in ranked]

    def _rank_agent_cases(
        self,
        query_text: str,
        query_tokens: set[str],
    ) -> list[AgentCaseRecord]:
        ranked: list[tuple[float, AgentCaseRecord]] = []
        execution_query = any(token in query_tokens for token in _EXECUTION_HINTS)
        for case in self.store.read_agent_cases(limit=120):
            haystack = "\n".join(
                part for part in (case.task_intent, case.approach, case.outcome_summary) if part
            )
            score = self._lexical_score(query_tokens, haystack)
            if execution_query:
                score += 0.35
            score += max(0.0, min(case.quality_score, 1.0)) * 0.4
            score += self._recency_bonus(case.timestamp)
            if query_text and query_text.casefold() in haystack.casefold():
                score += 0.5
            if score <= 0:
                continue
            ranked.append((score, case))
        ranked.sort(key=lambda item: (-item[0], -self._timestamp_value(item[1].timestamp), item[1].case_id))
        return [case for _, case in ranked]

    def _rank_support_facts(
        self,
        query_text: str,
        query_tokens: set[str],
        *,
        episodes: list[EpisodeRecord],
    ) -> list[FactRecord]:
        active = [
            fact for fact in self.fact_store.read_all()
            if fact.status in {"active", "pending_confirmation"}
        ]
        episode_tokens = set(query_tokens)
        for episode in episodes:
            episode_tokens |= _tokens(episode.summary)
            episode_tokens |= _tokens(episode.content)
        ranked: list[tuple[float, FactRecord]] = []
        for fact in active:
            haystack = "\n".join(part for part in (fact.content, fact.source_excerpt, fact.scope, fact.category) if part)
            score = self._lexical_score(episode_tokens, haystack)
            if query_text and query_text.casefold() in haystack.casefold():
                score += 0.35
            score += max(0.0, min(fact.confidence, 1.0)) * 0.15
            if score <= 0:
                continue
            ranked.append((score, fact))
        ranked.sort(key=lambda item: (-item[0], -self._timestamp_value(item[1].updated_at), item[1].fact_id))
        return [fact for _, fact in ranked]

    def _select_profile(self, query_tokens: set[str]) -> ProfileSnapshot | None:
        profiles = self.store.read_profiles(limit=40)
        if not profiles:
            return None
        ranked: list[tuple[float, ProfileSnapshot]] = []
        for profile in profiles:
            haystack = "\n".join(
                [profile.summary, *profile.explicit_traits, *profile.implicit_traits]
            )
            score = self._lexical_score(query_tokens, haystack)
            score += self._recency_bonus(profile.updated_at)
            ranked.append((score, profile))
        ranked.sort(key=lambda item: (-item[0], -self._timestamp_value(item[1].updated_at), item[1].profile_id))
        return ranked[0][1]

    def _render(
        self,
        *,
        episodes: list[EpisodeRecord],
        support_facts: list[FactRecord],
        foresights: list[ForesightRecord],
        agent_cases: list[AgentCaseRecord],
        profile: ProfileSnapshot | None,
    ) -> str:
        parts: list[str] = []
        if episodes:
            lines = ["## Relevant Episodes"]
            for episode in episodes:
                lines.append(f"- {truncate_text(episode.summary or episode.content, 220)}")
            parts.append("\n".join(lines))
        if support_facts:
            lines = ["## Supporting Facts"]
            for fact in support_facts:
                lines.append(f"- {truncate_text(fact.content, 180)}")
            parts.append("\n".join(lines))
        if foresights:
            lines = ["## Active Foresight"]
            for foresight in foresights:
                window = ""
                if foresight.start_at or foresight.end_at:
                    window = f" ({foresight.start_at or '?'} -> {foresight.end_at or '?'})"
                lines.append(f"- {truncate_text(foresight.content, 180)}{window}")
            parts.append("\n".join(lines))
        if agent_cases:
            lines = ["## Relevant Agent Experience"]
            for case in agent_cases:
                summary = " | ".join(
                    part for part in (case.task_intent, case.outcome_summary or case.approach) if part
                )
                lines.append(f"- {truncate_text(summary, 220)}")
            parts.append("\n".join(lines))
        if profile is not None:
            lines = ["## Profile Summary", truncate_text(profile.summary, 220)]
            traits = [*profile.explicit_traits[:3], *profile.implicit_traits[:2]]
            for trait in traits:
                lines.append(f"- {truncate_text(trait, 140)}")
            parts.append("\n".join(lines))
        return "\n\n".join(part for part in parts if part.strip())

    @staticmethod
    def _build_query_text(
        *,
        query: str | None,
        recent_history: Iterable[dict[str, Any]] | None,
    ) -> str:
        if query and query.strip():
            return query.strip()
        if recent_history:
            chunks: list[str] = []
            for item in recent_history:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, str) and content.strip():
                    chunks.append(content.strip())
            if chunks:
                return "\n".join(chunks[-3:])
        return ""

    @staticmethod
    def _lexical_score(query_tokens: set[str], haystack: str) -> float:
        if not haystack.strip():
            return 0.0
        if not query_tokens:
            return 0.0
        text_tokens = _tokens(haystack)
        if not text_tokens:
            return 0.0
        overlap = query_tokens & text_tokens
        if not overlap:
            return 0.0
        return (len(overlap) / max(1, len(query_tokens))) + (len(overlap) / max(1, len(text_tokens))) * 0.5

    @staticmethod
    def _recency_bonus(timestamp: str | None) -> float:
        value = NearlineMemoryRetriever._timestamp_value(timestamp)
        if value <= 0:
            return 0.0
        age_days = max(0.0, (datetime.now().timestamp() - value) / 86400)
        return max(0.0, 0.3 - min(age_days, 30.0) * 0.01)

    @staticmethod
    def _timestamp_value(value: str | None) -> float:
        if not isinstance(value, str) or not value.strip():
            return 0.0
        raw = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(raw).timestamp()
        except ValueError:
            return 0.0


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for match in _TOKEN_RE.finditer(text.casefold()):
        token = match.group(0).strip()
        if not token or token in _STOPWORDS or len(token) <= 1:
            continue
        out.add(token)
    return out
