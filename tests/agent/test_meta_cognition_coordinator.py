"""Tests for MetaCognitionCoordinator."""
import pytest
from unittest.mock import MagicMock
from OriginAgent.agent.meta_cognition_coordinator import MetaCognitionCoordinator


@pytest.fixture
def coordinator():
    """Create a coordinator with mocked dependencies."""
    runtime = MagicMock()
    reflector = MagicMock()
    regulator = MagicMock()
    return MetaCognitionCoordinator(
        runtime=runtime,
        reflector=reflector,
        regulator=regulator,
        config=MagicMock(),
    )


class TestMetaCognitionCoordinator:
    def test_start_turn_delegates_to_runtime(self, coordinator):
        coordinator.start_turn("turn_1")
        coordinator._runtime.start_turn.assert_called_once_with("turn_1")

    def test_end_turn_delegates_to_runtime(self, coordinator):
        coordinator.start_turn("turn_1")
        coordinator.end_turn("turn_1")
        coordinator._runtime.end_turn.assert_called_once_with("turn_1")

    def test_scan_triggers_returns_empty_when_disabled(self, coordinator):
        coordinator._runtime.enabled = False
        result = coordinator.scan_triggers_for_turn(
            turn_id="t1", session_key="s1"
        )
        assert result == []

    def test_get_status_returns_runtime_status(self, coordinator):
        coordinator._runtime.summary.return_value = {"enabled": True}
        status = coordinator.get_status()
        assert status["enabled"] is True

    def test_record_trigger_delegates_to_runtime(self, coordinator):
        trigger = MagicMock()
        trigger.trigger_id = "t1"
        trigger.trigger_type = "user_correction"
        trigger.session_key = "s1"
        trigger.source_type = "test"
        trigger.source_reference = "ref1"
        result = MagicMock()
        result.decision = "accepted"
        result.accepted = True
        result.suppression_reason = None
        coordinator._runtime.record_trigger.return_value = result
        coordinator._runtime.summary.return_value = {"enabled": True}

        coordinator.record_trigger(trigger, turn_id="turn_1")

        coordinator._runtime.record_trigger.assert_called_once_with(trigger, turn_id="turn_1")
        assert coordinator._last_meta_trigger_scan == [
            {
                "trigger_id": "t1",
                "trigger_type": "user_correction",
                "decision": "accepted",
                "accepted": True,
                "suppression_reason": None,
            }
        ]

    def test_fast_path_refs_tracking(self, coordinator):
        coordinator.add_fast_path_ref("ref1")
        coordinator.add_fast_path_ref("ref2")
        assert coordinator.fast_path_refs == {"ref1", "ref2"}

        coordinator.reset_fast_path()
        assert coordinator.fast_path_refs == set()

    def test_fast_path_decision_counting(self, coordinator):
        coordinator.record_fast_path_decision("fast_path_working_memory_written")
        assert coordinator.summary["fast_path_decision_counts"]["fast_path_working_memory_written"] == 1

        coordinator.record_fast_path_decision("fast_path_working_memory_written")
        assert coordinator.summary["fast_path_decision_counts"]["fast_path_working_memory_written"] == 2

    def test_summary_property(self, coordinator):
        coordinator._last_meta_cognition_summary = {"enabled": True, "artifact_status": {}}
        assert coordinator.summary["enabled"] is True
        assert coordinator.summary["artifact_status"] == {}

    def test_last_artifacts_property(self, coordinator):
        coordinator._last_meta_artifacts = {"recent_journals": [{"id": "j1"}]}
        assert coordinator.last_artifacts["recent_journals"] == [{"id": "j1"}]

    def test_runtime_reset_turn_delegates(self, coordinator):
        coordinator.runtime_reset_turn("turn_1")
        coordinator._runtime.reset_turn.assert_called_once_with("turn_1")

    def test_on_reflection_complete_updates_state(self, coordinator):
        reflector = MagicMock()
        reflector.recent_artifacts.return_value = {
            "recent_journals": [{"entry_id": "j1"}],
        }
        reflector.runtime_status.return_value = {
            "artifact_status": {"journals_written": 1},
        }

        coordinator.on_reflection_complete(reflector)

        assert coordinator.last_artifacts["recent_journals"] == [{"entry_id": "j1"}]
        assert coordinator.summary["artifact_status"]["journals_written"] == 1
