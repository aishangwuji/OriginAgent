"""Tests for BDI deliberation cycle logging — `bdi.cycle.complete` event.

验证 `DeliberationEngine.run_cycle` 在成功完成时记录 `bdi.cycle.complete`
事件，attrs 包含汇总字段：
- cycle_id
- desires_evaluated
- cached_intentions
- llm_desires
- intentions_emitted
- elapsed_ms
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from OriginAgent.bdi.deliberation import DeliberationEngine
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.models import (
    Desire,
    DesirePriority,
    DesireStatus,
)


# ---------------------------------------------------------------------------
# Fake LLM provider for deterministic testing
# ---------------------------------------------------------------------------

@dataclass
class FakeLLMResponse:
    should_execute_tools: bool = True
    has_tool_calls: bool = True
    tool_calls = None


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
                    "reasoning": "User needs reminder.",
                    "payload": {"text": "Hey, don't forget!"},
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


def _find_log_call(mock_log_event, event_name: str):
    """从 mock_log_event.call_args_list 中找出指定事件名的首次调用。"""
    for call in mock_log_event.call_args_list:
        if call.args and call.args[0] == event_name:
            return call
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeliberationCycleCompleteLogging:
    @pytest.fixture
    def store(self):
        with tempfile.TemporaryDirectory() as td:
            yield DesireStore(Path(td))

    def build_engine(self, store, provider=None):
        return DeliberationEngine(
            workspace=store.workspace,
            store=store,
            provider=provider or FakeProvider(),
            model="test-model",
            enabled=True,
            max_desires_per_cycle=10,
            auto_create_from_foresight=False,
        )

    @pytest.mark.asyncio
    async def test_cycle_complete_logs_summary(self, store):
        """run_cycle 成功完成时应记录 `bdi.cycle.complete` 事件，包含汇总 attrs。"""
        store.add(make_desire("d1", "Buy groceries by 6pm",
                              priority=DesirePriority.HIGH))
        engine = self.build_engine(store)

        with patch("OriginAgent.bdi.deliberation.log_event") as mock_log_event:
            result = await engine.run_cycle()

        # 1. run_cycle 应正常完成（前置条件，确保走的是成功路径）
        assert result.desires_evaluated == 1
        assert result.intentions_formed == 1

        # 2. log_event 应被调用过 bdi.cycle.complete
        complete_call = _find_log_call(mock_log_event, "bdi.cycle.complete")
        assert complete_call is not None, "bdi.cycle.complete 未被记录"
        kwargs = complete_call.kwargs

        # 3. 必需 attrs 存在性断言
        assert "cycle_id" in kwargs, "bdi.cycle.complete 缺少 cycle_id"
        assert "desires_evaluated" in kwargs, "缺少 desires_evaluated"
        assert "cached_intentions" in kwargs, "缺少 cached_intentions"
        assert "llm_desires" in kwargs, "缺少 llm_desires"
        assert "intentions_emitted" in kwargs, "缺少 intentions_emitted"
        assert "elapsed_ms" in kwargs, "缺少 elapsed_ms"

        # 4. 值的正确性断言
        # d1 无匹配 plan，故 cached_intentions=0、llm_desires=1；
        # 唯一 intention 的 trigger="deliberation"，故 intentions_emitted=1。
        assert kwargs["cycle_id"] == result.cycle_id
        assert kwargs["desires_evaluated"] == 1
        assert kwargs["cached_intentions"] == 0
        assert kwargs["llm_desires"] == 1
        assert kwargs["intentions_emitted"] == 1
        assert isinstance(kwargs["elapsed_ms"], (int, float))
        assert kwargs["elapsed_ms"] >= 0
