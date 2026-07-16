"""Tests for CS-004: MonologueFrame model + bridge + InnerMonologueEngine."""

from pathlib import Path
from typing import Any

from OriginAgent.agent.inner_monologue_engine import (
    InnerMonologueEngine,
    bridge_deliberation_to_monologue,
    derive_confidence_and_uncertainty,
)
from OriginAgent.agent.inner_monologue_models import MonologueFrame
from OriginAgent.bdi.models import DeliberationIntention, DeliberationResult, Desire, DesirePriority, DesireStatus


def _make_result(
    *,
    cycle_id: str = "cycle-1",
    reasoning: str = "Test reasoning about the current situation.",
    intentions: int = 1,
) -> DeliberationResult:
    return DeliberationResult(
        cycle_id=cycle_id,
        started_at="2026-07-01T00:00:00+00:00",
        finished_at="2026-07-01T00:00:10+00:00",
        desires_evaluated=max(1, intentions),
        intentions=[
            DeliberationIntention(
                desire_id=f"desire-{i}",
                action="send_message",
                scope="cli",
                reasoning=f"Intention reasoning {i}: because X happened.",
            )
            for i in range(intentions)
        ],
        reasoning=reasoning,
    )


def _make_desire(content: str) -> Desire:
    return Desire(
        desire_id=f"desire-{hash(content) % 1000}",
        owner_id="user",
        session_key="bdi:deliberation",
        content=content,
        status=DesireStatus.ACTIVE,
        priority=DesirePriority.MEDIUM,
    )


# ── MonologueFrame model tests ───────────────────────────────────────────────


class TestMonologueFrame:
    def test_full_fields(self) -> None:
        f = MonologueFrame(
            cycle_id="c1",
            session_key="sess-a",
            observation_summary="observed something",
            active_goal="fix the issue",
            candidate_hypotheses=["h1", "h2"],
            intended_strategy="strategy A",
            confidence=0.8,
            uncertainty_flags=["low_data"],
            created_at="2026-07-01T00:00:00+00:00",
        )
        assert f.cycle_id == "c1"
        assert f.confidence == 0.8
        assert f.candidate_hypotheses == ["h1", "h2"]

    def test_defaults(self) -> None:
        f = MonologueFrame(cycle_id="c2", session_key="sess-b")
        assert f.observation_summary == ""
        assert f.confidence == 0.0
        assert f.uncertainty_flags == []
        assert f.created_at != ""

    def test_normalization_clamps(self) -> None:
        f = MonologueFrame(cycle_id="c3", session_key="sess-c", confidence=99.9)
        assert f.confidence == 1.0

    def test_to_json_roundtrip(self) -> None:
        f = MonologueFrame(
            cycle_id="c4", session_key="sess-d",
            observation_summary="test", intended_strategy="plan",
            confidence=0.6,
        )
        data = f.to_json()
        restored = MonologueFrame.from_json(data)
        assert restored == f

    def test_from_json_missing_keys(self) -> None:
        raw = {"cycle_id": "c5", "session_key": "sess-e"}
        f = MonologueFrame.from_json(raw)
        assert f.intended_strategy == ""
        assert f.confidence == 0.0

    def test_frozen(self) -> None:
        f = MonologueFrame(cycle_id="c6", session_key="sess-f")
        try:
            f.observation_summary = "mutate"  # type: ignore[misc]
            assert False, "should be frozen"
        except Exception:
            pass


# ── Self-critique derivation tests ───────────────────────────────────────────


class TestDeriveConfidence:
    def test_base_confidence(self) -> None:
        result = _make_result(intentions=0, reasoning="")
        conf, flags = derive_confidence_and_uncertainty(result)
        assert conf == 0.5  # base
        assert "empty_reasoning" in flags

    def test_confidence_with_intentions(self) -> None:
        result = _make_result(intentions=2, reasoning="a well thought out plan with clear steps")
        conf, flags = derive_confidence_and_uncertainty(result)
        assert conf == 0.9  # 0.5 + 0.3 + 0.1
        assert "empty_reasoning" not in flags

    def test_confidence_stuck_with_desires(self) -> None:
        result = _make_result(intentions=0)
        desires = [_make_desire("do something")]
        conf, flags = derive_confidence_and_uncertainty(result, active_desires=desires)
        assert conf == 0.3  # 0.5 - 0.2
        assert "no_action_planned" in flags

    def test_no_desires_flag(self) -> None:
        result = _make_result(intentions=0)
        conf, flags = derive_confidence_and_uncertainty(result, active_desires=[])
        assert "no_desires" in flags

    def test_conflicting_desires_flag(self) -> None:
        result = _make_result(intentions=1)
        desires = [_make_desire(f"desire-{i}") for i in range(3)]
        conf, flags = derive_confidence_and_uncertainty(result, active_desires=desires)
        assert "conflicting_desires" in flags

    def test_reasoning_uncertain_flag(self) -> None:
        result = _make_result(intentions=1, reasoning="not sure about the root cause")
        conf, flags = derive_confidence_and_uncertainty(result)
        assert "reasoning_uncertain" in flags

    def test_case_insensitive_uncertainty(self) -> None:
        result = _make_result(intentions=1, reasoning="MAYBE it works")
        conf, flags = derive_confidence_and_uncertainty(result)
        assert "reasoning_uncertain" in flags

    def test_no_false_positive_uncertainty(self) -> None:
        """Word 'notable' should NOT match 'not sure' regex."""
        result = _make_result(intentions=1, reasoning="This is a notable result")
        conf, flags = derive_confidence_and_uncertainty(result)
        assert "reasoning_uncertain" not in flags


# ── Bridge function tests ────────────────────────────────────────────────────


class TestBridgeDeliberation:
    def test_happy_path(self) -> None:
        result = _make_result(intentions=2)
        frame = bridge_deliberation_to_monologue(
            result, session_key="bdi:deliberation",
            observation_summary="test obs",
            active_goal="test goal",
        )
        assert frame.cycle_id == "cycle-1"
        assert frame.session_key == "bdi:deliberation"
        assert frame.observation_summary == "test obs"
        assert frame.active_goal == "test goal"
        assert len(frame.candidate_hypotheses) == 2
        assert frame.confidence == 0.9

    def test_empty_intentions(self) -> None:
        result = _make_result(intentions=0, reasoning="")
        frame = bridge_deliberation_to_monologue(result, session_key="sess")
        assert frame.candidate_hypotheses == []
        assert frame.confidence == 0.5
        assert frame.intended_strategy == ""

    def test_active_goal_from_desires(self) -> None:
        result = _make_result(intentions=1)
        desires = [_make_desire("fix the network issue")]
        frame = bridge_deliberation_to_monologue(
            result, session_key="sess",
            active_desires=desires,
        )
        assert frame.active_goal == "fix the network issue"

    def test_active_goal_parameter_overrides_desires(self) -> None:
        result = _make_result(intentions=1)
        desires = [_make_desire("desire content")]
        frame = bridge_deliberation_to_monologue(
            result, session_key="sess",
            active_goal="parameter goal",
            active_desires=desires,
        )
        assert frame.active_goal == "parameter goal"

    def test_observation_fallback(self) -> None:
        result = _make_result(intentions=1, reasoning="my strategy")
        frame = bridge_deliberation_to_monologue(result, session_key="sess")
        # fallback uses reasoning text when observation_summary is empty
        assert frame.observation_summary == "my strategy"


# ── InnerMonologueEngine tests ───────────────────────────────────────────────


class TestInnerMonologueEngine:
    def test_disabled_returns_none(self) -> None:
        engine = InnerMonologueEngine(workspace=Path("/tmp"), enabled=False)
        result = _make_result()
        frame = None

        async def run() -> None:
            nonlocal frame
            frame = await engine.on_bdi_cycle(result)

        import asyncio
        asyncio.run(run())
        assert frame is None

    def test_no_engine_cycle_returns_none(self) -> None:
        engine = InnerMonologueEngine(workspace=Path("/tmp"), enabled=True)
        frame = None

        async def run() -> None:
            nonlocal frame
            frame = await engine.cycle()

        import asyncio
        asyncio.run(run())
        assert frame is None  # no deliberation engine attached

    def test_on_bdi_cycle_with_substrate(self, tmp_path: Path) -> None:
        from OriginAgent.agent.thought_substrate_store import ThoughtSubstrate

        substrate = ThoughtSubstrate(tmp_path)
        engine = InnerMonologueEngine(
            workspace=tmp_path,
            substrate=substrate,
            enabled=True,
        )
        result = _make_result(intentions=2, reasoning="check network and logs")
        frame = None

        async def run() -> None:
            nonlocal frame
            frame = await engine.on_bdi_cycle(result)

        import asyncio
        asyncio.run(run())
        assert frame is not None
        assert frame.confidence == 0.9
        # substrate should have an open frame
        open_frame = substrate.get_open_frame("bdi:deliberation")
        assert open_frame is not None
        assert open_frame.intended_strategy == "check network and logs"
        assert open_frame.confidence == 0.9
        assert open_frame.candidate_hypotheses == [
            "Intention reasoning 0: because X happened.",
            "Intention reasoning 1: because X happened.",
        ]

    def test_on_bdi_cycle_without_substrate(self) -> None:
        engine = InnerMonologueEngine(workspace=Path("/tmp"), enabled=True)
        result = _make_result(intentions=1)
        frame = None

        async def run() -> None:
            nonlocal frame
            frame = await engine.on_bdi_cycle(result)

        import asyncio
        asyncio.run(run())
        assert frame is not None
        assert frame.cycle_id == "cycle-1"

    def test_runtime_status(self) -> None:
        engine = InnerMonologueEngine(workspace=Path("/tmp"), enabled=True)
        status = engine.runtime_status()
        assert status["enabled"] is True
        assert "has_deliberation_engine" in status
        assert "has_substrate" in status

    def test_on_bdi_cycle_exception_handling(self) -> None:
        """Substrate failure should not crash the callback."""

        class BrokenSubstrate:
            def open_frame(self, **kwargs: Any) -> None:
                raise RuntimeError("simulated failure")

        engine = InnerMonologueEngine(
            workspace=Path("/tmp"),
            substrate=BrokenSubstrate(),  # type: ignore[arg-type]
            enabled=True,
        )
        result = _make_result(intentions=1)
        frame = None

        async def run() -> None:
            nonlocal frame
            frame = await engine.on_bdi_cycle(result)

        import asyncio
        asyncio.run(run())
        # Even though substrate failed, the MonologueFrame should still be returned
        assert frame is not None
        assert frame.cycle_id == "cycle-1"

    def test_cycle_with_mock_engine(self) -> None:
        """Test cycle() with a mock deliberation engine."""

        class MockEngine:
            async def run_cycle(self) -> DeliberationResult:
                return _make_result(intentions=2, reasoning="mock plan")

        engine = InnerMonologueEngine(
            workspace=Path("/tmp"),
            deliberation_engine=MockEngine(),
            enabled=True,
        )
        frame = None

        async def run() -> None:
            nonlocal frame
            frame = await engine.cycle()

        import asyncio
        asyncio.run(run())
        assert frame is not None
        assert frame.intended_strategy == "mock plan"
        assert frame.confidence == 0.9
