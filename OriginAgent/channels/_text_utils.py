"""Shared markdown-to-plain-text utilities for channel output rendering.

Extracted from telegram.py's _strip_md / _strip_md_block to eliminate
copy-paste across channel implementations (A2).
"""

from __future__ import annotations

import re

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
