"""Minimal i18n helpers for WebUI-localized slash-command surfaces.

Translation packs are JSON files under ``OriginAgent/i18n/``.
Localization is opt-in via the explicit ``lang`` argument; callers that do not
pass a language keep the historic English responses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PACKAGES: dict[str, dict[str, Any]] = {}
_ROOT = Path(__file__).parent


def _normalize_lang(lang: str | None) -> list[str]:
    if not lang:
        return ["en"]
    value = str(lang).strip()
    if not value:
        return ["en"]
    candidates: list[str] = []
    lowered = value.lower()
    if lowered.startswith("zh"):
        candidates.append("zh")
    else:
        candidates.append(value)
        if "-" in value:
            candidates.append(value.split("-", 1)[0])
        elif "_" in value:
            candidates.append(value.split("_", 1)[0])
    if "en" not in candidates:
        candidates.append("en")
    seen: set[str] = set()
    ordered: list[str] = []
    for code in candidates:
        if not code or code in seen:
            continue
        seen.add(code)
        ordered.append(code)
    return ordered


def _load_pack(lang: str) -> dict[str, Any]:
    """Lazy-load a language pack from its JSON file."""
    if lang not in _PACKAGES:
        path = _ROOT / f"{lang}.json"
        if path.is_file():
            _PACKAGES[lang] = json.loads(path.read_text(encoding="utf-8"))
        else:
            _PACKAGES[lang] = {}
    return _PACKAGES[lang]


def t(key: str, lang: str | None = None, **fmt) -> str:
    """Resolve a dotted translation key to a string.

    ``key`` supports dot notation: ``"command.new.description"``.
    ``**fmt`` kwargs are used for ``str.format(**fmt)`` on the resolved string.

    Fallback chain: explicit lang (normalized) → English.
    If no key found in any pack, the key itself is returned.
    """
    raw: str | None = None
    for code in _normalize_lang(lang):
        if not code:
            continue
        node: Any = _load_pack(code)
        try:
            for segment in key.split("."):
                node = node[segment]
            if isinstance(node, str):
                raw = node
                break
        except (KeyError, TypeError):
            node = {}
            continue

    result = raw if raw is not None else key
    if fmt:
        result = result.format(**fmt)
    return result


def clear_cache() -> None:
    """Clear cached language packs (useful for hot-reload or testing)."""
    _PACKAGES.clear()
