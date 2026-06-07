"""Session replay: ensure assistant ``media`` paths are under the media root.

WebUI history signing (``/api/.../messages``) only works for files inside
``get_media_dir``. Tool-driven attachments may live in the workspace; stage
copies into the websocket media bucket before persisting message JSON.
"""

from __future__ import annotations

import contextlib
import shutil
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from OriginAgent.config.paths import get_data_dir, get_media_dir, get_workspace_path
from OriginAgent.utils.helpers import safe_filename

MAX_SESSION_REPLAY_MEDIA_BYTES = 25 * 1024 * 1024
MAX_SESSION_REPLAY_MEDIA_ITEMS = 20


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _trusted_roots(workspace: str | Path | None = None) -> tuple[Path, ...]:
    if workspace is None:
        workspace_root = get_workspace_path()
    else:
        workspace_root = get_workspace_path(workspace)
    roots = [workspace_root, get_media_dir(), get_data_dir() / "tmp"]
    out: list[Path] = []
    for root in roots:
        with contextlib.suppress(OSError):
            resolved = root.resolve()
            if resolved not in out:
                out.append(resolved)
    return tuple(out)


def stage_media_paths_for_session_replay(
    paths: list[str],
    *,
    workspace: str | Path | None = None,
) -> list[str]:
    """Keep trusted local files only; copy non-media files into ``media/websocket``."""
    root = get_media_dir().resolve()
    trusted_roots = _trusted_roots(workspace)
    out: list[str] = []
    seen: set[str] = set()
    seen_sources: set[str] = set()
    for raw in paths[:MAX_SESSION_REPLAY_MEDIA_ITEMS]:
        if not isinstance(raw, str) or not raw.strip():
            continue
        if raw.lower().startswith(("http://", "https://")):
            continue
        try:
            candidate = Path(raw).expanduser()
            if candidate.is_symlink():
                continue
            p = candidate.resolve(strict=True)
        except OSError:
            continue
        if not p.is_file():
            continue
        source_key = str(p)
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        if p.stat().st_size > MAX_SESSION_REPLAY_MEDIA_BYTES:
            logger.warning("skipping oversized session media: {}", raw)
            continue
        if not any(_is_under(p, trusted) for trusted in trusted_roots):
            logger.warning("skipping untrusted session media path: {}", raw)
            continue
        try:
            p.relative_to(root)
            key = str(p)
        except ValueError:
            try:
                media_dir = get_media_dir("websocket")
                staged = media_dir / f"{uuid.uuid4().hex[:12]}-{safe_filename(p.name) or 'attachment'}"
                shutil.copyfile(p, staged)
                key = str(staged.resolve())
            except OSError as exc:
                logger.warning("failed to stage session media from {}: {}", raw, exc)
                continue
        if key not in seen:
            out.append(key)
            seen.add(key)
    return out


def merge_turn_media_into_last_assistant(
    all_messages: list[dict[str, Any]],
    generated_image_paths: list[str],
    extra_attachment_paths: list[str],
    *,
    workspace: str | Path | None = None,
) -> None:
    """Attach staged paths to the last assistant row in *all_messages* (in-place)."""
    merged = list(
        dict.fromkeys(
            [
                *stage_media_paths_for_session_replay(generated_image_paths, workspace=workspace),
                *stage_media_paths_for_session_replay(extra_attachment_paths, workspace=workspace),
            ]
        )
    )
    last = all_messages[-1] if all_messages else None
    if not merged or not last or last.get("role") != "assistant":
        return
    existing = last.get("media")
    base = existing if isinstance(existing, list) else []
    last["media"] = list(dict.fromkeys([*base, *merged]))
