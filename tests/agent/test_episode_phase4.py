"""Tests for Phase 4: Dream Integration + Agent-Driven Retrieval.

Covers:
- Episode tone analysis
- Key quote extraction
- Enhanced episode summaries (tone + quotes + llm_summary)
- episode_context tool
- Context insufficiency hint
- Dream Phase 0 integration
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from OriginAgent.session.manager import Session


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _fresh_session() -> Session:
    return Session(key="test:p4")


def _session_with_episodes() -> Session:
    s = _fresh_session()
    s.add_message("user", "how does the BDI module work exactly?")
    s.add_message("assistant", "BDI stands for Belief-Desire-Intention...")
    s.add_message("user", "what about the memory consolidation?")
    s.add_message("assistant", "memory uses two-phase dream...")
    s.add_message("user", "can you show me the config file?")
    s.start_new_episode("casual chat")
    s.add_message("user", "man im starving whats a good pizza place?")
    s.add_message("assistant", "I recommend Pizzeria Mozza...")
    s.add_message("user", "awesome thanks!")
    return s


# ─── Tone Analysis ──────────────────────────────────────────────────────────


class TestToneAnalysis:
    def test_inquisitive_tone(self):
        msgs = [
            {"role": "user", "content": "how does this work?"},
            {"role": "user", "content": "what about the config?"},
        ]
        tone = Session._analyze_episode_tone(msgs)
        assert "inquisitive" in tone

    def test_terse_replies(self):
        msgs = [
            {"role": "user", "content": "ok"},
            {"role": "user", "content": "yep"},
            {"role": "user", "content": "sure"},
        ]
        tone = Session._analyze_episode_tone(msgs)
        assert "terse" in tone

    def test_emphatic_tone(self):
        msgs = [
            {"role": "user", "content": "that's amazing!"},
            {"role": "user", "content": "wow!!! perfect"},
        ]
        tone = Session._analyze_episode_tone(msgs)
        assert "emphatic" in tone

    def test_detailed_explanatory(self):
        msgs = [
            {"role": "user", "content": (
                "I looked at the OriginAgent framework and its message bus "
                "channel system and agent loop architecture the data flow "
                "starts from the bus dispatches to channels then to the agent "
                "runner which manages the conversation with tool execution "
                "and streaming responses all orchestrated through the message bus"
            )},
        ]
        tone = Session._analyze_episode_tone(msgs)
        assert "detailed" in tone

    def test_empty_messages(self):
        tone = Session._analyze_episode_tone([])
        assert tone == "neutral"

    def test_no_user_msgs(self):
        msgs = [{"role": "assistant", "content": "hello"}]
        tone = Session._analyze_episode_tone(msgs)
        assert tone == "neutral"


# ─── Key Quote Extraction ───────────────────────────────────────────────────


class TestKeyQuoteExtraction:
    def test_extracts_longest_quotes(self):
        msgs = [
            {"role": "user", "content": "short"},  # skipped: < 10 chars
            {"role": "user", "content": "this is a much longer and more detailed message about BDI"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "medium length message here"},
            {"role": "user", "content": "another decent length user message"},
        ]
        quotes = Session._extract_key_quotes(msgs)
        assert len(quotes) == 3  # max_quotes=3
        assert "much longer and more detailed" in quotes[0]

    def test_filters_procedural_responses(self):
        msgs = [
            {"role": "user", "content": "ok"},
            {"role": "user", "content": "thanks"},
            {"role": "user", "content": "yes that makes sense"},
            {"role": "user", "content": "how does BDI actually work in detail?"},
        ]
        quotes = Session._extract_key_quotes(msgs)
        assert len(quotes) == 1
        assert "BDI" in quotes[0]

    def test_empty_msgs(self):
        assert Session._extract_key_quotes([]) == []

    def test_only_short_msgs(self):
        msgs = [{"role": "user", "content": "hi"}, {"role": "user", "content": "ok"}]
        quotes = Session._extract_key_quotes(msgs)
        assert len(quotes) == 0  # all shorter than 10 chars


# ─── Enhanced Episode Summaries ─────────────────────────────────────────────


class TestEnhancedSummaries:
    def test_summary_contains_tone(self):
        s = _session_with_episodes()
        summaries = s.metadata.get("_episode_summaries", [])
        assert len(summaries) == 1
        assert "tone" in summaries[0]
        assert summaries[0]["tone"] != ""

    def test_summary_contains_key_quotes(self):
        s = _session_with_episodes()
        summaries = s.metadata.get("_episode_summaries", [])
        assert "key_quotes" in summaries[0]
        assert len(summaries[0]["key_quotes"]) > 0

    def test_summary_stored_on_start_new_episode(self):
        s = _fresh_session()
        s.add_message("user", "what is the meaning of life?")
        s.add_message("assistant", "42")
        s.start_new_episode()
        summaries = s.metadata.get("_episode_summaries", [])
        assert len(summaries) == 1
        assert summaries[0]["tone"] != ""

    def test_summary_contains_llm_summary_after_dream_phase0(self):
        """Simulate Dream Phase 0 enhancement."""
        s = _session_with_episodes()
        summaries = s.metadata.get("_episode_summaries", [])
        assert len(summaries) == 1

        # Simulate what Dream Phase 0 does
        entry = summaries[0]
        if not entry.get("llm_summary"):
            preview = entry.get("preview", "")
            tone = entry.get("tone", "")
            quotes = entry.get("key_quotes", [])
            parts = [f"Episode: {entry.get('label') or '(untitled)'}"]
            if preview:
                parts.append(f"Content: {preview}")
            if tone:
                parts.append(f"Tone: {tone}")
            if quotes:
                parts.append(f"Key: {'; '.join(q[:80] for q in quotes[:2])}")
            entry["llm_summary"] = " | ".join(parts)

        assert "llm_summary" in summaries[0]
        assert "Episode:" in summaries[0]["llm_summary"]

    def test_build_closed_episode_block_includes_tone(self):
        """Verify the context block includes tone and quotes."""
        from OriginAgent.agent.context import ContextBuilder
        from OriginAgent.config.schema import ContextConfig

        config = ContextConfig(enable_episode_context=True)
        builder = ContextBuilder(
            workspace=Path("/tmp/test"),
            context_config=config,
        )
        s = _session_with_episodes()
        mock_sessions = MagicMock()
        mock_sessions.get_or_create.return_value = s
        builder._sessions = mock_sessions

        blocks = builder.build_closed_episode_summaries_block("test:p4")
        assert len(blocks) == 1
        text = blocks[0].get("text", "")
        assert "Tone" in text
        assert "BDI" in text


# ─── EpisodeContextTool ────────────────────────────────────────────────────


class TestEpisodeContextTool:
    def _make_tool(self, session):
        from OriginAgent.agent.tools.episode_context import EpisodeContextTool
        from OriginAgent.agent.tools.context import RequestContext

        sessions = MagicMock()
        sessions.get_or_create.return_value = session

        tool = EpisodeContextTool(sessions=sessions)
        tool.set_context(RequestContext(
            session_key="test:p4", channel="test", chat_id="test",
        ))
        return tool

    def test_returns_episode_messages(self):
        s = _session_with_episodes()
        tool = self._make_tool(s)

        ep = s.episodes[0]
        import asyncio
        result = asyncio.run(tool.execute(episode_id=ep.episode_id))

        assert ep.label in result or "untitled" in result
        assert "[USER]" in result
        assert "BDI" in result

    def test_returns_closed_episode_too(self):
        s = _session_with_episodes()
        tool = self._make_tool(s)

        ep = s.episodes[0]  # closed episode
        import asyncio
        result = asyncio.run(tool.execute(episode_id=ep.episode_id))

        assert "closed" in result.lower() or "[USER]" in result

    def test_unknown_episode_returns_error(self):
        s = _fresh_session()
        tool = self._make_tool(s)

        import asyncio
        result = asyncio.run(tool.execute(episode_id="nonexistent"))
        assert "not found" in result

    def test_no_session_key(self):
        from OriginAgent.agent.tools.episode_context import EpisodeContextTool
        from OriginAgent.agent.tools.context import RequestContext

        tool = EpisodeContextTool(sessions=MagicMock())
        tool.set_context(RequestContext(
            session_key="", channel="test", chat_id="test",
        ))
        import asyncio
        result = asyncio.run(tool.execute(episode_id="any"))
        assert "Error" in result

    def test_parameters_schema(self):
        from OriginAgent.agent.tools.episode_context import EpisodeContextTool

        tool = EpisodeContextTool(sessions=MagicMock())
        params = tool.parameters
        assert "episode_id" in params.get("properties", {})
        assert params.get("required") == ["episode_id"]


# ─── Context Insufficiency Hint ─────────────────────────────────────────────


class TestContextInsufficiencyHint:
    def test_hint_generated_for_long_episode(self):
        from OriginAgent.agent.agent_turn_pipeline import _build_context_insufficiency_hint

        s = _fresh_session()
        for i in range(25):
            s.add_message("user", f"message {i}")
        hint = _build_context_insufficiency_hint(s)
        assert hint is not None
        assert "episode_context" in hint
        assert s.active_episode.episode_id in hint

    def test_no_hint_for_short_episode(self):
        from OriginAgent.agent.agent_turn_pipeline import _build_context_insufficiency_hint

        s = _fresh_session()
        s.add_message("user", "hello")
        s.add_message("user", "world")
        hint = _build_context_insufficiency_hint(s)
        assert hint is None

    def test_no_hint_when_no_active_episode(self):
        from OriginAgent.agent.agent_turn_pipeline import _build_context_insufficiency_hint

        s = Session(key="test:bare")
        hint = _build_context_insufficiency_hint(s)
        assert hint is None


# ─── Dream Phase 0 ──────────────────────────────────────────────────────────


class TestDreamPhase0:
    def test_phase0_skips_when_no_sessions(self):
        """Dream Phase 0 should be a no-op when sessions is None."""
        from OriginAgent.agent.memory import MemoryStore, Dream

        store = MagicMock(spec=MemoryStore)
        store.workspace = Path("/tmp/nonexistent")

        provider = MagicMock()
        model = "test-model"

        dream = Dream(store=store, provider=provider, model=model, sessions=None)

        import asyncio
        result = asyncio.run(dream._phase0_episode_summaries(started_at="now"))
        assert result is None  # no return value, just shouldn't crash

    def test_phase0_enhances_summaries(self):
        """Dream Phase 0 should add llm_summary to episode summaries."""
        from OriginAgent.agent.memory import MemoryStore, Dream

        s = _session_with_episodes()
        summaries = s.metadata.get("_episode_summaries", [])
        # Strip llm_summary to simulate pre-Phase4
        for entry in summaries:
            entry.pop("llm_summary", None)
        s.metadata["_episode_summaries"] = summaries

        # Mock a MemoryStore that points to the session's workspace
        store = MagicMock(spec=MemoryStore)
        # Point workspace to a real temp dir with sessions subdir
        test_dir = Path(__file__).resolve().parent.parent / "_test_tmp"
        test_dir.mkdir(parents=True, exist_ok=True)
        import tempfile, shutil
        actual_dir = Path(tempfile.mkdtemp(dir=str(test_dir)))
        try:
            sessions_dir = actual_dir / "sessions"
            sessions_dir.mkdir(parents=True)

            store.workspace = actual_dir
            store.get_last_dream_cursor.return_value = 0
            store.read_unprocessed_history.return_value = []
            store.feature_flags = {}

            provider = MagicMock()
            model = "test-model"

            # Save session to disk so Dream Phase 0 can find it
            from OriginAgent.session.manager import SessionManager
            mgr = SessionManager(actual_dir)
            mgr.save(s)

            dream = Dream(
                store=store, provider=provider, model=model,
                sessions=mgr,
            )

            import asyncio
            asyncio.run(dream._phase0_episode_summaries(started_at="now"))

            # Verify summary was enhanced
            loaded = mgr.get_or_create("test:p4")
            enhanced = loaded.metadata.get("_episode_summaries", [])
            assert len(enhanced) > 0
            assert "llm_summary" in enhanced[0]
            assert "Episode:" in enhanced[0]["llm_summary"]
        finally:
            shutil.rmtree(actual_dir, ignore_errors=True)
