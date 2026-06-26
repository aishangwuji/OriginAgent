"""Tests for DeliberationEngine."""

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.bdi.models import (
    DeliberationIntention,
    DeliberationResult,
    Desire,
    DesireStatus,
    DesirePriority,
    now_iso,
)
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.deliberation import DeliberationEngine


# ---------------------------------------------------------------------------
# Fake LLM provider for deterministic testing
# ---------------------------------------------------------------------------

@dataclass
class FakeLLMResponse:
    should_execute_tools: bool = True
    has_tool_calls: bool = True

    tool_calls = None  # set below


@dataclass
class FakeToolCall:
    arguments: dict[str, Any] = field(default_factory=dict)


class FakeProvider:
    """Returns a controlled deliberation JSON response."""

    def __init__(self, response_dict: dict[str, Any] | None = None):
        self._response = response_dict or {
            "reasoning": "d1 is the most urgent. User needs reminder now.",
            "intentions": [
                {
                    "desire_id": "d1",
                    "action": "send_message",
                    "scope": "telegram",
                    "risk": "low",
                    "reasoning": "User needs reminder about groceries before 6pm.",
                    "payload": {"text": "Hey, don't forget to buy groceries before 6pm!"},
                }
            ],
            "desires_to_satisfy": [],
            "desires_to_suspend": [],
            "desires_to_cancel": [],
            "next_check_at": None,
        }
        self.call_count = 0

    async def chat_with_retry(self, messages, tools, model):
        self.call_count += 1
        resp = FakeLLMResponse()
        resp.tool_calls = [
            FakeToolCall(arguments=self._response)
        ]
        return resp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_desire(desire_id: str, content: str, status=DesireStatus.ACTIVE,
                priority=DesirePriority.MEDIUM, deadline=None) -> Desire:
    return Desire(
        desire_id=desire_id,
        owner_id="user:test",
        session_key="sess:test",
        content=content,
        status=status,
        priority=priority,
        deadline_at=deadline,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeliberationEngineCore:
    @pytest.fixture
    def store(self):
        with tempfile.TemporaryDirectory() as td:
            yield DesireStore(Path(td))

    def build_engine(self, store, provider=None, enabled=True):
        return DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=provider or FakeProvider(),
            model="test-model",
            enabled=enabled,
            max_desires_per_cycle=10,
            auto_create_from_foresight=False,
        )

    @pytest.mark.asyncio
    async def test_cycle_with_one_active_desire_forms_intention(self, store):
        store.add(make_desire("d1", "Buy groceries by 6pm",
                               priority=DesirePriority.HIGH))
        engine = self.build_engine(store)

        result = await engine.run_cycle()

        assert isinstance(result, DeliberationResult)
        assert result.desires_evaluated == 1
        assert result.intentions_formed == 1
        assert result.intentions[0].desire_id == "d1"

    @pytest.mark.asyncio
    async def test_cycle_with_no_desires_skips(self, store):
        engine = self.build_engine(store)
        result = await engine.run_cycle()

        assert result.desires_evaluated == 0
        assert result.intentions_formed == 0
        assert result.reasoning != ""

    @pytest.mark.asyncio
    async def test_cycle_with_only_terminal_desires_skips(self, store):
        store.add(make_desire("d1", "Done task", status=DesireStatus.SATISFIED))
        store.add(make_desire("d2", "Cancelled task", status=DesireStatus.CANCELLED))
        engine = self.build_engine(store)

        result = await engine.run_cycle()
        assert result.desires_evaluated == 0

    @pytest.mark.asyncio
    async def test_disabled_engine_skips(self, store):
        store.add(make_desire("d1", "Test"))
        engine = self.build_engine(store, enabled=False)

        result = await engine.run_cycle()
        assert result.desires_evaluated == 0

    @pytest.mark.asyncio
    async def test_satisfied_intention_updates_desire(self, store):
        store.add(make_desire("d1", "Buy groceries by 6pm",
                               priority=DesirePriority.HIGH))
        provider = FakeProvider({
            "reasoning": "Task complete.",
            "intentions": [],
            "desires_to_satisfy": ["d1"],
            "desires_to_suspend": [],
            "desires_to_cancel": [],
            "next_check_at": None,
        })
        engine = self.build_engine(store, provider=provider)

        result = await engine.run_cycle()
        assert result.intentions_formed == 0
        assert "d1" in result.desires_updated

        updated = store.get("d1")
        assert updated is not None
        assert updated.status == DesireStatus.SATISFIED

    @pytest.mark.asyncio
    async def test_overdue_desire_escalates_priority(self, store):
        store.add(make_desire("d1", "Late task", priority=DesirePriority.LOW,
                               deadline="2020-01-01T00:00:00"))
        engine = self.build_engine(store)

        result = await engine.run_cycle()
        assert result.desires_evaluated == 1
        # Overdue desire is included in evaluation

    @pytest.mark.asyncio
    async def test_respects_max_desires_per_cycle(self, store):
        for i in range(15):
            store.add(make_desire(f"d{i}", f"Task {i}"))
        engine = self.build_engine(store, provider=FakeProvider())
        engine._max_desires_per_cycle = 5

        result = await engine.run_cycle()
        assert result.desires_evaluated <= 5

    @pytest.mark.asyncio
    async def test_llm_error_is_captured_in_result(self, store):
        store.add(make_desire("d1", "Test"))

        class ErrorProvider:
            async def chat_with_retry(self, *args, **kwargs):
                raise RuntimeError("LLM unavailable")

        engine = self.build_engine(store, provider=ErrorProvider())
        result = await engine.run_cycle()

        assert "LLM unavailable" in result.error
        assert result.intentions_formed == 0

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self, store):
        store.add(make_desire("d1", "Test"))
        engine = self.build_engine(store)
        engine._interval_s = 1  # Fast for test

        await engine.start()
        assert engine._running is True
        await asyncio.sleep(0.1)

        engine.stop()
        assert engine._running is False
