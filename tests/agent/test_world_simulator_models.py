"""Tests for CS-005: world-simulator model contracts (§9.3-§9.7)."""

from dataclasses import FrozenInstanceError

from OriginAgent.agent.world_simulator_models import (
    CalibrationSummary,
    CausalEdge,
    EntityState,
    HypothesisRecord,
    SimulationFeedback,
    SimulationRequest,
    SimulationTrace,
)


# ── HypothesisRecord (§9.3) ──────────────────────────────────────────────────


class TestHypothesisRecord:
    def test_full_construction(self) -> None:
        h = HypothesisRecord(
            hypothesis_id="h-1",
            statement="CPU spike caused by cron job overlap",
            basis_refs=["fact:001", "fact:002"],
            confidence=0.7,
            status="active",
            competing_hypotheses=["h-2"],
            expires_at="2026-07-07T00:00:00+00:00",
        )
        assert h.hypothesis_id == "h-1"
        assert h.confidence == 0.7
        assert "h-2" in h.competing_hypotheses

    def test_default_status(self) -> None:
        h = HypothesisRecord(hypothesis_id="h-3", statement="test")
        assert h.status == "active"

    def test_invalid_status_resets(self) -> None:
        h = HypothesisRecord(hypothesis_id="h-4", statement="test", status="invalid")
        assert h.status == "active"

    def test_roundtrip(self) -> None:
        h = HypothesisRecord(hypothesis_id="h-5", statement="roundtrip test", confidence=0.5)
        data = h.to_json()
        restored = HypothesisRecord.from_json(data)
        assert restored == h

    def test_from_json_missing_fields(self) -> None:
        h = HypothesisRecord.from_json({"hypothesis_id": "h-6"})
        assert h.statement == ""
        assert h.status == "active"

    def test_normalization_clamps(self) -> None:
        h = HypothesisRecord(hypothesis_id="h-7", statement="x" * 1000, confidence=99.9)
        assert len(h.statement) < 500
        assert h.confidence == 1.0


# ── EntityState (§9.5) ───────────────────────────────────────────────────────


class TestEntityState:
    def test_enriched_fields(self) -> None:
        e = EntityState(
            entity_id="brightness", entity_type="ambient",
            scope="living_room", owner_id="user-1",
            stale_at="2026-07-07T00:00:00+00:00",
        )
        assert e.scope == "living_room"
        assert e.owner_id == "user-1"
        assert e.stale_at != ""

    def test_roundtrip(self) -> None:
        e = EntityState(entity_id="e1", entity_type="light", attributes={"band": "dark"})
        data = e.to_json()
        restored = EntityState.from_json(data)
        assert restored.entity_id == "e1"
        assert restored.attributes == {"band": "dark"}

    def test_frozen(self) -> None:
        e = EntityState(entity_id="e2", entity_type="test")
        try:
            e.attributes = {}  # type: ignore[misc]
            assert False, "should be frozen"
        except Exception:
            pass


# ── CausalEdge (§9.4) ────────────────────────────────────────────────────────


class TestCausalEdge:
    def test_enriched_fields(self) -> None:
        c = CausalEdge(
            edge_id="e1", cause="light_on", effect="camera_ir_on",
            confidence=0.7, alpha=5.0, beta=3.0,
            source_kind="rule", contradiction_refs=["obs:1"],
            last_validated_at="2026-07-01T00:00:00+00:00",
        )
        assert c.source_kind == "rule"
        assert c.contradiction_refs == ["obs:1"]
        assert c.last_validated_at != ""

    def test_frozen(self) -> None:
        c = CausalEdge(edge_id="e2", cause="a", effect="b", confidence=0.5, alpha=1.0, beta=1.0)
        try:
            c.confidence = 0.9  # type: ignore[misc]
            assert False, "CausalEdge should be frozen"
        except FrozenInstanceError:
            pass

    def test_from_json_backward_compat(self) -> None:
        old = {"edge_id": "e3", "cause": "a", "effect": "b", "confidence": 0.8, "alpha": 2.0, "beta": 1.0}
        c = CausalEdge.from_json(old)
        assert c.source_kind == ""  # new default
        assert c.contradiction_refs == []

    def test_roundtrip(self) -> None:
        c = CausalEdge(edge_id="e4", cause="x", effect="y", confidence=0.9, alpha=10.0, beta=1.0)
        data = c.to_json()
        restored = CausalEdge.from_json(data)
        assert restored.confidence == 0.9
        assert restored.alpha == 10.0


# ── SimulationRequest (§9.6) ────────────────────────────────────────────────


class TestSimulationRequest:
    def test_enriched_fields(self) -> None:
        r = SimulationRequest(
            request_id="r1", action="set_light_power", scope="room",
            trigger="user", risk="low",
            purpose="Test if light triggers night vision",
            active_hypotheses=["h1"],
            budget_tier="deep",
            requested_by_frame_id="tf-1",
        )
        assert r.purpose == "Test if light triggers night vision"
        assert r.active_hypotheses == ["h1"]
        assert r.budget_tier == "deep"
        assert r.requested_by_frame_id == "tf-1"

    def test_invalid_budget_resets(self) -> None:
        r = SimulationRequest(
            request_id="r2", action="set_light", scope="room",
            trigger="user", risk="low", budget_tier="ultra",
        )
        assert r.budget_tier == "normal"

    def test_roundtrip(self) -> None:
        r = SimulationRequest(
            request_id="r3", action="set_light", scope="room",
            trigger="user", risk="high",
        )
        data = r.to_json()
        restored = SimulationRequest.from_json(data)
        assert restored.risk == "high"
        assert restored.requested_by_frame_id == ""

    def test_backward_compat(self) -> None:
        old = {"request_id": "r4", "action": "set_light", "scope": "room", "trigger": "user", "risk": "low"}
        r = SimulationRequest.from_json(old)
        assert r.budget_tier == "normal"
        assert r.requested_by_frame_id == ""


# ── SimulationTrace (§9.7) ──────────────────────────────────────────────────


class TestSimulationTrace:
    def test_enriched_fields(self) -> None:
        t = SimulationTrace(
            trace_id="t1", request_id="r1", status="ok",
            risk_flags=["high_temp"],
            confidence=0.6,
            observed_mismatch_refs=["mismatch:1"],
        )
        assert t.risk_flags == ["high_temp"]
        assert t.confidence == 0.6
        assert t.observed_mismatch_refs == ["mismatch:1"]

    def test_backward_compat(self) -> None:
        old = {
            "trace_id": "t2", "request_id": "r2", "status": "ok",
            "predicted_outcomes": [],
            "risk_score": 0.3,
            "uncertainty_score": 0.0,
            "assumptions": ["old"],
        }
        t = SimulationTrace.from_json(old)
        assert t.risk_flags == []  # new default
        assert t.confidence == 0.0
        assert t.observed_mismatch_refs == []

    def test_roundtrip(self) -> None:
        t = SimulationTrace(
            trace_id="t3", request_id="r3", status="ok",
            risk_flags=["flag1"], confidence=0.8,
        )
        data = t.to_json()
        restored = SimulationTrace.from_json(data)
        assert restored.risk_flags == ["flag1"]
        assert restored.confidence == 0.8


# ── SimulationFeedback ───────────────────────────────────────────────────────


class TestSimulationFeedback:
    def test_roundtrip(self) -> None:
        f = SimulationFeedback(
            trace_id="t1", outcome="matched",
            mismatch_score=0.1,
        )
        data = f.to_json()
        restored = SimulationFeedback.from_json(data)
        assert restored.outcome == "matched"
        assert restored.mismatch_score == 0.1

    def test_defaults(self) -> None:
        f = SimulationFeedback(trace_id="t2", outcome="contradicted")
        assert f.calibration_state == "pending"

    def test_from_json_none_mismatch(self) -> None:
        f = SimulationFeedback.from_json({"trace_id": "t3", "outcome": "unknown"})
        assert f.mismatch_score is None


# ── CalibrationSummary ──────────────────────────────────────────────────────


class TestCalibrationSummary:
    def test_defaults(self) -> None:
        s = CalibrationSummary()
        assert s.processed_feedback == 0

    def test_roundtrip(self) -> None:
        s = CalibrationSummary(processed_feedback=5, updated_edges=3, skipped_feedback=1)
        data = s.to_json()
        assert data["processed_feedback"] == 5
