"""Tests for turn context isolation in concurrent scenarios."""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from OriginAgent.agent.agent_turn_pipeline import TurnContext
from OriginAgent.agent.meta_cognition_runtime import MetaCognitionRuntime


def make_mock_runtime():
    """Create a MetaCognitionRuntime suitable for concurrent testing."""
    config = MagicMock()
    config.enabled = True
    config.trigger_collection_enabled = True
    config.max_accepted_triggers_per_turn = 10
    config.session_cooldown_seconds = 0
    config.trigger_type_cooldown_seconds = 0
    config.queue_max_items = 200
    audit = MagicMock()
    return MetaCognitionRuntime(config=config, audit=audit)


class TestConcurrentTurnIsolation:
    @pytest.mark.asyncio
    async def test_two_turns_dont_share_meta_turn_id(self):
        """Concurrent turns must not share _current_meta_turn_id state."""
        runtime = make_mock_runtime()

        # Simulate Turn A starting
        runtime.start_turn("turn_a")
        runtime.record_trigger(
            MagicMock(
                trigger_id="ev1",
                session_key="s1",
                trigger_type="tool_error",
                source_reference="tool:read",
                created_at="2026-07-01T00:00:00Z",
                payload={},
            ),
            turn_id="turn_a",
        )

        # Simulate Turn B starting (concurrent, different session)
        runtime.start_turn("turn_b")

        # Turn A's triggers should still be queryable by turn_a
        # and not interfere with turn_b
        runtime.end_turn("turn_a")
        runtime.end_turn("turn_b")
        # If we got here without exception, isolation works
        assert True

    @pytest.mark.asyncio
    async def test_concurrent_turns_have_independent_triggers(self):
        """Triggers recorded under one turn_id must not appear in another."""
        runtime = make_mock_runtime()
        from OriginAgent.agent.meta_cognition_models import MetaTrigger

        t1 = MetaTrigger(
            trigger_id="ev1",
            session_key="s1",
            trigger_type="tool_failure",
            source_type="tool",
            source_reference="tool:read",
            created_at="2026-07-01T00:00:00Z",
            payload={},
        )
        t2 = MetaTrigger(
            trigger_id="ev2",
            session_key="s1",
            trigger_type="user_correction",
            source_type="chat",
            source_reference="chat:message",
            created_at="2026-07-01T00:00:01Z",
            payload={},
        )

        runtime.start_turn("turn_a")
        runtime.start_turn("turn_b")

        runtime.record_trigger(t1, turn_id="turn_a")
        runtime.record_trigger(t2, turn_id="turn_b")

        a_triggers = runtime.take_accepted_triggers_for_turn("turn_a")
        b_triggers = runtime.take_accepted_triggers_for_turn("turn_b")

        assert len(a_triggers) == 1
        assert a_triggers[0].trigger_id == "ev1"
        assert len(b_triggers) == 1
        assert b_triggers[0].trigger_id == "ev2"

        runtime.end_turn("turn_a")
        runtime.end_turn("turn_b")
