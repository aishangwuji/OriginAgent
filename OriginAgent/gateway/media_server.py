"""Media file serving and HMAC-signed URL handling.

Extracted from WebSocketChannel. Handles:
- Signed media URL generation and validation
- Media file streaming with MIME whitelist
- Workspace media staging
- Static SPA file serving
"""

from __future__ import annotations

import binascii
import hashlib
import hmac
import mimetypes
import shutil
import uuid
from pathlib import Path
from typing import Any


from OriginAgent.config.paths import get_media_dir
from OriginAgent.utils.helpers import safe_filename
from OriginAgent.gateway._helpers import (
    b64url_decode,
    b64url_encode,
    http_error,
    http_response,
)


# Allowed MIME types for the media endpoint. Anything outside this set
# is degraded to ``application/octet-stream`` so an attacker who somehow
# gets a signed URL for an unexpected file type can't trick the browser
# into sniffing executable content.
_MEDIA_ALLOWED_MIMES: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "video/mp4",
    "video/webm",
    "video/quicktime",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/x-m4a",
    "audio/wav",
    "audio/ogg",
    "application/pdf",
    "text/plain",
    "application/json",
    "application/octet-stream",
})


class MediaServer:
    """Signed media URL generation, validation, and static SPA file serving."""

    def __init__(self, media_secret: bytes, static_dist_path: Path | None) -> None:
        self._media_secret = media_secret
        self._static_dist_path = static_dist_path

    # ── Signed media URLs ──────────────────────────────────────────────

    def sign_media_path(self, abs_path: Path) -> str | None:
        """Return a ``/api/media/<sig>/<payload>`` URL for *abs_path*, or
        ``None`` when the path does not resolve inside the media root.

        The URL is self-authenticating: the signature binds the payload to
        this process's ``_media_secret``, so only paths we chose to sign can
        be fetched.
        """
        try:
            media_root = get_media_dir().resolve()
            rel = abs_path.resolve().relative_to(media_root)
        except (OSError, ValueError):
            return None
        payload = b64url_encode(rel.as_posix().encode("utf-8"))
        mac = hmac.new(
            self._media_secret, payload.encode("ascii"), hashlib.sha256
        ).digest()[:16]
        return f"/api/media/{b64url_encode(mac)}/{payload}"

    def sign_or_stage_media_path(self, path: Path, logger: Any) -> dict[str, str] | None:
        """Return a signed media URL payload for *path*.

        Persisted inbound media may already live under ``get_media_dir`` and
        can be signed directly. Workspace uploads and outbound bot-generated
        files may live elsewhere on disk; copy those into the websocket media
        bucket first.
        """
        signed = self.sign_media_path(path)
        if signed is not None:
            return {"url": signed, "name": path.name}
        try:
            if not path.is_file():
                return None
            media_dir = get_media_dir("websocket")
            safe_name = safe_filename(path.name) or "attachment"
            staged = media_dir / f"{uuid.uuid4().hex[:12]}-{safe_name}"
            shutil.copyfile(path, staged)
        except OSError as exc:
            logger.warning("failed to stage outbound media {}: {}", path, exc)
            return None
        signed = self.sign_media_path(staged)
        if signed is None:
            return None
        return {"url": signed, "name": path.name}

    def handle_media_fetch(self, sig: str, payload: str) -> Any:
        """Serve a single media file previously signed via :meth:`sign_media_path`.

        Validates the HMAC signature, decodes the payload to a relative path,
        and streams the file bytes with a long-lived immutable cache header.
        """
        try:
            provided_mac = b64url_decode(sig)
        except (ValueError, binascii.Error):
            return http_error(401, "invalid signature")
        expected_mac = hmac.new(
            self._media_secret, payload.encode("ascii"), hashlib.sha256
        ).digest()[:16]
        if not hmac.compare_digest(expected_mac, provided_mac):
            return http_error(401, "invalid signature")
        try:
            rel_bytes = b64url_decode(payload)
            rel_str = rel_bytes.decode("utf-8")
        except (ValueError, binascii.Error, UnicodeDecodeError):
            return http_error(400, "invalid payload")
        try:
            media_root = get_media_dir().resolve()
            candidate = (media_root / rel_str).resolve()
            candidate.relative_to(media_root)
        except (OSError, ValueError):
            return http_error(404, "not found")
        if not candidate.is_file():
            return http_error(404, "not found")
        try:
            body = candidate.read_bytes()
        except OSError:
            return http_error(500, "read error")
        mime, _ = mimetypes.guess_type(candidate.name)
        if mime not in _MEDIA_ALLOWED_MIMES:
            mime = "application/octet-stream"
        return http_response(
            body,
            content_type=mime,
            extra_headers=[
                ("Cache-Control", "private, max-age=31536000, immutable"),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )

    # ── Static SPA serving ─────────────────────────────────────────────

    def serve_static(self, request_path: str) -> Any | None:
        """Resolve *request_path* against the built SPA directory; SPA fallback to index.html."""
        if self._static_dist_path is None:
            return None
        rel = request_path.lstrip("/")
        if not rel:
            rel = "index.html"
        # Reject path-traversal attempts and absolute targets.
        if ".." in rel.split("/") or rel.startswith("/"):
            return http_error(403, "Forbidden")
        candidate = (self._static_dist_path / rel).resolve()
        try:
            candidate.relative_to(self._static_dist_path)
        except ValueError:
            return http_error(403, "Forbidden")
        if not candidate.is_file():
            # SPA history-mode fallback: unknown routes serve index.html so the
            # client-side router can render them.
            index = self._static_dist_path / "index.html"
            if index.is_file():
                candidate = index
            else:
                return None
        try:
            body = candidate.read_bytes()
        except OSError:
            return http_error(500, "Internal Server Error")
        ctype, _ = mimetypes.guess_type(candidate.name)
        if ctype is None:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
            ctype = f"{ctype}; charset=utf-8"
        if candidate.name == "index.html":
            return http_response(
                body,
                content_type=ctype,
                extra_headers=[("Cache-Control", "no-cache")],
            )
        return http_response(
            body,
            content_type=ctype,
            extra_headers=[("Cache-Control", "public, max-age=31536000, immutable")],
        )
