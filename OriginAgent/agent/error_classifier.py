"""Classified error types with user-facing messages for the agent loop."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum


class ErrorKind(str, Enum):
    """Machine-readable error category for logging and metrics."""

    NETWORK_TIMEOUT = "network_timeout"
    CONTEXT_OVERFLOW = "context_overflow"
    CONTENT_FILTER = "content_filter"
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TOOL_FAILURE = "tool_failure"
    INTERNAL = "internal"


@dataclass(frozen=True)
class ClassifiedError:
    """A classified error with retry guidance."""

    kind: ErrorKind
    technical_detail: str
    retryable: bool = False


# ── Classification heuristics ──────────────────────────────────────────

_CONTEXT_OVERFLOW_KEYWORDS = (
    "context length",
    "token limit",
    "too many tokens",
    "maximum context",
    "context window",
    "max_tokens",
    "context_length_exceeded",
    "reduce the length",
)

_CONTENT_FILTER_KEYWORDS = (
    "content filter",
    "content policy",
    "safety filter",
    "unsafe content",
    "blocked content",
    "content_filter",
    "moderation",
)

_AUTH_KEYWORDS = (
    "invalid api key",
    "unauthorized",
    "authentication",
    "401",
    "403",
    "not authorized",
)

_RATE_LIMIT_KEYWORDS = (
    "rate limit",
    "too many requests",
    "429",
    "quota exceeded",
    "rate_limit",
)


def classify_exception(exc: BaseException) -> ClassifiedError:
    """Map an exception to a classified error with retry guidance."""
    msg = str(exc).lower()

    # Timeout errors (network, not context)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ClassifiedError(
            kind=ErrorKind.NETWORK_TIMEOUT,
            technical_detail=str(exc),
            retryable=True,
        )

    # Context length overflow
    if any(kw in msg for kw in _CONTEXT_OVERFLOW_KEYWORDS):
        return ClassifiedError(
            kind=ErrorKind.CONTEXT_OVERFLOW,
            technical_detail=str(exc),
            retryable=False,
        )

    # Content filter / moderation
    if any(kw in msg for kw in _CONTENT_FILTER_KEYWORDS):
        return ClassifiedError(
            kind=ErrorKind.CONTENT_FILTER,
            technical_detail=str(exc),
            retryable=False,
        )

    # Authentication
    if any(kw in msg for kw in _AUTH_KEYWORDS):
        return ClassifiedError(
            kind=ErrorKind.AUTHENTICATION,
            technical_detail=str(exc),
            retryable=False,
        )

    # Rate limit
    if any(kw in msg for kw in _RATE_LIMIT_KEYWORDS):
        return ClassifiedError(
            kind=ErrorKind.RATE_LIMIT,
            technical_detail=str(exc),
            retryable=True,
        )

    # Default: internal error
    return ClassifiedError(
        kind=ErrorKind.INTERNAL,
        technical_detail=str(exc),
        retryable=False,
    )


# ── User-facing messages ───────────────────────────────────────────────

_USER_MESSAGES: dict[ErrorKind, str] = {
    ErrorKind.NETWORK_TIMEOUT: (
        "The AI model took too long to respond (network timeout). "
        "Please try again — if the problem persists, try a shorter prompt."
    ),
    ErrorKind.CONTEXT_OVERFLOW: (
        "Your conversation has grown too long for the model to process. "
        "Please start a new session or shorten your message and try again."
    ),
    ErrorKind.CONTENT_FILTER: (
        "The request was blocked by the AI provider's content safety filter. "
        "Please rephrase your request and try again."
    ),
    ErrorKind.AUTHENTICATION: (
        "Authentication with the AI provider failed. "
        "Please check your API key configuration and try again."
    ),
    ErrorKind.RATE_LIMIT: (
        "The AI provider is currently rate-limiting requests. "
        "Please wait a moment and try again."
    ),
    ErrorKind.TOOL_FAILURE: (
        "A tool execution failed while processing your request. "
        "Please try again or rephrase your request."
    ),
    ErrorKind.INTERNAL: (
        "An unexpected error occurred while processing your request. "
        "Please try again. If the problem persists, contact the system administrator."
    ),
}


def user_facing_message(error: ClassifiedError) -> str:
    """Return a user-friendly error message with action guidance.

    Never returns the old hardcoded strings. Each ErrorKind maps to a
    message that tells the user what happened and what to do about it.
    """
    return _USER_MESSAGES.get(
        error.kind,
        _USER_MESSAGES[ErrorKind.INTERNAL],
    )
