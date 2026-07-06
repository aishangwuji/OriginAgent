"""Tests for SkillBootstrapper online ingest_chunk."""
import pytest
from dataclasses import replace

from OriginAgent.agent.skill_bootstrapper import SkillBootstrapper, SkillBootstrapperScanner, SkillCandidateCompiler
from OriginAgent.agent.skill_bootstrapper_models import ActionTraceDigest


def make_digest(
    digest_id: str = "d1",
    tool_sequence: list[str] | None = None,
    correction_flag: bool = False,
    session_key: str = "sess1",
    task_reference: str = "test task",
) -> ActionTraceDigest:
    tools = tool_sequence or ["search", "read_file"]
    return ActionTraceDigest(
        digest_id=digest_id,
        session_key=session_key,
        tool_sequence=tools,
        param_preview="q=test",
        correction_flag=correction_flag,
        task_reference=task_reference,
    )


class TestIngestChunk:
    def test_single_ingest_no_compile(self):
        """Single ingest does not trigger compile."""
        bs = SkillBootstrapper(min_repeats=3)
        result = bs.ingest_chunk(make_digest("d1"))
        assert result is None
        assert bs.window_size == 1

    def test_below_threshold_no_compile(self):
        """Below min_repeats, no compile."""
        bs = SkillBootstrapper(min_repeats=3)
        bs.ingest_chunk(make_digest("d1"))
        bs.ingest_chunk(make_digest("d2"))
        assert bs.window_size == 2
        # No compile triggered

    def test_reaches_threshold_with_correction_triggers_compile(self):
        """Reaching min_repeats with correction_flag triggers compile."""
        bs = SkillBootstrapper(min_repeats=3)
        bs.ingest_chunk(make_digest("d1"))
        bs.ingest_chunk(make_digest("d2"))
        # Third one with correction_flag
        result = bs.ingest_chunk(make_digest("d3", correction_flag=True))
        # compile may return SkillCandidate or None (depends on dangerous_tools check)
        # but the fingerprint should be removed from window either way
        assert bs.window_size == 0  # fingerprint removed after compile attempt

    def test_reaches_threshold_without_correction_no_compile(self):
        """Reaching min_repeats without correction_flag does NOT compile."""
        bs = SkillBootstrapper(min_repeats=3)
        bs.ingest_chunk(make_digest("d1"))
        bs.ingest_chunk(make_digest("d2"))
        result = bs.ingest_chunk(make_digest("d3"))
        assert result is None
        assert bs.window_size == 3  # still in window

    def test_different_fingerprints_separate(self):
        """Different tool sequences have separate fingerprints."""
        bs = SkillBootstrapper(min_repeats=3)
        bs.ingest_chunk(make_digest("d1", tool_sequence=["search"]))
        bs.ingest_chunk(make_digest("d2", tool_sequence=["read_file"]))
        assert bs.window_size == 2

    def test_window_capacity_fifo_eviction(self):
        """Window evicts oldest digest when exceeding max_window_size."""
        bs = SkillBootstrapper(min_repeats=100, max_window_size=3)  # high min_repeats to avoid compile
        bs.ingest_chunk(make_digest("d1", tool_sequence=["a"]))
        bs.ingest_chunk(make_digest("d2", tool_sequence=["b"]))
        bs.ingest_chunk(make_digest("d3", tool_sequence=["c"]))
        assert bs.window_size == 3
        # Adding 4th should evict d1
        bs.ingest_chunk(make_digest("d4", tool_sequence=["d"]))
        assert bs.window_size == 3  # still 3 after eviction

    def test_compile_removes_fingerprint_from_window(self):
        """After compile, the fingerprint is removed from window."""
        bs = SkillBootstrapper(min_repeats=2)
        bs.ingest_chunk(make_digest("d1"))
        bs.ingest_chunk(make_digest("d2", correction_flag=True))
        # compile triggered, fingerprint removed
        assert bs.window_size == 0
        # New ingest of same fingerprint starts fresh
        bs.ingest_chunk(make_digest("d3"))
        assert bs.window_size == 1

    def test_window_size_property(self):
        """window_size property returns total digest count."""
        bs = SkillBootstrapper(min_repeats=10)
        assert bs.window_size == 0
        bs.ingest_chunk(make_digest("d1", tool_sequence=["a"]))
        assert bs.window_size == 1
        bs.ingest_chunk(make_digest("d2", tool_sequence=["a"]))  # same fingerprint
        assert bs.window_size == 2
        bs.ingest_chunk(make_digest("d3", tool_sequence=["b"]))  # different fingerprint
        assert bs.window_size == 3
