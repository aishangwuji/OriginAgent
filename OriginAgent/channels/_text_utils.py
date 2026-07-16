"""Shared utilities for channel output rendering.

Includes markdown-to-plain-text stripping (extracted from telegram.py's
_strip_md / _strip_md_block to eliminate copy-paste, A2) and local media
reference parsing (extracted from dingtalk/qq to eliminate copy-paste, C4).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

# ---------------------------------------------------------------------------
# Inline formatting
# ---------------------------------------------------------------------------

_BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")
_UNDERSCORE_BOLD_PATTERN = re.compile(r"__(.+?)__")
_STRIKETHROUGH_PATTERN = re.compile(r"~~(.+?)~~")
_INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")
# Italic: only match underscores that are NOT part of a URL or word boundary
_ITALIC_PATTERN = re.compile(r"(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])")


def strip_markdown_inline(s: str) -> str:
    """Strip bold, italic, strikethrough, and inline-code from text."""
    s = _BOLD_PATTERN.sub(r"\1", s)
    s = _UNDERSCORE_BOLD_PATTERN.sub(r"\1", s)
    s = _STRIKETHROUGH_PATTERN.sub(r"\1", s)
    s = _INLINE_CODE_PATTERN.sub(r"\1", s)
    return s.strip()


# ---------------------------------------------------------------------------
# Block + inline stripping (for streaming mid-edit previews)
# ---------------------------------------------------------------------------

_CODE_BLOCK_PATTERN = re.compile(r"```[\w]*\n?([\s\S]*?)```")
_HEADER_PATTERN = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_BLOCKQUOTE_PATTERN = re.compile(r"^>\s*(.*)$", re.MULTILINE)
_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_IMAGE_PATTERN = re.compile(r"!\[.*?\]\([^)]+\)")
_LIST_BULLET_PATTERN = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_HORIZONTAL_RULE_PATTERN = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)


def strip_markdown_block(text: str) -> str:
    """Strip block-level and inline markdown for readable plain-text preview.

    Used during streaming mid-edits so users see clean text instead of raw
    markdown syntax while the response is still being generated.
    """
    # Code blocks -> just the code
    text = _CODE_BLOCK_PATTERN.sub(r"\1", text)
    # Images -> remove entirely
    text = _IMAGE_PATTERN.sub("", text)
    # Headers -> plain text
    text = _HEADER_PATTERN.sub(r"\1", text)
    # Blockquotes
    text = _BLOCKQUOTE_PATTERN.sub(r"\1", text)
    # Horizontal rules -> blank line
    text = _HORIZONTAL_RULE_PATTERN.sub("", text)
    # List bullets
    text = _LIST_BULLET_PATTERN.sub("", text)
    # Links [text](url) -> text
    text = _LINK_PATTERN.sub(r"\1", text)
    # Inline formatting
    text = strip_markdown_inline(text)
    return text.strip()


# ---------------------------------------------------------------------------
# Local media reference parsing
# ---------------------------------------------------------------------------


def parse_local_media_ref(media_ref: str) -> Path | None:
    """Resolve a local media reference to an existing file Path.

    Accepts either a ``file://`` URI or a plain filesystem path (with ``~``
    expansion). Returns the resolved :class:`~pathlib.Path` when it points to
    an existing file, otherwise ``None``.

    Extracted from dingtalk/qq to provide a single source of truth for
    local-media resolution (C4).
    """
    if not media_ref:
        return None
    if media_ref.startswith("file://"):
        parsed = urlparse(media_ref)
        # Windows drive letter may land in netloc (file://C:/...); Unix uses path.
        raw = parsed.path or parsed.netloc
        local_path = Path(unquote(raw))
    else:
        local_path = Path(os.path.expanduser(media_ref))
    if not local_path.is_file():
        return None
    return local_path
