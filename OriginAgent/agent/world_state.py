"""Minimal Phase 2 world-state read models for continuity-aware context."""

from __future__ import annotations

import json
import hashlib
import mimetypes
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
    media_hash: str | None = None
    media_status: str = "pending"
    media_mime_type: str | None = None
    media_size_bytes: int | None = None
    media_mtime: float | None = None
    inspection_attempts: int = 0
    last_inspection_error: str | None = None

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
            media_hash=str(raw.get("media_hash")).strip() if raw.get("media_hash") else None,
            media_status=str(raw.get("media_status") or "pending").strip() or "pending",
            media_mime_type=str(raw.get("media_mime_type")).strip() if raw.get("media_mime_type") else None,
            media_size_bytes=int(raw.get("media_size_bytes")) if raw.get("media_size_bytes") is not None else None,
            media_mtime=float(raw.get("media_mtime")) if raw.get("media_mtime") is not None else None,
            inspection_attempts=max(0, int(raw.get("inspection_attempts", 0) or 0)),
            last_inspection_error=str(raw.get("last_inspection_error")).strip()
            if raw.get("last_inspection_error")
            else None,
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
    inspection_mode: str = "text"
    provider: str | None = None
    model: str | None = None
    source_media_ids: list[str] = field(default_factory=list)
    source_mime_type: str | None = None
    failure_reason: str | None = None

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
            inspection_mode=str(raw.get("inspection_mode") or "text").strip() or "text",
            provider=str(raw.get("provider")).strip() if raw.get("provider") else None,
            model=str(raw.get("model")).strip() if raw.get("model") else None,
            source_media_ids=_string_list(raw.get("source_media_ids"), limit=12, max_chars=120),
            source_mime_type=str(raw.get("source_mime_type")).strip() if raw.get("source_mime_type") else None,
            failure_reason=str(raw.get("failure_reason")).strip() if raw.get("failure_reason") else None,
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


_DEVICE_EVENT_KINDS = {"appeared", "updated", "disappeared", "renamed", "relocated", "stale"}
_DEVICE_PERMISSION_CAPABILITIES = {"camera", "screen", "audio"}
_DEVICE_PERMISSION_STATUSES = {"pending", "granted", "denied", "revoked", "expired"}
_MEDIA_EVENT_KINDS = {"discovered", "status_changed", "inspected", "failed", "skipped"}
_MEDIA_STATUSES = {"pending", "inspecting", "inspected", "failed", "skipped"}


@dataclass
class MediaLifecycleEvent:
    event_id: str
    kind: str
    snapshot_id: str
    media_path: str
    previous_status: str | None = None
    current_status: str | None = None
    summary: str = ""
    created_at: str = field(default_factory=_utcnow_iso)
    evidence: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "MediaLifecycleEvent | None":
        if not isinstance(raw, dict):
            return None
        event_id = str(raw.get("event_id") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        snapshot_id = str(raw.get("snapshot_id") or "").strip()
        media_path = str(raw.get("media_path") or "").strip()
        if not event_id or kind not in _MEDIA_EVENT_KINDS or not snapshot_id or not media_path:
            return None
        return cls(
            event_id=event_id,
            kind=kind,
            snapshot_id=snapshot_id,
            media_path=media_path,
            previous_status=str(raw.get("previous_status")).strip() if raw.get("previous_status") else None,
            current_status=str(raw.get("current_status")).strip() if raw.get("current_status") else None,
            summary=_trim_text(raw.get("summary"), max_chars=240),
            created_at=str(raw.get("created_at") or _utcnow_iso()).strip(),
            evidence=_string_list(raw.get("evidence"), limit=10, max_chars=200),
        )


@dataclass
class DeviceLifecycleEvent:
    event_id: str
    kind: str
    device_id: str
    change_summary: str
    previous_state: dict[str, Any] | None = None
    current_state: dict[str, Any] | None = None
    created_at: str = field(default_factory=_utcnow_iso)
    evidence: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "DeviceLifecycleEvent | None":
        if not isinstance(raw, dict):
            return None
        event_id = str(raw.get("event_id") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        device_id = str(raw.get("device_id") or "").strip()
        if not event_id or kind not in _DEVICE_EVENT_KINDS or not device_id:
            return None
        previous = raw.get("previous_state") if isinstance(raw.get("previous_state"), dict) else None
        current = raw.get("current_state") if isinstance(raw.get("current_state"), dict) else None
        return cls(
            event_id=event_id,
            kind=kind,
            device_id=device_id,
            change_summary=_trim_text(raw.get("change_summary"), max_chars=240),
            previous_state=dict(previous) if previous is not None else None,
            current_state=dict(current) if current is not None else None,
            created_at=str(raw.get("created_at") or _utcnow_iso()).strip(),
            evidence=_string_list(raw.get("evidence"), limit=10, max_chars=200),
        )


@dataclass
class DeviceBinding:
    device_id: str
    user_label: str
    location: str | None = None
    bound_at: str = field(default_factory=_utcnow_iso)
    bound_by_session: str | None = None
    status: str = "active"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "DeviceBinding | None":
        if not isinstance(raw, dict):
            return None
        device_id = str(raw.get("device_id") or "").strip()
        user_label = _trim_text(raw.get("user_label"), max_chars=120)
        if not device_id or not user_label:
            return None
        status = str(raw.get("status") or "active").strip() or "active"
        if status not in {"active", "revoked"}:
            status = "active"
        return cls(
            device_id=device_id,
            user_label=user_label,
            location=_trim_text(raw.get("location"), max_chars=120) or None,
            bound_at=str(raw.get("bound_at") or _utcnow_iso()).strip(),
            bound_by_session=str(raw.get("bound_by_session")).strip() if raw.get("bound_by_session") else None,
            status=status,
        )


@dataclass
class DevicePermission:
    device_id: str
    capability: str
    status: str = "pending"
    scope: str = "session"
    granted_at: str | None = None
    expires_at: str | None = None
    confirmation_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "DevicePermission | None":
        if not isinstance(raw, dict):
            return None
        device_id = str(raw.get("device_id") or "").strip()
        capability = str(raw.get("capability") or "").strip()
        if not device_id or capability not in _DEVICE_PERMISSION_CAPABILITIES:
            return None
        status = str(raw.get("status") or "pending").strip() or "pending"
        if status not in _DEVICE_PERMISSION_STATUSES:
            status = "pending"
        return cls(
            device_id=device_id,
            capability=capability,
            status=status,
            scope=str(raw.get("scope") or "session").strip() or "session",
            granted_at=str(raw.get("granted_at")).strip() if raw.get("granted_at") else None,
            expires_at=str(raw.get("expires_at")).strip() if raw.get("expires_at") else None,
            confirmation_id=str(raw.get("confirmation_id")).strip() if raw.get("confirmation_id") else None,
        )


@dataclass
class WorldAttentionNotice:
    notice_id: str
    kind: str
    severity: str
    title: str
    summary: str
    source_event_ids: list[str] = field(default_factory=list)
    related_device_ids: list[str] = field(default_factory=list)
    related_snapshot_ids: list[str] = field(default_factory=list)
    suggested_next_step: str | None = None
    requires_confirmation: bool = False
    created_at: str = field(default_factory=_utcnow_iso)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


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
    device_events: list[DeviceLifecycleEvent] = field(default_factory=list)
    device_bindings: list[DeviceBinding] = field(default_factory=list)
    device_permissions: list[DevicePermission] = field(default_factory=list)
    media_events: list[MediaLifecycleEvent] = field(default_factory=list)

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
            "device_events": [item.to_json() for item in self.device_events],
            "device_bindings": [item.to_json() for item in self.device_bindings],
            "device_permissions": [item.to_json() for item in self.device_permissions],
            "media_events": [item.to_json() for item in self.media_events],
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
        device_events = [
            item for item in (
                DeviceLifecycleEvent.from_json(value) for value in (raw.get("device_events") or [])
            ) if item is not None
        ]
        device_bindings = [
            item for item in (
                DeviceBinding.from_json(value) for value in (raw.get("device_bindings") or [])
            ) if item is not None
        ]
        device_permissions = [
            item for item in (
                DevicePermission.from_json(value) for value in (raw.get("device_permissions") or [])
            ) if item is not None
        ]
        media_events = [
            item for item in (
                MediaLifecycleEvent.from_json(value) for value in (raw.get("media_events") or [])
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
            device_events=device_events,
            device_bindings=device_bindings,
            device_permissions=device_permissions,
            media_events=media_events,
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

    def device_state_summary(self, session: Session, *, identity: RuntimeContext | None = None) -> dict[str, Any]:
        snapshot = self.load(session, identity=identity)
        return {
            "device_events_summary": self._device_events_summary(snapshot),
            "device_bindings_summary": self._device_bindings_summary(snapshot),
            "device_permissions_summary": self._device_permissions_summary(snapshot),
        }

    def media_state_summary(self, session: Session, *, identity: RuntimeContext | None = None) -> dict[str, Any]:
        snapshot = self.load(session, identity=identity)
        return {
            "media_queue_summary": self._media_queue_summary(snapshot),
            "recent_media_events": self._media_events_summary(snapshot).get("recent_media_events", []),
            "last_scene_inspection": self._last_scene_inspection(snapshot),
            "audio_status": self._audio_status_summary(snapshot),
        }

    def home_state_summary(
        self,
        session: Session,
        *,
        identity: RuntimeContext | None = None,
    ) -> dict[str, Any]:
        snapshot = self.load(session, identity=identity)
        return dict(self._home_state_bundle_from_snapshot(snapshot).get("home_state") or {})

    def attention_notices(
        self,
        session: Session,
        *,
        identity: RuntimeContext | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        snapshot = self.load(session, identity=identity)
        bundle = self._home_state_bundle_from_snapshot(snapshot, display_limit=limit)
        return {
            "attention_notices_summary": dict(bundle.get("attention_notices_summary") or {}),
            "attention_notices": list(bundle.get("attention_notices") or []),
            "suggested_next_steps": list(bundle.get("suggested_next_steps") or []),
        }

    def home_state_observability(
        self,
        session: Session,
        *,
        identity: RuntimeContext | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        snapshot = self.load(session, identity=identity)
        bundle = self._home_state_bundle_from_snapshot(snapshot, display_limit=limit)
        return {
            "home_state": dict(bundle.get("home_state") or {}),
            "attention_notices_summary": dict(bundle.get("attention_notices_summary") or {}),
            "suggested_next_steps": list(bundle.get("suggested_next_steps") or []),
        }

    def inspect_home_state(
        self,
        session: Session,
        *,
        identity: RuntimeContext | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        snapshot = self.load(session, identity=identity)
        bundle = self._home_state_bundle_from_snapshot(snapshot, display_limit=limit)
        return {
            "home_state": dict(bundle.get("home_state") or {}),
            "attention_notices_summary": dict(bundle.get("attention_notices_summary") or {}),
            "attention_notices": list(bundle.get("attention_notices") or []),
            "suggested_next_steps": list(bundle.get("suggested_next_steps") or []),
        }

    def snapshot_prompt_payload(
        self,
        session: Session,
        *,
        identity: RuntimeContext | None = None,
        current_message: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.load(session, identity=identity)
        device_state = {
            "device_events_summary": self._device_events_summary(snapshot),
            "device_bindings_summary": self._device_bindings_summary(snapshot),
            "device_permissions_summary": self._device_permissions_summary(snapshot),
        }
        media_state = {
            "media_queue_summary": self._media_queue_summary(snapshot),
            "recent_media_events": self._media_events_summary(snapshot).get("recent_media_events", []),
            "last_scene_inspection": self._last_scene_inspection(snapshot),
            "audio_status": self._audio_status_summary(snapshot),
        }
        home_state = self._home_state_bundle_from_snapshot(snapshot, display_limit=5)
        if identity is None:
            return {
                "status": snapshot.status,
                "version": snapshot.version,
                "updated_at": snapshot.updated_at,
                "device_map_summary": summarize_device_map(snapshot.device_map) if snapshot.device_map else {},
                **device_state,
                **media_state,
                "home_state": dict(home_state.get("home_state") or {}),
                "attention_notices_summary": dict(home_state.get("attention_notices_summary") or {}),
                "suggested_next_steps": list(home_state.get("suggested_next_steps") or []),
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
            "recent_events": list((self._recent_events_from_snapshot(snapshot, limit=5) or {}).get("recent_events", [])),
            "device_map_summary": summarize_device_map(snapshot.device_map) if snapshot.device_map else {},
            **device_state,
            **media_state,
            "home_state": dict(home_state.get("home_state") or {}),
            "attention_notices_summary": dict(home_state.get("attention_notices_summary") or {}),
            "suggested_next_steps": list(home_state.get("suggested_next_steps") or []),
        }

    def ingest_device_discovery(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        device_map: dict[str, Any],
    ) -> WorldStateSnapshot:
        snapshot = self.load(session, identity=runtime_context)
        previous_map = dict(snapshot.device_map)
        stabilized_map, lifecycle_events = self._stabilize_device_map(
            previous_map=previous_map,
            current_map=dict(device_map),
        )
        snapshot.status = "active"
        snapshot.version = "phase2"
        snapshot.scope = snapshot.scope or "session"
        snapshot.owner_id = snapshot.owner_id or runtime_context.user_id
        snapshot.device_map = stabilized_map
        snapshot.device_events = self._merge_device_events(
            existing=snapshot.device_events,
            new_events=lifecycle_events,
        )
        return self.save(session, snapshot)

    def bind_device(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        device_id: str,
        user_label: str,
        location: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.load(session, identity=runtime_context)
        if self._device_by_id(snapshot.device_map, device_id) is None:
            return {"status": "denied", "reason": "device_not_found", "device_id": device_id}
        binding = DeviceBinding(
            device_id=str(device_id).strip(),
            user_label=_trim_text(user_label, max_chars=120),
            location=_trim_text(location, max_chars=120) or None,
            bound_by_session=runtime_context.session_id,
        )
        snapshot.device_bindings = [
            item for item in snapshot.device_bindings
            if item.device_id != binding.device_id
        ]
        snapshot.device_bindings.append(binding)
        snapshot.device_map = self._apply_device_authorization(snapshot.device_map, snapshot)
        self.save(session, snapshot)
        return {"status": "ok", "binding": binding.to_json(), **self.device_state_summary(session, identity=runtime_context)}

    def revoke_device_binding(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        device_id: str,
    ) -> dict[str, Any]:
        snapshot = self.load(session, identity=runtime_context)
        found = False
        updated: list[DeviceBinding] = []
        for binding in snapshot.device_bindings:
            if binding.device_id == device_id and binding.status == "active":
                found = True
                updated.append(DeviceBinding(
                    device_id=binding.device_id,
                    user_label=binding.user_label,
                    location=binding.location,
                    bound_at=binding.bound_at,
                    bound_by_session=binding.bound_by_session,
                    status="revoked",
                ))
            else:
                updated.append(binding)
        snapshot.device_bindings = updated
        snapshot.device_permissions = [
            permission if permission.device_id != device_id or permission.status not in {"pending", "granted"}
            else DevicePermission(
                device_id=permission.device_id,
                capability=permission.capability,
                status="revoked",
                scope=permission.scope,
                granted_at=permission.granted_at,
                expires_at=permission.expires_at,
                confirmation_id=permission.confirmation_id,
            )
            for permission in snapshot.device_permissions
        ]
        snapshot.device_map = self._apply_device_authorization(snapshot.device_map, snapshot)
        self.save(session, snapshot)
        return {
            "status": "ok" if found else "not_found",
            "device_id": device_id,
            **self.device_state_summary(session, identity=runtime_context),
        }

    def request_device_permission(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        device_id: str,
        capability: str,
        status: str = "pending",
        scope: str = "session",
        confirmation_id: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        capability = str(capability or "").strip()
        if capability not in _DEVICE_PERMISSION_CAPABILITIES:
            return {"status": "denied", "reason": "unsupported_capability", "capability": capability}
        snapshot = self.load(session, identity=runtime_context)
        if self._active_binding(snapshot, device_id) is None:
            return {"status": "denied", "reason": "device_binding_required", "device_id": device_id}
        normalized_status = status if status in _DEVICE_PERMISSION_STATUSES else "pending"
        permission = DevicePermission(
            device_id=device_id,
            capability=capability,
            status=normalized_status,
            scope=scope or "session",
            granted_at=_utcnow_iso() if normalized_status == "granted" else None,
            expires_at=expires_at,
            confirmation_id=confirmation_id,
        )
        snapshot.device_permissions = [
            item for item in snapshot.device_permissions
            if not (item.device_id == device_id and item.capability == capability and item.scope == permission.scope)
        ]
        snapshot.device_permissions.append(permission)
        snapshot.device_map = self._apply_device_authorization(snapshot.device_map, snapshot)
        self.save(session, snapshot)
        return {"status": "ok", "permission": permission.to_json(), **self.device_state_summary(session, identity=runtime_context)}

    def device_capability_granted(
        self,
        session: Session,
        *,
        identity: RuntimeContext | None = None,
        device_id: str,
        capability: str,
    ) -> bool:
        snapshot = self.load(session, identity=identity)
        if self._active_binding(snapshot, device_id) is None:
            return False
        return any(
            permission.device_id == device_id
            and permission.capability == capability
            and self._permission_active(permission)
            for permission in snapshot.device_permissions
        )

    def ingest_media(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        media_paths: list[str] | None,
    ) -> WorldStateSnapshot:
        snapshot = self.load(session, identity=runtime_context)
        descriptors = [self._describe_workspace_attachment(media_path, source="media") for media_path in (media_paths or []) if isinstance(media_path, str) and media_path]
        return self._ingest_descriptors(
            session,
            runtime_context=runtime_context,
            snapshot=snapshot,
            descriptors=descriptors,
        )

    def ingest_media_scan(
        self,
        session: Session,
        *,
        runtime_context: RuntimeContext,
        media_paths: list[str] | None,
    ) -> WorldStateSnapshot:
        """Register workspace media discovered by an explicit scan."""

        return self.ingest_media(
            session,
            runtime_context=runtime_context,
            media_paths=media_paths,
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
            collected.append(self._describe_workspace_attachment(media_path, source="media"))
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
        snapshot = self.load(session, identity=runtime_context)
        filtered = self.filtered_candidates(
            session,
            runtime_context=runtime_context,
            current_message=None,
        )
        summary = filtered.get("included_summary") or {}
        notices = self._compute_attention_notices(snapshot)
        if not summary and not notices:
            return []
        grouped_items: list[tuple[str, list[str]]] = [
            # Surface contested state first so a small attention budget still
            # preserves disagreements uncovered by inspection.
            ("world_contested", list(summary.get("contested_items") or [])),
            ("world_uncertainty", list(summary.get("uncertainties") or [])),
            ("world_attention", self._attention_notice_lines(notices)),
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
        previous_status = target.media_status
        target.media_status = "inspecting"
        target.inspection_attempts = max(0, int(target.inspection_attempts or 0)) + 1
        target.last_inspection_error = None
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
            inspection_mode=str(inspection_payload.get("inspection_mode") or "text").strip() or "text",
            provider=str(inspection_payload.get("provider")).strip() if inspection_payload.get("provider") else None,
            model=str(inspection_payload.get("model")).strip() if inspection_payload.get("model") else None,
            source_media_ids=_string_list(inspection_payload.get("source_media_ids"), limit=12, max_chars=120),
            source_mime_type=str(inspection_payload.get("source_mime_type")).strip()
            if inspection_payload.get("source_mime_type")
            else target.media_mime_type,
            failure_reason=str(inspection_payload.get("failure_reason")).strip()
            if inspection_payload.get("failure_reason")
            else None,
        )
        if inspection.status == "completed":
            target.media_status = "inspected"
        elif inspection.status == "skipped":
            target.media_status = "skipped"
        else:
            target.media_status = "failed"
            target.last_inspection_error = inspection.failure_reason or "; ".join(inspection.uncertain[:2]) or inspection.status
        snapshot.inspections.append(inspection)
        snapshot.media_events = self._merge_media_events(
            existing=snapshot.media_events,
            new_events=[
                self._media_event(
                    kind="status_changed",
                    snapshot=target,
                    previous_status=previous_status,
                    current_status=target.media_status,
                    summary=f"Media inspection status changed to {target.media_status}: {target.media_path}",
                ),
                self._media_event(
                    kind="inspected" if target.media_status == "inspected" else "failed",
                    snapshot=target,
                    previous_status=previous_status,
                    current_status=target.media_status,
                    summary=(
                        f"Media inspected: {target.media_path}"
                        if target.media_status == "inspected"
                        else f"Media inspection failed: {target.media_path}"
                    ),
                    evidence=inspection.evidence_excerpt or inspection.uncertain,
                ),
            ],
        )
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
        return self._recent_events_from_snapshot(snapshot, limit=limit)

    def _recent_events_from_snapshot(
        self,
        snapshot: WorldStateSnapshot,
        *,
        limit: int = 5,
    ) -> dict[str, Any]:
        combined: list[dict[str, Any]] = []
        for item in snapshot.events:
            event = item.to_json()
            event["event_family"] = "scene"
            combined.append(event)
        for item in snapshot.device_events:
            event = item.to_json()
            event["event_family"] = "device"
            event["summary"] = event.get("change_summary", "")
            combined.append(event)
        for item in snapshot.media_events:
            event = item.to_json()
            event["event_family"] = "media"
            combined.append(event)
        ordered = sorted(
            combined,
            key=lambda item: _parse_dt(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        recent = ordered[: max(0, int(limit or 0))]
        by_kind: dict[str, int] = {}
        for item in combined:
            key = f"{item.get('event_family')}:{item.get('kind')}"
            by_kind[key] = by_kind.get(key, 0) + 1
        return {
            "recent_events": recent,
            "event_summary": {
                "total": len(combined),
                "by_kind": by_kind,
                "latest_created_at": recent[0].get("created_at") if recent else None,
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
            status="active" if kept_snapshots or snapshot.device_map or snapshot.device_bindings else "placeholder",
            version="phase2" if kept_snapshots or snapshot.device_map or snapshot.device_bindings else "phase1",
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
            device_events=sorted(
                snapshot.device_events,
                key=lambda item: _parse_dt(item.created_at) or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )[:50],
            device_bindings=list(snapshot.device_bindings),
            device_permissions=[
                self._expire_permission_if_needed(permission, now=now)
                for permission in snapshot.device_permissions
            ],
            media_events=sorted(
                snapshot.media_events,
                key=lambda item: _parse_dt(item.created_at) or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )[:50],
        )
        if not refreshed.snapshots:
            return refreshed
        refreshed.world_summary = self._build_world_summary(refreshed, now=now)
        return refreshed

    @staticmethod
    def _device_by_id(device_map: dict[str, Any], device_id: str) -> dict[str, Any] | None:
        for device in list(device_map.get("devices") or []) if isinstance(device_map, dict) else []:
            if isinstance(device, dict) and str(device.get("device_id") or "").strip() == str(device_id).strip():
                return dict(device)
        return None

    @staticmethod
    def _identity_key(device: dict[str, Any]) -> str:
        mac = _trim_text(device.get("mac_address"), max_chars=80).lower()
        if mac:
            return f"mac:{mac}"
        hostname = _trim_text(device.get("hostname"), max_chars=120).lower()
        vendor = _trim_text(device.get("vendor"), max_chars=120).lower()
        if hostname and vendor:
            return f"host_vendor:{hostname}|{vendor}"
        ips = sorted(str(ip).strip() for ip in device.get("ip_addresses") or [] if str(ip).strip())
        if ips:
            return f"ip:{','.join(ips)}"
        name = _trim_text(device.get("name"), max_chars=120).lower()
        return f"name:{name or device.get('device_id') or 'unknown'}"

    @staticmethod
    def _device_ips(device: dict[str, Any]) -> set[str]:
        return {str(ip).strip() for ip in device.get("ip_addresses") or [] if str(ip).strip()}

    @staticmethod
    def _device_services(device: dict[str, Any]) -> set[str]:
        services: set[str] = set()
        for service in device.get("services") or []:
            if not isinstance(service, dict):
                continue
            port = str(service.get("port") or "").strip()
            protocol = str(service.get("protocol") or service.get("name") or "").strip()
            if port or protocol:
                services.add(f"{protocol}:{port}")
        return services

    def _stabilize_device_map(
        self,
        *,
        previous_map: dict[str, Any],
        current_map: dict[str, Any],
    ) -> tuple[dict[str, Any], list[DeviceLifecycleEvent]]:
        now = _utcnow_iso()
        previous_devices = [dict(item) for item in previous_map.get("devices") or [] if isinstance(item, dict)]
        current_devices = [dict(item) for item in current_map.get("devices") or [] if isinstance(item, dict)]
        previous_by_id = {str(item.get("device_id") or ""): item for item in previous_devices if item.get("device_id")}
        previous_by_identity = {self._identity_key(item): item for item in previous_devices}
        matched_previous_ids: set[str] = set()
        stabilized: list[dict[str, Any]] = []
        events: list[DeviceLifecycleEvent] = []
        for current in current_devices:
            current_identity = self._identity_key(current)
            previous = previous_by_identity.get(current_identity)
            if previous is None:
                current_ips = self._device_ips(current)
                previous = next(
                    (
                        item for item in previous_devices
                        if current_ips and self._device_ips(item) & current_ips
                    ),
                    None,
                )
            previous_id = str((previous or {}).get("device_id") or "").strip()
            if previous is not None and previous_id:
                current["device_id"] = previous_id
                matched_previous_ids.add(previous_id)
            current["identity_key"] = current_identity
            if previous is None:
                events.append(self._device_event(
                    kind="appeared",
                    device_id=str(current.get("device_id") or current_identity),
                    previous=None,
                    current=current,
                    summary=f"Device appeared: {self._device_label(current)}",
                    created_at=now,
                ))
            else:
                events.extend(self._device_change_events(previous=previous, current=current, created_at=now))
            stabilized.append(current)
        current_ids = {str(item.get("device_id") or "") for item in stabilized}
        for previous_id, previous in previous_by_id.items():
            if previous_id in matched_previous_ids or previous_id in current_ids:
                continue
            events.append(self._device_event(
                kind="disappeared",
                device_id=previous_id,
                previous=previous,
                current=None,
                summary=f"Device disappeared: {self._device_label(previous)}",
                created_at=now,
            ))
        updated_map = dict(current_map)
        updated_map["devices"] = stabilized
        updated_map["device_count"] = len(stabilized)
        updated_map["summary"] = summarize_device_map(updated_map)
        return updated_map, events

    def _device_change_events(
        self,
        *,
        previous: dict[str, Any],
        current: dict[str, Any],
        created_at: str,
    ) -> list[DeviceLifecycleEvent]:
        events: list[DeviceLifecycleEvent] = []
        device_id = str(current.get("device_id") or previous.get("device_id") or "").strip()
        if not device_id:
            return []
        previous_name = _trim_text(previous.get("name") or previous.get("hostname"), max_chars=160)
        current_name = _trim_text(current.get("name") or current.get("hostname"), max_chars=160)
        if previous_name and current_name and previous_name != current_name:
            events.append(self._device_event(
                kind="renamed",
                device_id=device_id,
                previous=previous,
                current=current,
                summary=f"Device renamed from {previous_name} to {current_name}",
                created_at=created_at,
            ))
        if self._device_ips(previous) != self._device_ips(current):
            events.append(self._device_event(
                kind="relocated",
                device_id=device_id,
                previous=previous,
                current=current,
                summary=f"Device network address changed: {self._device_label(current)}",
                created_at=created_at,
            ))
        if self._device_services(previous) != self._device_services(current):
            events.append(self._device_event(
                kind="updated",
                device_id=device_id,
                previous=previous,
                current=current,
                summary=f"Device services changed: {self._device_label(current)}",
                created_at=created_at,
            ))
        return events

    @staticmethod
    def _device_label(device: dict[str, Any]) -> str:
        return (
            _trim_text(device.get("name"), max_chars=120)
            or _trim_text(device.get("hostname"), max_chars=120)
            or _trim_text(",".join(str(ip) for ip in device.get("ip_addresses") or []), max_chars=120)
            or _trim_text(device.get("device_id"), max_chars=120)
            or "unknown device"
        )

    @staticmethod
    def _device_event(
        *,
        kind: str,
        device_id: str,
        previous: dict[str, Any] | None,
        current: dict[str, Any] | None,
        summary: str,
        created_at: str,
    ) -> DeviceLifecycleEvent:
        evidence: list[str] = []
        for state in (current, previous):
            if not isinstance(state, dict):
                continue
            evidence.extend(_string_list(state.get("evidence"), limit=4, max_chars=160))
        return DeviceLifecycleEvent(
            event_id=f"dev_evt_{uuid.uuid4().hex[:12]}",
            kind=kind,
            device_id=device_id,
            change_summary=_trim_text(summary, max_chars=240),
            previous_state=dict(previous) if previous is not None else None,
            current_state=dict(current) if current is not None else None,
            created_at=created_at,
            evidence=_string_list(evidence, limit=8, max_chars=160),
        )

    @staticmethod
    def _merge_device_events(
        *,
        existing: list[DeviceLifecycleEvent],
        new_events: list[DeviceLifecycleEvent],
    ) -> list[DeviceLifecycleEvent]:
        merged = [*new_events, *existing]
        seen: set[tuple[str, str, str]] = set()
        out: list[DeviceLifecycleEvent] = []
        for event in sorted(
            merged,
            key=lambda item: _parse_dt(item.created_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        ):
            key = (event.kind, event.device_id, event.change_summary)
            if key in seen:
                continue
            seen.add(key)
            out.append(event)
            if len(out) >= 50:
                break
        return out

    @staticmethod
    def _media_event(
        *,
        kind: str,
        snapshot: SceneSnapshot,
        previous_status: str | None,
        current_status: str | None,
        summary: str,
        evidence: list[str] | None = None,
    ) -> MediaLifecycleEvent:
        return MediaLifecycleEvent(
            event_id=f"media_evt_{uuid.uuid4().hex[:12]}",
            kind=kind if kind in _MEDIA_EVENT_KINDS else "status_changed",
            snapshot_id=snapshot.snapshot_id,
            media_path=snapshot.media_path,
            previous_status=previous_status,
            current_status=current_status,
            summary=_trim_text(summary, max_chars=240),
            created_at=_utcnow_iso(),
            evidence=_string_list(evidence or [], limit=8, max_chars=160),
        )

    @staticmethod
    def _merge_media_events(
        *,
        existing: list[MediaLifecycleEvent],
        new_events: list[MediaLifecycleEvent],
    ) -> list[MediaLifecycleEvent]:
        merged = [*new_events, *existing]
        seen: set[tuple[str, str, str, str | None]] = set()
        out: list[MediaLifecycleEvent] = []
        for event in sorted(
            merged,
            key=lambda item: _parse_dt(item.created_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        ):
            key = (event.kind, event.snapshot_id, event.media_path, event.current_status)
            if key in seen:
                continue
            seen.add(key)
            out.append(event)
            if len(out) >= 50:
                break
        return out

    def _media_metadata(self, path: Path) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "media_hash": None,
            "media_mime_type": mimetypes.guess_type(path.name)[0],
            "media_size_bytes": None,
            "media_mtime": None,
        }
        try:
            stat = path.stat()
            metadata["media_size_bytes"] = int(stat.st_size)
            metadata["media_mtime"] = float(stat.st_mtime)
            hasher = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(chunk)
            metadata["media_hash"] = hasher.hexdigest()
        except OSError:
            return metadata
        return metadata

    @staticmethod
    def _active_binding(snapshot: WorldStateSnapshot, device_id: str) -> DeviceBinding | None:
        return next(
            (
                binding for binding in snapshot.device_bindings
                if binding.device_id == device_id and binding.status == "active"
            ),
            None,
        )

    @staticmethod
    def _permission_active(permission: DevicePermission) -> bool:
        if permission.status != "granted":
            return False
        expires = _parse_dt(permission.expires_at)
        return expires is None or expires > _utcnow()

    @staticmethod
    def _expire_permission_if_needed(permission: DevicePermission, *, now: datetime) -> DevicePermission:
        expires = _parse_dt(permission.expires_at)
        if permission.status == "granted" and expires is not None and expires <= now:
            return DevicePermission(
                device_id=permission.device_id,
                capability=permission.capability,
                status="expired",
                scope=permission.scope,
                granted_at=permission.granted_at,
                expires_at=permission.expires_at,
                confirmation_id=permission.confirmation_id,
            )
        return permission

    def _apply_device_authorization(
        self,
        device_map: dict[str, Any],
        snapshot: WorldStateSnapshot,
    ) -> dict[str, Any]:
        bindings = {
            binding.device_id: binding
            for binding in snapshot.device_bindings
            if binding.status == "active"
        }
        granted: dict[str, list[str]] = {}
        for permission in snapshot.device_permissions:
            if self._permission_active(permission):
                granted.setdefault(permission.device_id, []).append(permission.capability)
        updated = dict(device_map)
        devices: list[dict[str, Any]] = []
        for raw in updated.get("devices") or []:
            if not isinstance(raw, dict):
                continue
            device = dict(raw)
            device_id = str(device.get("device_id") or "").strip()
            binding = bindings.get(device_id)
            device["authorized_capabilities"] = sorted(set(granted.get(device_id, [])))
            device["binding"] = binding.to_json() if binding is not None else None
            device["controllable"] = False
            devices.append(device)
        updated["devices"] = devices
        updated["device_count"] = len(devices)
        updated["summary"] = summarize_device_map(updated)
        return updated

    @staticmethod
    def _device_events_summary(snapshot: WorldStateSnapshot) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for event in snapshot.device_events:
            by_kind[event.kind] = by_kind.get(event.kind, 0) + 1
        recent = sorted(
            snapshot.device_events,
            key=lambda item: _parse_dt(item.created_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:5]
        return {
            "device_event_count": len(snapshot.device_events),
            "by_kind": by_kind,
            "recent_device_events": [event.to_json() for event in recent],
        }

    @staticmethod
    def _media_events_summary(snapshot: WorldStateSnapshot) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for event in snapshot.media_events:
            by_kind[event.kind] = by_kind.get(event.kind, 0) + 1
        recent = sorted(
            snapshot.media_events,
            key=lambda item: _parse_dt(item.created_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:5]
        return {
            "media_event_count": len(snapshot.media_events),
            "by_kind": by_kind,
            "recent_media_events": [event.to_json() for event in recent],
        }

    @staticmethod
    def _media_queue_summary(snapshot: WorldStateSnapshot) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        for scene in snapshot.snapshots:
            status = scene.media_status or "pending"
            by_status[status] = by_status.get(status, 0) + 1
            by_kind[scene.kind] = by_kind.get(scene.kind, 0) + 1
        recent = sorted(
            snapshot.snapshots,
            key=lambda item: _parse_dt(item.captured_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:5]
        pending = [scene for scene in snapshot.snapshots if scene.media_status == "pending"]
        return {
            "media_count": len(snapshot.snapshots),
            "uninspected_count": len(pending),
            "by_status": by_status,
            "by_kind": by_kind,
            "recent_media": [
                {
                    "snapshot_id": scene.snapshot_id,
                    "kind": scene.kind,
                    "media_path": scene.media_path,
                    "media_status": scene.media_status,
                    "captured_at": scene.captured_at,
                    "media_mime_type": scene.media_mime_type,
                    "inspection_attempts": scene.inspection_attempts,
                }
                for scene in recent
            ],
            "pending_snapshot_ids": [scene.snapshot_id for scene in pending[:10]],
        }

    @staticmethod
    def _last_scene_inspection(snapshot: WorldStateSnapshot) -> dict[str, Any]:
        if not snapshot.inspections:
            return {}
        inspection = sorted(
            snapshot.inspections,
            key=lambda item: _parse_dt(item.requested_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[0]
        scene = next((item for item in snapshot.snapshots if item.snapshot_id == inspection.snapshot_id), None)
        return {
            "snapshot_id": inspection.snapshot_id,
            "status": inspection.status,
            "inspection_mode": inspection.inspection_mode,
            "provider": inspection.provider,
            "model": inspection.model,
            "confidence": inspection.confidence,
            "media_path": scene.media_path if scene is not None else None,
            "requested_at": inspection.requested_at,
            "failure_reason": inspection.failure_reason,
        }

    @staticmethod
    def _audio_status_summary(snapshot: WorldStateSnapshot) -> dict[str, Any]:
        audio = [scene for scene in snapshot.snapshots if scene.kind == "audio"]
        latest = sorted(
            audio,
            key=lambda item: _parse_dt(item.captured_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:1]
        latest_scene = latest[0] if latest else None
        latest_inspection = next(
            (
                item for item in sorted(
                    snapshot.inspections,
                    key=lambda inspection: _parse_dt(inspection.requested_at) or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True,
                )
                if latest_scene is not None and item.snapshot_id == latest_scene.snapshot_id
            ),
            None,
        )
        return {
            "audio_artifact_count": len(audio),
            "last_audio": {
                "snapshot_id": latest_scene.snapshot_id,
                "media_path": latest_scene.media_path,
                "media_status": latest_scene.media_status,
                "captured_at": latest_scene.captured_at,
            } if latest_scene is not None else {},
            "last_transcription_status": latest_inspection.status if latest_inspection is not None else None,
            "last_transcription_text": (
                latest_inspection.new_details[0]
                if latest_inspection is not None and latest_inspection.new_details
                else None
            ),
        }

    @staticmethod
    def _device_bindings_summary(snapshot: WorldStateSnapshot) -> dict[str, Any]:
        active = [binding for binding in snapshot.device_bindings if binding.status == "active"]
        return {
            "bound_device_count": len(active),
            "bindings": [binding.to_json() for binding in active[:10]],
        }

    def _home_state_bundle_from_snapshot(
        self,
        snapshot: WorldStateSnapshot,
        *,
        display_limit: int = 5,
    ) -> dict[str, Any]:
        if not self._home_situation_enabled():
            return {
                "home_state": {},
                "attention_notices_summary": {"notice_count": 0, "by_severity": {}, "recent_notices": []},
                "attention_notices": [],
                "suggested_next_steps": [],
            }
        notices = self._compute_attention_notices(snapshot)
        display_limit = max(0, min(int(display_limit or 0), 5))
        shown = [notice.to_json() for notice in notices[:display_limit]]
        by_severity: dict[str, int] = {}
        for notice in notices:
            by_severity[notice.severity] = by_severity.get(notice.severity, 0) + 1
        suggested_steps: list[str] = []
        for notice in notices[:display_limit]:
            step = _trim_text(notice.suggested_next_step, max_chars=200)
            if step and step not in suggested_steps:
                suggested_steps.append(step)
        return {
            "home_state": self._build_home_state(snapshot, notices=notices),
            "attention_notices_summary": {
                "notice_count": len(notices),
                "by_severity": by_severity,
                "recent_notices": shown,
            },
            "attention_notices": shown,
            "suggested_next_steps": suggested_steps,
        }

    def _build_home_state(
        self,
        snapshot: WorldStateSnapshot,
        *,
        notices: list[WorldAttentionNotice],
    ) -> dict[str, Any]:
        devices = [dict(item) for item in (snapshot.device_map.get("devices") or []) if isinstance(item, dict)]
        bindings = [binding for binding in snapshot.device_bindings if binding.status == "active"]
        binding_by_id = {binding.device_id: binding for binding in bindings}
        known_locations = sorted({
            binding.location.strip()
            for binding in bindings
            if isinstance(binding.location, str) and binding.location.strip()
        })
        known_devices = [
            self._device_home_view(device, binding_by_id.get(str(device.get("device_id") or "").strip()))
            for device in devices
            if str(device.get("kind") or "").strip() != "unknown"
            or str(device.get("device_id") or "").strip() in binding_by_id
        ][:10]
        unknown_devices = [
            self._device_home_view(device, binding_by_id.get(str(device.get("device_id") or "").strip()))
            for device in devices
            if str(device.get("kind") or "").strip() == "unknown"
        ][:10]
        media_queue = self._media_queue_summary(snapshot)
        recent_event_rows = self._recent_events_from_snapshot(snapshot, limit=5).get("recent_events", [])
        recent_changes = [
            {
                "kind": str(item.get("kind") or ""),
                "event_family": str(item.get("event_family") or ""),
                "summary": _trim_text(item.get("summary"), max_chars=200),
                "created_at": str(item.get("created_at") or ""),
            }
            for item in recent_event_rows
            if isinstance(item, dict)
        ]
        uncertainties = self._home_uncertainties(snapshot)
        risk_notes = self._home_risk_notes(snapshot)
        status = "idle"
        if any(notice.severity in {"high", "medium"} for notice in notices) or risk_notes:
            status = "attention"
        elif devices or snapshot.snapshots:
            status = "active"
        summary_parts = [
            f"{len(devices)} device(s) tracked",
            f"{int(media_queue.get('media_count', 0) or 0)} media item(s)",
        ]
        if known_locations:
            summary_parts.append(f"locations: {', '.join(known_locations[:3])}")
        if risk_notes:
            summary_parts.append(f"risks: {risk_notes[0]}")
        return {
            "status": status,
            "summary": "; ".join(summary_parts),
            "known_locations": known_locations,
            "known_devices": known_devices,
            "unknown_devices": unknown_devices,
            "active_media": {
                "media_count": int(media_queue.get("media_count", 0) or 0),
                "pending_count": int(media_queue.get("uninspected_count", 0) or 0),
                "recent_media": list(media_queue.get("recent_media") or []),
                "last_scene_inspection": self._last_scene_inspection(snapshot),
            },
            "recent_changes": recent_changes,
            "uncertainties": uncertainties,
            "risk_notes": risk_notes,
            "confidence": self._home_confidence(snapshot),
            "updated_at": snapshot.updated_at,
        }

    def _compute_attention_notices(self, snapshot: WorldStateSnapshot) -> list[WorldAttentionNotice]:
        if not self._home_attention_enabled():
            return []
        notices: list[WorldAttentionNotice] = []
        device_by_id = {
            str(item.get("device_id") or "").strip(): dict(item)
            for item in (snapshot.device_map.get("devices") or [])
            if isinstance(item, dict) and str(item.get("device_id") or "").strip()
        }
        active_binding_ids = {
            binding.device_id
            for binding in snapshot.device_bindings
            if binding.status == "active"
        }
        for event in snapshot.device_events:
            device = device_by_id.get(event.device_id) or dict(event.current_state or {}) or dict(event.previous_state or {})
            if event.kind == "appeared":
                device_kind = str(device.get("kind") or "unknown").strip() or "unknown"
                notices.append(self._notice(
                    kind="device_appeared",
                    severity="medium" if device_kind == "unknown" else "low",
                    title="Unknown device appeared" if device_kind == "unknown" else "Device appeared",
                    summary=event.change_summary or f"Device appeared: {self._device_label(device)}",
                    source_event_ids=[event.event_id],
                    related_device_ids=[event.device_id],
                    suggested_next_step=(
                        "Ask whether to bind or inspect this device before using capture or permission tools."
                        if device_kind == "unknown"
                        else "Review the newly discovered device if it matters to the current task."
                    ),
                    requires_confirmation=device_kind == "unknown",
                    created_at=event.created_at,
                ))
            elif event.kind == "disappeared":
                bound = event.device_id in active_binding_ids
                notices.append(self._notice(
                    kind="device_disappeared",
                    severity="medium" if bound else "low",
                    title="Bound device disappeared" if bound else "Device disappeared",
                    summary=event.change_summary or f"Device disappeared: {self._device_label(device)}",
                    source_event_ids=[event.event_id],
                    related_device_ids=[event.device_id],
                    suggested_next_step=(
                        "Confirm whether the bound device is offline before relying on it."
                        if bound
                        else "Re-scan later if this device was expected to stay online."
                    ),
                    requires_confirmation=False,
                    created_at=event.created_at,
                ))
        for event in snapshot.media_events:
            if event.kind not in {"failed", "skipped"}:
                continue
            notices.append(self._notice(
                kind="media_issue",
                severity="medium",
                title="Media inspection needs attention",
                summary=event.summary or f"Media {event.kind}: {event.media_path}",
                source_event_ids=[event.event_id],
                related_snapshot_ids=[event.snapshot_id],
                suggested_next_step="Retry analysis or inspect the media file details before relying on it.",
                requires_confirmation=False,
                created_at=event.created_at,
            ))
        for event in snapshot.events:
            if event.kind == "contested_world_state" or event.contested:
                notices.append(self._notice(
                    kind="scene_contested",
                    severity="medium",
                    title="Scene summary is contested",
                    summary=event.summary or "Recent scene understanding changed after inspection.",
                    source_event_ids=[event.event_id],
                    related_snapshot_ids=[event.snapshot_id],
                    suggested_next_step="Re-check the latest scene summary before making decisions from it.",
                    requires_confirmation=False,
                    created_at=event.created_at,
                ))
            elif event.kind == "uncertain_world_state":
                notices.append(self._notice(
                    kind="scene_uncertain",
                    severity="medium",
                    title="Scene understanding is uncertain",
                    summary=event.summary or "Recent scene observations still contain uncertainty.",
                    source_event_ids=[event.event_id],
                    related_snapshot_ids=[event.snapshot_id],
                    suggested_next_step="Review the uncertain scene details before acting on them.",
                    requires_confirmation=False,
                    created_at=event.created_at,
                ))
        last_inspection = self._last_inspection_record(snapshot)
        if last_inspection is not None and _trim_text(last_inspection.failure_reason, max_chars=240):
            notices.append(self._notice(
                kind="inspection_failed",
                severity="medium",
                title="Latest inspection failed",
                summary=_trim_text(last_inspection.failure_reason, max_chars=240),
                source_event_ids=[last_inspection.inspection_id],
                related_snapshot_ids=[last_inspection.snapshot_id],
                suggested_next_step="Retry inspection or verify the media file before trusting the scene summary.",
                requires_confirmation=False,
                created_at=last_inspection.requested_at,
            ))
        audio_status = self._audio_status_summary(snapshot)
        if audio_status.get("last_transcription_status") == "completed" and audio_status.get("last_audio"):
            last_audio = audio_status.get("last_audio") or {}
            notices.append(self._notice(
                kind="audio_transcribed",
                severity="low",
                title="Audio transcription is available",
                summary="A recent audio sample was transcribed and can be analyzed further if needed.",
                related_snapshot_ids=[str(last_audio.get("snapshot_id") or "")] if last_audio.get("snapshot_id") else [],
                suggested_next_step="Continue analyzing the available transcription if it helps the current task.",
                requires_confirmation=False,
                created_at=str(last_audio.get("captured_at") or snapshot.updated_at),
            ))
        now = _utcnow()
        for permission in [self._expire_permission_if_needed(item, now=now) for item in snapshot.device_permissions]:
            if permission.status not in {"pending", "denied"}:
                continue
            notices.append(self._notice(
                kind=f"device_permission_{permission.status}",
                severity="medium",
                title=f"Device permission {permission.status}",
                summary=f"{permission.capability} permission is {permission.status} for device {permission.device_id}.",
                related_device_ids=[permission.device_id],
                suggested_next_step="Ask for device permission confirmation before using targeted capture on this device.",
                requires_confirmation=True,
                created_at=permission.granted_at or permission.expires_at or snapshot.updated_at,
            ))
        risk_notes = self._home_risk_notes(snapshot)
        if risk_notes:
            notices.append(self._notice(
                kind="home_risk_note",
                severity="medium",
                title="Home state has open risks",
                summary=risk_notes[0],
                suggested_next_step="Review the current home-state risks before taking the next step.",
                requires_confirmation=False,
                created_at=snapshot.updated_at,
            ))
        return self._dedupe_notices(notices)

    @staticmethod
    def _notice(
        *,
        kind: str,
        severity: str,
        title: str,
        summary: str,
        source_event_ids: list[str] | None = None,
        related_device_ids: list[str] | None = None,
        related_snapshot_ids: list[str] | None = None,
        suggested_next_step: str | None = None,
        requires_confirmation: bool = False,
        created_at: str | None = None,
    ) -> WorldAttentionNotice:
        normalized_severity = severity if severity in {"high", "medium", "low"} else "low"
        return WorldAttentionNotice(
            notice_id=f"notice_{uuid.uuid4().hex[:12]}",
            kind=str(kind or "notice").strip() or "notice",
            severity=normalized_severity,
            title=_trim_text(title, max_chars=120),
            summary=_trim_text(summary, max_chars=240),
            source_event_ids=[value for value in (source_event_ids or []) if str(value or "").strip()],
            related_device_ids=[value for value in (related_device_ids or []) if str(value or "").strip()],
            related_snapshot_ids=[value for value in (related_snapshot_ids or []) if str(value or "").strip()],
            suggested_next_step=_trim_text(suggested_next_step, max_chars=200) or None,
            requires_confirmation=bool(requires_confirmation),
            created_at=str(created_at or _utcnow_iso()).strip(),
        )

    def _dedupe_notices(self, notices: list[WorldAttentionNotice]) -> list[WorldAttentionNotice]:
        severity_rank = {"high": 0, "medium": 1, "low": 2}
        ordered = sorted(
            notices,
            key=lambda item: (
                severity_rank.get(item.severity, 3),
                -((_parse_dt(item.created_at) or datetime.min.replace(tzinfo=timezone.utc)).timestamp()),
            ),
        )
        seen: set[tuple[str, tuple[str, ...], tuple[str, ...], str]] = set()
        out: list[WorldAttentionNotice] = []
        for notice in ordered:
            key = (
                notice.kind,
                tuple(sorted(notice.related_device_ids)),
                tuple(sorted(notice.related_snapshot_ids)),
                notice.title,
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(notice)
            if len(out) >= self._home_attention_max_notices():
                break
        return out

    @staticmethod
    def _attention_notice_lines(notices: list[WorldAttentionNotice]) -> list[str]:
        return [
            f"[{notice.severity}] {notice.title}: {notice.summary}"
            for notice in notices[:5]
            if notice.title and notice.summary
        ]

    @staticmethod
    def _device_home_view(device: dict[str, Any], binding: DeviceBinding | None = None) -> dict[str, Any]:
        return {
            "device_id": str(device.get("device_id") or "").strip(),
            "kind": str(device.get("kind") or "unknown").strip() or "unknown",
            "name": _trim_text(device.get("name"), max_chars=120) or None,
            "hostname": _trim_text(device.get("hostname"), max_chars=120) or None,
            "ip_addresses": [str(ip).strip() for ip in device.get("ip_addresses") or [] if str(ip).strip()][:4],
            "binding": binding.to_json() if binding is not None else device.get("binding"),
            "authorized_capabilities": list(device.get("authorized_capabilities") or []),
        }

    @staticmethod
    def _last_inspection_record(snapshot: WorldStateSnapshot) -> InspectionResult | None:
        if not snapshot.inspections:
            return None
        return sorted(
            snapshot.inspections,
            key=lambda item: _parse_dt(item.requested_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[0]

    @staticmethod
    def _home_uncertainties(snapshot: WorldStateSnapshot) -> list[str]:
        values: list[str] = []
        if snapshot.world_summary is not None:
            values.extend(_string_list(snapshot.world_summary.uncertainties, limit=8, max_chars=200))
        latest = WorldStateManager._last_inspection_record(snapshot)
        if latest is not None:
            values.extend(_string_list(latest.uncertain, limit=8, max_chars=200))
            if latest.failure_reason:
                values.append(_trim_text(latest.failure_reason, max_chars=200))
        seen: list[str] = []
        for value in values:
            if value and value not in seen:
                seen.append(value)
        return seen[:8]

    def _home_risk_notes(self, snapshot: WorldStateSnapshot) -> list[str]:
        notes: list[str] = []
        unknown_devices = [
            item for item in (snapshot.device_map.get("devices") or [])
            if isinstance(item, dict) and str(item.get("kind") or "").strip() == "unknown"
        ]
        if unknown_devices:
            notes.append(f"{len(unknown_devices)} unknown device(s) are currently visible.")
        failed_media = [event for event in snapshot.media_events if event.kind in {"failed", "skipped"}]
        if failed_media:
            notes.append("Some media analysis attempts failed or were skipped.")
        normalized_permissions = [
            self._expire_permission_if_needed(item, now=_utcnow())
            for item in snapshot.device_permissions
        ]
        pending_or_denied = [
            item for item in normalized_permissions
            if item.status in {"pending", "denied"}
        ]
        if pending_or_denied:
            notes.append("One or more device permissions still need attention.")
        disappeared_bound = [
            event for event in snapshot.device_events
            if event.kind == "disappeared" and self._active_binding(snapshot, event.device_id) is not None
        ]
        if disappeared_bound:
            notes.append("A bound device recently disappeared from discovery results.")
        return notes[:8]

    @staticmethod
    def _home_confidence(snapshot: WorldStateSnapshot) -> float | None:
        completed = [item.confidence for item in snapshot.inspections if item.status == "completed"]
        if not completed:
            return None
        return round(sum(completed) / len(completed), 4)

    def _home_situation_enabled(self) -> bool:
        config = self._context_config
        if config is None:
            return True
        return bool(getattr(config, "home_situation_enabled", True))

    def _home_attention_enabled(self) -> bool:
        config = self._context_config
        if config is None:
            return True
        return bool(getattr(config, "home_attention_enabled", True))

    def _home_attention_max_notices(self) -> int:
        config = self._context_config
        if config is None:
            return 50
        try:
            value = int(getattr(config, "home_attention_max_notices", 50) or 50)
        except (TypeError, ValueError):
            value = 50
        return max(1, min(value, 200))

    def _device_permissions_summary(self, snapshot: WorldStateSnapshot) -> dict[str, Any]:
        normalized = [self._expire_permission_if_needed(item, now=_utcnow()) for item in snapshot.device_permissions]
        granted = [item for item in normalized if self._permission_active(item)]
        pending = [item for item in normalized if item.status == "pending"]
        return {
            "granted_permission_count": len(granted),
            "pending_permission_count": len(pending),
            "permissions": [item.to_json() for item in normalized[:20]],
        }

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
        existing_by_path = {item.media_path: item for item in snapshot.snapshots}
        media_events: list[MediaLifecycleEvent] = []
        for descriptor in descriptors:
            if descriptor is None or descriptor.kind not in {"image", "audio", "video", "sensor"}:
                continue
            if descriptor.path.suffix.lower().endswith(".part") or descriptor.name.endswith(".part"):
                continue
            relative_media_path = _path_to_workspace(descriptor.path, workspace=self._workspace)
            if relative_media_path.endswith(".part"):
                continue
            metadata = self._media_metadata(descriptor.path)
            existing = existing_by_path.get(relative_media_path)
            if existing is not None:
                if metadata.get("media_hash") and metadata.get("media_hash") != existing.media_hash:
                    existing.media_hash = metadata.get("media_hash")
                    existing.media_mime_type = metadata.get("media_mime_type")
                    existing.media_size_bytes = metadata.get("media_size_bytes")
                    existing.media_mtime = metadata.get("media_mtime")
                    existing.media_status = "pending"
                    existing.last_inspection_error = None
                    media_events.append(self._media_event(
                        kind="status_changed",
                        snapshot=existing,
                        previous_status="inspected",
                        current_status="pending",
                        summary=f"Media changed and returned to pending: {relative_media_path}",
                    ))
                    added = True
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
            scene.media_hash = metadata.get("media_hash")
            scene.media_mime_type = metadata.get("media_mime_type") or descriptor.mime
            scene.media_size_bytes = metadata.get("media_size_bytes") or descriptor.size_bytes
            scene.media_mtime = metadata.get("media_mtime")
            scene.media_status = "pending"
            snapshot.snapshots.append(scene)
            existing_paths.add(relative_media_path)
            existing_by_path[relative_media_path] = scene
            media_events.append(self._media_event(
                kind="discovered",
                snapshot=scene,
                previous_status=None,
                current_status="pending",
                summary=f"Media discovered: {relative_media_path}",
            ))
            added = True
        if added:
            snapshot.status = "active"
            snapshot.version = "phase2"
            snapshot.owner_id = runtime_context.user_id
            snapshot.scope = "session"
        refreshed = self._refresh_summary(snapshot)
        refreshed.media_events = self._merge_media_events(
            existing=refreshed.media_events,
            new_events=media_events,
        )
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

    def _describe_workspace_attachment(self, path: str | Path, *, source: str) -> AttachmentDescriptor | None:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._workspace / candidate
        return describe_attachment(candidate, source=source)

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
