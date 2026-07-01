"""Tests for CS-007: MetaCognitionRegulator — monitoring, depth control, budget."""

from OriginAgent.agent.meta_cognition_regulator import (
    MetaCognitionRegulator,
    RegulatorRecommendation,
    RegulatorSnapshot,
)


class TestRegulatorSnapshot:
    def test_defaults(self) -> None:
        s = RegulatorSnapshot()
        assert s.collected_at != ""
        assert s.avg_trigger_acceptance_rate == 0.0

    def test_full_fields(self) -> None:
        s = RegulatorSnapshot(
            avg_trigger_acceptance_rate=0.8,
            recent_tool_failure_count=3,
            recent_user_correction_count=1,
            active_reflections_running=2,
            triggers_accepted_last_minute=10,
            triggers_suppressed_last_minute=2,
            pattern_count=5,
            journal_count=10,
            consecutive_reflection_failures=0,
            thought_production_rate=2.0,
            estimated_token_load=0.4,
        )
        assert s.avg_trigger_acceptance_rate == 0.8
        assert s.triggers_accepted_last_minute == 10


class TestRegulatorRecommendation:
    def test_defaults(self) -> None:
        r = RegulatorRecommendation(action="noop", reason="ok", priority="low")
        assert r.payload == {}


class TestMetaCognitionRegulator:
    def test_initial_snapshot(self) -> None:
        reg = MetaCognitionRegulator()
        snap = reg.snapshot()
        assert snap.avg_trigger_acceptance_rate == 0.0
        assert snap.active_reflections_running == 0

    def test_record_trigger_decision(self) -> None:
        reg = MetaCognitionRegulator()
        reg.record_trigger_decision(accepted=True, trigger_type="tool_failure")
        snap = reg.snapshot()
        assert snap.recent_tool_failure_count == 1
        assert snap.triggers_accepted_last_minute == 1

    def test_record_trigger_suppressed(self) -> None:
        reg = MetaCognitionRegulator()
        for _ in range(8):
            reg.record_trigger_decision(accepted=True, trigger_type="tool_failure")
        for _ in range(12):
            reg.record_trigger_decision(accepted=False, trigger_type="tool_failure")
        snap = reg.snapshot()
        assert snap.triggers_suppressed_last_minute == 12

    def test_reflection_lifecycle(self) -> None:
        reg = MetaCognitionRegulator()
        reg.record_reflection_start()
        reg.record_reflection_start()
        assert reg.snapshot().active_reflections_running == 2
        reg.record_reflection_end(success=True)
        assert reg.snapshot().active_reflections_running == 1

    def test_consecutive_failures(self) -> None:
        reg = MetaCognitionRegulator()
        for _ in range(5):
            reg.record_reflection_start()
            reg.record_reflection_end(success=False)
        assert reg.snapshot().consecutive_reflection_failures == 5


class TestRecommendations:
    def test_noop_when_normal(self) -> None:
        reg = MetaCognitionRegulator()
        # Record moderate activity so all signals stay in normal range
        for _ in range(5):
            reg.record_trigger_decision(accepted=True, trigger_type="task_completion")
            reg.record_reflection_start()
            reg.record_reflection_end(success=True)
        recs = reg.recommend()
        assert len(recs) >= 1
        assert recs[0].action == "noop"

    def test_cool_down_on_high_suppression(self) -> None:
        reg = MetaCognitionRegulator()
        for _ in range(3):
            reg.record_trigger_decision(accepted=True, trigger_type="task_completion")
        for _ in range(7):
            reg.record_trigger_decision(accepted=False, trigger_type="tool_failure")
        recs = reg.recommend()
        actions = [r.action for r in recs]
        assert "cool_down" in actions

    def test_cool_down_on_consecutive_failures(self) -> None:
        reg = MetaCognitionRegulator()
        for _ in range(4):
            reg.record_reflection_start()
            reg.record_reflection_end(success=False)
        recs = reg.recommend()
        assert any(r.action == "cool_down" and r.priority == "high" for r in recs)

    def test_switch_to_slow_on_low_confidence(self) -> None:
        reg = MetaCognitionRegulator()
        # Very low acceptance rate
        for _ in range(10):
            reg.record_trigger_decision(accepted=False, trigger_type="tool_failure")
        recs = reg.recommend()
        assert any(r.action == "switch_to_slow" for r in recs)

    def test_verify_first_on_high_corrections(self) -> None:
        reg = MetaCognitionRegulator()
        for _ in range(3):
            reg.record_trigger_decision(accepted=True, trigger_type="user_correction")
        for _ in range(3):
            reg.record_trigger_decision(accepted=True, trigger_type="task_completion")
        recs = reg.recommend()
        assert any(r.action == "verify_first" for r in recs)

    def test_deepen_on_high_failures(self) -> None:
        reg = MetaCognitionRegulator()
        for _ in range(3):
            reg.record_trigger_decision(accepted=True, trigger_type="tool_failure")
        for _ in range(5):
            reg.record_trigger_decision(accepted=True, trigger_type="task_completion")
        recs = reg.recommend()
        assert any(r.action == "deepen_reflection" for r in recs)

    def test_generate_evolution_seed_on_high_patterns(self) -> None:
        reg = MetaCognitionRegulator()
        # Manually create a snapshot with high pattern count
        snap = RegulatorSnapshot(pattern_count=15, estimated_token_load=0.2)
        recs = reg.recommend(snap)
        assert any(r.action == "generate_evolution_seed" for r in recs)

    def test_cool_down_on_high_token_load(self) -> None:
        reg = MetaCognitionRegulator()
        snap = RegulatorSnapshot(estimated_token_load=0.95)
        recs = reg.recommend(snap)
        assert any(r.action == "cool_down" for r in recs)

    def test_status_output(self) -> None:
        reg = MetaCognitionRegulator()
        reg.record_trigger_decision(accepted=True, trigger_type="task_completion")
        status = reg.status()
        assert "snapshot" in status
        assert "recommendations" in status
        assert "active_sessions" in status
        assert isinstance(status["recommendations"], list)
