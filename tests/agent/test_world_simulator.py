from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from OriginAgent.agent.facts import FactStore
from OriginAgent.agent.world_simulator import (
    CalibrationSummary,
    SimulationFeedback,
    SimulationRequest,
    WorldSimulator,
)
from OriginAgent.config.schema import DeviceToolsConfig
from OriginAgent.domain_packs.smart_home.runtime.device_actions import TypedDeviceAction
from OriginAgent.domain_packs.smart_home.runtime.device_factory import build_device_action_executor
from OriginAgent.domain_packs.smart_home.runtime.permissions import HouseholdActor, PermissionResolver
from OriginAgent.session.manager import Session, SessionManager


@dataclass
class _FakeSnapshot:
    snapshots: list[object]
    updated_at: str = "2026-06-17T11:59:30+00:00"


class _FakeWorldState:
    def __init__(self, *, snapshot_time: str, summary_text: str = "living room is dark") -> None:
        self._snapshot_time = snapshot_time
        self._summary_text = summary_text

    def load(self, session: Session, *, identity=None):  # noqa: ANN001
        del session, identity
        return _FakeSnapshot(
            snapshots=[
                type(
                    "Snap",
                    (),
                    {
                        "snapshot_id": "snap_1",
                        "captured_at": self._snapshot_time,
                        "summary": self._summary_text,
                        "confidence": 0.9,
                        "objects": ["lamp"],
                        "relationships": ["lamp near camera"],
                    },
                )()
            ],
            updated_at=self._snapshot_time,
        )

    def home_state_summary(self, session: Session, *, identity=None):  # noqa: ANN001
        del session, identity
        return {
            "status": "active",
            "summary": self._summary_text,
            "focus": [self._summary_text],
            "occupancy": "unknown",
        }


def _make_session(workspace: Path, key: str = "cli:home") -> tuple[SessionManager, Session]:
    sessions = SessionManager(workspace)
    return sessions, sessions.get_or_create(key)


def _write_fact(
    store: FactStore,
    *,
    fact_id: str = "fact-nightvision",
    confidence: float = 0.8,
    support_count: int = 1,
    contradiction_count: int = 0,
) -> None:
    store.upsert_fact(
        "When lights turn on in dark rooms, the camera night vision may turn on.",
        category="note",
        scope="home.living_room",
        owner="user",
        source_cursors=[1],
        source_excerpt="camera night vision may turn on",
        confidence=confidence,
        status="active",
        proposal_id=fact_id,
    )
    records = store.read_all()
    assert records
    records[0].support_count = support_count
    records[0].contradiction_count = contradiction_count
    records[0].confidence = confidence
    store._write_records_unlocked(records)  # noqa: SLF001 - test-only setup


def _simulator(workspace: Path, *, snapshot_time: str, summary_text: str = "living room is dark") -> tuple[WorldSimulator, SessionManager, Session]:
    sessions, session = _make_session(workspace)
    world_state = _FakeWorldState(snapshot_time=snapshot_time, summary_text=summary_text)
    fact_store = FactStore(workspace)
    return WorldSimulator(workspace, world_state=world_state, fact_store=fact_store), sessions, session


def _lighting_request(request_id: str = "req-1") -> SimulationRequest:
    return SimulationRequest(
        request_id=request_id,
        action="set_light_power",
        scope="home.living_room.lighting.ceiling_light",
        trigger="user_initiated",
        risk="low",
        payload={"domain": "lighting", "action_type": "set_light_power", "power": "on"},
        requested_by="alice",
        world_ref="world-1",
        facts_ref=["fact-1"],
    )


def test_world_state_state_staleness_skips_simulation(tmp_path: Path) -> None:
    simulator, _, session = _simulator(
        tmp_path,
        snapshot_time="2026-06-17T00:00:00+00:00",
    )

    trace = simulator.predict_action(
        _lighting_request(),
        session=session,
        current_time=datetime(2026, 6, 17, 0, 5, tzinfo=timezone.utc),
    )

    assert trace.status == "skipped_stale_data"
    assert trace.uncertainty_score == 0.0
    assert trace.recommended_confirmation is False
    assert trace.simulation_skipped_reason == "stale_entity_data"


def test_world_simulator_promotes_confirmation_for_dark_room_and_night_vision_edge(tmp_path: Path) -> None:
    simulator, _, session = _simulator(
        tmp_path,
        snapshot_time="2026-06-17T11:59:30+00:00",
    )
    _write_fact(simulator.fact_store)
    simulator.seed_edges_from_facts()

    trace = simulator.predict_action(
        _lighting_request("req-2"),
        session=session,
        current_time=datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc),
    )

    assert trace.status == "ok"
    assert trace.recommended_confirmation is True
    assert trace.risk_score > 0.0
    assert trace.uncertainty_score > 0.0


def test_quiet_hours_parser_and_beta_prior_mapping(tmp_path: Path) -> None:
    (tmp_path / "USER.md").write_text("- Quiet hours: 23:00-07:00\n", encoding="utf-8")
    simulator, _, session = _simulator(
        tmp_path,
        snapshot_time="2026-06-17T23:29:30+00:00",
    )
    _write_fact(simulator.fact_store, confidence=0.8, support_count=4, contradiction_count=2)
    simulator.seed_edges_from_facts()

    edge = simulator._read_edges()[0]  # noqa: SLF001 - test-only inspection

    assert edge.alpha == 12.0
    assert edge.beta == 5.0
    assert simulator._quiet_hours_weight(datetime(2026, 6, 17, 23, 30, tzinfo=timezone.utc)) == 0.2
    assert simulator._quiet_hours_weight(datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)) == 0.0

    trace = simulator.predict_action(
        _lighting_request("req-quiet"),
        session=session,
        current_time=datetime(2026, 6, 17, 23, 30, tzinfo=timezone.utc),
    )
    assert trace.status == "ok"


def test_calibrate_updates_edge_weights(tmp_path: Path) -> None:
    simulator, _, session = _simulator(
        tmp_path,
        snapshot_time="2026-06-17T11:59:30+00:00",
    )
    _write_fact(simulator.fact_store)
    simulator.seed_edges_from_facts()

    trace = simulator.predict_action(
        _lighting_request("req-3"),
        session=session,
        current_time=datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc),
    )
    simulator.record_outcome(
        SimulationFeedback(
            trace_id=trace.trace_id,
            outcome="matched",
            observed_outcome={"status": "executed"},
            evidence_refs={"action_id": "action-1"},
        )
    )
    summary = simulator.calibrate(limit=10)
    edge = simulator._read_edges()[0]  # noqa: SLF001 - test-only inspection

    assert summary == CalibrationSummary(processed_feedback=1, updated_edges=1, skipped_feedback=0)
    assert edge.alpha == 10.0
    assert edge.beta == 3.0


def test_device_executor_attaches_simulation_metadata_and_confirmation(tmp_path: Path) -> None:
    (tmp_path / "USER.md").write_text("- Quiet hours: 23:00-07:00\n", encoding="utf-8")
    sessions, session = _make_session(tmp_path)
    world_state = _FakeWorldState(snapshot_time="2026-06-17T11:59:30+00:00")
    fact_store = FactStore(tmp_path)
    _write_fact(fact_store)

    executor = build_device_action_executor(
        workspace=tmp_path,
        config=DeviceToolsConfig(enabled=True, lighting_enabled=True, backend="fake"),
        fact_store=fact_store,
        world_state=world_state,
        sessions=sessions,
        timezone_name="UTC",
        permission_resolver=PermissionResolver({"alice": HouseholdActor("alice", "resident")}),
    )

    assert executor is not None
    result = executor.submit_typed(
        TypedDeviceAction(
            action_type="set_light_power",
            device_id="ceiling_light",
            domain="lighting",
            room="living_room",
            parameters={"power": "on"},
            requested_by="alice",
            trigger="user_initiated",
        ),
        session_key=session.key,
        now=datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc),
    )

    assert result.status == "pending_confirmation"
    assert result.simulation_status == "ok"
    assert result.simulation_trace_id is not None
    assert result.simulation_risk is not None
    assert result.simulation_uncertainty is not None
