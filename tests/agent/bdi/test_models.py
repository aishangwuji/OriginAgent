"""Tests for BDI data models."""

import pytest

from OriginAgent.bdi.models import (
    ALLOWED_DESIRE_TRANSITIONS,
    BDICycleRecord,
    DeliberationIntention,
    DeliberationResult,
    Desire,
    DesirePriority,
    DesireStatus,
    now_iso,
)


class TestDesireStatus:
    def test_has_five_states(self):
        states = list(DesireStatus)
        assert len(states) == 5
        assert DesireStatus.PENDING in states
        assert DesireStatus.ACTIVE in states
        assert DesireStatus.SUSPENDED in states
        assert DesireStatus.SATISFIED in states
        assert DesireStatus.CANCELLED in states

    def test_allowed_transitions_are_valid(self):
        for from_state, to_states in ALLOWED_DESIRE_TRANSITIONS.items():
            for to_state in to_states:
                assert to_state in DesireStatus


class TestDesirePriority:
    def test_ordering(self):
        assert DesirePriority.LOW.value < DesirePriority.MEDIUM.value
        assert DesirePriority.MEDIUM.value < DesirePriority.HIGH.value
        assert DesirePriority.HIGH.value < DesirePriority.CRITICAL.value


class TestDesire:
    def test_immutable(self):
        d = Desire(
            desire_id="d1",
            owner_id="user:test",
            session_key="sess:test",
            content="Review the quarterly report by Friday",
            status=DesireStatus.PENDING,
            priority=DesirePriority.MEDIUM,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        with pytest.raises(Exception):
            d.content = "changed"  # type: ignore

    def test_serialization_roundtrip(self):
        d = Desire(
            desire_id="d1",
            owner_id="user:test",
            session_key="sess:test",
            content="Buy groceries tomorrow",
            status=DesireStatus.ACTIVE,
            priority=DesirePriority.HIGH,
            created_at=now_iso(),
            updated_at=now_iso(),
            deadline_at="2026-06-27T18:00:00",
            source_foresight_id="fs_abc",
            source_message_ids=["msg_1"],
            dependencies=["d0"],
            constraints=["must be organic"],
            metadata={"tags": ["personal", "urgent"]},
        )
        data = d.to_json()
        restored = Desire.from_json(data)
        assert restored.desire_id == d.desire_id
        assert restored.content == d.content
        assert restored.priority == DesirePriority.HIGH
        assert restored.deadline_at == "2026-06-27T18:00:00"
        assert restored.source_foresight_id == "fs_abc"
        assert restored.dependencies == ["d0"]

    def test_can_transition(self):
        d = Desire(
            desire_id="d1",
            owner_id="user:test",
            session_key="sess:test",
            content="Test",
            status=DesireStatus.PENDING,
            priority=DesirePriority.LOW,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        assert d.can_transition_to(DesireStatus.ACTIVE) is True
        assert d.can_transition_to(DesireStatus.CANCELLED) is True
        assert d.can_transition_to(DesireStatus.SATISFIED) is False  # PENDING -> SATISFIED not allowed

    def test_transition_returns_new_instance(self):
        d = Desire(
            desire_id="d1",
            owner_id="user:test",
            session_key="sess:test",
            content="Test",
            status=DesireStatus.PENDING,
            priority=DesirePriority.LOW,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        d2 = d.transition_to(DesireStatus.ACTIVE)
        assert d2 is not d
        assert d2.status == DesireStatus.ACTIVE
        assert d.status == DesireStatus.PENDING  # original unchanged


class TestDeliberationIntention:
    def test_immutable(self):
        intent = DeliberationIntention(
            desire_id="d1",
            action="send_message",
            scope="telegram",
            trigger="deliberation",
            risk="low",
            reasoning="User needs reminder about groceries",
            payload={"text": "Remember to buy groceries tomorrow"},
        )
        with pytest.raises(Exception):
            intent.action = "changed"  # type: ignore


class TestDeliberationResult:
    def test_has_required_fields(self):
        result = DeliberationResult(
            cycle_id="cycle_1",
            started_at=now_iso(),
            finished_at=now_iso(),
            desires_evaluated=3,
            intentions=[],
            reasoning="No urgent desires found.",
        )
        assert result.intentions_formed == 0
        assert result.desires_evaluated == 3

    def test_serialization_with_intentions(self):
        intent = DeliberationIntention(
            desire_id="d1",
            action="send_message",
            scope="telegram",
            trigger="deliberation",
            risk="low",
            reasoning="Test",
            payload={},
        )
        result = DeliberationResult(
            cycle_id="cycle_1",
            started_at=now_iso(),
            finished_at=now_iso(),
            desires_evaluated=1,
            intentions=[intent],
            reasoning="Acting on desire d1.",
        )
        data = result.to_json()
        restored = DeliberationResult.from_json(data)
        assert len(restored.intentions) == 1
        assert restored.intentions[0].desire_id == "d1"


class TestBDICycleRecord:
    def test_serialization(self):
        record = BDICycleRecord(
            cycle_id="cycle_1",
            started_at=now_iso(),
            finished_at=now_iso(),
            status="completed",
            desires_before=5,
            intentions_formed=2,
            intentions_executed=1,
            model_used="anthropic/claude-sonnet-4-6",
            token_usage={"input": 500, "output": 200},
        )
        data = record.to_json()
        assert data["status"] == "completed"
        assert data["token_usage"]["input"] == 500
