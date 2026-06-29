"""Tests for AskUserInterrupt propagation in parallel tool execution."""
import pytest
from unittest.mock import MagicMock

from OriginAgent.agent.runner import AgentRunner, AgentRunSpec
from OriginAgent.agent.tools.ask import AskUserInterrupt
from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.registry import ToolRegistry
from OriginAgent.providers.base import ToolCallRequest


class _FastTool(Tool):
    concurrency_safe = True

    @property
    def name(self) -> str:
        return "fast_tool"

    @property
    def description(self) -> str:
        return "fast"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "fast_result"


class _InterruptTool(Tool):
    concurrency_safe = True

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return "ask"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        raise AskUserInterrupt("Need input")


@pytest.mark.asyncio
async def test_interrupt_propagates_as_fatal_error():
    """When a tool raises AskUserInterrupt, it propagates as fatal."""
    reg = ToolRegistry()
    reg.register(_FastTool())
    reg.register(_InterruptTool())

    spec = AgentRunSpec(
        initial_messages=[],
        tools=reg,
        model="test",
        max_iterations=10,
        max_tool_result_chars=1000,
        concurrent_tools=True,
    )
    runner = AgentRunner(MagicMock())
    tool_calls = [
        ToolCallRequest(id="1", name="fast_tool", arguments={}),
        ToolCallRequest(id="2", name="ask_user", arguments={}),
    ]

    results, events, fatal = await runner._execute_tools(spec, tool_calls, {}, {})
    assert isinstance(fatal, AskUserInterrupt), f"Expected AskUserInterrupt, got {type(fatal)}"
    assert len(results) == 2


@pytest.mark.asyncio
async def test_interrupt_stops_subsequent_tools_in_sequential_mode():
    """In sequential mode, interrupt stops subsequent tools immediately."""
    reg = ToolRegistry()
    reg.register(_InterruptTool())
    reg.register(_FastTool())

    spec = AgentRunSpec(
        initial_messages=[],
        tools=reg,
        model="test",
        max_iterations=10,
        max_tool_result_chars=1000,
        concurrent_tools=False,
    )
    runner = AgentRunner(MagicMock())
    tool_calls = [
        ToolCallRequest(id="1", name="ask_user", arguments={}),
        ToolCallRequest(id="2", name="fast_tool", arguments={}),
    ]

    results, events, fatal = await runner._execute_tools(spec, tool_calls, {}, {})
    assert isinstance(fatal, AskUserInterrupt)
    # Only the interrupt tool should have executed
    assert len(results) == 1
