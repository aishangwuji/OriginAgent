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
    # 把 attrs 格式化为消息文本，便于人工阅读（sink format 只输出 {message}，
    # logger.bind 绑定的 attrs 不会显示在日志中，需要拼入消息体）
    visible_attrs = {
        k: v for k, v in attrs.items()
        if v is not None and v != "" and k != "event"
    }
    # 也加入 trace_context 的非空字段（trace_id/span_id/session_key），便于排查；
    # 显式传入的 attrs 优先（可能覆盖同名的 trace_context 字段）
    for k, v in ctx.items():
        if k != "event" and v and k not in visible_attrs:
            visible_attrs[k] = v
    attrs_str = " ".join(f"{k}={v}" for k, v in visible_attrs.items())
    msg = f"event.{event} | {attrs_str}" if attrs_str else f"event.{event}"
    logger.bind(**ctx).info(msg)


# ── Convenience aliases ──────────────────────────────────────────────

def set_session(sk: str) -> None:
    _session_key.set(sk)
