"""Tests for Soar data models."""
import json
import pytest
import tempfile
from pathlib import Path

from OriginAgent.agent.soar_models import SoarObstacle, SoarSubgoal, SoarSubgoalStack


def make_obstacle(obstacle_id: str = "obs1", obstacle_type: str = "tool_failure") -> SoarObstacle:
    return SoarObstacle(
        obstacle_id=obstacle_id,
        obstacle_type=obstacle_type,
        root_cause="test root cause",
        attempted_tools=["search", "read_file"],
        recoverable_hint="retry",
    )


def make_subgoal(subgoal_id: str = "sg1", obstacle: SoarObstacle | None = None) -> SoarSubgoal:
    return SoarSubgoal(
        subgoal_id=subgoal_id,
        parent_goal_id=None,
        obstacle=obstacle or make_obstacle(),
        working_state_snapshot={"attention": ["task1"]},
        subagent_task_id="task_abc",
    )


class TestSoarObstacle:
    def test_default_created_at_filled(self):
        """created_at is auto-filled when not provided."""
        obs = SoarObstacle(
            obstacle_id="o1", obstacle_type="tool_failure",
            root_cause="x", attempted_tools=[],
        )
        assert obs.created_at != ""

    def test_serialization_roundtrip(self):
        """to_json/from_json roundtrip preserves all fields."""
        obs = make_obstacle()
        restored = SoarObstacle.from_json(obs.to_json())
        assert restored.obstacle_id == "obs1"
        assert restored.obstacle_type == "tool_failure"
        assert restored.root_cause == "test root cause"
        assert restored.attempted_tools == ["search", "read_file"]
        assert restored.recoverable_hint == "retry"

    def test_from_json_missing_optional_fields(self):
        """from_json handles missing optional fields with defaults."""
        data = {"obstacle_id": "o1", "obstacle_type": "internal_error"}
        obs = SoarObstacle.from_json(data)
        assert obs.obstacle_type == "internal_error"
        assert obs.root_cause == ""
        assert obs.attempted_tools == []
        assert obs.recoverable_hint == "unknown"


class TestSoarSubgoal:
    def test_default_created_at_filled(self):
        """created_at is auto-filled when not provided."""
        sg = make_subgoal()
        assert sg.created_at != ""

    def test_serialization_roundtrip(self):
        """to_json/from_json roundtrip preserves nested obstacle."""
        sg = make_subgoal()
        restored = SoarSubgoal.from_json(sg.to_json())
        assert restored.subgoal_id == "sg1"
        assert restored.obstacle.obstacle_type == "tool_failure"
        assert restored.working_state_snapshot == {"attention": ["task1"]}
        assert restored.subagent_task_id == "task_abc"

    def test_with_solution_returns_new_instance(self):
        """with_solution returns a new frozen instance."""
        sg = make_subgoal()
        solution = {"tools": ["a", "b"], "result": "success"}
        updated = sg.with_solution(solution)
        assert updated.solution_path == solution
        assert sg.solution_path is None  # original unchanged

    def test_serialization_with_solution(self):
        """to_json/from_json preserves solution_path."""
        sg = make_subgoal().with_solution({"steps": [1, 2]})
        restored = SoarSubgoal.from_json(sg.to_json())
        assert restored.solution_path == {"steps": [1, 2]}


class TestSoarSubgoalStack:
    def test_empty_stack(self):
        """New stack is empty."""
        stack = SoarSubgoalStack()
        assert stack.is_empty
        assert stack.depth == 0
        assert stack.peek() is None
        assert stack.pop() is None

    def test_push_pop_lifo(self):
        """Stack follows LIFO semantics."""
        stack = SoarSubgoalStack(max_depth=10)
        sg1 = make_subgoal("sg1")
        sg2 = make_subgoal("sg2")

        stack.push(sg1)
        stack.push(sg2)
        assert stack.depth == 2
        assert stack.peek().subgoal_id == "sg2"

        popped = stack.pop()
        assert popped.subgoal_id == "sg2"
        assert stack.depth == 1

        popped = stack.pop()
        assert popped.subgoal_id == "sg1"
        assert stack.is_empty

    def test_overflow(self):
        """Push beyond max_depth raises OverflowError."""
        stack = SoarSubgoalStack(max_depth=2)
        stack.push(make_subgoal("sg1"))
        stack.push(make_subgoal("sg2"))
        with pytest.raises(OverflowError):
            stack.push(make_subgoal("sg3"))

    def test_find_by_subagent(self):
        """find_by_subagent locates frame by task ID."""
        stack = SoarSubgoalStack()
        sg1 = make_subgoal("sg1", obstacle=make_obstacle())
        sg1 = SoarSubgoal(
            subgoal_id="sg1", parent_goal_id=None,
            obstacle=make_obstacle(), working_state_snapshot={},
            subagent_task_id="task_111",
        )
        sg2 = SoarSubgoal(
            subgoal_id="sg2", parent_goal_id="sg1",
            obstacle=make_obstacle(), working_state_snapshot={},
            subagent_task_id="task_222",
        )
        stack.push(sg1)
        stack.push(sg2)

        found = stack.find_by_subagent("task_222")
        assert found is not None
        assert found.subgoal_id == "sg2"

        not_found = stack.find_by_subagent("task_999")
        assert not_found is None

    def test_persist_load_roundtrip(self):
        """Persist and load preserves stack state."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "stack.jsonl"
            stack = SoarSubgoalStack(max_depth=10)
            stack.push(make_subgoal("sg1"))
            stack.push(make_subgoal("sg2"))

            stack.persist_to(path)
            assert path.exists()

            loaded = SoarSubgoalStack.load_from(path)
            assert loaded.depth == 2
            assert loaded.peek().subgoal_id == "sg2"

    def test_load_nonexistent_returns_empty(self):
        """Loading from non-existent file returns empty stack."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "missing.jsonl"
            stack = SoarSubgoalStack.load_from(path)
            assert stack.is_empty

    def test_load_corrupt_returns_empty(self):
        """Corrupt file logs warning and returns empty stack."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "corrupt.jsonl"
            path.write_text("{ invalid json !!!", encoding="utf-8")
            stack = SoarSubgoalStack.load_from(path)
            assert stack.is_empty

    def test_persist_empty_stack(self):
        """Persisting empty stack writes valid file."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "stack.jsonl"
            stack = SoarSubgoalStack()
            stack.persist_to(path)
            assert path.exists()

            loaded = SoarSubgoalStack.load_from(path)
            assert loaded.is_empty

    def test_pop_then_persist(self):
        """After pop, persist reflects reduced stack."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "stack.jsonl"
            stack = SoarSubgoalStack(max_depth=10)
            stack.push(make_subgoal("sg1"))
            stack.push(make_subgoal("sg2"))

            stack.pop()
            stack.persist_to(path)

            loaded = SoarSubgoalStack.load_from(path)
            assert loaded.depth == 1
            assert loaded.peek().subgoal_id == "sg1"
