"""Tests for Phase 2: Episode-Aware Context Loading.

Tests cover:
- Session.active_episode property
- Episode summary storage and preview
- ContextBuilder closed-episode-summaries block
- Turn pipeline episode-scoped history loading
- Feature flag gating
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest

from OriginAgent.config.schema import ContextConfig
from OriginAgent.session.manager import Episode, Session, SessionManager


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _fresh_session() -> Session:
    """Return a clean session with no messages or episodes."""
    return Session(key="test:episodes")


def _session_with_closed_episodes() -> Session:
    """Return a session with two closed episodes and one active."""
    s = _fresh_session()
    s.add_message("user", "what is BDI?")
    s.add_message("assistant", "BDI stands for...")
    s.start_new_episode("second topic")
    s.add_message("user", "how about the session model?")
    s.add_message("assistant", "the session model is...")
    s.add_message("user", "i see, thanks")
    s.start_new_episode("third topic")
    s.add_message("user", "lets talk about something else")
    return s


# ─── Session.active_episode ─────────────────────────────────────────────────


class TestActiveEpisodeProperty:
    def test_active_episode_returns_none_when_no_episodes(self):
        s = Session(key="test:bare")
        assert s.active_episode is None

    def test_active_episode_returns_active_episode(self):
        s = _fresh_session()
        s.add_message("user", "hello")
        ep = s.active_episode
        assert ep is not None
        assert ep.status == "active"

    def test_active_episode_returns_none_when_closed(self):
        s = _fresh_session()
        s.add_message("user", "hello")
        s.close_active_episode()
        assert s.active_episode is None

    def test_active_episode_points_to_correct_episode(self):
        s = _session_with_closed_episodes()
        ep = s.active_episode
        assert ep is not None
        assert ep.label == "third topic"
        assert ep.status == "active"

    def test_active_episode_matches_index(self):
        s = _fresh_session()
        s.add_message("user", "ep0")
        s.start_new_episode("ep1")
        s.add_message("user", "in ep1")
        ep = s.active_episode
        assert ep is not None
        assert ep.label == "ep1"
        assert ep is s.episodes[s.active_episode_index]


# ─── Episode Summary Storage ────────────────────────────────────────────────


class TestEpisodeSummary:
    def test_summary_stored_on_start_new_episode(self):
        s = _fresh_session()
        s.add_message("user", "first topic message")
        s.start_new_episode()
        summaries = s.metadata.get("_episode_summaries", [])
        assert len(summaries) == 1
        assert summaries[0]["episode_id"] == s.episodes[0].episode_id
        assert summaries[0]["status"] == "closed"

    def test_summary_stored_on_close_active_episode(self):
        s = _fresh_session()
        s.add_message("user", "message")
        s.close_active_episode()
        summaries = s.metadata.get("_episode_summaries", [])
        assert len(summaries) == 1

    def test_multiple_episodes_stack_summaries(self):
        s = _session_with_closed_episodes()
        summaries = s.metadata.get("_episode_summaries", [])
        assert len(summaries) == 2  # two closed episodes
        # Most recent first
        assert summaries[0]["label"] == "second topic"
        assert summaries[1]["label"] == ""

    def test_max_5_summaries(self):
        s = _fresh_session()
        for i in range(7):
            s.add_message("user", f"msg {i}")
            s.start_new_episode(f"ep{i}")
        summaries = s.metadata.get("_episode_summaries", [])
        assert len(summaries) <= 5

    def test_summary_contains_preview(self):
        s = _fresh_session()
        s.add_message("user", "hello, this is a test message")
        s.start_new_episode("test ep")
        summaries = s.metadata.get("_episode_summaries", [])
        assert "preview" in summaries[0]
        assert "hello" in summaries[0]["preview"]

    def test_summary_contains_message_count(self):
        s = _fresh_session()
        s.add_message("user", "a")
        s.add_message("assistant", "b")
        s.add_message("user", "c")
        s.start_new_episode()
        summaries = s.metadata.get("_episode_summaries", [])
        assert summaries[0]["message_count"] == 3

    def test_clear_resets_summaries(self):
        s = _session_with_closed_episodes()
        s.clear()
        assert s.metadata.get("_episode_summaries") is None


# ─── _build_episode_preview ─────────────────────────────────────────────────


class TestEpisodePreview:
    def test_preview_empty_episode(self):
        s = _fresh_session()
        s.ensure_active_episode()
        preview = s._build_episode_preview(s.episodes[0])
        assert "no user text" in preview

    def test_preview_single_message(self):
        s = _fresh_session()
        s.add_message("user", "short message")
        preview = s._build_episode_preview(s.episodes[0])
        assert "short message" in preview

    def test_preview_multiple_messages(self):
        s = _fresh_session()
        s.add_message("user", "first message")
        s.add_message("assistant", "reply")
        s.add_message("user", "last message")
        preview = s._build_episode_preview(s.episodes[0])
        assert "first message" in preview
        assert "last message" in preview

    def test_preview_truncates_long_content(self):
        s = _fresh_session()
        s.add_message("user", "x" * 200)
        preview = s._build_episode_preview(s.episodes[0])
        assert len(preview) < 250  # truncated


# ─── ContextBuilder closed-episode-summaries block ──────────────────────────


class TestClosedEpisodeSummariesBlock:
    def _make_context_builder(self, enable_episode_context: bool = False):
        """Create a minimal ContextBuilder with the given flag."""
        from OriginAgent.agent.context import ContextBuilder

        config = ContextConfig(enable_episode_context=enable_episode_context)
        builder = ContextBuilder(
            workspace=Path("/tmp/test_workspace"),
            context_config=config,
        )
        return builder

    def test_returns_empty_when_flag_disabled(self):
        builder = self._make_context_builder(enable_episode_context=False)
        blocks = builder.build_closed_episode_summaries_block(session_key="test:key")
        assert blocks == []

    def test_returns_empty_when_no_sessions_available(self):
        builder = self._make_context_builder(enable_episode_context=True)
        blocks = builder.build_closed_episode_summaries_block(session_key="test:key")
        assert blocks == []

    def test_returns_empty_when_no_summaries(self):
        builder = self._make_context_builder(enable_episode_context=True)
        mock_sessions = MagicMock()
        mock_session = MagicMock()
        mock_session.metadata = {}
        mock_sessions.get_or_create.return_value = mock_session
        builder._sessions = mock_sessions

        blocks = builder.build_closed_episode_summaries_block(session_key="test:key")
        assert blocks == []

    def test_returns_block_with_summaries(self):
        builder = self._make_context_builder(enable_episode_context=True)
        mock_sessions = MagicMock()
        mock_session = MagicMock()
        mock_session.metadata = {
            "_episode_summaries": [
                {
                    "episode_id": "ep1",
                    "label": "BDI design",
                    "preview": "5 msgs: \"what is BDI?\" ... \"got it\"",
                    "message_count": 5,
                    "status": "closed",
                    "started_at": "2026-06-27T10:00:00",
                },
                {
                    "episode_id": "ep0",
                    "label": "",
                    "preview": "3 msgs: \"hello\"",
                    "message_count": 3,
                    "status": "closed",
                    "started_at": "2026-06-27T09:30:00",
                },
            ],
        }
        mock_sessions.get_or_create.return_value = mock_session
        builder._sessions = mock_sessions

        blocks = builder.build_closed_episode_summaries_block(session_key="test:key")

        assert len(blocks) == 1
        block = blocks[0]
        assert block.get("_meta", {}).get("kind") == "reference_context"
        assert block["_meta"]["source"] == "closed_episode_summaries"
        text = block.get("text", "")
        assert "BDI design" in text
        assert "hello" in text
        assert "earlier in this conversation" in text

    def test_block_included_in_reference_context_blocks(self):
        """When flag is enabled, build_reference_context_blocks includes episode summaries."""
        builder = self._make_context_builder(enable_episode_context=True)
        mock_sessions = MagicMock()
        mock_session = MagicMock()
        mock_session.metadata = {
            "_episode_summaries": [
                {
                    "episode_id": "ep1",
                    "label": "BDI",
                    "preview": "2 msgs: \"hello\"",
                    "message_count": 2,
                    "status": "closed",
                },
            ],
        }
        mock_sessions.get_or_create.return_value = mock_session
        builder._sessions = mock_sessions
        builder.memory = MagicMock()
        builder.memory.read_unprocessed_history.return_value = []
        builder.nearline_memory = MagicMock()
        builder.retrieval_fusion = MagicMock()
        builder.retrieval_fusion.retrieve.return_value = MagicMock(
            retrieved_blocks=[],
            audit={},
        )

        blocks = builder.build_reference_context_blocks(
            session_key="test:key",
            runtime_context=MagicMock(),
        )

        sources = [
            b.get("_meta", {}).get("source")
            for b in blocks
            if isinstance(b, dict)
        ]
        assert "closed_episode_summaries" in sources


# ─── History scoping via state_build ────────────────────────────────────────


class TestHistoryScoping:
    def test_episode_scoped_history_has_fewer_messages(self):
        """When episode context is enabled, history only includes active episode messages."""
        s = _fresh_session()
        # Episode 0: 4 messages
        s.add_message("user", "topic A q1")
        s.add_message("assistant", "topic A a1")
        s.add_message("user", "topic A q2")
        s.add_message("assistant", "topic A a2")
        s.start_new_episode("B")
        # Episode 1: 4 messages
        s.add_message("user", "topic B q1")
        s.add_message("assistant", "topic B a1")
        s.add_message("user", "topic B q2")
        s.add_message("assistant", "topic B a2")

        # Normal history: all 8 unconsolidated messages
        full_history = s.get_history(max_messages=100)
        assert len(full_history) == 8

        # Episode-scoped: only episode 1's 4 messages
        ep_history = s.get_episode_history(s.episodes[1].episode_id)
        assert len(ep_history) == 4
        assert all("B" in m.get("content", "") for m in ep_history)

    def test_get_episode_history_respects_token_budget(self, monkeypatch):
        s = _fresh_session()
        s.add_message("user", "q1")
        s.add_message("assistant", "a1")
        s.add_message("user", "q2")
        s.add_message("assistant", "a2")

        token_map = {"q1": 100, "a1": 100, "q2": 100, "a2": 100}
        monkeypatch.setattr(
            "OriginAgent.session.manager.estimate_message_tokens",
            lambda message: token_map.get(message.get("content"), 0),
        )

        ep_id = s.episodes[0].episode_id
        hist = s.get_episode_history(ep_id, max_tokens=120)
        # Should keep only the most recent pair within budget
        assert len(hist) == 2
        assert hist[-1]["content"] == "a2"

    def test_get_episode_history_includes_timestamps(self):
        s = _fresh_session()
        s.add_message("user", "hello")
        ep_id = s.episodes[0].episode_id
        hist = s.get_episode_history(ep_id, include_timestamps=True)
        assert hist[0]["content"].startswith("[Message Time:")

    def test_closed_episodes_not_in_episode_history(self):
        """get_episode_history for the active episode should only return its messages."""
        s = _session_with_closed_episodes()
        active = s.active_episode
        assert active is not None
        hist = s.get_episode_history(active.episode_id)
        # Only the third topic's messages
        assert len(hist) == 1
        assert "something else" in hist[0]["content"]


# ─── Feature flag integration ────────────────────────────────────────────────


class TestFeatureFlag:
    def test_default_is_true(self):
        config = ContextConfig()
        assert config.enable_episode_context is True

    def test_can_be_enabled(self):
        config = ContextConfig(enable_episode_context=True)
        assert config.enable_episode_context is True

    def test_accepts_camel_case_alias(self):
        config = ContextConfig.model_validate({"enableEpisodeContext": True})
        assert config.enable_episode_context is True


# ─── Integration: episode summary roundtrip via save/load ────────────────────


class TestIntegration:
    def test_summaries_survive_save_and_load(self):
        """Episode summaries stored in metadata persist through save/load."""
        from OriginAgent.session.manager import SessionManager

        test_dir = Path(__file__).resolve().parent.parent / "_test_tmp"
        test_dir.mkdir(parents=True, exist_ok=True)
        import tempfile
        actual_dir = Path(tempfile.mkdtemp(dir=str(test_dir)))
        try:
            manager = SessionManager(actual_dir)

            s = manager.get_or_create("test:summaries")
            s.add_message("user", "first topic")
            s.start_new_episode("second")
            s.add_message("user", "second topic")
            s.close_active_episode()
            manager.save(s)

            manager.invalidate("test:summaries")
            loaded = manager.get_or_create("test:summaries")

            summaries = loaded.metadata.get("_episode_summaries", [])
            assert len(summaries) == 2
            assert summaries[0]["label"] == "second"
            assert summaries[1]["label"] == ""
        finally:
            import shutil
            shutil.rmtree(actual_dir, ignore_errors=True)
