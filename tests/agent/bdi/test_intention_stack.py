"""Tests for IntentionStack — nested suspend/resume for BDI intentions."""

import pytest

from OriginAgent.bdi.models import (
    Desire,
    DesireStatus,
    DesirePriority,
    DeliberationIntention,
    IntentionStack,
    StackFrame,
    ResumeCandidate,
    now_iso,
)


def make_desire(desire_id: str, content: str, **kw) -> Desire:
    return Desire(
        desire_id=desire_id,
        owner_id="user:test",
        session_key="sess:test",
        content=content,
        **kw,
    )


def make_intent(desire_id: str, action: str = "send_message") -> DeliberationIntention:
    return DeliberationIntention(
        desire_id=desire_id,
        action=action,
        scope="test",
        reasoning="Test intention",
    )


class TestStackFrame:
    def test_immutable(self):
        frame = StackFrame(
            desire_id="d1",
            intention=make_intent("d1"),
            suspended_at=now_iso(),
            suspend_reason="Interrupted by higher-priority task",
        )
        with pytest.raises(Exception):
            frame.desire_id = "changed"

    def test_serialization_roundtrip(self):
        frame = StackFrame(
            desire_id="d1",
            intention=make_intent("d1", "exec"),
            suspended_at=now_iso(),
            suspend_reason="User asked to open the door",
        )
        data = frame.to_json()
        restored = StackFrame.from_json(data)
        assert restored.desire_id == "d1"
        assert restored.suspend_reason == "User asked to open the door"
        assert restored.intention.action == "exec"


class TestIntentionStack:
    @pytest.fixture
    def stack(self):
        return IntentionStack(max_depth=5)

    def test_push_and_peek(self, stack):
        frame = StackFrame(
            desire_id="d1", intention=make_intent("d1"),
            suspended_at=now_iso(), suspend_reason="Interrupted",
        )
        stack.push(frame)
        assert stack.depth == 1
        assert stack.peek().desire_id == "d1"

    def test_pop_returns_most_recent(self, stack):
        f1 = StackFrame(desire_id="d1", intention=make_intent("d1"),
                         suspended_at=now_iso(), suspend_reason="A")
        f2 = StackFrame(desire_id="d2", intention=make_intent("d2"),
                         suspended_at=now_iso(), suspend_reason="B")
        stack.push(f1)
        stack.push(f2)
        popped = stack.pop()
        assert popped.desire_id == "d2"
        assert stack.depth == 1
        assert stack.peek().desire_id == "d1"

    def test_pop_empty_returns_none(self, stack):
        assert stack.pop() is None

    def test_list_resumable_returns_lifo_order(self, stack):
        f1 = StackFrame(desire_id="d1", intention=make_intent("d1"),
                         suspended_at=now_iso(), suspend_reason="First")
        f2 = StackFrame(desire_id="d2", intention=make_intent("d2"),
                         suspended_at=now_iso(), suspend_reason="Second")
        stack.push(f1)
        stack.push(f2)
        candidates = stack.list_resumable()
        assert len(candidates) == 2
        assert candidates[0].desire_id == "d2"
        assert candidates[0].stack_position == 1

    def test_max_depth_raises(self, stack):
        for i in range(5):
            stack.push(StackFrame(
                desire_id=f"d{i}", intention=make_intent(f"d{i}"),
                suspended_at=now_iso(), suspend_reason="Test",
            ))
        with pytest.raises(OverflowError, match="max depth"):
            stack.push(StackFrame(
                desire_id="d_overflow", intention=make_intent("d_overflow"),
                suspended_at=now_iso(), suspend_reason="Overflow",
            ))

    def test_active_frame_when_non_empty(self, stack):
        f = StackFrame(desire_id="d_active", intention=make_intent("d_active"),
                        suspended_at=now_iso(), suspend_reason="Active")
        stack.push(f)
        assert stack.active_frame is not None
        assert stack.active_frame.desire_id == "d_active"

    def test_active_frame_when_empty(self, stack):
        assert stack.active_frame is None

    def test_serialization_full_stack(self, stack):
        f1 = StackFrame(desire_id="d1", intention=make_intent("d1"),
                         suspended_at=now_iso(), suspend_reason="A")
        f2 = StackFrame(desire_id="d2", intention=make_intent("d2"),
                         suspended_at=now_iso(), suspend_reason="B")
        stack.push(f1)
        stack.push(f2)
        data = stack.to_json()
        restored = IntentionStack.from_json(data)
        assert restored.depth == 2
        assert restored.pop().desire_id == "d2"
        assert restored.pop().desire_id == "d1"

    def test_find_by_desire_id(self, stack):
        f1 = StackFrame(desire_id="d1", intention=make_intent("d1"),
                         suspended_at=now_iso(), suspend_reason="A")
        f2 = StackFrame(desire_id="d2", intention=make_intent("d2"),
                         suspended_at=now_iso(), suspend_reason="B")
        stack.push(f1)
        stack.push(f2)
        found = stack.find_by_desire_id("d1")
        assert found is not None
        assert found.suspend_reason == "A"

    def test_find_missing_returns_none(self, stack):
        assert stack.find_by_desire_id("nonexistent") is None
