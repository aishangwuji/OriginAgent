"""Minimal Phase 2 world-state read models for continuity-aware context."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from OriginAgent.agent.identity import RuntimeContext
from OriginAgent.agent.local_awareness import summarize_device_map
from OriginAgent.session.manager import Session, SessionManager
from OriginAgent.utils.attachments import AttachmentDescriptor, describe_attachment


WORLD_STATE_METADATA_KEY = "world_state_v1"
DEFAULT_IMAGE_UNCERTAINTY = "image has not been deeply inspected yet"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _string_list(values: Any, *, limit: int = 8, max_chars: int = 240) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if not text:
            continue
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "..."
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _trim_text(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def _path_to_workspace(path: str | Path, *, workspace: Path) -> str:
    p = Path(path)
    try:
        return p.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(p).replace("\\", "/")


def _path_to_posix(path: str | Path) -> str:
    return str(path).replace("\\", "/")


@dataclass
class SceneSnapshot:
    snapshot_id: str
    kind: str
    source: str
    scope: str
    owner_id: str | None
    device_id: str | None
    captured_at: str
    media_path: str
    summary: str
    objects: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    confidence: float = 0.5
    uncertainties: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "SceneSnapshot | None":
        if not isinstance(raw, dict):
            return None
        snapshot_id = str(raw.get("snapshot_id") or "").strip()
        media_path = str(raw.get("media_path") or "").strip()
        if not snapshot_id or not media_path:
            return None
        try:
            confidence = float(raw.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        provenance = raw.get("provenance", {})
        if not isinstance(provenance, dict):
            provenance = {}
        return cls(
            snapshot_id=snapshot_id,
            kind=str(raw.get("kind") or "image").strip() or "image",
            source=str(raw.get("source") or "media.image").strip() or "media.image",
            scope=str(raw.get("scope") or "session").strip() or "session",
            owner_id=str(raw.get("owner_id")).strip() if raw.get("owner_id") else None,
            device_id=str(raw.get("device_id")).strip() if raw.get("device_id") else None,
            captured_at=str(raw.get("captured_at") or _utcnow_iso()).strip(),
            media_path=media_path,
            summary=str(raw.get("summary") or "").strip(),
            objects=_string_list(raw.get("objects")),
            relationships=_string_list(raw.get("relationships")),
            confidence=confidence,
            uncertainties=_string_list(raw.get("uncertainties")),
            provenance=provenance,
        )


@dataclass
class InspectionResult:
    inspection_id: str
    snapshot_id: str
    requested_by: str
    requested_at: str
    confirmed: list[str] = field(default_factory=list)
    corrected: list[str] = field(default_factory=list)
    new_details: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)
    confidence: float = 0.5
    status: str = "pending"
    contested: bool = False
    contested_reasons: list[str] = field(default_factory=list)
    evidence_excerpt: list[str] = field(default_factory=list)
    inspector: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "InspectionResult | None":
        if not isinstance(raw, dict):
            return None
        inspection_id = str(raw.get("inspection_id") or "").strip()
        snapshot_id = str(raw.get("snapshot_id") or "").strip()
        if not inspection_id or not snapshot_id:
            return None
        try:
            confidence = float(raw.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        return cls(
            inspection_id=inspection_id,
            snapshot_id=snapshot_id,
            requested_by=str(raw.get("requested_by") or "system").strip() or "system",
            requested_at=str(raw.get("requested_at") or _utcnow_iso()).strip(),
            confirmed=_string_list(raw.get("confirmed")),
            corrected=_string_list(raw.get("corrected")),
            new_details=_string_list(raw.get("new_details")),
            uncertain=_string_list(raw.get("uncertain")),
            confidence=confidence,
            status=str(raw.get("status") or "pending").strip() or "pending",
            contested=bool(raw.get("contested")),
            contested_reasons=_string_list(raw.get("contested_reasons")),
            evidence_excerpt=_string_list(raw.get("evidence_excerpt")),
            inspector=str(raw.get("inspector") or "").strip(),
        )


@dataclass
class WorldSummary:
    summary_id: str
    scope: str
    owner_id: str | None
    generated_at: str
    fresh_until: str
    focus: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    source_snapshot_ids: list[str] = field(default_factory=list)
    inspection_ids: list[str] = field(default_factory=list)
    contested: bool = False
    contested_items: list[str] = field(default_factory=list)
    source_count: int = 0
    last_inspected_at: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "WorldSummary | None":
        if not isinstance(raw, dict):
            return None
        summary_id = str(raw.get("summary_id") or "").strip()
        if not summary_id:
            return None
        return cls(
            summary_id=summary_id,
            scope=str(raw.get("scope") or "session").strip() or "session",
            owner_id=str(raw.get("owner_id")).strip() if raw.get("owner_id") else None,
            generated_at=str(raw.get("generated_at") or _utcnow_iso()).strip(),
            fresh_until=str(raw.get("fresh_until") or _utcnow_iso()).strip(),
            focus=_string_list(raw.get("focus")),
            relationships=_string_list(raw.get("relationships")),
            constraints=_string_list(raw.get("constraints")),
            uncertainties=_string_list(raw.get("uncertainties")),
            source_snapshot_ids=_string_list(raw.get("source_snapshot_ids")),
            inspection_ids=_string_list(raw.get("inspection_ids")),
            contested=bool(raw.get("contested")),
            contested_items=_string_list(raw.get("contested_items")),
            source_count=max(0, int(raw.get("source_count", 0) or 0)),
            last_inspected_at=str(raw.get("last_inspected_at")).strip() if raw.get("last_inspected_at") else None,
        )


@dataclass
class PerceptionEventCandidate:
    event_id: str
    kind: str
    snapshot_id: str
    inspection_id: str | None
    summary: str
    confidence: float
    contested: bool
    created_at: str
    source: str
    scope: str
    owner_id: str | None
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "PerceptionEventCandidate | None":
        if not isinstance(raw, dict):
            return None
        event_id = str(raw.get("event_id") or "").strip()
        snapshot_id = str(raw.get("snapshot_id") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        if not event_id or not snapshot_id or not kind:
            return None
        try:
            confidence = float(raw.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        provenance = raw.get("provenance", {})
        if not isinstance(provenance, dict):
            provenance = {}
        inspection_id = str(raw.get("inspection_id")).strip() if raw.get("inspection_id") else None
        return cls(
            event_id=event_id,
            kind=kind,
            snapshot_id=snapshot_id,
            inspection_id=inspection_id,
            summary=str(raw.get("summary") or "").strip(),
            confidence=confidence,
            contested=bool(raw.get("contested")),
            created_at=str(raw.get("created_at") or _utcnow_iso()).strip(),
            source=str(raw.get("source") or "media.image").strip() or "media.image",
            scope=str(raw.get("scope") or "session").strip() or "session",
            owner_id=str(raw.get("owner_id")).strip() if raw.get("owner_id") else None,
            provenance=provenance,
        )


@dataclass
class WorldStateSnapshot:
    status: str = "placeholder"
    version: str = "phase1"
    updated_at: str = field(default_factory=_utcnow_iso)
    scope: str = "session"
    owner_id: str | None = None
    snapshots: list[SceneSnapshot] = field(default_factory=list)
    inspections: list[InspectionResult] = field(default_factory=list)
    events: list[PerceptionEventCandidate] = field(default_factory=list)
    world_summary: WorldSummary | None = None
    pruning: dict[str, Any] = field(default_factory=dict)
    device_map: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "version": self.version,
            "updated_at": self.updated_at,
            "scope": self.scope,
            "owner_id": self.owner_id,
            "snapshots": [item.to_json() for item in self.snapshots],
            "inspections": [item.to_json() for item in self.inspections],
            "events": [item.to_json() for item in self.events],
            "world_summary": self.world_summary.to_json() if self.world_summary is not None else None,
            "pruning": dict(self.pruning),
            "device_map": dict(self.device_map),
        }

    @classmethod
    def from_json(cls, raw: Any) -> "WorldStateSnapshot":
        if not isinstance(raw, dict):
            return cls()
        snapshots = [
            item for item in (
                SceneSnapshot.from_json(value) for value in (raw.get("snapshots") or [])
            ) if item is not None
        ]
        inspections = [
            item for item in (
                InspectionResult.from_json(value) for value in (raw.get("inspections") or [])
            ) if item is not None
        ]
        events = [
            item for item in (
                PerceptionEventCandidate.from_json(value) for value in (raw.get("events") or [])
            ) if item is not None
        ]
        summary = WorldSummary.from_json(raw.get("world_summary"))
        return cls(
            status=str(raw.get("status") or "placeholder").strip() or "placeholder",
            version=str(raw.get("version") or "phase1").strip() or "phase1",
            updated_at=str(raw.get("updated_at") or _utcnow_iso()).strip(),
            scope=str(raw.get("scope") or "session").strip() or "session",
            owner_id=str(raw.get("owner_id")).strip() if raw.get("owner_id") else None,
            snapshots=snapshots,
            inspections=inspections,
            events=events,
            world_summary=summary,
            pruning=dict(raw.get("pruning") or {}) if isinstance(raw.get("pruning"), dict) else {},
            device_map=dict(raw.get("device_map") or {}) if isinstance(raw.get("device_map"), dict) else {},
        )


class WorldStateManager:
    """Session-backed minimal world-state manager for Phase 2."""

    EVENT_KINDS = frozenset({
        "snapshot_ingested",
        "inspection_changed_summary",
        "contested_world_state",
        "uncertain_world_state",
    })

    def __init__(
        self,
        workspace: Path,
        sessions: SessionManager,
        context_config: Any | None = None,
    ) -> None:
        self._workspace = Path(workspace)
        self._sessions = sessions
        self._context_config = context_config

    def load(self, session: Session, *, identity: RuntimeContext | None = None) -> WorldStateSnapshot:
        snapshot = WorldStateSnapshot.from_json(session.metadata.get(WORLD_STATE_METADATA_KEY))
        if identity is not None and not snapshot.owner_id:
            snapshot.owner_id = identity.user_id
        return self._refresh_summary(snapshot)

    def save(self, session: Session, snapshot: WorldStateSnapshot) -> WorldStateSnapshot:
        snapshot.updated_at = _utcnow_iso()
        session.metadata[WORLD_STATE_METADATA_KEY] = snapshot.to_json()
        return snapshot

    def inspect(self, session: Session, *, identity: RuntimeContext | None = None) -> dict[str, Any]:
        return self.load(session, identity=identity).to_json()

    def snapshot_prompt_payload(
        self,
        session: Session,
        *,
        identity: RuntimeContext | None = None,
        current_message: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.load(session, identity=identity)
        if identity is None:
            return {
                "status": snapshot.status,
                "version": snapshot.version,
                "updated_at": snapshot.updated_at,
                "device_map_summary": summarize_device_map(snapshot.device_map) if snapshot.device_map else {},
            }
        filtered = self.filtered_candidates(
            session,
            runtime_context=identity,
            current_message=current_message,
        )
        return {
            "status": snapshot.status,
            "version": snapshot.version,
            "updated_at": snapshot.updated_at,
            "scope": snapshot.scope,
            "owner_id": snapshot.owner_id,
            "world_summary": filtered.get("included_summary") or {},
            "world_relationships": list((filtered.get("included_summary") or {}).get("relationships") or []),
            "recent_events": list((self.recent_events(session, identity=identity, limit=5) or {}).get("recent_events", [])),
            "device_map_summary": summarize_device_map(snapshot.device_map) if snapshot.device_map else {},
        }

    def ingest_device_discovery(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        device_map: dict[str, Any],
    ) -> WorldStateSnapshot:
        snapshot = self.load(session, identity=runtime_context)
        snapshot.status = "active"
        snapshot.version = "phase2"
        snapshot.scope = snapshot.scope or "session"
        snapshot.owner_id = snapshot.owner_id or runtime_context.user_id
        snapshot.device_map = dict(device_map)
        return self.save(session, snapshot)

    def ingest_media(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        media_paths: list[str] | None,
    ) -> WorldStateSnapshot:
        snapshot = self.load(session, identity=runtime_context)
        descriptors = [describe_attachment(media_path, source="media") for media_path in (media_paths or []) if isinstance(media_path, str) and media_path]
        return self._ingest_descriptors(
            session,
            runtime_context=runtime_context,
            snapshot=snapshot,
            descriptors=descriptors,
        )

    def ingest_producer_batch(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        media_paths: list[str] | None = None,
        descriptors: list[AttachmentDescriptor | None] | None = None,
    ) -> WorldStateSnapshot:
        snapshot = self.load(session, identity=runtime_context)
        collected: list[AttachmentDescriptor | None] = list(descriptors or [])
        for media_path in media_paths or []:
            if not isinstance(media_path, str) or not media_path:
                continue
            collected.append(describe_attachment(media_path, source="media"))
        return self._ingest_descriptors(
            session,
            runtime_context=runtime_context,
            snapshot=snapshot,
            descriptors=collected,
        )

    def current_attention_items(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        limit: int = 3,
    ) -> list[str]:
        filtered = self.filtered_candidates(
            session,
            runtime_context=runtime_context,
            current_message=None,
        )
        summary = filtered.get("included_summary") or {}
        if not summary:
            return []
        grouped_items: list[tuple[str, list[str]]] = [
            # Surface contested state first so a small attention budget still
            # preserves disagreements uncovered by inspection.
            ("world_contested", list(summary.get("contested_items") or [])),
            ("world_uncertainty", list(summary.get("uncertainties") or [])),
            ("world_attention", list(summary.get("focus") or [])),
        ]
        trimmed: list[str] = []
        # First pass: preserve one item per category when possible.
        for prefix, items in grouped_items:
            for item in items:
                text = str(item or "").strip()
                candidate = f"{prefix}: {text}"
                if text and candidate not in trimmed:
                    trimmed.append(candidate)
                    break
            if len(trimmed) >= limit:
                return trimmed[:limit]
        # Second pass: backfill remaining budget with extra items in priority order.
        for prefix, items in grouped_items:
            for item in items:
                text = str(item or "").strip()
                candidate = f"{prefix}: {text}"
                if not text or candidate in trimmed:
                    continue
                trimmed.append(candidate)
                if len(trimmed) >= limit:
                    return trimmed[:limit]
        return trimmed

    def get_snapshot(
        self,
        session: Session,
        *,
        identity: RuntimeContext | None = None,
        snapshot_id: str,
    ) -> SceneSnapshot | None:
        snapshot = self.load(session, identity=identity)
        return next(
            (item for item in snapshot.snapshots if item.snapshot_id == str(snapshot_id).strip()),
            None,
        )

    def apply_inspection(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        snapshot_id: str,
        inspection_payload: dict[str, Any],
        requested_by: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.load(session, identity=runtime_context)
        target = next(
            (item for item in snapshot.snapshots if item.snapshot_id == str(snapshot_id).strip()),
            None,
        )
        if target is None:
            return {"error": f"snapshot '{snapshot_id}' not found"}
        try:
            confidence = float(inspection_payload.get("confidence", target.confidence))
        except (TypeError, ValueError):
            confidence = target.confidence
        confidence = max(0.0, min(1.0, confidence))
        corrected = _string_list(inspection_payload.get("corrected"))
        inspection = InspectionResult(
            inspection_id=f"inspect_{uuid.uuid4().hex[:12]}",
            snapshot_id=target.snapshot_id,
            requested_by=str(requested_by or inspection_payload.get("requested_by") or "user_turn").strip() or "user_turn",
            requested_at=str(inspection_payload.get("requested_at") or _utcnow_iso()).strip(),
            confirmed=_string_list(inspection_payload.get("confirmed")),
            corrected=corrected,
            new_details=_string_list(inspection_payload.get("new_details")),
            uncertain=_string_list(inspection_payload.get("uncertain")),
            confidence=confidence,
            status=str(inspection_payload.get("status") or "completed").strip() or "completed",
            contested=bool(inspection_payload.get("contested")) or bool(corrected),
            contested_reasons=_string_list(inspection_payload.get("contested_reasons")) or corrected[:4],
            evidence_excerpt=_string_list(inspection_payload.get("evidence_excerpt")),
            inspector=str(inspection_payload.get("inspector") or "").strip(),
        )
        snapshot.inspections.append(inspection)
        refreshed = self._refresh_summary(snapshot)
        refreshed.events = self._merge_recent_events(
            previous=snapshot,
            refreshed=refreshed,
            new_events=self._compute_event_candidates(
                previous=snapshot,
                refreshed=refreshed,
                trigger="inspection",
            ),
        )
        self.save(session, refreshed)
        return {
            "snapshot": target.to_json(),
            "inspection": inspection.to_json(),
            "world_summary": refreshed.world_summary.to_json() if refreshed.world_summary is not None else {},
        }

    def inspect_snapshot(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        snapshot_id: str,
        requested_by: str = "user_turn",
    ) -> dict[str, Any]:
        target = self.get_snapshot(
            session,
            identity=runtime_context,
            snapshot_id=snapshot_id,
        )
        if target is None:
            return {"error": f"snapshot '{snapshot_id}' not found"}
        result = self.apply_inspection(
            session,
            runtime_context=runtime_context,
            snapshot_id=snapshot_id,
            requested_by=requested_by,
            inspection_payload={
                "confirmed": [line for line in target.relationships if line][:4],
                "corrected": [],
                "new_details": [
                    line for line in [
                        f"objects observed: {', '.join(target.objects)}" if target.objects else "",
                        f"source media path: {target.media_path}",
                    ] if line
                ][:4],
                "uncertain": target.uncertainties[:4],
                "confidence": max(0.4, min(0.95, target.confidence)),
                "status": "completed",
                "contested": False,
                "contested_reasons": [],
                "evidence_excerpt": [f"media_path: {target.media_path}"],
                "inspector": "legacy_world_state",
            },
        )
        result["inspection_path"] = "legacy"
        return result

    def filtered_candidates(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        current_message: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.load(session, identity=runtime_context)
        now = _utcnow()
        message = str(current_message or "").strip().lower()
        session_tokens = {
            token
            for token in (
                message.replace("\n", " ").replace(",", " ").replace(".", " ").split()
            )
            if token
        }
        scope_resolver = runtime_context.identity and runtime_context
        visible: list[SceneSnapshot] = []
        filtered_candidates: list[dict[str, Any]] = []
        selection_reasons: list[str] = []
        for candidate in snapshot.snapshots:
            reasons: list[str] = []
            if not self._is_snapshot_fresh(candidate, now=now):
                reasons.append("stale")
            if not self._scope_visible(candidate, runtime_context=runtime_context):
                reasons.append("scope_hidden")
            if reasons:
                filtered_candidates.append(self._candidate_view(candidate, included=False, reasons=reasons))
                continue
            visible.append(candidate)
        relevant = [item for item in visible if self._is_relevant(item, session_tokens=session_tokens)]
        included: list[SceneSnapshot]
        if relevant:
            included = relevant
            selection_reasons.append("relevant_to_message")
        else:
            inspected_override = [
                item for item in visible
                if self._has_completed_inspection_with_findings(snapshot, item.snapshot_id)
            ]
            if inspected_override:
                included = inspected_override
                selection_reasons.append("inspected_override")
            else:
                included = visible[-1:] if visible else []
                if included:
                    selection_reasons.append("fresh_visible_fallback")
        for candidate in visible:
            if candidate in included:
                continue
            filtered_candidates.append(
                self._candidate_view(candidate, included=False, reasons=["not_relevant"])
            )
        included_summary = self._build_filtered_summary(
            snapshot,
            included_snapshots=included,
            now=now,
        )
        contested_summary = {
            "contested": bool(included_summary.contested) if included_summary is not None else False,
            "items": list(included_summary.contested_items) if included_summary is not None else [],
        }
        return {
            "included_summary": included_summary.to_json() if included_summary is not None else {},
            "filtered_candidates": filtered_candidates,
            "freshness": {
                "generated_at": included_summary.generated_at if included_summary is not None else None,
                "fresh_until": included_summary.fresh_until if included_summary is not None else None,
                "is_fresh": bool(
                    included_summary is not None
                    and _parse_dt(included_summary.fresh_until) is not None
                    and _parse_dt(included_summary.fresh_until) >= now
                ),
            },
            "selection_reasons": selection_reasons,
            "contested_summary": contested_summary,
        }

    def recent_events(
        self,
        session: Session,
        *,
        identity: RuntimeContext | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        snapshot = self.load(session, identity=identity)
        ordered = sorted(
            snapshot.events,
            key=lambda item: _parse_dt(item.created_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        recent = ordered[: max(0, int(limit or 0))]
        by_kind: dict[str, int] = {}
        for item in snapshot.events:
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
        return {
            "recent_events": [item.to_json() for item in recent],
            "event_summary": {
                "total": len(snapshot.events),
                "by_kind": by_kind,
                "latest_created_at": recent[0].created_at if recent else None,
            },
        }

    def _refresh_summary(self, snapshot: WorldStateSnapshot) -> WorldStateSnapshot:
        now = _utcnow()
        fresh_snapshots = [
            item for item in snapshot.snapshots
            if self._is_snapshot_fresh(item, now=now)
        ]
        kept_snapshots = fresh_snapshots[-6:]
        kept_inspections = [
            item for item in snapshot.inspections
            if any(scene.snapshot_id == item.snapshot_id for scene in kept_snapshots)
        ][-6:]
        refreshed = WorldStateSnapshot(
            status="active" if kept_snapshots or snapshot.device_map else "placeholder",
            version="phase2" if kept_snapshots or snapshot.device_map else "phase1",
            updated_at=snapshot.updated_at,
            scope=snapshot.scope,
            owner_id=snapshot.owner_id,
            snapshots=list(kept_snapshots),
            inspections=list(kept_inspections),
            events=[
                item for item in snapshot.events
                if any(scene.snapshot_id == item.snapshot_id for scene in kept_snapshots)
            ][-20:],
            world_summary=None,
            pruning={
                "snapshot_count": max(0, len(snapshot.snapshots) - len(kept_snapshots)),
                "inspection_count": max(0, len(snapshot.inspections) - len(kept_inspections)),
                "refreshed_at": now.isoformat(),
            },
            device_map=dict(snapshot.device_map),
        )
        if not refreshed.snapshots:
            return refreshed
        refreshed.world_summary = self._build_world_summary(refreshed, now=now)
        return refreshed

    def _ingest_descriptors(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        snapshot: WorldStateSnapshot,
        descriptors: list[AttachmentDescriptor | None],
    ) -> WorldStateSnapshot:
        previous_snapshot = WorldStateSnapshot.from_json(snapshot.to_json())
        added = False
        existing_paths = {item.media_path for item in snapshot.snapshots}
        for descriptor in descriptors:
            if descriptor is None or descriptor.kind not in {"image", "audio", "video", "sensor"}:
                continue
            if descriptor.path.suffix.lower().endswith(".part") or descriptor.name.endswith(".part"):
                continue
            relative_media_path = _path_to_workspace(descriptor.path, workspace=self._workspace)
            if relative_media_path.endswith(".part") or relative_media_path in existing_paths:
                continue
            sidecar = self._read_sidecar(descriptor.path)
            inferred_provenance = self._infer_provenance_from_path(relative_media_path)
            scene = self._build_scene_snapshot(
                descriptor,
                media_path=relative_media_path,
                runtime_context=runtime_context,
                sidecar=sidecar,
                inferred_provenance=inferred_provenance,
            )
            snapshot.snapshots.append(scene)
            existing_paths.add(relative_media_path)
            added = True
        if added:
            snapshot.status = "active"
            snapshot.version = "phase2"
            snapshot.owner_id = runtime_context.user_id
            snapshot.scope = "session"
        refreshed = self._refresh_summary(snapshot)
        refreshed.events = self._merge_recent_events(
            previous=previous_snapshot,
            refreshed=refreshed,
            new_events=self._compute_event_candidates(
                previous=previous_snapshot,
                refreshed=refreshed,
                trigger="ingest" if added else "refresh",
            ),
        )
        return self.save(session, refreshed)

    def _compute_event_candidates(
        self,
        *,
        previous: WorldStateSnapshot,
        refreshed: WorldStateSnapshot,
        trigger: str,
    ) -> list[PerceptionEventCandidate]:
        events: list[PerceptionEventCandidate] = []
        previous_ids = {item.snapshot_id for item in previous.snapshots}
        refreshed_summary = refreshed.world_summary
        previous_summary = previous.world_summary
        now = _utcnow_iso()
        if trigger == "ingest":
            for item in refreshed.snapshots:
                if item.snapshot_id in previous_ids:
                    continue
                events.append(
                    self._event_from_snapshot(
                        kind="snapshot_ingested",
                        snapshot=item,
                        inspection_id=None,
                        summary=item.summary or f"snapshot ingested from {item.source}",
                        confidence=item.confidence,
                        contested=False,
                        created_at=now,
                    )
                )
        if refreshed_summary is None:
            return events
        if trigger == "inspection":
            latest = refreshed.inspections[-1] if refreshed.inspections else None
            if latest is not None:
                latest_snapshot = next(
                    (item for item in refreshed.snapshots if item.snapshot_id == latest.snapshot_id),
                    None,
                )
                if latest_snapshot is not None and (
                    latest.corrected
                    or latest.new_details
                    or self._focus_changed(previous_summary, refreshed_summary)
                ):
                    events.append(
                        self._event_from_snapshot(
                            kind="inspection_changed_summary",
                            snapshot=latest_snapshot,
                            inspection_id=latest.inspection_id,
                            summary=_trim_text(
                                latest.corrected[0]
                                if latest.corrected
                                else latest.new_details[0]
                                if latest.new_details
                                else latest_snapshot.summary
                            ),
                            confidence=latest.confidence,
                            contested=latest.contested,
                            created_at=now,
                        )
                    )
        if self._became_contested(previous_summary, refreshed_summary):
            contested_snapshot = self._latest_summary_snapshot(refreshed, refreshed_summary)
            if contested_snapshot is not None:
                events.append(
                    self._event_from_snapshot(
                        kind="contested_world_state",
                        snapshot=contested_snapshot,
                        inspection_id=refreshed_summary.inspection_ids[-1] if refreshed_summary.inspection_ids else None,
                        summary=_trim_text(
                            refreshed_summary.contested_items[0]
                            if refreshed_summary.contested_items
                            else refreshed_summary.focus[0]
                            if refreshed_summary.focus
                            else contested_snapshot.summary
                        ),
                        confidence=contested_snapshot.confidence,
                        contested=True,
                        created_at=now,
                    )
                )
        if self._uncertainty_opened(previous_summary, refreshed_summary):
            uncertain_snapshot = self._latest_summary_snapshot(refreshed, refreshed_summary)
            if uncertain_snapshot is not None:
                events.append(
                    self._event_from_snapshot(
                        kind="uncertain_world_state",
                        snapshot=uncertain_snapshot,
                        inspection_id=refreshed_summary.inspection_ids[-1] if refreshed_summary.inspection_ids else None,
                        summary=_trim_text(
                            refreshed_summary.uncertainties[0]
                            if refreshed_summary.uncertainties
                            else uncertain_snapshot.summary
                        ),
                        confidence=uncertain_snapshot.confidence,
                        contested=bool(refreshed_summary.contested),
                        created_at=now,
                    )
                )
        deduped: list[PerceptionEventCandidate] = []
        seen_keys: set[tuple[str, str, str | None, str]] = set()
        for event in events:
            if event.kind not in self.EVENT_KINDS:
                continue
            key = (event.kind, event.snapshot_id, event.inspection_id, event.summary)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(event)
        return deduped

    def _merge_recent_events(
        self,
        *,
        previous: WorldStateSnapshot,
        refreshed: WorldStateSnapshot,
        new_events: list[PerceptionEventCandidate],
    ) -> list[PerceptionEventCandidate]:
        kept_snapshot_ids = {item.snapshot_id for item in refreshed.snapshots}
        carried = [
            item for item in previous.events
            if item.snapshot_id in kept_snapshot_ids
        ]
        merged = [*new_events, *carried]
        merged.sort(
            key=lambda item: _parse_dt(item.created_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        deduped: list[PerceptionEventCandidate] = []
        seen_keys: set[tuple[str, str, str | None, str]] = set()
        for item in merged:
            key = (item.kind, item.snapshot_id, item.inspection_id, item.summary)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(item)
            if len(deduped) >= 20:
                break
        return deduped

    @staticmethod
    def _focus_changed(previous: WorldSummary | None, refreshed: WorldSummary | None) -> bool:
        previous_focus = set(previous.focus if previous is not None else [])
        refreshed_focus = set(refreshed.focus if refreshed is not None else [])
        return previous_focus != refreshed_focus

    @staticmethod
    def _became_contested(previous: WorldSummary | None, refreshed: WorldSummary | None) -> bool:
        return bool(refreshed and refreshed.contested and not bool(previous and previous.contested))

    @staticmethod
    def _uncertainty_opened(previous: WorldSummary | None, refreshed: WorldSummary | None) -> bool:
        previous_uncertainties = WorldStateManager._meaningful_uncertainties(previous)
        refreshed_uncertainties = WorldStateManager._meaningful_uncertainties(refreshed)
        return not previous_uncertainties and bool(refreshed_uncertainties)

    @staticmethod
    def _meaningful_uncertainties(summary: WorldSummary | None) -> list[str]:
        if summary is None:
            return []
        return [
            item for item in summary.uncertainties
            if str(item or "").strip() and str(item or "").strip() != DEFAULT_IMAGE_UNCERTAINTY
        ]

    @staticmethod
    def _latest_summary_snapshot(
        snapshot: WorldStateSnapshot,
        summary: WorldSummary,
    ) -> SceneSnapshot | None:
        source_ids = set(summary.source_snapshot_ids)
        for item in reversed(snapshot.snapshots):
            if item.snapshot_id in source_ids:
                return item
        return snapshot.snapshots[-1] if snapshot.snapshots else None

    @staticmethod
    def _event_from_snapshot(
        *,
        kind: str,
        snapshot: SceneSnapshot,
        inspection_id: str | None,
        summary: str,
        confidence: float,
        contested: bool,
        created_at: str,
    ) -> PerceptionEventCandidate:
        return PerceptionEventCandidate(
            event_id=f"evt_{uuid.uuid4().hex[:12]}",
            kind=kind,
            snapshot_id=snapshot.snapshot_id,
            inspection_id=inspection_id,
            summary=_trim_text(summary, max_chars=200),
            confidence=max(0.0, min(1.0, float(confidence or 0.0))),
            contested=contested,
            created_at=created_at,
            source=snapshot.source,
            scope=snapshot.scope,
            owner_id=snapshot.owner_id,
            provenance=dict(snapshot.provenance),
        )

    def _build_filtered_summary(
        self,
        snapshot: WorldStateSnapshot,
        *,
        included_snapshots: list[SceneSnapshot],
        now: datetime,
    ) -> WorldSummary | None:
        if not included_snapshots:
            return None
        filtered_snapshot = WorldStateSnapshot(
            status="active",
            version="phase2",
            updated_at=snapshot.updated_at,
            scope=snapshot.scope,
            owner_id=snapshot.owner_id,
            snapshots=included_snapshots,
            inspections=[
                item for item in snapshot.inspections
                if any(scene.snapshot_id == item.snapshot_id for scene in included_snapshots)
            ],
            world_summary=None,
        )
        return self._build_world_summary(filtered_snapshot, now=now)

    def _build_world_summary(
        self,
        snapshot: WorldStateSnapshot,
        *,
        now: datetime,
    ) -> WorldSummary:
        focus: list[str] = []
        relationships: list[str] = []
        constraints: list[str] = []
        uncertainties: list[str] = []
        inspection_ids: list[str] = []
        contested_items: list[str] = []
        last_inspected_at: str | None = None
        by_snapshot = {item.snapshot_id: item for item in snapshot.snapshots}
        for item in snapshot.snapshots:
            if item.summary and item.summary not in focus:
                focus.append(item.summary)
            for relationship in item.relationships:
                if relationship and relationship not in relationships:
                    relationships.append(relationship)
            confidence_line = (
                f"snapshot confidence is {'high' if item.confidence >= 0.8 else 'moderate' if item.confidence >= 0.6 else 'low'}"
            )
            if confidence_line not in constraints:
                constraints.append(confidence_line)
            for uncertainty in item.uncertainties:
                if uncertainty not in uncertainties:
                    uncertainties.append(uncertainty)
        for inspection in snapshot.inspections:
            if inspection.snapshot_id not in by_snapshot:
                continue
            if inspection.status != "completed":
                continue
            inspection_ids.append(inspection.inspection_id)
            last_inspected_at = inspection.requested_at
            for line in inspection.confirmed:
                if line and line not in relationships:
                    relationships.append(line)
            for line in inspection.corrected:
                if line and line not in focus:
                    focus.append(line)
            for line in inspection.new_details:
                if line and line not in constraints:
                    constraints.append(line)
            for line in inspection.uncertain:
                if line and line not in uncertainties:
                    uncertainties.append(line)
            if inspection.contested:
                for line in [*inspection.contested_reasons, *inspection.corrected]:
                    if line and line not in contested_items:
                        contested_items.append(line)
        generated_at = now.isoformat()
        summary_ttl_minutes = max(
            1,
            int(getattr(self._context_config, "world_summary_ttl_minutes", 5) or 5),
        )
        fresh_until = (now + timedelta(minutes=summary_ttl_minutes)).isoformat()
        return WorldSummary(
            summary_id=f"world_{uuid.uuid4().hex[:12]}",
            scope=snapshot.scope or "session",
            owner_id=snapshot.owner_id,
            generated_at=generated_at,
            fresh_until=fresh_until,
            focus=_string_list(focus, limit=6, max_chars=200),
            relationships=_string_list(relationships, limit=6, max_chars=200),
            constraints=_string_list(constraints, limit=6, max_chars=200),
            uncertainties=_string_list(uncertainties, limit=6, max_chars=200),
            source_snapshot_ids=[item.snapshot_id for item in snapshot.snapshots],
            inspection_ids=inspection_ids[:6],
            contested=bool(contested_items),
            contested_items=_string_list(contested_items, limit=6, max_chars=200),
            source_count=len(snapshot.snapshots),
            last_inspected_at=last_inspected_at,
        )

    @staticmethod
    def _candidate_view(
        snapshot: SceneSnapshot,
        *,
        included: bool,
        reasons: list[str],
    ) -> dict[str, Any]:
        return {
            "snapshot_id": snapshot.snapshot_id,
            "scope": snapshot.scope,
            "owner_id": snapshot.owner_id,
            "device_id": snapshot.device_id,
            "captured_at": snapshot.captured_at,
            "summary": _trim_text(snapshot.summary, max_chars=160),
            "included": included,
            "reasons": reasons,
        }

    @staticmethod
    def _is_relevant(snapshot: SceneSnapshot, *, session_tokens: set[str]) -> bool:
        if not session_tokens:
            return True
        haystack = " ".join(
            [
                snapshot.summary,
                " ".join(snapshot.objects),
                " ".join(snapshot.relationships),
            ]
        ).lower()
        env_tokens = {"scene", "room", "door", "desk", "device", "camera", "see", "look", "now", "current"}
        if session_tokens & env_tokens:
            return True
        return any(token in haystack for token in session_tokens if len(token) >= 3)

    @staticmethod
    def _has_completed_inspection_with_findings(
        snapshot: WorldStateSnapshot,
        snapshot_id: str,
    ) -> bool:
        for inspection in snapshot.inspections:
            if inspection.snapshot_id != snapshot_id:
                continue
            if inspection.status != "completed":
                continue
            if inspection.corrected or inspection.new_details or inspection.confirmed:
                return True
        return False

    @staticmethod
    def _scope_visible(snapshot: SceneSnapshot, *, runtime_context: RuntimeContext) -> bool:
        from OriginAgent.agent.scope import ScopeResolver

        resolver = ScopeResolver()
        current_scope = runtime_context.default_scope
        current_device_scope = "device" if runtime_context.device_id else current_scope
        return resolver.is_visible(
            scope=snapshot.scope,
            current_scope=current_device_scope,
            owner_id=snapshot.owner_id,
            current_owner_id=runtime_context.user_id,
            device_id=snapshot.device_id,
            current_device_id=runtime_context.device_id,
        )

    def _build_scene_snapshot(
        self,
        descriptor: AttachmentDescriptor,
        *,
        media_path: str,
        runtime_context: RuntimeContext,
        sidecar: dict[str, Any] | None,
        inferred_provenance: dict[str, str] | None = None,
    ) -> SceneSnapshot:
        captured_at = _utcnow_iso()
        kind = str(descriptor.kind or "image").strip() or "image"
        summary = f"Uninspected {kind} recording from {runtime_context.channel}."
        objects: list[str] = []
        relationships: list[str] = []
        uncertainties = [
            DEFAULT_IMAGE_UNCERTAINTY
            if kind == "image"
            else f"{kind} has not been deeply inspected yet"
        ]
        confidence = 0.35
        inferred_provenance = dict(inferred_provenance or {})
        provenance = {
            "producer": str(
                (sidecar or {}).get("producer")
                or inferred_provenance.get("producer")
                or descriptor.source
                or "media"
            ),
            "ingest_method": str(
                (sidecar or {}).get("ingest_method")
                or inferred_provenance.get("ingest_method")
                or "workspace_media"
            ),
        }
        if isinstance(sidecar, dict):
            captured_at = str(sidecar.get("captured_at") or captured_at).strip()
            summary = str(sidecar.get("summary") or summary).strip()
            objects = _string_list(sidecar.get("objects"))
            relationships = _string_list(sidecar.get("relationships"))
            uncertainties = _string_list(sidecar.get("uncertainties")) or uncertainties
            try:
                confidence = float(sidecar.get("confidence", confidence))
            except (TypeError, ValueError):
                confidence = confidence
            confidence = max(0.0, min(1.0, confidence))
            if isinstance(sidecar.get("provenance"), dict):
                provenance.update(sidecar["provenance"])
        source = str((sidecar or {}).get("source") or f"{descriptor.source}.{descriptor.kind}").strip()
        scope = str((sidecar or {}).get("scope") or "session").strip() or "session"
        owner_id = str((sidecar or {}).get("owner_id") or runtime_context.user_id).strip() or runtime_context.user_id
        device_id = str((sidecar or {}).get("device_id") or runtime_context.device_id or "").strip() or runtime_context.device_id
        return SceneSnapshot(
            snapshot_id=f"snap_{uuid.uuid4().hex[:12]}",
            kind=kind,
            source=source,
            scope=scope,
            owner_id=owner_id,
            device_id=device_id,
            captured_at=captured_at,
            media_path=media_path,
            summary=summary,
            objects=objects,
            relationships=relationships,
            confidence=confidence,
            uncertainties=uncertainties,
            provenance=provenance,
        )

    @staticmethod
    def _read_sidecar(path: Path) -> dict[str, Any] | None:
        candidates = [path.with_suffix(".json")]
        legacy_candidate = path.with_suffix(path.suffix + ".json")
        if legacy_candidate not in candidates:
            candidates.append(legacy_candidate)
        for sidecar_path in candidates:
            if not sidecar_path.is_file():
                continue
            try:
                data = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                return data
        return None

    @staticmethod
    def _infer_provenance_from_path(media_path: str) -> dict[str, str]:
        normalized = _path_to_posix(media_path).lstrip("/")
        parts = [part for part in normalized.split("/") if part]
        if len(parts) >= 2 and parts[0] == "uploads":
            return {
                "producer": parts[1],
                "ingest_method": "workspace_upload",
            }
        if len(parts) >= 2 and parts[0] == "inbox":
            return {
                "producer": parts[1],
                "ingest_method": "workspace_inbox",
            }
        return {
            "producer": "media",
            "ingest_method": "workspace_media",
        }

    def _is_snapshot_fresh(self, snapshot: SceneSnapshot, *, now: datetime) -> bool:
        captured_at = _parse_dt(snapshot.captured_at)
        if captured_at is None:
            return True
        freshness_minutes = max(
            1,
            int(getattr(self._context_config, "snapshot_freshness_minutes", 30) or 30),
        )
        return now - captured_at <= timedelta(minutes=freshness_minutes)
