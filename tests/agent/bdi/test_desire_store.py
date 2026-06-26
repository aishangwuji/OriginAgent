"""Tests for DesireStore JSONL persistence."""

import pytest
import tempfile
from pathlib import Path

from OriginAgent.bdi.models import Desire, DesireStatus, DesirePriority, now_iso
from OriginAgent.bdi.desire_store import DesireStore


@pytest.fixture
def tmp_store():
    with tempfile.TemporaryDirectory() as td:
        workspace = Path(td)
        store = DesireStore(workspace)
        yield store


class TestDesireStoreBasic:
    def test_add_and_get(self, tmp_store: DesireStore):
        d = Desire(
            desire_id="d1",
            owner_id="user:test",
            session_key="sess:test",
            content="Write quarterly report",
            status=DesireStatus.PENDING,
            priority=DesirePriority.HIGH,
        )
        tmp_store.add(d)
        restored = tmp_store.get("d1")
        assert restored is not None
        assert restored.content == "Write quarterly report"
        assert restored.priority == DesirePriority.HIGH

    def test_get_missing_returns_none(self, tmp_store: DesireStore):
        assert tmp_store.get("nonexistent") is None

    def test_add_duplicate_raises(self, tmp_store: DesireStore):
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X")
        tmp_store.add(d)
        with pytest.raises(ValueError, match="already exists"):
            tmp_store.add(d)

    def test_update_transitions_status(self, tmp_store: DesireStore):
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X")
        tmp_store.add(d)
        updated = tmp_store.update("d1", status=DesireStatus.ACTIVE, reasoning="Let's do it")
        assert updated is not None
        assert updated.status == DesireStatus.ACTIVE
        assert updated.last_reasoning == "Let's do it"

        # Verify persisted
        restored = tmp_store.get("d1")
        assert restored is not None
        assert restored.status == DesireStatus.ACTIVE

    def test_update_invalid_transition_raises(self, tmp_store: DesireStore):
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X")
        tmp_store.add(d)
        with pytest.raises(ValueError, match="Cannot transition"):
            tmp_store.update("d1", status=DesireStatus.SATISFIED)

    def test_update_satisfied_sets_satisfied_at(self, tmp_store: DesireStore):
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.ACTIVE)
        tmp_store.add(d)
        updated = tmp_store.update("d1", status=DesireStatus.SATISFIED)
        assert updated.status == DesireStatus.SATISFIED
        assert updated.satisfied_at is not None

    def test_update_missing_returns_none(self, tmp_store: DesireStore):
        assert tmp_store.update("nonexistent", status=DesireStatus.ACTIVE) is None


class TestDesireStoreQuery:
    @pytest.fixture
    def populated(self, tmp_store: DesireStore) -> DesireStore:
        for i, (status, pri) in enumerate([
            (DesireStatus.PENDING, DesirePriority.HIGH),
            (DesireStatus.ACTIVE, DesirePriority.CRITICAL),
            (DesireStatus.ACTIVE, DesirePriority.LOW),
            (DesireStatus.SATISFIED, DesirePriority.MEDIUM),
            (DesireStatus.SUSPENDED, DesirePriority.HIGH),
            (DesireStatus.PENDING, DesirePriority.LOW),
        ]):
            tmp_store.add(Desire(
                desire_id=f"d{i}",
                owner_id="u",
                session_key="s",
                content=f"Task {i}",
                status=status,
                priority=pri,
            ))
        return tmp_store

    def test_list_active(self, populated: DesireStore):
        active = populated.list_active()
        assert len(active) == 2  # d1 (ACTIVE/CRITICAL) and d2 (ACTIVE/LOW)

    def test_list_deliberable(self, populated: DesireStore):
        deliberable = populated.list_deliberable()
        assert len(deliberable) == 4  # d0, d1, d2, d5 (PENDING + ACTIVE)

    def test_list_by_status(self, populated: DesireStore):
        pending = populated.list_by_status(DesireStatus.PENDING)
        assert len(pending) == 2

    def test_count_by_status(self, populated: DesireStore):
        counts = populated.count_by_status()
        assert counts[DesireStatus.PENDING] == 2
        assert counts[DesireStatus.ACTIVE] == 2
        assert counts[DesireStatus.SATISFIED] == 1
        assert counts[DesireStatus.SUSPENDED] == 1

    def test_sort_by_priority_desc(self, populated: DesireStore):
        active = populated.list_deliberable()
        # Should be sorted by priority descending (CRITICAL first)
        assert active[0].priority == DesirePriority.CRITICAL

    def test_find_by_source_foresight(self, tmp_store: DesireStore):
        d = Desire(
            desire_id="d_fs",
            owner_id="u",
            session_key="s",
            content="From foresight",
            source_foresight_id="fs_abc",
        )
        tmp_store.add(d)
        found = tmp_store.find_by_source_foresight("fs_abc")
        assert len(found) == 1
        assert found[0].desire_id == "d_fs"

    def test_find_by_source_foresight_empty(self, tmp_store: DesireStore):
        assert tmp_store.find_by_source_foresight("no_match") == []


class TestDesireStoreAtomicWrite:
    def test_crash_safety(self, tmp_store: DesireStore):
        """Desires survive a simulated crash (teardown + reload)."""
        d = Desire(desire_id="d_crash", owner_id="u", session_key="s", content="Survive")
        tmp_store.add(d)

        # Simulate reload -- new store pointing at same workspace
        store2 = DesireStore(tmp_store.workspace)
        restored = store2.get("d_crash")
        assert restored is not None
        assert restored.content == "Survive"
