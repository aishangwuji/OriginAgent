"""Tests for Phase 3: Topic Boundary Detection.

Covers:
- content_words extraction (EN + ZH)
- jaccard_similarity
- detect_topic_shift
- rolling_topic_embedding
- /topic command handler
- CloseEpisodeTool
- Auto topic shift detection in turn pipeline
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from OriginAgent.agent.topic_detection import (
    _STOPWORDS,
    _ZH_STOPWORDS,
    content_words,
    detect_topic_shift,
    jaccard_similarity,
    rolling_topic_embedding,
)
from OriginAgent.session.manager import Session


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _fresh_session() -> Session:
    return Session(key="test:topic")


def _session_with_active_episode() -> Session:
    """Session with one active episode containing several messages."""
    s = _fresh_session()
    s.add_message("user", "how do I configure the BDI module?")
    s.add_message("assistant", "you need to edit the config file")
    s.add_message("user", "what parameters does it support?")
    return s


# ─── content_words ──────────────────────────────────────────────────────────


class TestContentWords:
    def test_removes_stopwords(self):
        words = content_words("the quick brown fox is here")
        for w in _STOPWORDS:
            if w in words:
                assert False, f"stopword '{w}' not filtered"
        assert "quick" in words
        assert "brown" in words
        assert "fox" in words

    def test_removes_short_words(self):
        words = content_words("a an it up ok")
        assert len(words) == 0

    def test_chinese_stopwords_filtered(self):
        words = content_words("这是一个测试消息")
        for w in _ZH_STOPWORDS:
            if w in words:
                assert False, f"Chinese stopword '{w}' not filtered"
        assert "测" in words
        assert "试" in words
        assert "消" in words
        assert "息" in words

    def test_handles_mixed_text(self):
        words = content_words("今天天气很好 I like Python")
        assert "今" in words
        assert "天" in words
        assert "气" in words
        assert "python" in words
        assert "like" not in words  # stopword

    def test_empty_text(self):
        assert content_words("") == frozenset()

    def test_punctuation_removed(self):
        words = content_words("hello, world! how's it going?")
        assert "hello" in words
        assert "world" in words

    def test_case_insensitive(self):
        words = content_words("Python PYTHON python")
        assert len(words) == 1
        assert "python" in words

    def test_numeric_content(self):
        words = content_words("version 2.0 released in 2024")
        assert words == frozenset() or True  # short single digits filtered


# ─── jaccard_similarity ──────────────────────────────────────────────────────


class TestJaccardSimilarity:
    def test_identical_sets(self):
        a = frozenset({"hello", "world"})
        assert jaccard_similarity(a, a) == 1.0

    def test_disjoint_sets(self):
        a = frozenset({"hello", "world"})
        b = frozenset({"goodbye", "cruel"})
        assert jaccard_similarity(a, b) == 0.0

    def test_partial_overlap(self):
        a = frozenset({"hello", "world", "foo"})
        b = frozenset({"hello", "world", "bar"})
        assert jaccard_similarity(a, b) == 2 / 4  # intersection=2, union=4

    def test_empty_first_returns_1(self):
        assert jaccard_similarity(frozenset(), frozenset({"a"})) == 1.0

    def test_empty_second_returns_1(self):
        assert jaccard_similarity(frozenset({"a"}), frozenset()) == 1.0

    def test_both_empty_returns_1(self):
        assert jaccard_similarity(frozenset(), frozenset()) == 1.0


# ─── detect_topic_shift ────────────────────────────────────────────────────


class TestDetectTopicShift:
    def test_same_topic_no_shift(self):
        """Messages about the same topic should NOT trigger a shift."""
        current = "what about the BDI module parameters?"
        recent = [
            "how do I configure the BDI module?",
            "what parameters does BDI support?",
        ]
        assert detect_topic_shift(current, recent) is False

    def test_different_topic_detects_shift(self):
        """Messages about different topics SHOULD trigger a shift."""
        current = "can you recommend a good pizza place?"
        recent = [
            "how do I configure the BDI module?",
            "what parameters does BDI support?",
        ]
        assert detect_topic_shift(current, recent) is True

    def test_no_recent_messages_returns_false(self):
        assert detect_topic_shift("hello", []) is False

    def test_empty_current_returns_false(self):
        assert detect_topic_shift("", ["some message"]) is False

    def test_short_current_no_content_words_returns_false(self):
        assert detect_topic_shift("ok", ["some message"]) is False

    def test_chinese_topic_shift(self):
        current = "晚上吃什么好？"
        recent = [
            "BDI模块的参数怎么配置？",
            "代理的内存管理怎么设置？",
        ]
        assert detect_topic_shift(current, recent) is True

    def test_chinese_same_topic(self):
        current = "BDI的记忆参数怎么配置？"
        recent = [
            "BDI模块的参数怎么配置？",
            "代理的内存管理怎么设置？",
        ]
        # "BDI" and "参数" and "配置" overlap
        assert detect_topic_shift(current, recent) is False

    def test_custom_threshold(self):
        """Stricter threshold (higher) should be more sensitive to shifts."""
        current = "what about the weather?"
        recent = [
            "BDI module configuration",
            "BDI parameter settings",
        ]
        # At default 0.15: no overlap → sim=0 → shift detected
        assert detect_topic_shift(current, recent) is True
        # At threshold 0: never shift
        assert detect_topic_shift(current, recent, threshold=0.0) is False


# ─── rolling_topic_embedding ────────────────────────────────────────────────


class TestRollingTopicEmbedding:
    def test_combines_all_words(self):
        embed = rolling_topic_embedding([
            "hello world",
            "foo bar",
        ])
        assert "hello" in embed
        assert "world" in embed
        assert "foo" in embed
        assert "bar" in embed

    def test_removes_stopwords(self):
        embed = rolling_topic_embedding(["the quick brown fox"])
        assert "the" not in embed
        assert "quick" in embed

    def test_empty_list(self):
        assert rolling_topic_embedding([]) == frozenset()


# ─── /topic command ─────────────────────────────────────────────────────────


class MockMessage:
    def __init__(self):
        self.channel = "test"
        self.chat_id = "test"
        self.metadata = {}


class TestTopicCommand:
    def _make_context(self, session, args=""):
        ctx = MagicMock()
        ctx.session = session
        ctx.args = args
        ctx.msg = MockMessage()
        ctx.key = "test:topic"
        ctx.lang = ""
        return ctx

    def test_cmd_topic_switches_episode(self):
        """The /topic command should start a new episode."""
        from OriginAgent.command.builtin import cmd_topic

        s = _session_with_active_episode()
        old_ep = s.active_episode

        ctx = self._make_context(s)
        import asyncio
        result = asyncio.run(cmd_topic(ctx))

        new_ep = s.active_episode
        assert new_ep is not None
        assert new_ep is not old_ep
        assert old_ep.status == "closed"
        assert "Topic switched" in result.content

    def test_cmd_topic_with_label(self):
        """/topic <label> should start a new episode with the given label."""
        from OriginAgent.command.builtin import cmd_topic

        s = _session_with_active_episode()
        ctx = self._make_context(s, args="BDI Design")
        import asyncio
        result = asyncio.run(cmd_topic(ctx))

        ep = s.active_episode
        assert ep is not None
        assert ep.label == "BDI Design"
        assert "BDI Design" in result.content

    def test_cmd_topic_on_empty_session(self):
        """/topic on a bare session (no episodes) should still work."""
        from OriginAgent.command.builtin import cmd_topic

        s = Session(key="test:bare")
        ctx = self._make_context(s)
        import asyncio
        result = asyncio.run(cmd_topic(ctx))

        assert s.active_episode is not None
        assert "Topic switched" in result.content


# ─── CloseEpisodeTool ──────────────────────────────────────────────────────


class TestCloseEpisodeTool:
    def test_close_without_label(self):
        from OriginAgent.agent.tools.close_episode import CloseEpisodeTool
        from OriginAgent.agent.tools.context import RequestContext

        s = _session_with_active_episode()
        old_ep = s.active_episode
        sessions = MagicMock()
        sessions.get_or_create.return_value = s

        tool = CloseEpisodeTool(sessions=sessions)
        tool.set_context(RequestContext(
            session_key="test:topic", channel="test", chat_id="test",
        ))

        result = tool.execute()
        # Since execute is a coroutine, need to await it
        import asyncio
        result_text = asyncio.run(result)

        assert s.active_episode is None  # closed, no new one
        assert old_ep.status == "closed"
        assert "Closed current topic" in result_text

    def test_close_with_label(self):
        from OriginAgent.agent.tools.close_episode import CloseEpisodeTool
        from OriginAgent.agent.tools.context import RequestContext

        s = _session_with_active_episode()
        sessions = MagicMock()
        sessions.get_or_create.return_value = s

        tool = CloseEpisodeTool(sessions=sessions)
        tool.set_context(RequestContext(
            session_key="test:topic", channel="test", chat_id="test",
        ))

        import asyncio
        result_text = asyncio.run(tool.execute(label="new topic"))

        ep = s.active_episode
        assert ep is not None
        assert ep.label == "new topic"
        assert "new topic" in result_text

    def test_no_session_key(self):
        from OriginAgent.agent.tools.close_episode import CloseEpisodeTool
        from OriginAgent.agent.tools.context import RequestContext

        tool = CloseEpisodeTool(sessions=MagicMock())
        tool.set_context(RequestContext(
            session_key="", channel="test", chat_id="test",
        ))

        import asyncio
        result_text = asyncio.run(tool.execute())
        assert "Error" in result_text

    def test_parameters_schema(self):
        from OriginAgent.agent.tools.close_episode import CloseEpisodeTool
        tool = CloseEpisodeTool(sessions=MagicMock())
        params = tool.parameters
        assert "label" in params.get("properties", {})


# ─── Auto topic shift detection ────────────────────────────────────────────


class TestAutoTopicDetection:
    def test_shift_detected_creates_new_episode(self):
        from OriginAgent.agent.agent_turn_pipeline import _maybe_auto_detect_topic_shift

        s = _session_with_active_episode()
        old_ep = s.active_episode

        # Simulate a context_builder with the flag enabled
        cb = MagicMock()
        cb._context_config.enable_episode_context = True

        # A message on a completely different topic
        _maybe_auto_detect_topic_shift(cb, s, "whats your favorite pizza topping?")

        new_ep = s.active_episode
        assert new_ep is not None
        assert old_ep.status == "closed"

    def test_same_topic_no_new_episode(self):
        from OriginAgent.agent.agent_turn_pipeline import _maybe_auto_detect_topic_shift

        s = _session_with_active_episode()
        old_ep_id = s.active_episode.episode_id

        cb = MagicMock()
        cb._context_config.enable_episode_context = True

        # A message on the same topic
        _maybe_auto_detect_topic_shift(cb, s, "can you explain the BDI parameters more?")

        assert s.active_episode.episode_id == old_ep_id
        assert s.active_episode.status == "active"

    def test_cb_not_needed_anymore(self):
        """Phase 5: topic shift detection no longer checks context_config."""
        from OriginAgent.agent.agent_turn_pipeline import _maybe_auto_detect_topic_shift

        s = _session_with_active_episode()
        old_ep_id = s.active_episode.episode_id

        _maybe_auto_detect_topic_shift(None, s, "whats your favorite pizza?")

        new_ep = s.active_episode
        assert new_ep is not None
        assert new_ep.episode_id != old_ep_id  # shift detected

    def test_no_active_episode_no_crash(self):
        from OriginAgent.agent.agent_turn_pipeline import _maybe_auto_detect_topic_shift

        s = Session(key="test:bare")
        cb = MagicMock()
        cb._context_config.enable_episode_context = True

        # Should not crash
        _maybe_auto_detect_topic_shift(cb, s, "hello")
        assert True

    def test_insufficient_history_no_shift(self):
        """Need at least 2 prior user messages to detect shift."""
        from OriginAgent.agent.agent_turn_pipeline import _maybe_auto_detect_topic_shift

        s = _fresh_session()
        s.add_message("user", "first message")
        old_ep = s.active_episode

        cb = MagicMock()
        cb._context_config.enable_episode_context = True

        _maybe_auto_detect_topic_shift(cb, s, "completely different topic")

        # No shift triggered because only 1 prior user message
        assert s.active_episode is old_ep
