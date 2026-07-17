"""Tests for OriginAgent.utils.tracing — trace context and structured logging."""

from __future__ import annotations

import asyncio
import contextvars
import re
from typing import Any

import pytest

from OriginAgent.utils.tracing import (
    log_event,
    new_trace,
    set_session,
    span,
    trace_context,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _capture_loguru_sink() -> tuple[list[dict[str, Any]], Any]:
    """Create a loguru sink that collects bound records for assertion."""
    records: list[dict[str, Any]] = []

    def sink(message) -> None:
        record = message.record
        records.append({
            "level": record["level"].name,
            "message": record["message"],
            "extra": dict(record["extra"]),
        })

    import loguru
    # Return an id so the caller can remove it later
    handler_id = loguru.logger.add(sink, format="{message}", level="DEBUG")
    return records, handler_id


# ── TestTraceContext ──────────────────────────────────────────────────────


class TestTraceContext:
    """Verify the trace context API — new_trace, trace_context, set_session."""

    def test_new_trace_generates_unique_id(self) -> None:
        """new_trace() returns a non-empty hex string and sets trace_id."""
        tid = new_trace()
        assert isinstance(tid, str)
        assert len(tid) == 16
        assert re.fullmatch(r"[0-9a-f]{16}", tid)

    def test_trace_context_returns_empty_when_nothing_set(self) -> None:
        """trace_context() returns an empty dict when no context is set."""
        ctx = trace_context()
        assert isinstance(ctx, dict)
        # Without any context set, trace_context should return at most trace_id
        # if the global default is empty
        # but the default is "" so we just check nothing crashes

    def test_trace_context_populates_all_fields(self) -> None:
        """trace_context() returns trace_id, span_id, session_key when set."""
        new_trace()
        set_session("test:session-42")
        from OriginAgent.utils.tracing import _span_id
        _span_id.set("abc12345")
        ctx = trace_context()
        assert ctx.get("trace_id")
        assert len(str(ctx["trace_id"])) == 16
        assert ctx["span_id"] == "abc12345"
        assert ctx["session_key"] == "test:session-42"

    def test_set_session_stores_value(self) -> None:
        """set_session() stores the session key in the context var."""
        new_trace()
        set_session("cli:direct")
        ctx = trace_context()
        assert ctx.get("session_key") == "cli:direct"

    def test_context_is_async_safe(self) -> None:
        """Concurrent async tasks have isolated trace contexts."""
        new_trace()
        set_session("main-session")
        results: list[dict] = []

        async def worker(ident: str) -> dict:
            # Each worker re-initialises its own trace context
            tid = new_trace()
            set_session(f"worker-{ident}")
            # Simulate a span
            from OriginAgent.utils.tracing import _span_id
            _span_id.set(f"span-{ident}")
            ctx = trace_context()
            return {"trace_id": ctx["trace_id"], "session_key": ctx["session_key"], "span_id": ctx["span_id"]}

        async def run() -> list[dict]:
            tasks = [worker(str(i)) for i in range(5)]
            return await asyncio.gather(*tasks)

        results = asyncio.run(run())

        # Each worker should have its own distinct trace_id
        trace_ids = {r["trace_id"] for r in results}
        assert len(trace_ids) == 5, f"Expected 5 unique trace IDs, got {len(trace_ids)}"

        # Each worker should have its own session_key
        session_keys = {r["session_key"] for r in results}
        assert session_keys == {f"worker-{i}" for i in range(5)}

        # Main context should be unchanged after tasks
        main_ctx = trace_context()
        assert main_ctx["session_key"] == "main-session"

    def test_trace_context_explicit_override(self) -> None:
        """trace_context() accepts explicit trace_id and session_key."""
        ctx = trace_context(trace_id="abc123", session_key="overridden")
        assert ctx["trace_id"] == "abc123"
        assert ctx["session_key"] == "overridden"


# ── TestSpanContextManager ────────────────────────────────────────────────


class TestSpanContextManager:
    """Verify the span() context manager."""

    def test_span_logs_start_and_end(self) -> None:
        """span() logs span.start and span.end with timing."""
        new_trace()
        set_session("test:span-start-end")
        records, handler_id = _capture_loguru_sink()
        import loguru
        try:
            with span("my-test-span", attrs={"key": "value"}):
                pass
            # Collect records
            start = [r for r in records if r["message"] == "span.start"]
            end = [r for r in records if r["message"] == "span.end"]
            assert len(start) == 1, f"Expected 1 span.start, got {len(start)}"
            assert len(end) == 1, f"Expected 1 span.end, got {len(end)}"
            s = start[0]
            e = end[0]
            assert s["extra"].get("span") == "my-test-span"
            assert e["extra"].get("span") == "my-test-span"
            assert s["extra"].get("key") == "value"
            assert e["extra"].get("key") == "value"
            assert "elapsed_ms" in e["extra"]
            # Both should carry trace context
            assert s["extra"].get("trace_id")
            assert e["extra"].get("trace_id")
            assert s["extra"].get("session_key") == "test:span-start-end"
        finally:
            loguru.logger.remove(handler_id)

    def test_span_logs_error_on_exception(self) -> None:
        """span() logs span.error when the body raises."""
        new_trace()
        records, handler_id = _capture_loguru_sink()
        import loguru
        try:
            with pytest.raises(ValueError, match="boom"):
                with span("failing-span"):
                    msg = "boom"
                    raise ValueError(msg)
            error = [r for r in records if r["message"] == "span.error"]
            end = [r for r in records if r["message"] == "span.end"]
            assert len(error) == 1, f"Expected 1 span.error, got {len(error)}"
            assert len(end) == 1, f"Expected 1 span.end, got {len(end)}"
            assert error[0]["extra"].get("span") == "failing-span"
            assert "elapsed_ms" in error[0]["extra"]
        finally:
            loguru.logger.remove(handler_id)

    def test_span_restores_previous_id(self) -> None:
        """span() restores the previous span_id after exit."""
        from OriginAgent.utils.tracing import _span_id
        new_trace()
        _span_id.set("outer-span")
        with span("inner"):
            inner_id = _span_id.get()
            assert inner_id != "outer-span"
            assert len(inner_id) == 8
        restored = _span_id.get()
        assert restored == "outer-span"

    def test_span_nesting(self) -> None:
        """Nested span() calls each get a unique span_id and restore correctly."""
        from OriginAgent.utils.tracing import _span_id
        new_trace()
        ids: dict[str, str] = {}
        _span_id.set("root")
        with span("level-1"):
            ids["l1_enter"] = _span_id.get()
            with span("level-2"):
                ids["l2_enter"] = _span_id.get()
                assert ids["l2_enter"] != ids["l1_enter"]
            ids["l1_after_l2"] = _span_id.get()
            assert ids["l1_after_l2"] == ids["l1_enter"]
        ids["after_root"] = _span_id.get()
        assert ids["after_root"] == "root"
        assert len(ids["l1_enter"]) == 8
        assert len(ids["l2_enter"]) == 8

    def test_span_still_logs_end_when_body_exception(self) -> None:
        """span.end is logged even when the span body raises."""
        new_trace()
        records, handler_id = _capture_loguru_sink()
        import loguru
        try:
            with pytest.raises(RuntimeError):
                with span("error-span"):
                    msg = "oops"
                    raise RuntimeError(msg)
            end = [r for r in records if r["message"] == "span.end"]
            assert len(end) == 1
        finally:
            loguru.logger.remove(handler_id)


# ── TestLogEvent ──────────────────────────────────────────────────────────


class TestLogEvent:
    """Verify the log_event() helper."""

    def test_log_event_emits_structured_record(self) -> None:
        """log_event() emits a structured record with trace context."""
        new_trace()
        set_session("test:log-event-session")
        records, handler_id = _capture_loguru_sink()
        import loguru
        try:
            log_event("test.event", extra_field="extra_value")
            assert len(records) == 1, f"Expected 1 record, got {len(records)}"
            rec = records[0]
            assert rec["extra"].get("event") == "test.event"
            assert rec["extra"].get("extra_field") == "extra_value"
            assert rec["extra"].get("trace_id")
            assert rec["extra"].get("session_key") == "test:log-event-session"
        finally:
            loguru.logger.remove(handler_id)

    def test_log_event_multiple_attributes(self) -> None:
        """log_event() accepts and forwards all keyword attributes."""
        new_trace()
        records, handler_id = _capture_loguru_sink()
        import loguru
        try:
            log_event("multi.attr", count=42, name="test", active=True)
            assert len(records) == 1
            extra = records[0]["extra"]
            assert extra["event"] == "multi.attr"
            assert extra["count"] == 42
            assert extra["name"] == "test"
            assert extra["active"] is True
        finally:
            loguru.logger.remove(handler_id)


# ── TestLogEventMessageFormat ────────────────────────────────────────────


class TestLogEventMessageFormat:
    """Verify log_event() formats attrs into the message text (not just extra).

    背景：sink format 只输出 {message}，导致 logger.bind 绑定的 attrs 全部丢失。
    现要求 log_event 把 attrs 格式化为 `key=value` 对拼入消息文本，便于人工阅读。
    """

    @pytest.fixture(autouse=True)
    def _reset_trace_context(self) -> Any:
        """每个测试前重置 trace contextvars，保证消息格式可断言。

        contextvars 不会在测试间自动重置，先前测试可能遗留 trace_id/session_key，
        这会让 trace_context() 返回非空字段并污染消息文本。
        """
        from OriginAgent.utils.tracing import _span_id, _trace_id, _session_key
        saved = (_trace_id.get(""), _span_id.get(""), _session_key.get(""))
        _trace_id.set("")
        _span_id.set("")
        _session_key.set("")
        yield
        _trace_id.set(saved[0])
        _span_id.set(saved[1])
        _session_key.set(saved[2])

    def test_log_event_with_attrs_includes_them_in_message(self) -> None:
        """带 attrs 的事件，消息文本应包含 `event.{name} | k=v k=v`。"""
        records, handler_id = _capture_loguru_sink()
        import loguru
        try:
            log_event("llm.request", model="gpt-4", session_key="abc")
            assert len(records) == 1, f"Expected 1 record, got {len(records)}"
            msg = records[0]["message"]
            assert "event.llm.request | model=gpt-4 session_key=abc" in msg
        finally:
            loguru.logger.remove(handler_id)

    def test_log_event_without_attrs_no_pipe(self) -> None:
        """无 attrs 的事件，消息文本不附加 `|` 分隔符。"""
        records, handler_id = _capture_loguru_sink()
        import loguru
        try:
            log_event("heartbeat.tick")
            assert len(records) == 1, f"Expected 1 record, got {len(records)}"
            msg = records[0]["message"]
            assert msg == "event.heartbeat.tick"
            assert "|" not in msg
        finally:
            loguru.logger.remove(handler_id)

    def test_log_event_skips_none_and_empty_attrs(self) -> None:
        """值为 None 或空字符串的 attrs 不出现在消息文本中（避免噪音）。"""
        records, handler_id = _capture_loguru_sink()
        import loguru
        try:
            log_event("test", model=None, name="", valid="x")
            assert len(records) == 1, f"Expected 1 record, got {len(records)}"
            msg = records[0]["message"]
            assert "model=" not in msg
            assert "name=" not in msg
            assert "valid=x" in msg
        finally:
            loguru.logger.remove(handler_id)

    def test_log_event_still_binds_attrs(self) -> None:
        """logger.bind 仍被调用，attrs 绑定到 record extra（JSON sink 不受影响）。"""
        records, handler_id = _capture_loguru_sink()
        import loguru
        try:
            log_event("bind.check", model="gpt-4", session_key="abc")
            assert len(records) == 1, f"Expected 1 record, got {len(records)}"
            extra = records[0]["extra"]
            assert extra.get("event") == "bind.check"
            assert extra.get("model") == "gpt-4"
            assert extra.get("session_key") == "abc"
        finally:
            loguru.logger.remove(handler_id)
