"""Standalone algorithms for the intelligent web search pipeline.

Contains RRF merge, relevance filtering, and enhanced output formatting —
these are pure functions with no dependency on the WebSearchTool class or
the auxiliary router.  They can be tested in isolation and reused by other
components.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

# ── Reciprocal Rank Fusion ──────────────────────────────────────────────


def rrf_merge(
    all_results: list[list[dict[str, Any]]],
    k: int = 60,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion over multiple ranked result lists.

    Each result list must contain dicts with a ``"url"`` key.  Items
    without a URL are assigned a synthetic key based on content hash so
    they still participate in scoring.

    Returns a deduplicated list sorted by RRF score descending, with an
    ``_rrf_score`` float attached to each item.
    """
    url_to_item: dict[str, dict[str, Any]] = {}
    rrf_scores: dict[str, float] = defaultdict(float)

    for rank_list in all_results:
        for rank, item in enumerate(rank_list):
            url = item.get("url", "") or item.get("href", "")
            if not url:
                url = f"_inline_{hash(item.get('title', '') + item.get('content', ''))}"
            item["_dedup_key"] = url
            if url not in url_to_item:
                url_to_item[url] = item
            rrf_scores[url] += 1.0 / (k + rank + 1)

    sorted_items = sorted(
        url_to_item.values(),
        key=lambda it: rrf_scores[it["_dedup_key"]],
        reverse=True,
    )
    for it in sorted_items:
        it["_rrf_score"] = rrf_scores[it["_dedup_key"]]
        if "_dedup_key" in it:
            del it["_dedup_key"]
    return sorted_items


# ── Relevance Filtering ────────────────────────────────────────────────


def filter_by_relevance(
    items: list[dict[str, Any]],
    query: str,
    min_score: float = 0.05,
) -> list[dict[str, Any]]:
    """Filter out low-scoring results based on RRF score and keyword overlap.

    Items without an ``_rrf_score`` key are kept as-is (they may come from
    a non-RRF path).  The combined score normalises to [0, 1] so that
    *min_score* is a stable threshold.
    """
    query_lower = query.lower()
    query_tokens = set(query_lower.split())

    def _relevance_score(item: dict[str, Any]) -> float:
        rrf = item.get("_rrf_score", 0.0)
        title_content = (item.get("title", "") + " " + item.get("content", "")).lower()
        overlap = sum(1 for t in query_tokens if t in title_content)
        overlap_frac = overlap / max(len(query_tokens), 1)
        return rrf + overlap_frac * 0.3

    scored = [(it, _relevance_score(it)) for it in items]
    max_score = max((s for _, s in scored), default=1.0)
    if max_score > 0:
        scored = [(it, s / max_score) for it, s in scored]

    filtered = [it for it, s in scored if s >= min_score]
    return filtered if filtered else items[:1]  # keep at least the top result


# ── Enhanced Output Formatting ─────────────────────────────────────────


def format_enhanced_results(
    query: str,
    items: list[dict[str, Any]],
) -> str:
    """Format enhanced search results with source diversity annotation."""
    if not items:
        return f"No results for: {query}"

    domains = set()
    for it in items:
        url = it.get("url", "") or it.get("href", "")
        try:
            domains.add(urlparse(url).netloc or "unknown")
        except Exception:
            domains.add("unknown")

    lines: list[str] = []
    if len(domains) >= 2:
        lines.append(
            f"Results for: {query}  (synthesised from {len(domains)} sources)\n"
        )
    else:
        lines.append(f"Results for: {query}\n")

    for i, item in enumerate(items, 1):
        title = _normalize(_strip_tags(item.get("title", "")))
        snippet = _normalize(_strip_tags(item.get("content", "")))
        lines.append(f"{i}. {title}\n   {item.get('url', item.get('href', ''))}")
        if snippet:
            lines.append(f"   {snippet}")

    return "\n".join(lines)


# ── Shared helpers (duplicated from web.py to keep this module standalone) ─


def _strip_tags(text: str) -> str:
    import html

    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
