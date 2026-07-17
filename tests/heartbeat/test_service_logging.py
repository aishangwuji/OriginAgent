"""Tests for HeartbeatService logging instrumentation."""

import pytest

from OriginAgent.heartbeat.service import HeartbeatService
from OriginAgent.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class _StubProvider(LLMProvider):
    """Minimal provider that returns a single canned heartbeat tool call."""

    def __init__(self, arguments: dict) -> None:
        super().__init__()
        self._arguments = arguments

    async def chat(self, **kwargs) -> LLMResponse:
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="hb_1",
                    name="heartbeat",
                    arguments=self._arguments,
                )
            ],
        )

    def get_default_model(self) -> str:
        return "test-model"


@pytest.mark.asyncio
async def test_decide_logs_action_and_tasks(tmp_path, monkeypatch) -> None:
    """_decide should emit heartbeat.decided with action/tasks_preview/tasks_len."""
    captured: list[dict] = []

    def _capture(event: str, **attrs):
        captured.append({"event": event, **attrs})

    # Patch log_event where it is looked up (module namespace of HeartbeatService).
    monkeypatch.setattr("OriginAgent.heartbeat.service.log_event", _capture)

    service = HeartbeatService(
        workspace=tmp_path,
        provider=_StubProvider({"action": "run", "tasks": "check inbox"}),
        model="test-model",
    )

    action, tasks = await service._decide("- [ ] check inbox")

    assert action == "run"
    assert tasks == "check inbox"
    assert len(captured) == 1, f"expected exactly one log_event call, got {captured}"

    record = captured[0]
    assert record["event"] == "heartbeat.decided"
    assert record["action"] == "run"
    assert record["tasks_preview"] == "check inbox"
    assert record["tasks_len"] == len("check inbox")
