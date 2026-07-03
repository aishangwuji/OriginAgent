"""Lightweight trace context and structured logging for OriginAgent.

Uses contextvars for async-safe trace propagation (no otel dependency).
Provides structured log helpers that decorate loguru with trace context.
"""

from __future__ import annotations

import contextvars
import time
import uuid
from contextlib import contextmanager
from typing import Any

from loguru import logger

# ── Trace context (contextvars — async-safe) ──────────────────────────

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=""
)
_span_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "span_id", default=""
)
_session_key: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_key", default=""
)


def new_trace() -> str:
    """Start a new trace and return its trace_id."""
    tid = uuid.uuid4().hex[:16]
    _trace_id.set(tid)
    _span_id.set("")
    return tid


def trace_context(
    *,
    trace_id: str | None = None,
    session_key: str | None = None,
) -> dict[str, Any]:
    """Return current trace context as a dict for structured logging."""
    ctx: dict[str, Any] = {}
    tid = trace_id or _trace_id.get("")
    if tid:
        ctx["trace_id"] = tid
    sid = _span_id.get("")
    if sid:
        ctx["span_id"] = sid
    sk = session_key or _session_key.get("")
    if sk:
        ctx["session_key"] = sk
    return ctx


@contextmanager
def span(name: str, *, attrs: dict[str, Any] | None = None):
    """Create a span — logs start/end with timing."""
    saved = _span_id.get("")
    sid = uuid.uuid4().hex[:8]
    _span_id.set(sid)
    start = time.monotonic()
    ctx = trace_context()
    ctx.update(attrs or {})
    ctx["span"] = name
    logger.bind(**ctx).info("span.start")
    try:
        yield
    except Exception:
        elapsed_ms = (time.monotonic() - start) * 1000
        ctx["elapsed_ms"] = round(elapsed_ms, 2)
        logger.bind(**ctx).error("span.error")
        raise
    finally:
        _span_id.set(saved)
        elapsed_ms = (time.monotonic() - start) * 1000
        ctx["elapsed_ms"] = round(elapsed_ms, 2)
        logger.bind(**ctx).info("span.end")


def log_event(event: str, **attrs: Any) -> None:
    """Log a structured event with current trace context."""
    ctx = trace_context()
    ctx["event"] = event
    ctx.update(attrs)
    logger.bind(**ctx).info("event.{event}", event=event)


# ── Convenience aliases ──────────────────────────────────────────────

def set_session(sk: str) -> None:
    _session_key.set(sk)
