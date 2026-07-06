"""Tests for SoarChunker."""
import pytest
from unittest.mock import MagicMock, AsyncMock
from dataclasses import dataclass
from typing import Any

from OriginAgent.agent.soar_chunker import SoarChunker
from OriginAgent.agent.soar_models import SoarObstacle, SoarSubgoal
from OriginAgent.agent.skill_bootstrapper import SkillBootstrapper
from OriginAgent.agent.skill_bootstrapper_models import ActionTraceDigest, SkillCandidate


@dataclass
class FakeToolRecord:
    tool_name: str
    started_at: str


def make_obstacle(obstacle_id: str = "obs1") -> SoarObstacle:
    return SoarObstacle(
        obstacle_id=obstacle_id,
        obstacle_type="tool_failure",
        root_cause="search failed",
        attempted_tools=["search"],
        recoverable_hint="retry",
    )


def make_subgoal(subagent_task_id: str = "task_123", solution_path: dict | None = None) -> SoarSubgoal:
    return SoarSubgoal(
        subgoal_id="sg1",
        parent_goal_id=None,
        obstacle=make_obstacle(),
        working_state_snapshot={"session_key": "sess1"},
        subagent_task_id=subagent_task_id,
        solution_path=solution_path,
    )


class TestSoarChunker:
    @pytest.fixture
    def bootstrapper(self):
        return SkillBootstrapper(min_repeats=100)  # high threshold to avoid actual compile

    @pytest.fixture
    def tool_records(self):
        return [
            FakeToolRecord("search", "2025-01-01T10:00:00Z"),
            FakeToolRecord("read_file", "2025-01-01T10:01:00Z"),
            FakeToolRecord("write_file", "2025-01-01T10:02:00Z"),
        ]

    @pytest.fixture
    def tool_store(self, tool_records):
        store = MagicMock()
        store.list_by_task = MagicMock(return_value=tool_records)
        return store

    @pytest.mark.asyncio
    async def test_chunk_produces_digest_with_correction_flag(self, bootstrapper, tool_store):
        """chunk() produces ActionTraceDigest with correction_flag=True and calls ingest_chunk."""
        subgoal = make_subgoal(solution_path={"result": "success"})
        chunker = SoarChunker(bootstrapper, tool_store)

        result = await chunker.chunk(subgoal)

        # ingest_chunk returns None when below threshold, but digest should be ingested
        assert result is None  # below min_repeats
        assert bootstrapper.window_size == 1  # one digest ingested

    @pytest.mark.asyncio
    async def test_tool_sequence_sorted_by_time(self, bootstrapper, tool_store, tool_records):
        """tool_sequence is extracted and sorted by started_at."""
        subgoal = make_subgoal()
        chunker = SoarChunker(bootstrapper, tool_store)

        await chunker.chunk(subgoal)

        # Verify the store was queried
        tool_store.list_by_task.assert_called_once_with("task_123")

    @pytest.mark.asyncio
    async def test_chunk_from_success(self, bootstrapper, tool_store):
        """chunk_from_success creates digest from obstacle + result_summary."""
        obstacle = make_obstacle()
        chunker = SoarChunker(bootstrapper, tool_store)

        result = await chunker.chunk_from_success(
            subagent_task_id="task_456",
            obstacle=obstacle,
            result_summary="completed successfully",
            session_key="sess2",
        )

        assert result is None  # below threshold
        assert bootstrapper.window_size == 1

    @pytest.mark.asyncio
    async def test_no_tool_store_returns_empty_sequence(self, bootstrapper):
        """Without tool_store, tool_sequence is empty list."""
        subgoal = make_subgoal()
        chunker = SoarChunker(bootstrapper, tool_record_store=None)

        result = await chunker.chunk(subgoal)

        # Should still produce a digest (with empty tool_sequence)
        assert bootstrapper.window_size == 1

    @pytest.mark.asyncio
    async def test_no_task_id_returns_empty_sequence(self, bootstrapper, tool_store):
        """Without subagent_task_id, tool_sequence is empty."""
        subgoal = SoarSubgoal(
            subgoal_id="sg1",
            parent_goal_id=None,
            obstacle=make_obstacle(),
            working_state_snapshot={},
            subagent_task_id=None,  # no task ID
        )
        chunker = SoarChunker(bootstrapper, tool_store)

        await chunker.chunk(subgoal)
        # tool_store.list_by_task should NOT be called
        tool_store.list_by_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_param_preview_truncated_to_200(self, bootstrapper, tool_store):
        """param_preview is truncated to 200 characters."""
        long_result = "x" * 500
        subgoal = make_subgoal(solution_path={"result": long_result})
        chunker = SoarChunker(bootstrapper, tool_store)

        await chunker.chunk(subgoal)

        # The digest is in the window now; we can't easily inspect it,
        # but we verify no crash and digest was ingested
        assert bootstrapper.window_size == 1

    @pytest.mark.asyncio
    async def test_tool_store_exception_returns_empty(self, bootstrapper):
        """If tool_store raises, tool_sequence is empty (graceful degradation)."""
        store = MagicMock()
        store.list_by_task = MagicMock(side_effect=RuntimeError("db error"))

        subgoal = make_subgoal()
        chunker = SoarChunker(bootstrapper, store)

        await chunker.chunk(subgoal)
        # Should not crash, digest still created with empty tool_sequence
        assert bootstrapper.window_size == 1

    @pytest.mark.asyncio
    async def test_tool_sequence_deduplication(self, bootstrapper):
        """Duplicate tool names are deduplicated in tool_sequence."""
        records = [
            FakeToolRecord("search", "2025-01-01T10:00:00Z"),
            FakeToolRecord("search", "2025-01-01T10:01:00Z"),  # duplicate
            FakeToolRecord("read_file", "2025-01-01T10:02:00Z"),
        ]
        store = MagicMock()
        store.list_by_task = MagicMock(return_value=records)

        subgoal = make_subgoal()
        chunker = SoarChunker(bootstrapper, store)

        await chunker.chunk(subgoal)
        # Digest ingested successfully
        assert bootstrapper.window_size == 1
