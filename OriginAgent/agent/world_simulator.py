"""Local-first world simulation for low-risk smart-home actions.

P1 scope:
- only user-initiated smart-home actions
- deterministic rules + lightweight causal edge aggregation
- append-only local persistence
"""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import replace
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable

from filelock import FileLock

from OriginAgent.agent.facts import FactRecord, FactStore
from OriginAgent.agent.world_simulator_models import (
    CalibrationSummary,
    CausalEdge,
    EntityState,
    SimulationFeedback,
    SimulationRequest,
    SimulationTrace,
    _normalize_probability,
)
from OriginAgent.agent.world_state import SceneSnapshot, WorldStateManager
from OriginAgent.utils.helpers import ensure_dir

_WORLD_SIMULATOR_DIR = Path("memory") / "world_simulator"
_EDGE_FILE = "causal_edges.jsonl"
_TRACE_FILE = "simulation_traces.jsonl"
_FEEDBACK_FILE = "simulation_feedback.jsonl"
_DEFAULT_PRIOR_ALPHA = 1.5
_DEFAULT_PRIOR_BETA = 1.5
_PRIOR_EQUIVALENT_SAMPLES = 10.0
_SIMULATION_STALENESS_SECONDS = 120.0
_CALIBRATION_DECAY_FACTOR = 0.995
_QUIET_HOURS_RE = re.compile(r"^\s*(\d{2}):(\d{2})-(\d{2}):(\d{2})\s*$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _round_weight(value: float, *, ndigits: int = 10) -> float:
    return round(float(value), ndigits)


def _normalized_entropy(probability: float) -> float:
    p = _normalize_probability(probability)
    if p in {0.0, 1.0}:
        return 0.0
    q = 1.0 - p
    return -((p * math.log2(p)) + (q * math.log2(q)))


class WorldSimulator:
    """Minimal P1 world simulator for user-triggered smart-home actions."""

    def __init__(
        self,
        workspace: Path,
        *,
        world_state: WorldStateManager,
        fact_store: FactStore,
        timezone_name: str | None = None,
        simulation_staleness_seconds: float = _SIMULATION_STALENESS_SECONDS,
    ) -> None:
        self.workspace = Path(workspace)
        self.world_state = world_state
        self.fact_store = fact_store
        self.timezone_name = str(timezone_name or "UTC").strip() or "UTC"
        self.simulation_staleness_seconds = max(1.0, float(simulation_staleness_seconds or _SIMULATION_STALENESS_SECONDS))
        self._root = ensure_dir(self.workspace / _WORLD_SIMULATOR_DIR)
        self._edge_file = self._root / _EDGE_FILE
        self._trace_file = self._root / _TRACE_FILE
        self._feedback_file = self._root / _FEEDBACK_FILE
        self._edge_lock = FileLock(str(self._root / ".edges.lock"))
        self._trace_lock = FileLock(str(self._root / ".traces.lock"))
        self._feedback_lock = FileLock(str(self._root / ".feedback.lock"))

    def predict_action(
        self,
        request: SimulationRequest,
        *,
        session: Any,
        current_time: datetime | None = None,
    ) -> SimulationTrace:
        now = current_time or _utcnow()
        if str(request.trigger or "").strip().lower() != "user_initiated":
            trace = SimulationTrace(
                trace_id=f"sim_{uuid.uuid4().hex[:12]}",
                request_id=request.request_id,
                status="not_applicable",
                evidence_refs=self._evidence_refs(request),
            )
            self._append_trace(trace)
            return trace
        if not self._is_supported_smart_home_action(request):
            trace = SimulationTrace(
                trace_id=f"sim_{uuid.uuid4().hex[:12]}",
                request_id=request.request_id,
                status="not_applicable",
                evidence_refs=self._evidence_refs(request),
            )
            self._append_trace(trace)
            return trace

        entities, missing_required, stale_required = self._build_entity_states(
            session,
            request=request,
            now=now,
        )
        if stale_required:
            trace = SimulationTrace(
                trace_id=f"sim_{uuid.uuid4().hex[:12]}",
                request_id=request.request_id,
                status="skipped_stale_data",
                evidence_refs=self._evidence_refs(request, entity_refs=entities.keys()),
                simulation_skipped_reason="stale_entity_data",
            )
            self._append_trace(trace)
            return trace
        if missing_required:
            trace = SimulationTrace(
                trace_id=f"sim_{uuid.uuid4().hex[:12]}",
                request_id=request.request_id,
                status="insufficient_data",
                assumptions=["required entities are missing for this action"],
                evidence_refs=self._evidence_refs(request, entity_refs=entities.keys()),
            )
            self._append_trace(trace)
            return trace

        outcomes, assumptions = self._predict_rule_outcomes(request, entities)
        edges = self._matching_edges(request, entities)
        if edges:
            probabilities = [self._edge_probability(edge) for edge in edges]
            risk_score = self._noisy_or(probabilities)
            uncertainty_score = max(_normalized_entropy(prob) for prob in probabilities)
        else:
            risk_score = 0.0
            uncertainty_score = 0.0
        recommended_confirmation = self._should_confirm(
            request=request,
            risk_score=risk_score,
            uncertainty_score=uncertainty_score,
            now=now,
        )
        trace = SimulationTrace(
            trace_id=f"sim_{uuid.uuid4().hex[:12]}",
            request_id=request.request_id,
            status="ok",
            predicted_outcomes=outcomes,
            risk_score=risk_score,
            uncertainty_score=uncertainty_score,
            assumptions=assumptions,
            recommended_confirmation=recommended_confirmation,
            evidence_refs=self._evidence_refs(
                request,
                entity_refs=entities.keys(),
                edge_refs=[edge.edge_id for edge in edges],
            ),
        )
        self._append_trace(trace)
        return trace

    def record_outcome(self, feedback: SimulationFeedback) -> None:
        self._append_feedback(feedback)

    def calibrate(self, *, limit: int = 100) -> CalibrationSummary:
        records = self._read_feedback()
        if not records:
            return CalibrationSummary()
        traces_by_id = {trace.trace_id: trace for trace in self._read_traces()}
        edges_by_id = {edge.edge_id: edge for edge in self._read_edges()}
        processed = 0
        updated_edges: set[str] = set()
        skipped = 0
        pending_updates: list[SimulationFeedback] = []
        for feedback in records:
            if processed >= max(1, int(limit or 100)):
                pending_updates.append(feedback)
                continue
            if feedback.calibration_state != "pending":
                pending_updates.append(feedback)
                continue
            trace = traces_by_id.get(feedback.trace_id)
            if trace is None or trace.status != "ok":
                skipped += 1
                pending_updates.append(SimulationFeedback(
                    trace_id=feedback.trace_id,
                    outcome=feedback.outcome,
                    observed_outcome=dict(feedback.observed_outcome),
                    mismatch_score=feedback.mismatch_score,
                    evidence_refs=dict(feedback.evidence_refs),
                    observed_at=feedback.observed_at,
                    calibration_state="skipped",
                ))
                processed += 1
                continue
            edge_refs = trace.evidence_refs.get("edge_refs") or []
            if not isinstance(edge_refs, list) or not edge_refs:
                skipped += 1
                pending_updates.append(SimulationFeedback(
                    trace_id=feedback.trace_id,
                    outcome=feedback.outcome,
                    observed_outcome=dict(feedback.observed_outcome),
                    mismatch_score=feedback.mismatch_score,
                    evidence_refs=dict(feedback.evidence_refs),
                    observed_at=feedback.observed_at,
                    calibration_state="skipped",
                ))
                processed += 1
                continue
            if feedback.outcome == "evidence_insufficient":
                skipped += 1
                pending_updates.append(SimulationFeedback(
                    trace_id=feedback.trace_id,
                    outcome=feedback.outcome,
                    observed_outcome=dict(feedback.observed_outcome),
                    mismatch_score=feedback.mismatch_score,
                    evidence_refs=dict(feedback.evidence_refs),
                    observed_at=feedback.observed_at,
                    calibration_state="skipped",
                ))
                processed += 1
                continue
            for edge_id in edge_refs:
                edge = edges_by_id.get(str(edge_id))
                if edge is None:
                    continue
                decayed = self._apply_calibration_decay(edge)
                if feedback.outcome == "matched":
                    decayed = replace(decayed, alpha=_round_weight(decayed.alpha + 1.0))
                elif feedback.outcome == "contradicted":
                    decayed = replace(decayed, beta=_round_weight(decayed.beta + 1.0))
                decayed = replace(
                    decayed,
                    confidence=self._edge_probability(decayed),
                )
                edges_by_id[edge.edge_id] = decayed
                updated_edges.add(edge.edge_id)
            pending_updates.append(SimulationFeedback(
                trace_id=feedback.trace_id,
                outcome=feedback.outcome,
                observed_outcome=dict(feedback.observed_outcome),
                mismatch_score=feedback.mismatch_score,
                evidence_refs=dict(feedback.evidence_refs),
                observed_at=feedback.observed_at,
                calibration_state="applied",
            ))
            processed += 1
        self._write_feedback(pending_updates)
        self._write_edges(edges_by_id.values())
        return CalibrationSummary(
            processed_feedback=processed,
            updated_edges=len(updated_edges),
            skipped_feedback=skipped,
        )

    def seed_edges_from_facts(self) -> None:
        existing = {edge.edge_id: edge for edge in self._read_edges()}
        changed = False
        for record in self.fact_store.read_all():
            edge = self._edge_from_fact(record)
            if edge is None:
                continue
            if edge.edge_id in existing:
                continue
            existing[edge.edge_id] = edge
            changed = True
        if changed:
            self._write_edges(existing.values())

    def _build_entity_states(
        self,
        session: Any,
        *,
        request: SimulationRequest,
        now: datetime,
    ) -> tuple[dict[str, EntityState], bool, bool]:
        snapshot = self.world_state.load(session)
        summary = self.world_state.home_state_summary(session)
        entities: dict[str, EntityState] = {}
        latest_snapshot = self._latest_scene_snapshot(snapshot.snapshots)
        brightness_state = self._build_brightness_state(summary, latest_snapshot, snapshot.updated_at, now)
        if brightness_state is not None:
            entities[brightness_state.entity_id] = brightness_state
        presence_state = self._build_presence_state(summary, snapshot.updated_at, now)
        if presence_state is not None:
            entities[presence_state.entity_id] = presence_state
        ir_state = self._build_camera_ir_state(summary, latest_snapshot, snapshot.updated_at, now)
        if ir_state is not None:
            entities[ir_state.entity_id] = ir_state
        required = self._required_entities(request)
        missing_required = any(entity_id not in entities for entity_id in required)
        stale_required = any(
            entities[entity_id].staleness_seconds > self.simulation_staleness_seconds
            for entity_id in required
            if entity_id in entities
        )
        return entities, missing_required, stale_required

    def _predict_rule_outcomes(
        self,
        request: SimulationRequest,
        entities: dict[str, EntityState],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        outcomes: list[dict[str, Any]] = []
        assumptions: list[str] = []
        if request.action == "set_light_power" and str(request.payload.get("power") or "").strip().lower() == "on":
            brightness = entities.get("ambient_brightness")
            if brightness is not None:
                assumptions.append("turning lights on increases room brightness")
                outcomes.append({
                    "effect": "ambient_brightness_increase",
                    "predicted": {"lux_band": "brightened"},
                    "confidence": brightness.confidence,
                })
            ir = entities.get("camera_ir_mode")
            if ir is not None and brightness is not None:
                current_band = str(brightness.attributes.get("lux_band") or "").strip().lower()
                if current_band in {"dark", "very_dark"}:
                    assumptions.append("dark ambient scenes may trigger camera night vision side effects")
                    outcomes.append({
                        "effect": "camera_ir_mode_change",
                        "predicted": {"mode": "may_switch"},
                        "confidence": ir.confidence,
                    })
        return outcomes, assumptions

    def _matching_edges(
        self,
        request: SimulationRequest,
        entities: dict[str, EntityState],
    ) -> list[CausalEdge]:
        edges = []
        for edge in self._read_edges():
            if request.action == "set_light_power" and edge.cause == "light_on" and edge.effect == "camera_ir_on":
                brightness = entities.get("ambient_brightness")
                if brightness is None:
                    continue
                if str(brightness.attributes.get("lux_band") or "").strip().lower() not in {"dark", "very_dark"}:
                    continue
                edges.append(edge)
        return edges

    def _should_confirm(
        self,
        *,
        request: SimulationRequest,
        risk_score: float,
        uncertainty_score: float,
        now: datetime,
    ) -> bool:
        if risk_score >= 0.8:
            return True
        if risk_score < 0.25 or uncertainty_score <= 0.7:
            return False
        confirmation_cost = 0.10 + self._quiet_hours_weight(now)
        expected_loss_without_info = risk_score
        expected_loss_with_info = max(0.0, risk_score * (1.0 - uncertainty_score))
        voi = expected_loss_without_info - expected_loss_with_info
        return voi > confirmation_cost

    def _quiet_hours_weight(self, now: datetime) -> float:
        quiet_hours = self._read_quiet_hours_text()
        if quiet_hours is None:
            return 0.0
        parsed = self._parse_quiet_hours(quiet_hours)
        if parsed is None:
            return 0.0
        start, end = parsed
        try:
            from zoneinfo import ZoneInfo

            local_now = now.astimezone(ZoneInfo(self.timezone_name))
        except Exception:
            local_now = now.astimezone(timezone.utc)
        current = local_now.timetz().replace(tzinfo=None)
        if start <= end:
            in_quiet_hours = start <= current < end
        else:
            in_quiet_hours = current >= start or current < end
        return 0.20 if in_quiet_hours else 0.0

    def _read_quiet_hours_text(self) -> str | None:
        path = self.workspace / "USER.md"
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return None
        for line in content.splitlines():
            normalized = line.strip().lstrip("-").strip()
            if normalized.lower().startswith("quiet hours:"):
                return normalized.split(":", 1)[1].strip()
        return None

    @staticmethod
    def _parse_quiet_hours(value: str) -> tuple[time, time] | None:
        match = _QUIET_HOURS_RE.match(str(value or ""))
        if match is None:
            return None
        start_hour, start_minute, end_hour, end_minute = (int(group) for group in match.groups())
        if start_hour > 23 or end_hour > 23 or start_minute > 59 or end_minute > 59:
            return None
        return time(start_hour, start_minute), time(end_hour, end_minute)

    def _read_edges(self) -> list[CausalEdge]:
        with self._edge_lock:
            return [CausalEdge.from_json(row) for row in self._read_jsonl(self._edge_file)]

    def _write_edges(self, edges: Iterable[CausalEdge]) -> None:
        with self._edge_lock:
            self._write_jsonl(self._edge_file, [edge.to_json() for edge in edges])

    def _append_trace(self, trace: SimulationTrace) -> None:
        with self._trace_lock:
            self._append_jsonl(self._trace_file, trace.to_json())

    def _read_traces(self) -> list[SimulationTrace]:
        with self._trace_lock:
            return [SimulationTrace.from_json(row) for row in self._read_jsonl(self._trace_file)]

    def _append_feedback(self, feedback: SimulationFeedback) -> None:
        with self._feedback_lock:
            self._append_jsonl(self._feedback_file, feedback.to_json())

    def _read_feedback(self) -> list[SimulationFeedback]:
        with self._feedback_lock:
            return [SimulationFeedback.from_json(row) for row in self._read_jsonl(self._feedback_file)]

    def _write_feedback(self, records: Iterable[SimulationFeedback]) -> None:
        with self._feedback_lock:
            self._write_jsonl(self._feedback_file, [record.to_json() for record in records])

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                rows.append(raw)
        return rows

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
        text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def _edge_from_fact(self, record: FactRecord) -> CausalEdge | None:
        text = str(record.content or "").strip().lower()
        if "night vision" in text or "nightvision" in text or "ir" in text:
            if "light" not in text and "lighting" not in text:
                return None
            alpha_base = _round_weight(1.0 + (record.confidence * _PRIOR_EQUIVALENT_SAMPLES))
            beta_base = _round_weight(1.0 + ((1.0 - record.confidence) * _PRIOR_EQUIVALENT_SAMPLES))
            alpha = _round_weight(alpha_base + max(0, int(record.support_count) - 1))
            beta = _round_weight(beta_base + max(0, int(record.contradiction_count)))
            return CausalEdge(
                edge_id=f"edge:{record.fact_id}",
                cause="light_on",
                effect="camera_ir_on",
                confidence=float(alpha / (alpha + beta)),
                alpha=float(alpha),
                beta=float(beta),
                source_fact_id=record.fact_id,
                support_count=int(record.support_count),
                contradiction_count=int(record.contradiction_count),
            )
        return None

    @staticmethod
    def _edge_probability(edge: CausalEdge) -> float:
        if (edge.alpha + edge.beta) <= 0:
            return 0.0
        return _normalize_probability(edge.alpha / (edge.alpha + edge.beta))

    @staticmethod
    def _noisy_or(probabilities: Iterable[float]) -> float:
        combined = 1.0
        seen = False
        for probability in probabilities:
            seen = True
            combined *= 1.0 - _normalize_probability(probability)
        return 1.0 - combined if seen else 0.0

    @staticmethod
    def _apply_calibration_decay(edge: CausalEdge) -> CausalEdge:
        return replace(
            edge,
            alpha=_round_weight(edge.alpha * _CALIBRATION_DECAY_FACTOR),
            beta=_round_weight(edge.beta * _CALIBRATION_DECAY_FACTOR),
        )

    @staticmethod
    def _required_entities(request: SimulationRequest) -> tuple[str, ...]:
        if request.action == "set_light_power":
            return ("ambient_brightness", "camera_ir_mode")
        return tuple()

    @staticmethod
    def _is_supported_smart_home_action(request: SimulationRequest) -> bool:
        domain = str(request.payload.get("domain") or "").strip().lower()
        return request.action in {"set_light_power", "set_light_brightness", "set_light_color_temperature"} and domain == "lighting"

    def _evidence_refs(
        self,
        request: SimulationRequest,
        *,
        entity_refs: Iterable[str] = (),
        edge_refs: Iterable[str] = (),
    ) -> dict[str, list[str] | str]:
        refs: dict[str, list[str] | str] = {
            "world_ref": request.world_ref or "",
            "facts_ref": [str(item) for item in request.facts_ref],
        }
        entity_list = [str(item) for item in entity_refs if str(item)]
        if entity_list:
            refs["entity_refs"] = entity_list
        edge_list = [str(item) for item in edge_refs if str(item)]
        if edge_list:
            refs["edge_refs"] = edge_list
        return refs

    @staticmethod
    def _latest_scene_snapshot(snapshots: Iterable[SceneSnapshot]) -> SceneSnapshot | None:
        latest: tuple[float, SceneSnapshot] | None = None
        for snapshot in snapshots:
            timestamp = _parse_datetime(snapshot.captured_at)
            score = timestamp.timestamp() if timestamp is not None else 0.0
            if latest is None or score > latest[0]:
                latest = (score, snapshot)
        return latest[1] if latest is not None else None

    def _build_brightness_state(
        self,
        summary: dict[str, Any],
        latest_snapshot: SceneSnapshot | None,
        fallback_timestamp: str | None,
        now: datetime,
    ) -> EntityState | None:
        focus_text = " ".join(str(item).lower() for item in (summary.get("focus") or []))
        summary_text = " ".join(
            [
                str(summary.get("status") or ""),
                str(summary.get("summary") or ""),
                focus_text,
                str(getattr(latest_snapshot, "summary", "") or ""),
            ]
        ).lower()
        if "dark" in summary_text or "dim" in summary_text or "lights off" in summary_text:
            band = "dark"
        elif "bright" in summary_text:
            band = "bright"
        else:
            band = "unknown"
        timestamp = getattr(latest_snapshot, "captured_at", None) or fallback_timestamp
        return self._entity_state(
            entity_id="ambient_brightness",
            entity_type="ambient_condition",
            attributes={"lux_band": band},
            confidence=getattr(latest_snapshot, "confidence", 0.5) if latest_snapshot is not None else 0.5,
            timestamp=timestamp,
            now=now,
            source_refs=[getattr(latest_snapshot, "snapshot_id", "")] if latest_snapshot is not None else [],
        )

    def _build_camera_ir_state(
        self,
        summary: dict[str, Any],
        latest_snapshot: SceneSnapshot | None,
        fallback_timestamp: str | None,
        now: datetime,
    ) -> EntityState | None:
        text = " ".join(
            [
                str(summary.get("status") or ""),
                str(summary.get("summary") or ""),
                str(getattr(latest_snapshot, "summary", "") or ""),
                " ".join(str(item) for item in getattr(latest_snapshot, "objects", []) or []),
                " ".join(str(item) for item in getattr(latest_snapshot, "relationships", []) or []),
            ]
        ).lower()
        mode = "unknown"
        if "night vision" in text or "ir" in text or "infrared" in text:
            mode = "active_or_relevant"
        timestamp = getattr(latest_snapshot, "captured_at", None) or fallback_timestamp
        return self._entity_state(
            entity_id="camera_ir_mode",
            entity_type="camera",
            attributes={"mode": mode},
            confidence=getattr(latest_snapshot, "confidence", 0.5) if latest_snapshot is not None else 0.5,
            timestamp=timestamp,
            now=now,
            source_refs=[getattr(latest_snapshot, "snapshot_id", "")] if latest_snapshot is not None else [],
        )

    def _build_presence_state(
        self,
        summary: dict[str, Any],
        fallback_timestamp: str | None,
        now: datetime,
    ) -> EntityState | None:
        status = str(summary.get("occupancy") or summary.get("presence_status") or "unknown").strip().lower() or "unknown"
        return self._entity_state(
            entity_id="presence_status",
            entity_type="presence",
            attributes={"status": status},
            confidence=0.7 if status != "unknown" else 0.4,
            timestamp=fallback_timestamp,
            now=now,
            source_refs=[],
        )

    @staticmethod
    def _entity_state(
        *,
        entity_id: str,
        entity_type: str,
        attributes: dict[str, Any],
        confidence: float,
        timestamp: str | None,
        now: datetime,
        source_refs: list[str],
    ) -> EntityState:
        verified = _parse_datetime(timestamp)
        if verified is None:
            verified = now
        staleness_seconds = max(0.0, (now - verified).total_seconds())
        return EntityState(
            entity_id=entity_id,
            entity_type=entity_type,
            attributes=dict(attributes),
            confidence=_normalize_probability(confidence),
            last_verified_at=verified.isoformat(),
            staleness_seconds=staleness_seconds,
            source_refs=[item for item in source_refs if item],
        )
