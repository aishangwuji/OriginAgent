"""Audio capture and playback utilities."""
from __future__ import annotations

import base64
import os
import re
import uuid
from pathlib import Path

from loguru import logger

_DATA_URL_RE = re.compile(r"^data:([^;]+);base64,(.+)$", re.DOTALL)


def save_audio_data_url(url: str, dest_dir: Path, prefix: str = "audio") -> Path | None:
    """Decode a base64 data URL to an audio file.

    Returns the saved file path, or None if the URL is invalid.
    """
    if not url or not isinstance(url, str):
        return None
    m = _DATA_URL_RE.match(url.strip())
    if not m:
        return None
    mime_type = m.group(1).strip().lower()
    b64_data = m.group(2)
    try:
        raw = base64.b64decode(b64_data)
    except Exception as exc:
        logger.exception("Base64 decode failed: {}", exc)
        return None

    ext = ".wav"
    if "ogg" in mime_type:
        ext = ".ogg"
    elif "mp3" in mime_type or "mpeg" in mime_type:
        ext = ".mp3"
    elif "mp4" in mime_type:
        ext = ".m4a"
    elif "webm" in mime_type:
        ext = ".webm"
    elif "flac" in mime_type:
        ext = ".flac"
    elif "aac" in mime_type:
        ext = ".aac"

    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}_{uuid.uuid4().hex[:12]}{ext}"
    dest = dest_dir / filename
    dest.write_bytes(raw)
    return dest


class AudioPlayback:
    """Cross-platform audio playback.

    On Windows, uses winsound. On other platforms, playback is a no-op.
    """

    def play(self, path: str | Path) -> bool:
        """Play an audio file. Returns True if playback was attempted."""
        p = Path(path)
        if not p.exists():
            logger.warning("Audio file not found: {}", path)
            return False

        if os.name != "nt":
            return False

        try:
            import winsound

            winsound.PlaySound(str(p), winsound.SND_FILENAME)
            return True
        except Exception as exc:
            logger.exception("winsound playback failed: {}", exc)
            return False
