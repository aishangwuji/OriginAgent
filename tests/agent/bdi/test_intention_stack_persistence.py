"""Tests for IntentionStack persistence."""
import json
import pytest
import tempfile
from pathlib import Path

from OriginAgent.bdi.models import IntentionStack, StackFrame, DeliberationIntention, now_iso


def make_frame(desire_id: str = "d1") -> StackFrame:
    return StackFrame(
        desire_id=desire_id,
        intention=DeliberationIntention(
            desire_id=desire_id,
            action="send_message",
            scope="system",
        ),
        suspended_at=now_iso(),
        suspend_reason="test",
        original_priority=50,
    )


class TestIntentionStackPersistence:
    def test_load_from_nonexistent_file_returns_empty(self):
        """Loading from a non-existent file returns an empty stack."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "stack.jsonl"
            stack = IntentionStack.load_from(path)
            assert stack.is_empty
            assert stack.depth == 0

    def test_persist_then_load_roundtrip(self):
        """Persisting and loading preserves stack state."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "stack.jsonl"
            stack = IntentionStack(max_depth=10)
            stack.push(make_frame("d1"))
            stack.push(make_frame("d2"))

            stack.persist_to(path)
            assert path.exists()

            loaded = IntentionStack.load_from(path)
            assert loaded.depth == 2
            candidates = loaded.list_resumable()
            assert len(candidates) == 2

    def test_persist_empty_stack(self):
        """Persisting an empty stack writes a valid file."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "stack.jsonl"
            stack = IntentionStack()
            stack.persist_to(path)
            assert path.exists()

            loaded = IntentionStack.load_from(path)
            assert loaded.is_empty

    def test_load_from_corrupt_file_returns_empty(self):
        """Corrupt file logs warning and returns empty stack."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "stack.jsonl"
            path.write_text("{ invalid json !!!", encoding="utf-8")

            stack = IntentionStack.load_from(path)
            assert stack.is_empty  # graceful degradation

    def test_pop_then_persist(self):
        """After pop, persist reflects the reduced stack."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "stack.jsonl"
            stack = IntentionStack(max_depth=10)
            stack.push(make_frame("d1"))
            stack.push(make_frame("d2"))

            stack.pop()  # remove d2
            stack.persist_to(path)

            loaded = IntentionStack.load_from(path)
            assert loaded.depth == 1
            assert loaded.list_resumable()[0].desire_id == "d1"
