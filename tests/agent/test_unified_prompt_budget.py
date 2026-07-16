"""Tests for the unified prompt budget mechanism (Task B2, spec 1.5).

Verifies that:
- ``AgentRunResult.last_sent_messages`` captures the messages actually sent to
  the LLM after runner governance (``_snip_history`` etc.), not the persisted
  conversation.
- When ``_snip_history`` trims, the captured snapshot reflects the trimmed
  message count, enabling callers to refresh ``state.last_context_assembly``
  so the audit matches the real sent state (规则5 / 规则7).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from OriginAgent.config.schema import AgentDefaults
from OriginAgent.providers.base import LLMResponse

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


@pytest.mark.asyncio
async def test_last_sent_messages_captures_governed_messages():
    """last_sent_messages must equal the messages handed to the provider,
    not the persisted ``messages`` list (which keeps growing)."""
    from OriginAgent.agent.runner import AgentRunSpec, AgentRunner

    captured: list[dict] = []

    async def chat_with_retry(*, messages, **kwargs):
        captured[:] = messages
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider = MagicMock()
    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    initial_messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=initial_messages,
        tools=tools,
        model="m",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    assert result.last_sent_messages == captured
    # The persisted messages may differ (e.g. final assistant message appended);
    # last_sent_messages must reflect what the provider actually saw.
    assert len(result.last_sent_messages) == len(captured)


@pytest.mark.asyncio
async def test_last_sent_messages_reflects_snip_history_trimming(monkeypatch):
    """When _snip_history trims messages, last_sent_messages must reflect
    the reduced message count — this is what enables the audit refresh in
    agent_runtime so state.last_context_assembly stays consistent."""
    from OriginAgent.agent.runner import AgentRunSpec, AgentRunner

    captured: list[dict] = []

    async def chat_with_retry(*, messages, **kwargs):
        captured[:] = messages
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider = MagicMock()
    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old user"},
        {"role": "assistant", "content": "a" * 200},
        {"role": "user", "content": "b" * 200},
        {"role": "assistant", "content": "c" * 200},
        {"role": "user", "content": "current user"},
    ]

    spec = AgentRunSpec(
        initial_messages=messages,
        tools=tools,
        model="m",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        context_window_tokens=2000,
        context_block_limit=100,
    )

    # Force _snip_history to estimate over-budget so it activates.
    monkeypatch.setattr(
        "OriginAgent.agent.runner.estimate_prompt_tokens_chain",
        lambda *_a, **_kw: (500, None),
    )
    token_sizes = {
        "system": 0,
        "old user": 200,
        "current user": 80,
    }
    monkeypatch.setattr(
        "OriginAgent.agent.runner.estimate_message_tokens",
        lambda msg: token_sizes.get(str(msg.get("content")), 100),
    )

    runner = AgentRunner(provider)
    result = await runner.run(spec)

    # _snip_history should have trimmed — fewer messages than the input.
    assert len(result.last_sent_messages) < len(messages)
    # The captured messages (what the provider saw) must match last_sent_messages.
    assert len(result.last_sent_messages) == len(captured)
    # runner_governance_applied can be inferred: message count changed.
    assert len(result.last_sent_messages) != len(messages)


@pytest.mark.asyncio
async def test_last_sent_messages_empty_when_no_iterations():
    """When max_iterations=0, no messages are sent; last_sent_messages is empty."""
    from OriginAgent.agent.runner import AgentRunSpec, AgentRunner

    provider = MagicMock()
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        model="m",
        max_iterations=0,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    assert result.last_sent_messages == []


@pytest.mark.asyncio
async def test_audit_consistency_after_snip_history(monkeypatch):
    """After _snip_history trims, the audit refresh signal
    (len(last_sent_messages) != len(initial_messages)) must be True,
    indicating the ContextBudgetManager audit is superseded and needs refresh.
    This is the contract agent_runtime._run_agent_loop relies on."""
    from OriginAgent.agent.runner import AgentRunSpec, AgentRunner

    captured: list[dict] = []

    async def chat_with_retry(*, messages, **kwargs):
        captured[:] = messages
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider = MagicMock()
    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []

    # Build messages that exceed budget so _snip_history activates.
    messages = [
        {"role": "system", "content": "system"},
    ]
    for i in range(6):
        messages.append({"role": "user", "content": f"msg-{i}-" + "x" * 200})
        messages.append({"role": "assistant", "content": f"reply-{i}-" + "y" * 200})
    messages.append({"role": "user", "content": "final user"})

    spec = AgentRunSpec(
        initial_messages=messages,
        tools=tools,
        model="m",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        context_window_tokens=2000,
        context_block_limit=100,
    )

    monkeypatch.setattr(
        "OriginAgent.agent.runner.estimate_prompt_tokens_chain",
        lambda *_a, **_kw: (500, None),
    )
    monkeypatch.setattr(
        "OriginAgent.agent.runner.estimate_message_tokens",
        lambda msg: 100,
    )

    runner = AgentRunner(provider)
    result = await runner.run(spec)

    # Trimming happened: sent count < initial count.
    assert len(result.last_sent_messages) < len(messages)
    # Audit consistency contract: the refresh signal (count mismatch) is True.
    refresh_needed = len(result.last_sent_messages) != len(messages)
    assert refresh_needed is True
    # The captured messages match last_sent_messages exactly.
    assert result.last_sent_messages == captured
