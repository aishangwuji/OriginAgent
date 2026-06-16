"""Controlled local-awareness tools for P5B."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from OriginAgent.agent.local_awareness import LocalAwarenessBackend
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
    cached["updated_at"] = datetime.now(timezone.utc).isoformat()
    from OriginAgent.agent.local_awareness import normalize_local_awareness_summary

    config = _local_config(getattr(loop, "tools_config", None))
    backend = getattr(loop, "_local_awareness_backend", None)
    loop._last_local_awareness_summary = normalize_local_awareness_summary(
        config,
        backend=backend,
        cached=cached,
    )


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

    def _ingest_device_map(self, result: dict[str, Any]) -> None:
        device_map = result.get("device_map")
        if not isinstance(device_map, dict):
            return
        loop = self._loop
        ctx = self._request_ctx.get()
        if loop is None or ctx is None or not ctx.session_key:
            return
        world_state = getattr(loop, "world_state", None)
        sessions = getattr(loop, "sessions", None)
        runtime_context = getattr(ctx, "runtime_context", None)
        if world_state is None or sessions is None or runtime_context is None:
            return
        session = sessions.get_or_create(ctx.session_key)
        if hasattr(world_state, "ingest_device_discovery"):
            world_state.ingest_device_discovery(
                session,
                runtime_context=runtime_context,
                device_map=device_map,
            )


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
        return True

    async def execute(self) -> dict[str, Any]:
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        elif not bool(getattr(self._config, "device_discovery_enabled", False)):
            result = _disabled("device_discovery_disabled")
        else:
            result = self._backend.discover_local_devices(config=self._config)
        _update_summary(self._loop, key="last_discovery", value=result)
        self._ingest_device_map(result)
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
        return True

    async def execute(self) -> dict[str, Any]:
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        else:
            result = self._backend.discover_lan_devices(config=self._config)
        _update_summary(self._loop, key="last_discovery", value=result)
        self._ingest_device_map(result)
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
        elif bool(getattr(camera, "require_confirmation", True)):
            result = _pending_confirmation("camera_capture_requires_confirmation")
        else:
            result = self._backend.capture_camera_frame(
                workspace=self._workspace,
                save_dir=str(getattr(camera, "save_dir", "uploads/perception")),
                device_id=device_id or getattr(camera, "device_id", None),
            )
            if attach_to_world:
                self._ingest_media(result.get("absolute_path") or result.get("media_path"))
        _update_summary(self._loop, key="last_capture", value=result)
        return result

    def _ingest_media(self, media_path: Any) -> None:
        loop = self._loop
        ctx = self._request_ctx.get()
        if loop is None or ctx is None or not ctx.session_key or not media_path:
            return
        world_state = getattr(loop, "world_state", None)
        sessions = getattr(loop, "sessions", None)
        runtime_context = getattr(ctx, "runtime_context", None)
        if world_state is None or sessions is None or runtime_context is None:
            return
        session = sessions.get_or_create(ctx.session_key)
        world_state.ingest_media(session, runtime_context=runtime_context, media_paths=[str(media_path)])


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
                "attach_to_world": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        }

    async def execute(self, screen_id: str | None = None, attach_to_world: bool = True) -> dict[str, Any]:  # type: ignore[override]
        screen = getattr(self._config, "screen", None)
        if not self._enabled():
            result = _disabled("local_awareness_disabled")
        elif not bool(getattr(screen, "enabled", False)):
            result = _disabled("screen_disabled")
        elif bool(getattr(screen, "require_confirmation", True)):
            result = _pending_confirmation("screen_capture_requires_confirmation")
        else:
            result = self._backend.capture_screen(
                workspace=self._workspace,
                save_dir=str(getattr(screen, "save_dir", "uploads/perception")),
                screen_id=screen_id or getattr(screen, "screen_id", None),
            )
            if attach_to_world:
                self._ingest_media(result.get("absolute_path") or result.get("media_path"))
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
        elif bool(getattr(audio, "require_confirmation", True)):
            result = _pending_confirmation("audio_record_requires_confirmation")
        else:
            result = self._backend.record_audio_sample(
                workspace=self._workspace,
                save_dir=str(getattr(audio, "save_dir", "uploads/perception")),
                seconds=int(seconds),
                device_id=device_id or getattr(audio, "device_id", None),
            )
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
