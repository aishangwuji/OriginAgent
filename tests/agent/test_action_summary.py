"""Tests for action_summary module — caching failure observability."""
from __future__ import annotations

from loguru import logger as loguru_logger

from OriginAgent.agent.action_summary import (
    ACTION_SUMMARY_SOURCE,
    action_summary_from_loop,
)


class _ReadOnlyLoop:
    """A loop-like object whose attribute setting always fails."""

    def __setattr__(self, name, value):
        raise RuntimeError("read-only loop attribute")


def test_cache_failure_logs_debug():
    """When setattr fails on loop, a debug log is emitted and summary is still returned.

    Locks the expected behavior for the TD-2026-010 fix at action_summary.py L60-63:
    a swallowed exception must still produce a debug-level log line so that
    repeated cache failures are observable (rule 1 — full-chain tracing).
    """
    loop = _ReadOnlyLoop()

    records: list[str] = []
    handler_id = loguru_logger.add(lambda m: records.append(str(m)), level="DEBUG")
    try:
        result = action_summary_from_loop(loop)
    finally:
        loguru_logger.remove(handler_id)

    # 功能正确性不受影响:返回正常 summary 字典
    assert isinstance(result, dict)
    assert result.get("source") == ACTION_SUMMARY_SOURCE
    # 应有 debug 日志包含 "Failed to cache action summary"
    assert any("Failed to cache action summary" in r for r in records)
