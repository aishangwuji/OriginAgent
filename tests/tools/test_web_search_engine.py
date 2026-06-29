"""Tests for the standalone web search engine algorithms."""

from __future__ import annotations

from OriginAgent.agent.tools.web_search_engine import (
    filter_by_relevance,
    format_enhanced_results,
    rrf_merge,
)


# ── RRF merge ───────────────────────────────────────────────────────────


class TestRRFMerge:
    def test_merge_two_lists(self):
        list_a = [
            {"title": "A1", "url": "https://a.com/1", "content": "First result"},
            {"title": "A2", "url": "https://a.com/2", "content": "Second result"},
        ]
        list_b = [
            {"title": "B1", "url": "https://b.com/1", "content": "Other result"},
            {"title": "A1", "url": "https://a.com/1", "content": "First result"},
        ]
        merged = rrf_merge([list_a, list_b], k=60)
        # A1 should appear only once and be ranked higher due to appearing in both lists
        urls = [it["url"] for it in merged]
        assert len(urls) == len(set(urls)), "Duplicates should be removed"
        assert "https://a.com/1" in urls
        assert "https://a.com/2" in urls
        assert "https://b.com/1" in urls
        # A1 appears in both lists → highest RRF score
        assert urls[0] == "https://a.com/1"

    def test_merge_empty_lists(self):
        assert rrf_merge([[], []], k=60) == []

    def test_merge_single_list(self):
        items = [{"title": "X", "url": "https://x.com", "content": ""}]
        merged = rrf_merge([items], k=60)
        assert len(merged) == 1
        assert merged[0]["url"] == "https://x.com"
        assert "_rrf_score" in merged[0]

    def test_merge_items_without_url(self):
        items = [
            {"title": "No URL", "url": "", "content": "some text"},
        ]
        merged = rrf_merge([items], k=60)
        assert len(merged) == 1
        assert "_rrf_score" in merged[0]

    def test_rrf_score_assigned(self):
        items = [{"title": "T", "url": "https://t.com", "content": ""}]
        merged = rrf_merge([items], k=60)
        score = merged[0].get("_rrf_score")
        assert score is not None
        assert score > 0

    def test_dedup_key_cleaned(self):
        items = [{"title": "T", "url": "https://t.com", "content": ""}]
        merged = rrf_merge([items], k=60)
        assert "_dedup_key" not in merged[0]


# ── Relevance filtering ────────────────────────────────────────────────


class TestFilterByRelevance:
    def test_keeps_relevant(self):
        items = [
            {"title": "Python programming", "url": "https://py.com", "content": "Learn Python", "_rrf_score": 0.5},
            {"title": "Cooking recipes", "url": "https://cook.com", "content": "Best recipes", "_rrf_score": 0.01},
        ]
        filtered = filter_by_relevance(items, "python", min_score=0.05)
        urls = [it["url"] for it in filtered]
        assert "https://py.com" in urls

    def test_keeps_at_least_one(self):
        items = [
            {"title": "Unrelated", "url": "https://x.com", "content": "something", "_rrf_score": 0.001},
        ]
        filtered = filter_by_relevance(items, "python programming tutorial", min_score=0.5)
        assert len(filtered) >= 1

    def test_empty_input(self):
        assert filter_by_relevance([], "query", min_score=0.05) == []

    def test_score_no_rrf(self):
        """Items without _rrf_score should still be kept."""
        items = [
            {"title": "Plain", "url": "https://plain.com", "content": "no rrf score"},
        ]
        filtered = filter_by_relevance(items, "query", min_score=0.05)
        assert len(filtered) == 1


# ── Enhanced output formatting ─────────────────────────────────────────


class TestFormatEnhancedResults:
    def test_single_domain(self):
        items = [
            {"title": "Result", "url": "https://example.com/page1", "content": "First"},
            {"title": "Result 2", "url": "https://example.com/page2", "content": "Second"},
        ]
        result = format_enhanced_results("test query", items)
        assert "Results for: test query" in result
        assert "synthesised from" not in result

    def test_multiple_domains(self):
        items = [
            {"title": "From A", "url": "https://site-a.com/page", "content": "Content A"},
            {"title": "From B", "url": "https://site-b.com/page", "content": "Content B"},
        ]
        result = format_enhanced_results("test", items)
        assert "synthesised from 2 sources" in result
        assert "From A" in result
        assert "From B" in result

    def test_empty_results(self):
        assert format_enhanced_results("query", []) == "No results for: query"

    def test_items_without_url(self):
        items = [
            {"title": "No URL", "url": "", "content": "content"},
        ]
        result = format_enhanced_results("q", items)
        assert "No URL" in result

    def test_html_tags_cleaned(self):
        items = [
            {"title": "<b>Bold</b> title", "url": "https://x.com", "content": "<p>para</p>"},
        ]
        result = format_enhanced_results("q", items)
        assert "<b>" not in result
        assert "<p>" not in result
        assert "Bold" in result
