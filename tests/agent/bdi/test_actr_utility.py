"""Tests for the ACT-R utility learning feature (Task 6).

Covers:
- Desire.utility field semantics and serialization
- DesireStore.update with utility parameter
- UtilityRewardBridge reward extraction and merging
- DeliberationEngine stochastic prioritization (softmax over utility)
- DeliberationEngine._update_utilities ACT-R formula application
- Backward compatibility (flag off by default, legacy JSON loads)
"""

import datetime
import random
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from OriginAgent.bdi.deliberation import DeliberationEngine
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.models import (
    Desire,
    DesirePriority,
    DesireStatus,
    now_iso,
)
from OriginAgent.bdi.utility_reward_bridge import UtilityRewardBridge


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

def make_desire(desire_id, utility=0.5, overdue=False, priority=DesirePriority.MEDIUM):
    """Helper: create a Desire with optional overdue deadline."""
    deadline = None
    if overdue:
        # Set deadline in the past to make it overdue
        past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1))
        deadline = past.isoformat()
    return Desire(
        desire_id=desire_id,
        owner_id="u",
        session_key="s",
        content=f"desire {desire_id}",
        status=DesireStatus.ACTIVE,
        priority=priority,
        utility=utility,
        deadline_at=deadline,
    )


# ---------------------------------------------------------------------------
# 1. Desire.utility field
# ---------------------------------------------------------------------------

class TestDesireUtilityField:
    def test_default_utility_is_0_5(self):
        """New Desire has utility=0.5 by default."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X")
        assert d.utility == 0.5

    def test_with_utility_clamps_high(self):
        """with_utility clamps values > 1.0 to 1.0."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X")
        updated = d.with_utility(1.5)
        assert updated.utility == 1.0

    def test_with_utility_clamps_low(self):
        """with_utility clamps values < 0.0 to 0.0."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X")
        updated = d.with_utility(-0.5)
        assert updated.utility == 0.0

    def test_with_utility_preserves_other_fields(self):
        """with_utility only changes utility and updated_at."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.ACTIVE, priority=DesirePriority.HIGH)
        updated = d.with_utility(0.8)
        assert updated.desire_id == "d1"
        assert updated.content == "X"
        assert updated.status == DesireStatus.ACTIVE
        assert updated.priority == DesirePriority.HIGH
        assert updated.utility == 0.8

    def test_from_json_legacy_data_without_utility(self):
        """Old JSON data without utility field defaults to 0.5."""
        legacy = {
            "desire_id": "d1",
            "owner_id": "u",
            "session_key": "s",
            "content": "X",
            "status": "pending",
            "priority": 50,
        }
        d = Desire.from_json(legacy)
        assert d.utility == 0.5

    def test_to_json_includes_utility(self):
        """to_json includes the utility field."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X", utility=0.7)
        data = d.to_json()
        assert "utility" in data
        assert data["utility"] == 0.7

    def test_serialization_roundtrip_with_utility(self):
        """Full roundtrip preserves utility."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X", utility=0.9)
        restored = Desire.from_json(d.to_json())
        assert restored.utility == 0.9


# ---------------------------------------------------------------------------
# 2. DesireStore.update with utility
# ---------------------------------------------------------------------------

class TestDesireStoreUpdateUtility:
    @pytest.fixture
    def tmp_store(self):
        with tempfile.TemporaryDirectory() as td:
            yield DesireStore(Path(td))

    def test_update_utility(self, tmp_store):
        """update(utility=...) persists the new utility value."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X")
        tmp_store.add(d)
        updated = tmp_store.update("d1", utility=0.8)
        assert updated is not None
        assert updated.utility == 0.8
        # Verify persisted
        restored = tmp_store.get("d1")
        assert restored is not None
        assert restored.utility == 0.8

    def test_update_utility_clamps(self, tmp_store):
        """update(utility=...) clamps to [0.0, 1.0]."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X")
        tmp_store.add(d)
        updated = tmp_store.update("d1", utility=2.0)
        assert updated.utility == 1.0

    def test_update_without_utility_preserves_value(self, tmp_store):
        """update() without utility param preserves existing utility."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X", utility=0.7)
        tmp_store.add(d)
        updated = tmp_store.update("d1", status=DesireStatus.ACTIVE, reasoning="test")
        assert updated is not None
        assert updated.utility == 0.7  # unchanged
        assert updated.status == DesireStatus.ACTIVE

    def test_update_utility_none_preserves_value(self, tmp_store):
        """update(utility=None) explicitly preserves existing utility."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X", utility=0.6)
        tmp_store.add(d)
        updated = tmp_store.update("d1", utility=None, reasoning="noop")
        assert updated.utility == 0.6


# ---------------------------------------------------------------------------
# 3. UtilityRewardBridge
# ---------------------------------------------------------------------------

class TestUtilityRewardBridge:
    def test_status_reward_satisfied(self):
        """SATISFIED desire gets +1.0 reward."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.SATISFIED)
        bridge = UtilityRewardBridge()
        rewards = bridge.extract_status_rewards([d])
        assert rewards == {"d1": 1.0}

    def test_status_reward_cancelled(self):
        """CANCELLED desire gets -0.5 reward."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.CANCELLED)
        bridge = UtilityRewardBridge()
        rewards = bridge.extract_status_rewards([d])
        assert rewards == {"d1": -0.5}

    def test_status_reward_stagnant(self):
        """ACTIVE desire with evaluation_count > 3 gets -0.1 reward."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.ACTIVE, evaluation_count=4)
        bridge = UtilityRewardBridge()
        rewards = bridge.extract_status_rewards([d])
        assert rewards == {"d1": -0.1}

    def test_status_reward_active_not_stagnant(self):
        """ACTIVE desire with evaluation_count <= 3 gets no reward."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.ACTIVE, evaluation_count=2)
        bridge = UtilityRewardBridge()
        rewards = bridge.extract_status_rewards([d])
        assert rewards == {}

    def test_status_reward_pending_no_reward(self):
        """PENDING desire gets no reward."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.PENDING)
        bridge = UtilityRewardBridge()
        rewards = bridge.extract_status_rewards([d])
        assert rewards == {}

    def test_reflection_reward_success(self):
        """ReflectionRecord with outcome_class='success' gives +confidence reward."""
        mock_ledger = MagicMock()
        mock_ledger.recent_reflections.return_value = [
            {
                "reflection_id": "r1",
                "session_key": "s",
                "created_at": now_iso(),
                "outcome_class": "success",
                "confidence": 0.9,
                "payload": {"evidence_refs": ["d1"]},
            }
        ]
        bridge = UtilityRewardBridge(audit_ledger=mock_ledger)
        rewards = bridge.extract_reflection_rewards(
            since="", session_keys=["s"], desire_id_lookup={"d1"},
        )
        assert "d1" in rewards
        assert rewards["d1"] == pytest.approx(0.9)

    def test_reflection_reward_failure(self):
        """ReflectionRecord with outcome_class='failure' gives -confidence reward."""
        mock_ledger = MagicMock()
        mock_ledger.recent_reflections.return_value = [
            {
                "reflection_id": "r1",
                "session_key": "s",
                "created_at": now_iso(),
                "outcome_class": "failure",
                "confidence": 0.8,
                "payload": {"evidence_refs": ["d1"]},
            }
        ]
        bridge = UtilityRewardBridge(audit_ledger=mock_ledger)
        rewards = bridge.extract_reflection_rewards(
            since="", session_keys=["s"], desire_id_lookup={"d1"},
        )
        assert rewards["d1"] == pytest.approx(-0.8)

    def test_reflection_reward_neutral_ignored(self):
        """ReflectionRecord with unknown outcome_class is ignored."""
        mock_ledger = MagicMock()
        mock_ledger.recent_reflections.return_value = [
            {
                "reflection_id": "r1",
                "session_key": "s",
                "created_at": now_iso(),
                "outcome_class": "partial",
                "confidence": 0.5,
                "payload": {"evidence_refs": ["d1"]},
            }
        ]
        bridge = UtilityRewardBridge(audit_ledger=mock_ledger)
        rewards = bridge.extract_reflection_rewards(
            since="", session_keys=["s"], desire_id_lookup={"d1"},
        )
        assert rewards == {}

    def test_reflection_reward_time_filter(self):
        """Reflections created before 'since' timestamp are filtered out."""
        mock_ledger = MagicMock()
        old_time = "2020-01-01T00:00:00+00:00"
        mock_ledger.recent_reflections.return_value = [
            {
                "reflection_id": "r1",
                "session_key": "s",
                "created_at": old_time,
                "outcome_class": "success",
                "confidence": 0.9,
                "payload": {"evidence_refs": ["d1"]},
            }
        ]
        bridge = UtilityRewardBridge(audit_ledger=mock_ledger)
        # 'since' is after the reflection's created_at
        rewards = bridge.extract_reflection_rewards(
            since="2025-01-01T00:00:00+00:00",
            session_keys=["s"],
            desire_id_lookup={"d1"},
        )
        assert rewards == {}

    def test_reflection_reward_no_desire_match(self):
        """Reflections that don't match any desire_id are dropped."""
        mock_ledger = MagicMock()
        mock_ledger.recent_reflections.return_value = [
            {
                "reflection_id": "r1",
                "session_key": "s",
                "created_at": now_iso(),
                "outcome_class": "success",
                "confidence": 0.9,
                "payload": {"evidence_refs": ["unknown_desire"]},
            }
        ]
        bridge = UtilityRewardBridge(audit_ledger=mock_ledger)
        rewards = bridge.extract_reflection_rewards(
            since="", session_keys=["s"], desire_id_lookup={"d1"},
        )
        assert rewards == {}

    def test_reflection_reward_no_ledger(self):
        """When audit_ledger is None, returns empty dict."""
        bridge = UtilityRewardBridge(audit_ledger=None)
        rewards = bridge.extract_reflection_rewards(
            since="", session_keys=["s"], desire_id_lookup={"d1"},
        )
        assert rewards == {}

    def test_merge_rewards_both_present(self):
        """When both sources have reward, uses weighted average 0.7*status + 0.3*reflection."""
        status_rewards = {"d1": 1.0}
        reflection_rewards = {"d1": 0.5}
        merged = UtilityRewardBridge.merge_rewards(status_rewards, reflection_rewards)
        assert merged["d1"] == pytest.approx(0.7 * 1.0 + 0.3 * 0.5)

    def test_merge_rewards_only_status(self):
        """When only status reward exists, uses it directly."""
        status_rewards = {"d1": 1.0}
        reflection_rewards = {}
        merged = UtilityRewardBridge.merge_rewards(status_rewards, reflection_rewards)
        assert merged["d1"] == pytest.approx(1.0)

    def test_merge_rewards_only_reflection(self):
        """When only reflection reward exists, uses it directly."""
        status_rewards = {}
        reflection_rewards = {"d1": -0.5}
        merged = UtilityRewardBridge.merge_rewards(status_rewards, reflection_rewards)
        assert merged["d1"] == pytest.approx(-0.5)

    def test_merge_rewards_empty(self):
        """Empty inputs produce empty output."""
        merged = UtilityRewardBridge.merge_rewards({}, {})
        assert merged == {}


# ---------------------------------------------------------------------------
# 4. Stochastic prioritization
# ---------------------------------------------------------------------------

class TestStochasticPrioritize:
    @pytest.fixture
    def store(self):
        with tempfile.TemporaryDirectory() as td:
            yield DesireStore(Path(td))

    @pytest.fixture
    def engine(self, store):
        """Engine with ACT-R enabled but no LLM provider needed."""
        return DeliberationEngine(
            workspace=Path(tempfile.gettempdir()),
            store=store,
            provider=MagicMock(),
            model="test",
            use_actr_utility=True,
            selection_temperature=0.1,
            max_desires_per_cycle=5,
        )

    def test_single_desire_returns_directly(self, engine):
        """Single desire is returned without random sampling."""
        d = make_desire("d1")
        result = engine._stochastic_prioritize([d])
        assert len(result) == 1
        assert result[0].desire_id == "d1"

    def test_empty_list_returns_empty(self, engine):
        """Empty desire list returns empty."""
        result = engine._stochastic_prioritize([])
        assert result == []

    def test_overdue_desires_prioritized(self, engine):
        """Overdue desires are always at the top of the list."""
        random.seed(42)
        overdue1 = make_desire("overdue1", utility=0.1, overdue=True)
        overdue2 = make_desire("overdue2", utility=0.2, overdue=True)
        normal1 = make_desire("normal1", utility=0.9)
        normal2 = make_desire("normal2", utility=0.8)

        result = engine._stochastic_prioritize([normal1, normal2, overdue1, overdue2])
        # First two should be the overdue ones
        overdue_ids = {result[0].desire_id, result[1].desire_id}
        assert overdue_ids == {"overdue1", "overdue2"}

    def test_higher_utility_selected_more_often(self, engine):
        """With low temperature, high-utility desire is selected more often."""
        random.seed(42)
        high_u = make_desire("high", utility=0.9)
        low_u = make_desire("low", utility=0.1)

        # Run many trials and count selections
        high_count = 0
        for _ in range(1000):
            result = engine._stochastic_prioritize([high_u, low_u])
            if result[0].desire_id == "high":
                high_count += 1

        # With temperature=0.1, high utility should be selected >90% of the time
        assert high_count > 900

    def test_temperature_near_zero_is_greedy(self, store):
        """Very low temperature makes selection near-deterministic (greedy)."""
        engine = DeliberationEngine(
            workspace=Path(tempfile.gettempdir()),
            store=store,
            provider=MagicMock(),
            model="test",
            use_actr_utility=True,
            selection_temperature=0.001,  # near-zero temperature
            max_desires_per_cycle=3,
        )
        random.seed(42)
        d1 = make_desire("d1", utility=0.3)
        d2 = make_desire("d2", utility=0.9)
        d3 = make_desire("d3", utility=0.1)

        # With near-zero temp, highest utility always comes first
        for _ in range(100):
            result = engine._stochastic_prioritize([d1, d2, d3])
            assert result[0].desire_id == "d2"  # highest utility

    def test_max_desires_cap(self, store):
        """Result is capped at max_desires_per_cycle."""
        engine = DeliberationEngine(
            workspace=Path(tempfile.gettempdir()),
            store=store,
            provider=MagicMock(),
            model="test",
            use_actr_utility=True,
            selection_temperature=1.0,
            max_desires_per_cycle=3,
        )
        random.seed(42)
        desires = [make_desire(f"d{i}", utility=0.5) for i in range(10)]
        result = engine._stochastic_prioritize(desires)
        assert len(result) == 3


class TestPrioritizeFeatureFlag:
    @pytest.fixture
    def store(self):
        with tempfile.TemporaryDirectory() as td:
            yield DesireStore(Path(td))

    def test_flag_off_uses_deterministic_sort(self, store):
        """When use_actr_utility=False, _prioritize uses deterministic sort."""
        engine = DeliberationEngine(
            workspace=Path(tempfile.gettempdir()),
            store=store,
            provider=MagicMock(),
            model="test",
            use_actr_utility=False,
        )
        d1 = make_desire("d1", utility=0.1, priority=DesirePriority.LOW)
        d2 = make_desire("d2", utility=0.9, priority=DesirePriority.HIGH)

        result = engine._prioritize([d1, d2])
        # HIGH priority should come first, regardless of utility
        assert result[0].desire_id == "d2"

    def test_flag_on_uses_stochastic(self, store):
        """When use_actr_utility=True, _prioritize delegates to stochastic."""
        engine = DeliberationEngine(
            workspace=Path(tempfile.gettempdir()),
            store=store,
            provider=MagicMock(),
            model="test",
            use_actr_utility=True,
        )
        random.seed(42)
        d1 = make_desire("d1", utility=0.1, priority=DesirePriority.HIGH)
        d2 = make_desire("d2", utility=0.9, priority=DesirePriority.LOW)

        # With stochastic, result may not follow priority order
        result = engine._prioritize([d1, d2])
        assert len(result) == 2
        # Just verify it returns a valid result (stochastic may put either first)


# ---------------------------------------------------------------------------
# 5. _update_utilities (ACT-R formula)
# ---------------------------------------------------------------------------

class TestUpdateUtilities:
    @pytest.fixture
    def store(self):
        with tempfile.TemporaryDirectory() as td:
            yield DesireStore(Path(td))

    @pytest.fixture
    def bridge(self):
        return UtilityRewardBridge(audit_ledger=MagicMock())

    @pytest.fixture
    def engine_with_actr(self, store, bridge):
        return DeliberationEngine(
            workspace=Path(tempfile.gettempdir()),
            store=store,
            provider=MagicMock(),
            model="test",
            use_actr_utility=True,
            utility_learning_rate=0.2,
            reward_bridge=bridge,
        )

    @pytest.fixture
    def engine_without_actr(self, store, bridge):
        return DeliberationEngine(
            workspace=Path(tempfile.gettempdir()),
            store=store,
            provider=MagicMock(),
            model="test",
            use_actr_utility=False,
            reward_bridge=bridge,
        )

    @pytest.mark.asyncio
    async def test_skipped_when_flag_off(self, engine_without_actr, store):
        """_update_utilities is a no-op when use_actr_utility=False."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.SATISFIED, utility=0.5)
        store.add(d)
        original_utility = d.utility

        await engine_without_actr._update_utilities([d], ["d1"])

        restored = store.get("d1")
        assert restored.utility == original_utility  # unchanged

    @pytest.mark.asyncio
    async def test_skipped_when_no_bridge(self, store):
        """_update_utilities is a no-op when reward_bridge is None."""
        engine = DeliberationEngine(
            workspace=Path(tempfile.gettempdir()),
            store=store,
            provider=MagicMock(),
            model="test",
            use_actr_utility=True,
            reward_bridge=None,
        )
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.SATISFIED, utility=0.5)
        store.add(d)

        await engine._update_utilities([d], ["d1"])

        restored = store.get("d1")
        assert restored.utility == 0.5  # unchanged

    @pytest.mark.asyncio
    async def test_satisfied_increases_utility(self, engine_with_actr, store):
        """SATISFIED desire gets utility boost."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.SATISFIED, utility=0.5)
        store.add(d)

        await engine_with_actr._update_utilities([d], ["d1"])

        restored = store.get("d1")
        # R = 1.0, U_new = 0.5 + 0.2*(1.0 - 0.5) = 0.6
        assert restored.utility == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_cancelled_decreases_utility(self, engine_with_actr, store):
        """CANCELLED desire gets utility penalty."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.CANCELLED, utility=0.5)
        store.add(d)

        await engine_with_actr._update_utilities([d], ["d1"])

        restored = store.get("d1")
        # R = -0.5, U_new = 0.5 + 0.2*(-0.5 - 0.5) = 0.5 - 0.2 = 0.3
        assert restored.utility == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_stagnant_decreases_utility(self, engine_with_actr, store):
        """Stagnant ACTIVE desire (eval_count > 3) gets small penalty."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.ACTIVE, utility=0.5, evaluation_count=5)
        store.add(d)

        await engine_with_actr._update_utilities([d], [])

        restored = store.get("d1")
        # R = -0.1, U_new = 0.5 + 0.2*(-0.1 - 0.5) = 0.5 - 0.12 = 0.38
        assert restored.utility == pytest.approx(0.38)

    @pytest.mark.asyncio
    async def test_last_utility_update_at_refreshed(self, engine_with_actr, store):
        """_last_utility_update_at is updated after _update_utilities."""
        assert engine_with_actr._last_utility_update_at == ""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.SATISFIED)
        store.add(d)

        await engine_with_actr._update_utilities([d], ["d1"])

        assert engine_with_actr._last_utility_update_at != ""

    @pytest.mark.asyncio
    async def test_no_rewards_still_updates_timestamp(self, engine_with_actr, store):
        """Even with no rewards, timestamp is updated."""
        d = Desire(desire_id="d1", owner_id="u", session_key="s", content="X",
                    status=DesireStatus.ACTIVE, evaluation_count=1)
        store.add(d)

        await engine_with_actr._update_utilities([d], [])

        assert engine_with_actr._last_utility_update_at != ""
        # Utility should be unchanged
        restored = store.get("d1")
        assert restored.utility == 0.5


# ---------------------------------------------------------------------------
# 6. Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    @pytest.fixture
    def store(self):
        with tempfile.TemporaryDirectory() as td:
            yield DesireStore(Path(td))

    def test_default_engine_has_actr_disabled(self, store):
        """Default DeliberationEngine has use_actr_utility=False."""
        engine = DeliberationEngine(
            workspace=Path(tempfile.gettempdir()),
            store=store,
            provider=MagicMock(),
            model="test",
        )
        assert engine._use_actr_utility is False
        assert engine._utility_learning_rate == 0.2
        assert engine._selection_temperature == 0.1
        assert engine._reward_bridge is None
        assert engine._last_utility_update_at == ""

    def test_prioritize_deterministic_by_default(self, store):
        """Default _prioritize uses deterministic sort (not stochastic)."""
        engine = DeliberationEngine(
            workspace=Path(tempfile.gettempdir()),
            store=store,
            provider=MagicMock(),
            model="test",
        )
        d1 = make_desire("d1", utility=0.1, priority=DesirePriority.HIGH)
        d2 = make_desire("d2", utility=0.9, priority=DesirePriority.LOW)

        result = engine._prioritize([d1, d2])
        # HIGH priority first, regardless of utility
        assert result[0].desire_id == "d1"

    def test_legacy_desire_json_loads_without_utility(self):
        """Desire JSON without utility field loads correctly."""
        legacy_json = {
            "desire_id": "d1",
            "owner_id": "u",
            "session_key": "s",
            "content": "legacy",
            "status": "pending",
            "priority": 50,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        d = Desire.from_json(legacy_json)
        assert d.utility == 0.5  # default
        assert d.desire_id == "d1"
