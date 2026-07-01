"""Integration tests for concurrent multi-session safety."""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.bus.queue import MessageBus


class TestConcurrentSessionIsolation:
    @pytest.mark.asyncio
    async def test_message_bus_handles_concurrent_publishers(self):
        """Multiple concurrent publishers must not cause data loss or cross-talk."""
        bus = MessageBus(maxsize=100)

        async def publisher(session_id: str, count: int):
            results = []
            for i in range(count):
                msg = InboundMessage(
                    channel="test",
                    sender_id=f"user_{session_id}",
                    content=f"msg {i} from {session_id}",
                    chat_id=f"chat_{session_id}",
                    session_key_override=session_id,
                )
                ok = await bus.publish_inbound(msg)
                results.append(ok)
            return results

        # Run 10 concurrent publishers, each sending 5 messages
        tasks = [
            publisher(f"session_{i}", 5)
            for i in range(10)
        ]
        all_results = await asyncio.gather(*tasks)

        # Every message should be accepted (queue is large enough)
        for i, results in enumerate(all_results):
            assert all(results), f"Session {i} had dropped messages: {results}"

        # Verify we can consume all 50 messages
        consumed = 0
        for _ in range(50):
            msg = await bus.consume_inbound()
            assert msg.session_key.startswith("session_")
            consumed += 1
        assert consumed == 50

    @pytest.mark.asyncio
    async def test_message_bus_drop_detection_under_pressure(self):
        """When queue overflows, drop must be detectable via return value."""
        bus = MessageBus(maxsize=2, overflow_timeout=0.01)

        # Fill the queue quickly
        ok1 = await bus.publish_inbound(
            InboundMessage(channel="t", sender_id="u1", content="1", chat_id="c", session_key_override="s1")
        )
        ok2 = await bus.publish_inbound(
            InboundMessage(channel="t", sender_id="u2", content="2", chat_id="c", session_key_override="s2")
        )
        assert ok1 is True
        assert ok2 is True

        # This should drop (no consumer draining, no persistence)
        ok3 = await bus.publish_inbound(
            InboundMessage(channel="t", sender_id="u3", content="3", chat_id="c", session_key_override="s3")
        )
        assert ok3 is False, "Third message should be dropped when queue is full"

        # Verify drop counter incremented
        assert bus.stats["dropped_inbound"] >= 1

    @pytest.mark.asyncio
    async def test_meta_cognition_runtime_concurrent_turns(self):
        """MetaCognitionRuntime must isolate concurrent turns."""
        from OriginAgent.agent.meta_cognition_runtime import MetaCognitionRuntime
        from OriginAgent.agent.meta_cognition_models import MetaTrigger

        config = MagicMock()
        config.enabled = True
        config.trigger_collection_enabled = True
        config.max_accepted_triggers_per_turn = 10
        config.session_cooldown_seconds = 0
        config.trigger_type_cooldown_seconds = 0
        config.queue_max_items = 200
        audit = MagicMock()
        runtime = MetaCognitionRuntime(config=config, audit=audit)

        # Simulate concurrent turns
        for turn_id in [f"turn_{i}" for i in range(20)]:
            runtime.start_turn(turn_id)

        for turn_id in [f"turn_{i}" for i in range(20)]:
            trigger = MetaTrigger(
                trigger_id=f"ev_{turn_id}",
                session_key="s1",
                trigger_type="tool_failure",
                source_type="test",
                source_reference=f"tool:test_{turn_id}",
                created_at="2026-07-01T00:00:00Z",
                payload={},
            )
            result = runtime.record_trigger(trigger, turn_id=turn_id)
            assert result.accepted is True, f"Trigger for {turn_id} should be accepted"

        # Each turn should have exactly 1 trigger
        for turn_id in [f"turn_{i}" for i in range(20)]:
            triggers = runtime.take_accepted_triggers_for_turn(turn_id)
            assert len(triggers) == 1, f"{turn_id} should have 1 trigger, got {len(triggers)}"

        # Cleanup
        for turn_id in [f"turn_{i}" for i in range(20)]:
            runtime.end_turn(turn_id)
