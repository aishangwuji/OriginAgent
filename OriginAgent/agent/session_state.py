"""Session-scoped state holder for AgentLoop cross-turn scratchpad.

Replaces flat ``self._last_*`` fields on AgentLoop with a session-keyed
container so concurrent sessions cannot overwrite each other's audit trails.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass
class SessionScopedState:
    """Mutable scratchpad state scoped to a single session key."""

    runtime_vars: dict[str, Any] = field(default_factory=dict)
    last_runtime_context: Any = None  # RuntimeContext | None
    last_continuity_session_key: str | None = None
    last_context_assembly: dict[str, Any] = field(default_factory=dict)
    last_recovered_continuity_checkpoint: dict[str, Any] = field(default_factory=dict)
    last_governance_audit: dict[str, Any] = field(default_factory=dict)
    last_action_continuity_audit: dict[str, Any] = field(default_factory=dict)
    cached_action_summary: dict[str, Any] = field(default_factory=dict)
    last_cognitive_scan: dict[str, Any] = field(default_factory=dict)
    last_world_attention_write: dict[str, Any] = field(default_factory=dict)

    # TTL tracking — updated on every get()
    last_access_s: float = field(default_factory=time.monotonic)


class SessionStateHolder:
    """Thread-safe container for per-session :class:`SessionScopedState`.

    Usage::

        holder = SessionStateHolder(ttl_s=3600.0)
        state = holder.get("cli:direct")
        state.last_runtime_context = ctx
        holder.drop("cli:direct")   # explicit cleanup
    """

    def __init__(self, ttl_s: float = 3600.0) -> None:
        self._states: dict[str, SessionScopedState] = {}
        self._lock = threading.Lock()
        self._ttl_s = ttl_s

    # ── core API ──────────────────────────────────────────────────────

    def get(self, session_key: str) -> SessionScopedState:
        """Return (creating if needed) the state for *session_key*.

        Uses double-checked locking so the common path (key exists) is
        lock-free.
        """
        state = self._states.get(session_key)
        if state is not None:
            state.last_access_s = time.monotonic()
            return state
        with self._lock:
            state = self._states.get(session_key)
            if state is None:
                state = SessionScopedState()
                self._states[session_key] = state
            return state

    def drop(self, session_key: str) -> None:
        """Remove state for *session_key* (no-op if absent)."""
        with self._lock:
            self._states.pop(session_key, None)

    # ── lifecycle ─────────────────────────────────────────────────────

    def expire_stale(self, now: float | None = None) -> int:
        """Remove entries whose ``last_access_s`` exceeds the TTL.

        Returns the number of entries removed.
        """
        if self._ttl_s <= 0:
            return 0
        cutoff = (now or time.monotonic()) - self._ttl_s
        stale: list[str] = []
        with self._lock:
            for key, state in self._states.items():
                if state.last_access_s < cutoff:
                    stale.append(key)
            for key in stale:
                del self._states[key]
        if stale:
            logger.info("SessionStateHolder: expired {} stale session(s)", len(stale))
        return len(stale)

    @property
    def size(self) -> int:
        """Current number of tracked sessions."""
        return len(self._states)
