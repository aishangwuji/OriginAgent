"""Tests for ``Provider._run_with_retry`` logging enhancements (Task 3).

Covers spec ``fix-cron-runtime-and-context-gaps`` P1-1 / Requirement: LLM
Retry Outcome Logging. Verifies that:

1. Successful retry logs ``"LLM retry succeeded on attempt N/M"``.
2. Exception raised by ``call(**kw)`` (e.g. ``ConnectionError``,
   ``asyncio.TimeoutError``) is caught and logged as
   ``"LLM call raised exception on attempt N/M: {exc_type}: {exc}"``,
   then retried per existing strategy.
3. Retry exhaustion emits ``event.llm.retry_exhausted`` with
   ``attempts=N`` and ``final_error=...`` attrs.

These tests call ``_run_with_retry`` directly with a mock ``call`` so the
new try/except path is exercised even though the production path normally
goes through ``_safe_chat`` (which already converts exceptions to error
responses).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from loguru import logger

from OriginAgent.providers.base import LLMProvider, LLMResponse
from OriginAgent.utils import tracing


class _MinimalProvider(LLMProvider):
    """Minimal concrete provider for exercising ``_run_with_retry`` directly."""

    async def chat(self, *args: Any, **kwargs: Any) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError(
            "chat() is not used; tests invoke _run_with_retry directly"
        )

    def get_default_model(self) -> str:
        return "test-model"


def _make_scripted_call(responses: list[Any]):
    """Build an async ``call`` that pops from ``responses``.

    Each entry is either an ``LLMResponse`` (returned) or a ``BaseException``
    (raised). Exposes a ``state`` dict with ``calls`` for assertion.
    """
    state = {"calls": 0, "responses": list(responses)}

    async def _call(**kwargs: Any) -> LLMResponse:
        state["calls"] += 1
        if not state["responses"]:
            raise RuntimeError("no more scripted responses")
        item = state["responses"].pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    _call.state = state  # type: ignore[attr-defined]
    return _call


@pytest.fixture
def loguru_sink():
    """Capture loguru records for assertion, removed after test."""
    records: list[dict[str, Any]] = []

    def sink(message):
        records.append(message.record)

    handler_id = logger.add(sink, format="{message}", level="DEBUG")
    try:
        yield records
    finally:
        logger.remove(handler_id)


@pytest.mark.asyncio
async def test_retry_succeeds_on_attempt_2_logs_success(
    monkeypatch, loguru_sink
) -> None:
    """ConnectionError on attempt 1, success on attempt 2 -> success log emitted."""
    provider = _MinimalProvider()
    call = _make_scripted_call([
        ConnectionError("connection refused"),
        LLMResponse(content="ok"),
    ])

    async def _fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("OriginAgent.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider._run_with_retry(
        call,
        {"messages": [{"role": "user", "content": "hello"}]},
        [{"role": "user", "content": "hello"}],
        retry_mode="standard",
        on_retry_wait=None,
    )

    assert response.content == "ok"
    assert call.state["calls"] == 2

    messages = [str(r["message"]) for r in loguru_sink]
    success_logs = [m for m in messages if "LLM retry succeeded on attempt" in m]
    assert success_logs, (
        f"Expected 'LLM retry succeeded on attempt' log, got: {messages}"
    )
    assert "attempt 2/3" in success_logs[0], (
        f"Expected 'attempt 2/3' in success log, got: {success_logs[0]}"
    )


@pytest.mark.asyncio
async def test_retry_exhausted_emits_event(monkeypatch) -> None:
    """All retries fail -> ``event.llm.retry_exhausted`` emitted with attrs."""
    provider = _MinimalProvider()
    call = _make_scripted_call([
        LLMResponse(content="429 rate limit a", finish_reason="error"),
        LLMResponse(content="429 rate limit b", finish_reason="error"),
        LLMResponse(content="429 rate limit c", finish_reason="error"),
        LLMResponse(content="503 final server error", finish_reason="error"),
    ])

    async def _fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("OriginAgent.providers.base.asyncio.sleep", _fake_sleep)

    captured_events: list[tuple[str, dict[str, Any]]] = []
    original_log_event = tracing.log_event

    def _capture_log_event(event: str, **attrs: Any) -> None:
        captured_events.append((event, dict(attrs)))
        original_log_event(event, **attrs)

    monkeypatch.setattr("OriginAgent.providers.base.log_event", _capture_log_event)

    response = await provider._run_with_retry(
        call,
        {"messages": [{"role": "user", "content": "hello"}]},
        [{"role": "user", "content": "hello"}],
        retry_mode="standard",
        on_retry_wait=None,
    )

    assert response.finish_reason == "error"
    assert call.state["calls"] == 4

    exhausted = [(e, a) for e, a in captured_events if e == "llm.retry_exhausted"]
    assert exhausted, (
        f"Expected event.llm.retry_exhausted, got events: "
        f"{[e for e, _ in captured_events]}"
    )
    _event_name, event_attrs = exhausted[0]
    # With delays=(1,2,4) the standard retry exhausts at attempt=4
    # (1 initial + 3 retries). The ``attempts`` attribute reflects the
    # total attempt counter at exhaustion, matching the existing
    # "LLM request failed after {attempt} retries, giving up" log convention.
    assert event_attrs["attempts"] == 4, (
        f"Expected attempts=4, got: {event_attrs}"
    )
    assert "final_error" in event_attrs, (
        f"Expected final_error attr, got: {event_attrs}"
    )
    assert event_attrs["final_error"], (
        f"final_error should be non-empty, got: {event_attrs}"
    )


@pytest.mark.asyncio
async def test_call_exception_is_caught_and_logged(
    monkeypatch, loguru_sink
) -> None:
    """``asyncio.TimeoutError`` on attempt 1 is caught, logged, and retried."""
    provider = _MinimalProvider()
    call = _make_scripted_call([
        asyncio.TimeoutError("LLM call timed out"),
        LLMResponse(content="ok"),
    ])

    async def _fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("OriginAgent.providers.base.asyncio.sleep", _fake_sleep)

    response = await provider._run_with_retry(
        call,
        {"messages": [{"role": "user", "content": "hello"}]},
        [{"role": "user", "content": "hello"}],
        retry_mode="standard",
        on_retry_wait=None,
    )

    # The exception must NOT propagate out of _run_with_retry; it is caught
    # and the retry strategy continues, ultimately returning success.
    assert response.content == "ok"
    assert call.state["calls"] == 2

    messages = [str(r["message"]) for r in loguru_sink]
    exc_logs = [m for m in messages if "LLM call raised exception on attempt" in m]
    assert exc_logs, (
        f"Expected 'LLM call raised exception on attempt' log, got: {messages}"
    )
    assert "attempt 1/3" in exc_logs[0], (
        f"Expected 'attempt 1/3' in exception log, got: {exc_logs[0]}"
    )
    assert "TimeoutError" in exc_logs[0], (
        f"Expected 'TimeoutError' in exception log, got: {exc_logs[0]}"
    )
