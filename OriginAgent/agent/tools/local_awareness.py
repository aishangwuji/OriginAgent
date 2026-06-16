"""Controlled local-awareness tools for P5B."""

from __future__ import annotations

import mimetypes
from collections.abc import Iterable
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from OriginAgent.agent.local_awareness import LocalAwarenessBackend
from OriginAgent.providers.transcription import GroqTranscriptionProvider, OpenAITranscriptionProvider
from OriginAgent.agent.snapshot_inspection import SnapshotInspectionService
from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.context import RequestContext


def _disabled(reason: str) -> dict[str, Any]:
    return {"status": "disabled", "reason": reason}


def _pending_confirmation(reason: str) -> dict[str, Any]:
    return {"status": "pending_confirmation", "reason": reason}


def _local_config(config: Any) -> Any:
    return getattr(config, "local_awareness", None)


def _update_summary(loop: Any | None, *, key: str, value: dict[str, Any]) -> None:
    if loop is None:
        return
    cached = dict(getattr(loop, "_last_local_awareness_summary", {}) or {})
    cached[key] = dict(value)
    if isinstance(value.get("device_map"), dict):
        cached["device_map"] = dict(value["device_map"])
        if isinstance(value["device_map"].get("summary"), dict):
            cached["device_map_summary"] = dict(value["device_map"]["summary"])
    for summary_key in (
        "device_events_summary",
        "device_bindings_summary",
        "device_permissions_summary",
        "media_queue_summary",
        "last_scene_inspection",
        "audio_status",
    ):
        if isinstance(value.get(summary_key), dict):
            cached[summary_key] = dict(value[summary_key])
    if isinstance(value.get("recent_media_events"), list):
        cached["recent_media_events"] = list(value["recent_media_events"])
    cached["updated_at"] = datetime.now(timezone.utc).isoformat()
    from OriginAgent.agent.local_awareness import normalize_local_awareness_summary

    config = _local_config(getattr(loop, "tools_config", None))
    backend = getattr(loop, "_local_awareness_backend", None)
    loop._last_local_awareness_summary = normalize_local_awareness_summary(
        config,
        backend=backend,
        cached=cached,
    )


def _workspace_roots(config: Any) -> list[Path]:
    media = getattr(config, "media", None)
    roots = list(getattr(media, "workspace_roots", None) or [])
    if not roots:
        roots = ["uploads/perception"]
    return [Path(root) for root in roots]


def _within_workspace(path: Path, *, workspace: Path, roots: Iterable[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    try:
        resolved.relative_to(workspace.resolve())
        return True
    except Exception:
        pass
    for root in roots:
        try:
            resolved.relative_to((workspace / root).resolve())
            return True
        except Exception:
            continue
    return False


class _LocalAwarenessTool(Tool):
    def __init__(
        self,
        *,
        workspace: Path,
        config: Any,
        backend: LocalAwarenessBackend | Any | None = None,
        introspection_service: Any | None = None,
    ) -> None:
        self._workspace = Path(workspace)
        self._config = _local_config(config)
        self._backend = backend or LocalAwarenessBackend()
        self._introspection_service = introspection_service
        self._request_ctx: ContextVar[RequestContext | None] = ContextVar(
            f"{self.__class__.__name__}_request_ctx",
            default=None,
        )

    def set_context(self, ctx: RequestContext) -> None:
        self._request_ctx.set(ctx)

    @property
    def _loop(self) -> Any | None:
        return getattr(self._introspection_service, "_loop", None) if self._introspection_service else None

    def _enabled(self) -> bool:
        return bool(getattr(self._config, "enabled", False))

    def _world_context(self) -> tuple[Any, Any, Any] | None:
        loop = self._loop
        ctx = self._request_ctx.get()
        if loop is None or ctx is None or not ctx.session_key:
            return None
        world_state = getattr(loop, "world_state", None)
        sessions = getattr(loop, "sessions", None)
        runtime_context = getattr(ctx, "runtime_context", None)
        if world_state is None or sessions is None or runtime_context is None:
            return None
        return world_state, sessions.get_or_create(ctx.session_key), runtime_context

    def _device_state_summary(self) -> dict[str, Any]:
        world = self._world_context()
        if world is None:
            return {}
        world_state, session, runtime_context = world
        if hasattr(world_state, "device_state_summary"):
            return world_state.device_state_summary(session, identity=runtime_context)
        return {}

    def _ingest_device_map(self, result: dict[str, Any]) -> None:
        device_map = result.get("device_map")
        if not isinstance(device_map, dict):
            return
        world = self._world_context()
        if world is None:
            return
        world_state, session, runtime_context = world
        if hasattr(world_state, "ingest_device_discovery"):
            snapshot = world_state.ingest_device_discovery(
                session,
                runtime_context=runtime_context,
                device_map=device_map,
            )
            if hasattr(world_state, "device_state_summary"):
                result.update(world_state.device_state_summary(session, identity=runtime_context))
            result["device_map"] = dict(getattr(snapshot, "device_map", device_map))

    def _refresh_world_summaries(self, result: dict[str, Any]) -> None:
        world = self._world_context()
        if world is None:
            return
        world_state, session, runtime_context = world
        if hasattr(world_state, "device_state_summary"):
            result.update(world_state.device_state_summary(session, identity=runtime_context))
        if hasattr(world_state, "media_state_summary"):
            result.update(world_state.media_state_summary(session, identity=runtime_context))

    def _require_device_permission(self, device_id: str | None, capability: str) -> dict[str, Any] | None:
        if not device_id:
            return None
        world = self._world_context()
        if world is None:
            return {"status": "denied", "reason": "world_state_missing", "device_id": device_id}
        world_state, session, runtime_context = world
        if not hasattr(world_state, "device_capability_granted"):
            return {"status": "denied", "reason": "device_permission_unavailable", "device_id": device_id}
        if world_state.device_capability_granted(
            session,
            identity=runtime_context,
            device_id=device_id,
            capability=capability,
        ):
            return None
        summary = world_state.device_state_summary(session, identity=runtime_context) if hasattr(world_state, "device_state_summary") else {}
        return {
            "status": "pending_confirmation",
            "reason": f"{capability}_device_permission_required",
            "device_id": device_id,
            "capability": capability,
            **summary,
        }

    async def _auto_inspect_if_enabled(self, snapshot_id: str | None) -> dict[str, Any]:
        media = getattr(self._config, "media", None)
        if not snapshot_id:
            return {"triggered": False, "reason": "snapshot_missing"}
        if not bool(getattr(media, "auto_inspect_after_capture", False)):
            return {"triggered": False, "reason": "auto_inspect_after_capture_disabled"}
        world = self._world_context()
        if world is None:
            return {"triggered": False, "reason": "world_state_missing"}
        loop = self._loop
        world_state, session, runtime_context = world
        service = SnapshotInspectionService(
            world_state=world_state,
            provider=getattr(loop, "provider", None),
            model=getattr(loop, "model", None),
            auxiliary_router=getattr(loop, "auxiliary_router", None),
            workspace=self._workspace,
        )
        result = await service.inspect(
            session,
            runtime_context=runtime_context,
            snapshot_id=snapshot_id,
            requested_by=str(getattr(runtime_context, "source", None) or "local_awareness"),
        )
        return {"triggered": True, "result": result}


class DiscoverLocalDevicesTool(_LocalAwarenessTool):
    name = "originagent_discover_local_devices"

    @property
    def description(self) -> str:
        return "Return a safe summary of configured local camera/audio/screen capabilities."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self) -> dict[str, Any]:
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        elif not bool(getattr(self._config, "device_discovery_enabled", False)):
            result = _disabled("device_discovery_disabled")
        else:
            result = self._backend.discover_local_devices(config=self._config)
        self._ingest_device_map(result)
        _update_summary(self._loop, key="last_discovery", value=result)
        return result


class DiscoverLanDevicesTool(_LocalAwarenessTool):
    name = "originagent_discover_lan_devices"

    @property
    def description(self) -> str:
        return "Return a low-intrusion LAN visibility summary; no port scanning."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self) -> dict[str, Any]:
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        else:
            result = self._backend.discover_lan_devices(config=self._config)
        self._ingest_device_map(result)
        _update_summary(self._loop, key="last_discovery", value=result)
        return result


class ListDeviceBindingsTool(_LocalAwarenessTool):
    name = "originagent_list_device_bindings"

    @property
    def description(self) -> str:
        return "List session-scoped device bindings, permissions, and recent device lifecycle events."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self) -> dict[str, Any]:
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        else:
            result = {"status": "ok", **self._device_state_summary()}
        _update_summary(self._loop, key="last_discovery", value=result)
        return result


class BindDeviceTool(_LocalAwarenessTool):
    name = "originagent_bind_device"

    @property
    def description(self) -> str:
        return "Create a session-scoped semantic binding for a discovered device; this does not grant capture or control permission."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "minLength": 1},
                "user_label": {"type": "string", "minLength": 1, "maxLength": 120},
                "location": {"type": ["string", "null"], "maxLength": 120},
            },
            "required": ["device_id", "user_label"],
            "additionalProperties": False,
        }

    async def execute(self, device_id: str, user_label: str, location: str | None = None) -> dict[str, Any]:
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        else:
            world = self._world_context()
            if world is None:
                result = {"status": "denied", "reason": "world_state_missing"}
            else:
                world_state, session, runtime_context = world
                result = world_state.bind_device(
                    session,
                    runtime_context=runtime_context,
                    device_id=device_id,
                    user_label=user_label,
                    location=location,
                )
        _update_summary(self._loop, key="last_discovery", value=result)
        return result


class RevokeDeviceBindingTool(_LocalAwarenessTool):
    name = "originagent_revoke_device_binding"

    @property
    def description(self) -> str:
        return "Revoke a session-scoped device binding and any active permissions for that device."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "minLength": 1},
            },
            "required": ["device_id"],
            "additionalProperties": False,
        }

    async def execute(self, device_id: str) -> dict[str, Any]:
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        else:
            world = self._world_context()
            if world is None:
                result = {"status": "denied", "reason": "world_state_missing"}
            else:
                world_state, session, runtime_context = world
                result = world_state.revoke_device_binding(
                    session,
                    runtime_context=runtime_context,
                    device_id=device_id,
                )
        _update_summary(self._loop, key="last_discovery", value=result)
        return result


class RequestDevicePermissionTool(_LocalAwarenessTool):
    name = "originagent_request_device_permission"

    @property
    def description(self) -> str:
        return "Request or record session-scoped permission for a bound device capability; default is pending confirmation."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "minLength": 1},
                "capability": {"type": "string", "enum": ["camera", "screen", "audio"]},
                "grant": {"type": "boolean", "default": False},
                "confirmation_id": {"type": ["string", "null"]},
            },
            "required": ["device_id", "capability"],
            "additionalProperties": False,
        }

    async def execute(
        self,
        device_id: str,
        capability: str,
        grant: bool = False,
        confirmation_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        else:
            world = self._world_context()
            if world is None:
                result = {"status": "denied", "reason": "world_state_missing"}
            else:
                world_state, session, runtime_context = world
                result = world_state.request_device_permission(
                    session,
                    runtime_context=runtime_context,
                    device_id=device_id,
                    capability=capability,
                    status="granted" if grant else "pending",
                    confirmation_id=confirmation_id,
                )
                if not grant and result.get("status") == "ok":
                    result["status"] = "pending_confirmation"
                    result["reason"] = "device_permission_requires_confirmation"
        _update_summary(self._loop, key="last_discovery", value=result)
        return result


class CaptureCameraFrameTool(_LocalAwarenessTool):
    name = "originagent_capture_camera_frame"

    @property
    def description(self) -> str:
        return "Capture one camera frame into the workspace when local awareness camera capture is enabled."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "device_id": {"type": ["string", "null"]},
                "attach_to_world": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        }

    async def execute(self, device_id: str | None = None, attach_to_world: bool = True) -> dict[str, Any]:
        camera = getattr(self._config, "camera", None)
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        elif not bool(getattr(camera, "enabled", False)):
            result = _disabled("camera_disabled")
        elif (permission_result := self._require_device_permission(device_id, "camera")) is not None:
            result = permission_result
        elif bool(getattr(camera, "require_confirmation", True)):
            result = _pending_confirmation("camera_capture_requires_confirmation")
        else:
            result = self._backend.capture_camera_frame(
                workspace=self._workspace,
                save_dir=str(getattr(camera, "save_dir", "uploads/perception")),
                device_id=device_id or getattr(camera, "device_id", None),
            )
            if attach_to_world:
                snapshot_id = self._ingest_media(result.get("absolute_path") or result.get("media_path"))
                result["snapshot_id"] = snapshot_id
                result["auto_inspection"] = await self._auto_inspect_if_enabled(snapshot_id)
                self._refresh_world_summaries(result)
        _update_summary(self._loop, key="last_capture", value=result)
        return result

    def _ingest_media(self, media_path: Any) -> str | None:
        loop = self._loop
        ctx = self._request_ctx.get()
        if loop is None or ctx is None or not ctx.session_key or not media_path:
            return None
        world_state = getattr(loop, "world_state", None)
        sessions = getattr(loop, "sessions", None)
        runtime_context = getattr(ctx, "runtime_context", None)
        if world_state is None or sessions is None or runtime_context is None:
            return None
        session = sessions.get_or_create(ctx.session_key)
        snapshot = world_state.ingest_media(session, runtime_context=runtime_context, media_paths=[str(media_path)])
        path_text = str(media_path).replace("\\", "/")
        for item in reversed(getattr(snapshot, "snapshots", []) or []):
            if item.media_path == path_text or path_text.endswith(item.media_path):
                return item.snapshot_id
        return getattr(snapshot.snapshots[-1], "snapshot_id", None) if getattr(snapshot, "snapshots", None) else None


class CaptureScreenTool(CaptureCameraFrameTool):
    name = "originagent_capture_screen"

    @property
    def description(self) -> str:
        return "Capture one screen image into the workspace when local awareness screen capture is enabled."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "screen_id": {"type": ["string", "null"]},
                "device_id": {"type": ["string", "null"]},
                "attach_to_world": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        }

    async def execute(
        self,
        screen_id: str | None = None,
        attach_to_world: bool = True,
        device_id: str | None = None,
    ) -> dict[str, Any]:  # type: ignore[override]
        screen = getattr(self._config, "screen", None)
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        elif not bool(getattr(screen, "enabled", False)):
            result = _disabled("screen_disabled")
        elif (permission_result := self._require_device_permission(device_id, "screen")) is not None:
            result = permission_result
        elif bool(getattr(screen, "require_confirmation", True)):
            result = _pending_confirmation("screen_capture_requires_confirmation")
        else:
            result = self._backend.capture_screen(
                workspace=self._workspace,
                save_dir=str(getattr(screen, "save_dir", "uploads/perception")),
                screen_id=device_id or screen_id or getattr(screen, "screen_id", None),
            )
            if attach_to_world:
                snapshot_id = self._ingest_media(result.get("absolute_path") or result.get("media_path"))
                result["snapshot_id"] = snapshot_id
                result["auto_inspection"] = await self._auto_inspect_if_enabled(snapshot_id)
                self._refresh_world_summaries(result)
        _update_summary(self._loop, key="last_capture", value=result)
        return result


class RecordAudioSampleTool(_LocalAwarenessTool):
    name = "originagent_record_audio_sample"

    @property
    def description(self) -> str:
        return "Record a bounded audio sample into the workspace when audio input is enabled."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "minimum": 1, "default": 3},
                "device_id": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        }

    async def execute(self, seconds: int = 3, device_id: str | None = None) -> dict[str, Any]:
        audio = getattr(self._config, "audio", None)
        max_seconds = int(getattr(audio, "max_record_seconds", 5) or 5)
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        elif not bool(getattr(audio, "input_enabled", False)):
            result = _disabled("audio_input_disabled")
        elif int(seconds) > max_seconds:
            result = {"status": "denied", "reason": "record_seconds_exceeds_limit", "max_seconds": max_seconds}
        elif (permission_result := self._require_device_permission(device_id, "audio")) is not None:
            result = permission_result
        elif bool(getattr(audio, "require_confirmation", True)):
            result = _pending_confirmation("audio_record_requires_confirmation")
        else:
            result = self._backend.record_audio_sample(
                workspace=self._workspace,
                save_dir=str(getattr(audio, "save_dir", "uploads/perception")),
                seconds=int(seconds),
                device_id=device_id or getattr(audio, "device_id", None),
            )
            snapshot_id = self._ingest_media(result.get("absolute_path") or result.get("media_path"))
            result["snapshot_id"] = snapshot_id
            self._refresh_world_summaries(result)
        _update_summary(self._loop, key="last_audio", value=result)
        return result


class SpeakTextTool(_LocalAwarenessTool):
    name = "originagent_speak_text"

    @property
    def description(self) -> str:
        return "Speak text through local audio output when enabled; default backend is dry-run."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 1000},
                "voice": {"type": ["string", "null"]},
            },
            "required": ["text"],
            "additionalProperties": False,
        }

    async def execute(self, text: str, voice: str | None = None) -> dict[str, Any]:
        audio = getattr(self._config, "audio", None)
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        elif not bool(getattr(audio, "output_enabled", False)):
            result = _disabled("audio_output_disabled")
        elif bool(getattr(audio, "require_confirmation", True)):
            result = _pending_confirmation("audio_output_requires_confirmation")
        else:
            result = self._backend.speak_text(text=text, voice=voice or getattr(audio, "voice", None))
            result["tts_enabled"] = bool(getattr(audio, "tts_enabled", False))
        _update_summary(self._loop, key="last_audio", value=result)
        return result


class InspectMediaTool(_LocalAwarenessTool):
    name = "originagent_inspect_media"

    @property
    def description(self) -> str:
        return "Inspect an existing workspace media file through the world snapshot inspection path."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "media_path": {"type": "string", "minLength": 1},
            },
            "required": ["media_path"],
            "additionalProperties": False,
        }

    async def execute(self, media_path: str) -> dict[str, Any]:
        media_inspection = getattr(self._config, "media_inspection", None)
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
            _update_summary(self._loop, key="last_media_inspection", value=result)
            return result
        if not bool(getattr(media_inspection, "enabled", True)):
            result = _disabled("media_inspection_disabled")
            _update_summary(self._loop, key="last_media_inspection", value=result)
            return result
        loop = self._loop
        ctx = self._request_ctx.get()
        if loop is None or ctx is None or not ctx.session_key or getattr(ctx, "runtime_context", None) is None:
            result = {"status": "error", "reason": "runtime_context_missing"}
            _update_summary(self._loop, key="last_media_inspection", value=result)
            return result
        world_state = getattr(loop, "world_state", None)
        sessions = getattr(loop, "sessions", None)
        if world_state is None or sessions is None:
            result = {"status": "error", "reason": "world_state_missing"}
            _update_summary(self._loop, key="last_media_inspection", value=result)
            return result
        session = sessions.get_or_create(ctx.session_key)
        snapshot = world_state.ingest_media(
            session,
            runtime_context=ctx.runtime_context,
            media_paths=[media_path],
        )
        target = snapshot.snapshots[-1] if snapshot.snapshots else None
        if target is None:
            return {"status": "error", "reason": "snapshot_missing"}
        service = SnapshotInspectionService(
            world_state=world_state,
            provider=getattr(loop, "provider", None),
            model=getattr(loop, "model", None),
            auxiliary_router=getattr(loop, "auxiliary_router", None),
            workspace=self._workspace,
        )
        result = await service.inspect(
            session,
            runtime_context=ctx.runtime_context,
            snapshot_id=target.snapshot_id,
            requested_by=str(getattr(ctx.runtime_context, "source", None) or "local_awareness"),
        )
        result["status"] = result.get("status") or ("error" if result.get("error") else "ok")
        result["media_path"] = target.media_path
        _update_summary(self._loop, key="last_media_inspection", value=result)
        return result


class ScanWorkspaceMediaTool(_LocalAwarenessTool):
    name = "originagent_scan_workspace_media"

    @property
    def description(self) -> str:
        return "Scan configured workspace roots for new media files and register them in world state without auto-inspecting."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self) -> dict[str, Any]:
        media = getattr(self._config, "media", None)
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        elif not bool(getattr(media, "enabled", False)):
            result = _disabled("media_scan_disabled")
        else:
            result = await self._scan_workspace_media()
        _update_summary(self._loop, key="last_media_scan", value=result)
        return result

    async def _scan_workspace_media(self) -> dict[str, Any]:
        media = getattr(self._config, "media", None)
        workspace_roots = _workspace_roots(self._config)
        supported = {str(item).lower() for item in (getattr(media, "supported_mime_types", None) or [])}
        max_files = int(getattr(media, "max_files", 100) or 100)
        max_file_bytes = int(getattr(media, "max_file_bytes", 10 * 1024 * 1024) or (10 * 1024 * 1024))
        skipped: list[str] = []
        discovered: list[str] = []
        queued: list[str] = []
        loop = self._loop
        world = self._world_context()
        if loop is None or world is None:
            return {"status": "error", "reason": "world_state_missing"}
        world_state, session, runtime_context = world
        if not hasattr(world_state, "ingest_media"):
            return {"status": "error", "reason": "world_state_unavailable"}
        seen = 0
        for root in workspace_roots:
            root_path = (self._workspace / root).resolve()
            try:
                root_path.relative_to(self._workspace.resolve())
            except Exception:
                skipped.append(f"path_outside_workspace:{root.as_posix()}")
                continue
            if not root_path.exists():
                continue
            for path in sorted(root_path.rglob("*")):
                if seen >= max_files:
                    skipped.append("max_files_reached")
                    break
                if not path.is_file():
                    continue
                if path.is_symlink():
                    skipped.append(f"symlink_skipped:{path.as_posix()}")
                    continue
                mime_type = mimetypes.guess_type(path.name)[0] or ""
                if supported and mime_type and mime_type.lower() not in supported:
                    skipped.append(f"mime_skipped:{path.as_posix()}")
                    continue
                if supported and not mime_type:
                    skipped.append(f"mime_unknown:{path.as_posix()}")
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    skipped.append(f"unreadable:{path.as_posix()}")
                    continue
                if stat.st_size > max_file_bytes:
                    skipped.append(f"file_too_large:{path.as_posix()}")
                    continue
                relative = path.resolve().relative_to(self._workspace.resolve()).as_posix()
                before_snapshot = world_state.load(session, identity=runtime_context)
                before_paths = {item.media_path for item in getattr(before_snapshot, "snapshots", []) or []}
                ingest = getattr(world_state, "ingest_media_scan", world_state.ingest_media)
                snapshot = ingest(session, runtime_context=runtime_context, media_paths=[relative])
                after_paths = {item.media_path for item in getattr(snapshot, "snapshots", []) or []}
                if relative not in before_paths and relative in after_paths:
                    discovered.append(relative)
                else:
                    queued.append(relative)
                seen += 1
        summary = world_state.media_state_summary(session, identity=runtime_context) if hasattr(world_state, "media_state_summary") else {}
        return {
            "status": "ok",
            "scanned_count": seen,
            "discovered_media": discovered,
            "queued_media": queued,
            "skipped_reasons": skipped,
            "workspace_roots": [root.as_posix() for root in workspace_roots],
            **summary,
        }


class TranscribeAudioSampleTool(_LocalAwarenessTool):
    name = "originagent_transcribe_audio_sample"

    @property
    def description(self) -> str:
        return "Transcribe an existing workspace audio sample through the configured transcription provider."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "media_path": {"type": "string", "minLength": 1},
            },
            "required": ["media_path"],
            "additionalProperties": False,
        }

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, media_path: str) -> dict[str, Any]:
        audio = getattr(self._config, "audio", None)
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        elif not bool(getattr(audio, "transcription_enabled", False)):
            result = _disabled("transcription_disabled")
        else:
            result = await self._transcribe_audio(media_path=media_path)
        _update_summary(self._loop, key="last_audio", value=result)
        return result

    async def _transcribe_audio(self, *, media_path: str) -> dict[str, Any]:
        loop = self._loop
        if loop is None:
            return {"status": "error", "reason": "loop_missing"}
        audio = getattr(self._config, "audio", None)
        injected = getattr(loop, "_transcription_provider", None)
        if injected is not None and hasattr(injected, "transcribe"):
            provider = injected
        else:
            provider_name = str(getattr(audio, "transcription_provider", None) or getattr(getattr(loop, "channels_config", None), "transcription_provider", "") or "groq").strip()
            provider_key = getattr(getattr(loop, "channels_config", None), "transcription_api_key", None)
            provider_base = getattr(getattr(loop, "channels_config", None), "transcription_api_base", None)
            language = getattr(getattr(loop, "channels_config", None), "transcription_language", None)
            if not provider_key:
                return {
                    "status": "disabled",
                    "reason": "transcription_provider_unavailable",
                    "provider": provider_name,
                    "media_path": media_path,
                }
            if provider_name == "openai":
                provider = OpenAITranscriptionProvider(api_key=provider_key or "", api_base=provider_base or None, language=language or None)
            else:
                provider = GroqTranscriptionProvider(api_key=provider_key or "", api_base=provider_base or None, language=language or None)
        path = Path(media_path)
        if not path.is_absolute():
            path = self._workspace / path
        if not _within_workspace(path, workspace=self._workspace, roots=[Path(".")]):
            return {"status": "denied", "reason": "media_path_outside_workspace", "media_path": media_path}
        if not path.exists() or not path.is_file():
            return {"status": "failed", "reason": "audio_file_missing", "media_path": media_path}
        mime_type = mimetypes.guess_type(path.name)[0] or ""
        if not mime_type.startswith("audio/"):
            return {
                "status": "failed",
                "reason": "unsupported_audio_mime_type",
                "media_path": media_path,
                "source_mime_type": mime_type or None,
            }
        try:
            text = await provider.transcribe(str(path))
        except Exception as exc:
            return {
                "status": "failed",
                "reason": "transcription_failed",
                "failure_reason": str(exc) or "transcription_failed",
                "media_path": media_path,
            }
        world = self._world_context()
        if world is not None:
            world_state, session, runtime_context = world
            snapshot = world_state.ingest_media(session, runtime_context=runtime_context, media_paths=[str(path)])
            target = next(
                (
                    item for item in reversed(getattr(snapshot, "snapshots", []) or [])
                    if item.media_path == str(media_path).replace("\\", "/") or str(path).replace("\\", "/").endswith(item.media_path)
                ),
                None,
            )
            if target is not None:
                world_state.apply_inspection(
                    session,
                    runtime_context=runtime_context,
                    snapshot_id=target.snapshot_id,
                    requested_by="local_awareness_transcription",
                    inspection_payload={
                        "confirmed": [],
                        "corrected": [],
                        "new_details": [text] if text else [],
                        "uncertain": [] if text else ["transcription returned no text"],
                        "confidence": 0.7 if text else 0.2,
                        "status": "completed" if text else "failed",
                        "contested": False,
                        "contested_reasons": [],
                        "evidence_excerpt": [f"media_path: {target.media_path}"],
                        "inspector": provider.__class__.__name__,
                        "inspection_mode": "audio_transcription",
                        "provider": provider.__class__.__name__,
                        "source_media_ids": [target.snapshot_id],
                        "source_mime_type": getattr(target, "media_mime_type", None),
                        "failure_reason": None if text else "empty_transcription",
                    },
                )
        return {
            "status": "ok" if text else "failed",
            "reason": None if text else "empty_transcription",
            "transcription": text or "",
            "media_path": media_path,
            "provider": provider.__class__.__name__,
            "transcription_enabled": True,
            **(world_state.media_state_summary(session, identity=runtime_context) if world is not None and hasattr(world_state, "media_state_summary") else {}),
        }
