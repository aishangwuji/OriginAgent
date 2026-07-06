"""Tests for DesireStore.update increment_eval parameter."""
import pytest
import tempfile
from pathlib import Path

from OriginAgent.bdi.models import Desire, DesireStatus, DesirePriority
from OriginAgent.bdi.desire_store import DesireStore


@pytest.fixture
def tmp_store():
    with tempfile.TemporaryDirectory() as td:
        yield DesireStore(Path(td))


class TestIncrementEval:
    def test_increment_eval_true_increments_count(self, tmp_store):
        """increment_eval=True increments evaluation_count by 1."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.ACTIVE, evaluation_count=2)
        tmp_store.add(d)
        updated = tmp_store.update("d1", increment_eval=True, reasoning="test")
        assert updated is not None
        assert updated.evaluation_count == 3

    def test_increment_eval_false_default_no_change(self, tmp_store):
        """Default (increment_eval=False) does not change evaluation_count."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.ACTIVE, evaluation_count=2)
        tmp_store.add(d)
        updated = tmp_store.update("d1", reasoning="test")
        assert updated is not None
        assert updated.evaluation_count == 2  # unchanged

    def test_increment_eval_with_utility(self, tmp_store):
        """increment_eval=True works alongside utility update."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.ACTIVE, evaluation_count=1, utility=0.5)
        tmp_store.add(d)
        updated = tmp_store.update("d1", increment_eval=True, utility=0.8, reasoning="both")
        assert updated is not None
        assert updated.evaluation_count == 2
        assert updated.utility == 0.8

    def test_increment_eval_repeated(self, tmp_store):
        """Repeated increment_eval calls can reach >3 (stagnation threshold)."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.ACTIVE, evaluation_count=0)
        tmp_store.add(d)
        for i in range(5):
            tmp_store.update("d1", increment_eval=True, reasoning=f"cycle {i}")
        restored = tmp_store.get("d1")
        assert restored.evaluation_count == 5  # >3, stagnation detection can fire

    def test_backward_compatibility_no_increment(self, tmp_store):
        """Existing callers without increment_eval behave as before (no increment)."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.PENDING, evaluation_count=0)
        tmp_store.add(d)
        updated = tmp_store.update("d1", status=DesireStatus.ACTIVE, reasoning="activate")
        assert updated is not None
        assert updated.status == DesireStatus.ACTIVE
        assert updated.evaluation_count == 0  # no increment when increment_eval not passed
