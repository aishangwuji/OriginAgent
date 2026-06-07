"""Provider-neutral attachment helpers.

These helpers keep the core runtime attachment model path-first and vendor-
neutral. Provider adapters can later map the descriptors/blocks emitted here
to provider-specific native multimodal formats.
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from OriginAgent.utils.helpers import detect_image_mime

AttachmentKind = Literal["image", "video", "audio", "document", "file"]

_DOCUMENT_MIME_PREFIXES = (
    "text/",
)
_DOCUMENT_MIMES = frozenset({
    "application/pdf",
    "application/json",
    "application/xml",
    "text/csv",
})
_DOCUMENT_EXTENSIONS = frozenset({
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".log",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
})


@dataclass(frozen=True)
class AttachmentDescriptor:
    """A provider-neutral description of a staged attachment."""

    path: Path
    name: str
    mime: str | None
    kind: AttachmentKind
    size_bytes: int
    source: str = "media"
    metadata: dict[str, Any] = field(default_factory=dict)


def _kind_from_path(path: Path, mime: str | None) -> AttachmentKind:
    if mime:
        if mime.startswith("image/"):
            return "image"
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("audio/"):
            return "audio"
        if mime == "application/pdf" or mime.startswith(_DOCUMENT_MIME_PREFIXES) or mime in _DOCUMENT_MIMES:
            return "document"
    if path.suffix.lower() in _DOCUMENT_EXTENSIONS:
        return "document"
    return "file"


def describe_attachment(path: str | Path, *, source: str = "media") -> AttachmentDescriptor | None:
    """Return a descriptor for *path*, or ``None`` when it cannot be inspected."""
    p = Path(path)
    try:
        stat = p.stat()
    except OSError:
        return None
    if not p.is_file():
        return None

    mime = None
    try:
        with open(p, "rb") as f:
            header = f.read(32)
        mime = detect_image_mime(header) or mimetypes.guess_type(str(p))[0]
    except OSError:
        mime = mimetypes.guess_type(str(p))[0]

    return AttachmentDescriptor(
        path=p,
        name=p.name,
        mime=mime,
        kind=_kind_from_path(p, mime),
        size_bytes=stat.st_size,
        source=source,
    )


def attachment_block(descriptor: AttachmentDescriptor) -> dict[str, Any]:
    """Convert a descriptor to a provider-neutral content block."""
    return {
        "type": "attachment_ref",
        "attachment": {
            "kind": descriptor.kind,
            "mime": descriptor.mime,
            "name": descriptor.name,
            "path": str(descriptor.path),
            "size_bytes": descriptor.size_bytes,
            "source": descriptor.source,
            "metadata": descriptor.metadata,
        },
        "_meta": {"path": str(descriptor.path)},
    }


def image_url_block(descriptor: AttachmentDescriptor) -> dict[str, Any] | None:
    """Return an OpenAI-style ``image_url`` block for an image descriptor."""
    if descriptor.kind != "image":
        return None
    try:
        raw = descriptor.path.read_bytes()
    except OSError:
        return None
    mime = descriptor.mime or detect_image_mime(raw) or mimetypes.guess_type(str(descriptor.path))[0]
    if not mime or not mime.startswith("image/"):
        return None
    b64 = base64.b64encode(raw).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}"},
        "_meta": {"path": str(descriptor.path)},
    }


def attachment_placeholder_text(descriptor: AttachmentDescriptor) -> str:
    """Return a stable textual breadcrumb for providers without native support."""
    return f"[{descriptor.kind}: {descriptor.path}]"
