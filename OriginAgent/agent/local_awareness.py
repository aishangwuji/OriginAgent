"""P5B local awareness helpers for controlled device discovery and media capture."""

from __future__ import annotations

import base64
import socket
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_PNG_1X1_TRANSPARENT = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def _workspace_rel(path: Path, *, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return path.as_posix()


@dataclass(frozen=True)
class LocalAwarenessSummary:
    enabled: bool = False
    device_discovery_enabled: bool = False
    lan_discovery_enabled: bool = False
    camera_enabled: bool = False
    screen_enabled: bool = False
    audio_input_enabled: bool = False
    audio_output_enabled: bool = False
    media_inspection_enabled: bool = False
    last_discovery: dict[str, Any] = field(default_factory=dict)
    last_capture: dict[str, Any] = field(default_factory=dict)
    last_audio: dict[str, Any] = field(default_factory=dict)
    last_media_inspection: dict[str, Any] = field(default_factory=dict)
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LocalAwarenessBackend:
    """Safe, dependency-free backend used by P5B tools.

    Real camera/audio/screen implementations can replace this via loop overrides later.
    Until then, capture writes auditable placeholder media only when the matching
    config gate is explicitly enabled.
    """

    def discover_local_devices(self, *, config: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "generated_at": _utcnow_iso(),
            "local_devices": {
                "cameras": [],
                "microphones": [],
                "speakers": [],
                "screens": [],
            },
            "capabilities": {
                "camera_capture": bool(getattr(getattr(config, "camera", None), "enabled", False)),
                "screen_capture": bool(getattr(getattr(config, "screen", None), "enabled", False)),
                "audio_record": bool(getattr(getattr(config, "audio", None), "input_enabled", False)),
                "audio_output": bool(getattr(getattr(config, "audio", None), "output_enabled", False)),
            },
            "reason": "dependency_free_backend_no_hardware_probe",
        }

    def discover_lan_devices(self, *, config: Any) -> dict[str, Any]:
        if not bool(getattr(config, "lan_discovery_enabled", False)):
            return {
                "status": "disabled",
                "devices": [],
                "reason": "lan_discovery_disabled",
            }
        host = socket.gethostname()
        addresses: list[str] = []
        try:
            addresses = sorted({
                item[4][0]
                for item in socket.getaddrinfo(host, None)
                if item and item[4] and item[4][0]
            })
        except OSError:
            addresses = []
        return {
            "status": "ok",
            "generated_at": _utcnow_iso(),
            "devices": [
                {
                    "kind": "local_host",
                    "hostname": host,
                    "addresses": addresses,
                }
            ],
            "method": "local_hostname_only",
        }

    def capture_camera_frame(
        self,
        *,
        workspace: Path,
        save_dir: str,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        return self._write_placeholder_image(
            workspace=workspace,
            save_dir=save_dir,
            prefix="camera",
            source="local_awareness.camera",
            device_id=device_id,
        )

    def capture_screen(
        self,
        *,
        workspace: Path,
        save_dir: str,
        screen_id: str | None = None,
    ) -> dict[str, Any]:
        return self._write_placeholder_image(
            workspace=workspace,
            save_dir=save_dir,
            prefix="screen",
            source="local_awareness.screen",
            device_id=screen_id,
        )

    def record_audio_sample(
        self,
        *,
        workspace: Path,
        save_dir: str,
        seconds: int,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        dest_dir = self._safe_output_dir(workspace=workspace, save_dir=save_dir)
        filename = f"audio_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}.wav"
        dest = dest_dir / filename
        # Minimal RIFF/WAVE header with no samples. This is an auditable placeholder,
        # not a real microphone capture.
        dest.write_bytes(
            b"RIFF\x24\x00\x00\x00WAVEfmt "
            b"\x10\x00\x00\x00\x01\x00\x01\x00\x40\x1f\x00\x00\x80>\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        )
        return {
            "status": "ok",
            "media_path": _workspace_rel(dest, workspace=workspace),
            "absolute_path": str(dest),
            "kind": "audio",
            "source": "local_awareness.audio",
            "device_id": _safe_text(device_id),
            "seconds": int(seconds),
            "captured_at": _utcnow_iso(),
            "placeholder": True,
        }

    def speak_text(self, *, text: str, voice: str | None = None) -> dict[str, Any]:
        return {
            "status": "dry_run",
            "reason": "audio_output_backend_not_connected",
            "text_preview": _safe_text(text),
            "voice": _safe_text(voice),
            "is_real_output": False,
            "backend_kind": "local_awareness_placeholder",
        }

    def _write_placeholder_image(
        self,
        *,
        workspace: Path,
        save_dir: str,
        prefix: str,
        source: str,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        dest_dir = self._safe_output_dir(workspace=workspace, save_dir=save_dir)
        filename = f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
        dest = dest_dir / filename
        dest.write_bytes(_PNG_1X1_TRANSPARENT)
        return {
            "status": "ok",
            "media_path": _workspace_rel(dest, workspace=workspace),
            "absolute_path": str(dest),
            "kind": "image",
            "source": source,
            "device_id": _safe_text(device_id),
            "captured_at": _utcnow_iso(),
            "placeholder": True,
        }

    @staticmethod
    def _safe_output_dir(*, workspace: Path, save_dir: str) -> Path:
        workspace = Path(workspace).resolve()
        relative = str(save_dir or "uploads/perception").strip().replace("\\", "/")
        relative = relative.lstrip("/")
        dest = (workspace / relative).resolve()
        if workspace not in dest.parents and dest != workspace:
            raise ValueError("save_dir must stay inside workspace")
        dest.mkdir(parents=True, exist_ok=True)
        return dest


def normalize_local_awareness_summary(
    config: Any | None,
    *,
    backend: Any | None = None,
    cached: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a stable local-awareness observability shape."""

    raw = dict(cached or {})
    camera = getattr(config, "camera", None)
    screen = getattr(config, "screen", None)
    audio = getattr(config, "audio", None)
    media_inspection = getattr(config, "media_inspection", None)
    def _flag(config_value: bool, cached_key: str) -> bool:
        if config is None:
            return bool(raw.get(cached_key, False))
        return bool(config_value)

    summary = LocalAwarenessSummary(
        enabled=_flag(bool(getattr(config, "enabled", False)), "enabled"),
        device_discovery_enabled=_flag(
            bool(getattr(config, "device_discovery_enabled", False)),
            "device_discovery_enabled",
        ),
        lan_discovery_enabled=_flag(
            bool(getattr(config, "lan_discovery_enabled", False)),
            "lan_discovery_enabled",
        ),
        camera_enabled=_flag(bool(getattr(camera, "enabled", False)), "camera_enabled"),
        screen_enabled=_flag(bool(getattr(screen, "enabled", False)), "screen_enabled"),
        audio_input_enabled=_flag(bool(getattr(audio, "input_enabled", False)), "audio_input_enabled"),
        audio_output_enabled=_flag(bool(getattr(audio, "output_enabled", False)), "audio_output_enabled"),
        media_inspection_enabled=_flag(
            bool(getattr(media_inspection, "enabled", False)),
            "media_inspection_enabled",
        ),
        last_discovery=dict(raw.get("last_discovery") or {}),
        last_capture=dict(raw.get("last_capture") or {}),
        last_audio=dict(raw.get("last_audio") or {}),
        last_media_inspection=dict(raw.get("last_media_inspection") or {}),
        updated_at=str(raw.get("updated_at") or "") or None,
    ).to_dict()
    summary["backend_kind"] = (
        getattr(backend, "__class__", type(backend)).__name__
        if backend is not None
        else raw.get("backend_kind")
    )
    return summary
