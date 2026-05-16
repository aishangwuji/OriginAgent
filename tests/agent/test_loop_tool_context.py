from pathlib import Path
from unittest.mock import MagicMock

import pytest

from OpenHome.agent.loop import AgentLoop
from OpenHome.agent.action_runtime import ActionExecutionResult
from OpenHome.bus.queue import MessageBus
from OpenHome.providers.base import LLMResponse, ToolCallRequest


class _ContextRecordingTool:
    name = "cron"
    concurrency_safe = False

    def __init__(self) -> None:
        self.contexts: list[dict] = []

    def set_context(
        self,
        channel: str,
        chat_id: str,
        metadata: dict | None = None,
        session_key: str | None = None,
    ) -> None:
        self.contexts.append({
            "channel": channel,
            "chat_id": chat_id,
            "metadata": metadata,
            "session_key": session_key,
        })

    async def execute(self, **_kwargs) -> str:
        return "created"


class _Tools:
    def __init__(self, tool: _ContextRecordingTool) -> None:
        self.tool = tool
        self.snapshots = []
        self.audit_contexts = []

    def get(self, name: str):
        return self.tool if name == "cron" else None

    def get_definitions(self) -> list:
        return []

    def prepare_call(self, name: str, arguments: dict):
        return (self.tool, arguments, None) if name == "cron" else (None, arguments, None)

    def set_capability_snapshot(self, snapshot):
        self._capability_snapshot = snapshot
        self.snapshots.append(snapshot)

    def set_audit_context(self, *, actor_id=None, session_key=None):
        self.audit_contexts.append((actor_id, session_key))


class _FakeDeviceExecutor:
    def __init__(self) -> None:
        self.actions = []

    def submit_typed(self, action):
        self.actions.append(action)
        return ActionExecutionResult(
            status="executed",
            action_id="action_123",
            reason="ok",
            backend_called=True,
            permission_status="allow",
        )


def _provider():
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return provider


def test_loop_without_device_executor_does_not_register_lighting_tools(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )

    assert not any(name.startswith("openhome_device_lighting_") for name in loop.tools.tool_names)


def test_loop_with_device_executor_registers_exactly_three_lighting_tools(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
        device_action_executor=_FakeDeviceExecutor(),
    )

    lighting_names = [
        name for name in loop.tools.tool_names if name.startswith("openhome_device_lighting_")
    ]
    assert lighting_names == [
        "openhome_device_lighting_set_power",
        "openhome_device_lighting_set_brightness",
        "openhome_device_lighting_set_color_temperature",
    ]


@pytest.mark.asyncio
async def test_loop_set_tool_context_injects_device_actor_and_trigger(tmp_path: Path) -> None:
    executor = _FakeDeviceExecutor()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
        device_action_executor=executor,
    )
    loop._set_tool_context(
        "cron",
        "home",
        actor_id="alice",
        trigger="scheduled",
    )

    tool = loop.tools.get("openhome_device_lighting_set_power")
    assert tool is not None
    result = await tool.execute(device_id="lamp", power="on")

    assert result["status"] == "success"
    assert executor.actions[0].requested_by == "alice"
    assert executor.actions[0].trigger == "scheduled"


@pytest.mark.asyncio
async def test_loop_hook_refresh_preserves_explicit_device_actor_and_trigger(tmp_path: Path) -> None:
    executor = _FakeDeviceExecutor()
    provider = _provider()
    calls = {"n": 0}

    async def chat_with_retry(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="openhome_device_lighting_set_power",
                        arguments={"device_id": "lamp", "power": "on"},
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])

    provider.chat_with_retry = chat_with_retry
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        device_action_executor=executor,
    )

    await loop._run_agent_loop(
        [],
        channel="chat",
        chat_id="home",
        actor_id="alice",
        trigger="user_initiated",
    )

    assert executor.actions[0].requested_by == "alice"
    assert executor.actions[0].trigger == "user_initiated"


@pytest.mark.asyncio
async def test_loop_hook_preserves_metadata_when_resetting_tool_context(tmp_path: Path) -> None:
    provider = MagicMock()
    calls = {"n": 0}

    async def chat_with_retry(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="call_1", name="cron", arguments={"action": "add"})],
            )
        return LLMResponse(content="done", tool_calls=[])

    provider.chat_with_retry = chat_with_retry
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    cron = _ContextRecordingTool()
    loop.tools = _Tools(cron)

    metadata = {"slack": {"thread_ts": "111.222", "channel_type": "channel"}}
    await loop._run_agent_loop(
        [],
        channel="slack",
        chat_id="C123",
        metadata=metadata,
        session_key="slack:C123:111.222",
    )

    assert cron.contexts[-1] == {
        "channel": "slack",
        "chat_id": "C123",
        "metadata": metadata,
        "session_key": "slack:C123:111.222",
    }
    assert loop.tools.snapshots[-1].trigger == "user_initiated"
    assert loop.tools.audit_contexts[-1] == (None, "slack:C123:111.222")


@pytest.mark.asyncio
async def test_loop_hook_refresh_preserves_scheduled_capability_snapshot(tmp_path: Path) -> None:
    provider = MagicMock()
    calls = {"n": 0}

    async def chat_with_retry(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="call_1", name="cron", arguments={"action": "list"})],
            )
        return LLMResponse(content="done", tool_calls=[])

    provider.chat_with_retry = chat_with_retry
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    cron = _ContextRecordingTool()
    loop.tools = _Tools(cron)

    await loop._run_agent_loop(
        [],
        channel="cron",
        chat_id="home",
        actor_id="cron",
        trigger="scheduled",
        session_key="cron:home",
    )

    assert loop.tools.snapshots[-1].trigger == "scheduled"
    assert loop.tools.snapshots[-1].can_exec is False


def test_snapshot_for_subagent_does_not_fallback_to_user_turn() -> None:
    snapshot = AgentLoop._snapshot_for_trigger("subagent")

    assert snapshot.source == "subagent"
    assert snapshot.can_exec is False
    assert snapshot.can_write_files is False
    assert snapshot.can_spawn is False
    assert snapshot.allowed_device_domains == ()
