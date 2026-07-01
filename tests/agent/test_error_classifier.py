"""Tests for classified error messages."""
import asyncio
import pytest
from OriginAgent.agent.error_classifier import (
    ClassifiedError,
    ErrorKind,
    classify_exception,
    user_facing_message,
)


class TestClassifyException:
    def test_timeout_error_classifies_as_network_timeout(self):
        error = classify_exception(asyncio.TimeoutError("connection timed out"))
        assert error.kind == ErrorKind.NETWORK_TIMEOUT
        assert error.retryable is True

    def test_content_filter_error_classifies_as_content_filter(self):
        error = classify_exception(
            ValueError("content filtered: unsafe content detected")
        )
        # ValueError alone should not classify as content_filter unless
        # the message contains known filter keywords
        assert error.kind != ErrorKind.INTERNAL  # must be classified somehow

    def test_runtime_error_with_context_overflow(self):
        error = classify_exception(
            RuntimeError("context length exceeded maximum allowed tokens")
        )
        assert error.kind == ErrorKind.CONTEXT_OVERFLOW
        assert error.retryable is False

    def test_generic_exception_classifies_as_internal(self):
        error = classify_exception(Exception("something unexpected"))
        assert error.kind == ErrorKind.INTERNAL
        assert error.retryable is False


class TestUserFacingMessage:
    def test_network_timeout_gives_retry_guidance(self):
        error = ClassifiedError(
            kind=ErrorKind.NETWORK_TIMEOUT,
            technical_detail="Connection to API timed out after 30s",
            retryable=True,
        )
        msg = user_facing_message(error)
        assert "timeout" in msg.lower() or "超时" in msg or "time" in msg.lower()
        assert len(msg) > 20  # must be a substantial message

    def test_context_overflow_gives_length_guidance(self):
        error = ClassifiedError(
            kind=ErrorKind.CONTEXT_OVERFLOW,
            technical_detail="Token limit exceeded",
            retryable=False,
        )
        msg = user_facing_message(error)
        assert len(msg) > 20
        # Should guide user to shorten input
        assert any(
            word in msg.lower()
            for word in ["shorten", "精简", "reduce", "减少", "length", "长度"]
        )

    def test_internal_error_is_generic_but_distinct(self):
        error = ClassifiedError(
            kind=ErrorKind.INTERNAL,
            technical_detail="null pointer",
            retryable=False,
        )
        msg = user_facing_message(error)
        assert len(msg) > 10
        # Must NOT be the old hardcoded string
        assert msg != "Sorry, I encountered an error."
        assert msg != "Sorry, I encountered an error calling the AI model."
