"""Baseline tests documenting current (broken) behavior before repair.

These tests capture the existing behavior where exceptions are silently
suppressed. After the corresponding fixes in runner.py and bus/queue.py,
the assertions in these tests will change to reflect correct behavior.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_prepare_call_exception_does_not_silently_pass():
    """When prepare_call raises, the exception should propagate — not be suppressed.

    Currently _run_tool wraps prepare_call in contextlib.suppress(Exception),
    which silently swallows any exception and allows the tool to execute
    without preparation.  After the fix, this exception must propagate.
    """
    from OriginAgent.agent.runner import AgentRunner
    from OriginAgent.providers.base import LLMProvider

    provider = MagicMock(spec=LLMProvider)
    runner = AgentRunner(provider)

    # Simulate a spec where prepare_call raises
    spec = MagicMock()
    spec.tools.prepare_call = MagicMock(side_effect=ValueError("prepare_call crashed"))
    spec.tools.execute = AsyncMock(return_value="unexpected execution")
    spec.fail_on_tool_error = False
    spec.tools.audit_tool_result_async = None
    spec.tools.audit_tool_result = None
    spec.hook = None

    # Inject a tool call
    tool_call = MagicMock()
    tool_call.name = "test_tool"
    tool_call.arguments = {"unsafe_param": "malicious_input"}

    # Run the tool — currently the exception is suppressed and the tool
    # executes anyway.  After the fix this should raise or return an error.
    result = await runner._run_tool(spec, tool_call, {}, {})

    # If prepare_call was bypassed, spec.tools.execute would have been called
    # We expect either an error payload or a raised exception
    assert "Error" in str(result[0]) or result[2] is not None, (
        "prepare_call exception was silently swallowed and tool executed without preparation"
    )


@pytest.mark.asyncio
async def test_subscriber_failure_signals_upstream():
    """When a subscriber raises, the failure is counted in stats."""
    from OriginAgent.bus.queue import InboundMessage, MessageBus

    bus = MessageBus(maxsize=10)

    # Subscribe a handler that always fails
    class FailingSubscriber:
        async def on_inbound(self, msg):
            raise RuntimeError("subscriber crashed")

        async def on_outbound(self, msg):
            pass

    bus.subscribe(FailingSubscriber())

    msg = InboundMessage(
        channel="test",
        sender_id="tester",
        chat_id="test",
        content="hello",
    )

    # Message is still accepted (enqueued) even if subscriber fails
    result = await bus.publish_inbound(msg)
    assert result is True  # Subscriber failures don't affect enqueueing

    # Stats now count the failure
    stats = bus.stats
    assert stats["inbound_subscriber_failures"] == 1, (
        f"Expected 1 inbound subscriber failure, got {stats['inbound_subscriber_failures']}"
    )


@pytest.mark.asyncio
async def test_prepare_call_exception_becomes_prep_error():
    """When prepare_call raises, it must be treated as a prep_error — not suppressed."""
    from OriginAgent.agent.runner import AgentRunner
    from OriginAgent.providers.base import LLMProvider

    provider = MagicMock(spec=LLMProvider)
    runner = AgentRunner(provider)

    spec = MagicMock()
    spec.tools.prepare_call = MagicMock(side_effect=ValueError("prepare crashed"))
    spec.tools.execute = AsyncMock(return_value="unexpected bypass")
    spec.fail_on_tool_error = False
    spec.tools.audit_tool_result_async = None
    spec.tools.audit_tool_result = None
    spec.hook = None

    tool_call = MagicMock()
    tool_call.name = "test_tool"
    tool_call.arguments = {"input": "data"}

    result = await runner._run_tool(spec, tool_call, {}, {})

    payload, event, error = result
    # The tool should NOT have been executed
    spec.tools.execute.assert_not_called()  # Critical assertion
    # The error should mention the prepare failure
    assert "prepare" in payload.lower() or "prepare" in str(event), (
        "prepare_call exception should be reflected in the error payload"
    )
