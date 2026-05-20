"""Compatibility bridge for smart-home presence storage."""

from __future__ import annotations

import json
from typing import Any

from OpenHome.domain_packs.smart_home.runtime import presence as _smart_home_presence
from OpenHome.domain_packs.smart_home.runtime.presence import *  # noqa: F401,F403

_format_timestamp = _smart_home_presence._format_timestamp
_is_expired = _smart_home_presence._is_expired
_timestamp = _smart_home_presence._timestamp
_write_text_atomic = _smart_home_presence._write_text_atomic


class PresenceStore(_smart_home_presence.PresenceStore):
    """Compatibility wrapper that keeps module-level monkeypatch hooks working."""

    def _write_state_unlocked(self, state: dict[str, Any]) -> None:
        text = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        _write_text_atomic(self.presence_file, text)
