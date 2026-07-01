"""Tests for turn-isolated dedup in MetaCognitionRuntime."""
import pytest
from unittest.mock import MagicMock
from OriginAgent.agent.meta_cognition_models import MetaTrigger, RecordTriggerResult
from OriginAgent.agent.meta_cognition_runtime import MetaCognitionRuntime


def make_trigger(
    session_key: str = "s1",
    trigger_type: str = "tool_failure",
    source_reference: str = "tool:read_file",
    source_type: str = "tool",
    trigger_id: str | None = None,
    created_at: str = "2026-07-01T00:00:00Z",
) -> MetaTrigger:
    tid = trigger_id or f"{session_key}:{trigger_type}:{source_reference}"
    return MetaTrigger(
        trigger_id=tid,
        session_key=session_key,
        trigger_type=trigger_type,
        source_type=source_type,
        source_reference=source_reference,
        created_at=created_at,
        payload={},
    )


@pytest.fixture
def runtime():
    """Create a MetaCognitionRuntime with collection enabled."""
    config = MagicMock()
    config.enabled = True
    config.trigger_collection_enabled = True
    config.max_accepted_triggers_per_turn = 10
    config.session_cooldown_seconds = 0
    config.trigger_type_cooldown_seconds = 0
    config.queue_max_items = 200
    audit = MagicMock()
    return MetaCognitionRuntime(config=config, audit=audit)


class TestTurnIsolation:
    def test_same_source_accepted_in_different_turns(self, runtime):
        """Same source+type should be accepted in different turns — not suppressed as dup."""
        trigger_a = make_trigger(session_key="s1")

        # Turn A: accept the trigger
        result1 = runtime.record_trigger(trigger_a, turn_id="turn_a")
        assert result1.accepted is True

        # Turn B: same source — should ALSO be accepted (different turn)
        result2 = runtime.record_trigger(trigger_a, turn_id="turn_b")
        assert result2.accepted is True, (
            f"Same source in different turn should be accepted, got {result2.decision}"
        )

    def test_duplicate_source_suppressed_within_same_turn(self, runtime):
        """Same source+type within the SAME turn should be suppressed."""
        trigger_a = make_trigger(session_key="s1")

        result1 = runtime.record_trigger(trigger_a, turn_id="turn_a")
        assert result1.accepted is True

        # Same trigger, same turn → suppressed
        result2 = runtime.record_trigger(trigger_a, turn_id="turn_a")
        assert result2.accepted is False
        assert result2.decision == "suppressed_duplicate"

    def test_end_turn_cleans_up_dedup_state(self, runtime):
        """After end_turn, a new turn with same source should not see old dedup state."""
        trigger_a = make_trigger(session_key="s1")

        runtime.record_trigger(trigger_a, turn_id="turn_a")
        runtime.end_turn("turn_a")

        # New turn B — should be accepted (dedup state for turn_a is gone)
        result = runtime.record_trigger(trigger_a, turn_id="turn_b")
        assert result.accepted is True

    def test_take_accepted_triggers_returns_only_that_turn(self, runtime):
        """take_accepted_triggers_for_turn returns triggers for the specific turn."""
        t1 = make_trigger(source_reference="tool:read", trigger_id="id1")
        t2 = make_trigger(source_reference="tool:write", trigger_id="id2")

        runtime.record_trigger(t1, turn_id="turn_a")
        runtime.record_trigger(t2, turn_id="turn_b")

        a_triggers = runtime.take_accepted_triggers_for_turn("turn_a")
        b_triggers = runtime.take_accepted_triggers_for_turn("turn_b")

        assert len(a_triggers) == 1
        assert a_triggers[0].source_reference == "tool:read"
        assert len(b_triggers) == 1
        assert b_triggers[0].source_reference == "tool:write"
