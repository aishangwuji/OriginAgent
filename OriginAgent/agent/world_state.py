"""Minimal Phase 2 world-state read models for continuity-aware context."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from OriginAgent.agent.identity import RuntimeContext
from OriginAgent.session.manager import Session, SessionManager
from OriginAgent.utils.attachments import AttachmentDescriptor, describe_attachment


WORLD_STATE_METADATA_KEY = "world_state_v1"


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
        )


@dataclass
class WorldSummary:
    summary_id: str
    scope: str
    owner_id: str | None
    generated_at: str
    fresh_until: str
    focus: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    source_snapshot_ids: list[str] = field(default_factory=list)
    inspection_ids: list[str] = field(default_factory=list)

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
            constraints=_string_list(raw.get("constraints")),
            uncertainties=_string_list(raw.get("uncertainties")),
            source_snapshot_ids=_string_list(raw.get("source_snapshot_ids")),
            inspection_ids=_string_list(raw.get("inspection_ids")),
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
    world_summary: WorldSummary | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "version": self.version,
            "updated_at": self.updated_at,
            "scope": self.scope,
            "owner_id": self.owner_id,
            "snapshots": [item.to_json() for item in self.snapshots],
            "inspections": [item.to_json() for item in self.inspections],
            "world_summary": self.world_summary.to_json() if self.world_summary is not None else None,
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
        summary = WorldSummary.from_json(raw.get("world_summary"))
        return cls(
            status=str(raw.get("status") or "placeholder").strip() or "placeholder",
            version=str(raw.get("version") or "phase1").strip() or "phase1",
            updated_at=str(raw.get("updated_at") or _utcnow_iso()).strip(),
            scope=str(raw.get("scope") or "session").strip() or "session",
            owner_id=str(raw.get("owner_id")).strip() if raw.get("owner_id") else None,
            snapshots=snapshots,
            inspections=inspections,
            world_summary=summary,
        )


class WorldStateManager:
    """Session-backed minimal world-state manager for Phase 2."""

    def __init__(self, workspace: Path, sessions: SessionManager) -> None:
        self._workspace = Path(workspace)
        self._sessions = sessions

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
        }

    def ingest_media(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        media_paths: list[str] | None,
    ) -> WorldStateSnapshot:
        snapshot = self.load(session, identity=runtime_context)
        media_paths = [path for path in (media_paths or []) if isinstance(path, str) and path]
        added = False
        existing_paths = {item.media_path for item in snapshot.snapshots}
        for media_path in media_paths:
            descriptor = describe_attachment(media_path, source="media")
            if descriptor is None or descriptor.kind != "image":
                continue
            relative_media_path = _path_to_workspace(descriptor.path, workspace=self._workspace)
            if relative_media_path in existing_paths:
                continue
            sidecar = self._read_sidecar(descriptor.path)
            scene = self._build_scene_snapshot(
                descriptor,
                media_path=relative_media_path,
                runtime_context=runtime_context,
                sidecar=sidecar,
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
        return self.save(session, refreshed)

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
        items = [
            *list(summary.get("focus") or []),
            *list(summary.get("uncertainties") or []),
        ]
        trimmed: list[str] = []
        for item in items:
            text = str(item or "").strip()
            if text and text not in trimmed:
                trimmed.append(f"world_attention: {text}")
            if len(trimmed) >= limit:
                break
        return trimmed

    def inspect_snapshot(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        snapshot_id: str,
        requested_by: str = "user_turn",
    ) -> dict[str, Any]:
        snapshot = self.load(session, identity=runtime_context)
        target = next(
            (item for item in snapshot.snapshots if item.snapshot_id == str(snapshot_id).strip()),
            None,
        )
        if target is None:
            return {"error": f"snapshot '{snapshot_id}' not found"}
        inspection = InspectionResult(
            inspection_id=f"inspect_{uuid.uuid4().hex[:12]}",
            snapshot_id=target.snapshot_id,
            requested_by=str(requested_by or "user_turn").strip() or "user_turn",
            requested_at=_utcnow_iso(),
            confirmed=[line for line in target.relationships if line][:4],
            corrected=[],
            new_details=[
                line for line in [
                    f"objects observed: {', '.join(target.objects)}" if target.objects else "",
                    f"source media path: {target.media_path}",
                ] if line
            ][:4],
            uncertain=target.uncertainties[:4],
            confidence=max(0.4, min(0.95, target.confidence)),
        )
        snapshot.inspections.append(inspection)
        refreshed = self._refresh_summary(snapshot)
        self.save(session, refreshed)
        return {
            "snapshot": target.to_json(),
            "inspection": inspection.to_json(),
            "world_summary": refreshed.world_summary.to_json() if refreshed.world_summary is not None else {},
        }

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
        relevant = [
            item for item in visible
            if self._is_relevant(item, session_tokens=session_tokens)
        ]
        included = relevant or visible[-1:] if visible else []
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
        }

    def _refresh_summary(self, snapshot: WorldStateSnapshot) -> WorldStateSnapshot:
        now = _utcnow()
        fresh_snapshots = [
            item for item in snapshot.snapshots
            if self._is_snapshot_fresh(item, now=now)
        ]
        snapshot.snapshots = fresh_snapshots[-6:]
        snapshot.inspections = [
            item for item in snapshot.inspections
            if any(scene.snapshot_id == item.snapshot_id for scene in snapshot.snapshots)
        ][-6:]
        if not snapshot.snapshots:
            snapshot.status = "placeholder"
            snapshot.version = "phase1"
            snapshot.world_summary = None
            return snapshot
        snapshot.status = "active"
        snapshot.version = "phase2"
        snapshot.world_summary = self._build_world_summary(snapshot, now=now)
        return snapshot

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
        constraints: list[str] = []
        uncertainties: list[str] = []
        inspection_ids: list[str] = []
        by_snapshot = {item.snapshot_id: item for item in snapshot.snapshots}
        for item in snapshot.snapshots:
            if item.summary and item.summary not in focus:
                focus.append(item.summary)
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
            inspection_ids.append(inspection.inspection_id)
            for line in inspection.corrected:
                if line and line not in focus:
                    focus.append(line)
            for line in inspection.new_details:
                if line and line not in constraints:
                    constraints.append(line)
            for line in inspection.uncertain:
                if line and line not in uncertainties:
                    uncertainties.append(line)
        generated_at = now.isoformat()
        fresh_until = (now + timedelta(minutes=5)).isoformat()
        return WorldSummary(
            summary_id=f"world_{uuid.uuid4().hex[:12]}",
            scope=snapshot.scope or "session",
            owner_id=snapshot.owner_id,
            generated_at=generated_at,
            fresh_until=fresh_until,
            focus=_string_list(focus, limit=6, max_chars=200),
            constraints=_string_list(constraints, limit=6, max_chars=200),
            uncertainties=_string_list(uncertainties, limit=6, max_chars=200),
            source_snapshot_ids=[item.snapshot_id for item in snapshot.snapshots],
            inspection_ids=inspection_ids[:6],
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
    ) -> SceneSnapshot:
        captured_at = _utcnow_iso()
        summary = f"Uninspected image snapshot captured from {runtime_context.channel}."
        objects: list[str] = []
        relationships: list[str] = []
        uncertainties = ["image has not been deeply inspected yet"]
        confidence = 0.35
        provenance = {
            "producer": str((sidecar or {}).get("producer") or descriptor.source or "media"),
            "ingest_method": str((sidecar or {}).get("ingest_method") or "workspace_media"),
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
            kind=descriptor.kind,
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
        sidecar_path = path.with_suffix(path.suffix + ".json")
        if not sidecar_path.is_file():
            return None
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _is_snapshot_fresh(snapshot: SceneSnapshot, *, now: datetime) -> bool:
        captured_at = _parse_dt(snapshot.captured_at)
        if captured_at is None:
            return True
        return now - captured_at <= timedelta(minutes=30)
