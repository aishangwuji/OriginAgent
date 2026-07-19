"""Tests for cached token extraction from OpenAI-compatible providers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from OriginAgent.providers.openai_compat_provider import OpenAICompatProvider
from OriginAgent.providers.registry import PROVIDERS, find_by_name


class FakeUsage:
    """Mimics an OpenAI SDK usage object (has attributes, not dict keys)."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakePromptDetails:
    """Mimics prompt_tokens_details sub-object."""
    def __init__(self, cached_tokens=0):
        self.cached_tokens = cached_tokens


class _FakeSpec:
    supports_prompt_caching = False
    model_id_prefix = None
    strip_model_prefix = False
    max_completion_tokens = False
    reasoning_effort = None


def _provider():
    from unittest.mock import MagicMock
    p = OpenAICompatProvider.__new__(OpenAICompatProvider)
    p.client = MagicMock()
    p.spec = _FakeSpec()
    return p


# Minimal valid choice so _parse reaches _extract_usage.
_DICT_CHOICE = {"message": {"content": "Hello"}}

class _FakeMessage:
    content = "Hello"
    tool_calls = None


class _FakeChoice:
    message = _FakeMessage()
    finish_reason = "stop"


# --- dict-based response (raw JSON / mapping) ---

def test_extract_usage_openai_cached_tokens_dict():
    """prompt_tokens_details.cached_tokens from a dict response."""
    p = _provider()
    response = {
        "choices": [_DICT_CHOICE],
        "usage": {
            "prompt_tokens": 2000,
            "completion_tokens": 300,
            "total_tokens": 2300,
            "prompt_tokens_details": {"cached_tokens": 1200},
        }
    }
    result = p._parse(response)
    assert result.usage["cached_tokens"] == 1200
    assert result.usage["prompt_tokens"] == 2000


def test_extract_usage_deepseek_cached_tokens_dict():
    """prompt_cache_hit_tokens from a DeepSeek dict response."""
    p = _provider()
    response = {
        "choices": [_DICT_CHOICE],
        "usage": {
            "prompt_tokens": 1500,
            "completion_tokens": 200,
            "total_tokens": 1700,
            "prompt_cache_hit_tokens": 1200,
            "prompt_cache_miss_tokens": 300,
        }
    }
    result = p._parse(response)
    assert result.usage["cached_tokens"] == 1200


def test_extract_usage_no_cached_tokens_dict():
    """Response without any cache fields -> no cached_tokens key."""
    p = _provider()
    response = {
        "choices": [_DICT_CHOICE],
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
        }
    }
    result = p._parse(response)
    assert "cached_tokens" not in result.usage


def test_extract_usage_openai_cached_zero_dict():
    """cached_tokens=0 should NOT be included (same as existing fields)."""
    p = _provider()
    response = {
        "choices": [_DICT_CHOICE],
        "usage": {
            "prompt_tokens": 2000,
            "completion_tokens": 300,
            "total_tokens": 2300,
            "prompt_tokens_details": {"cached_tokens": 0},
        }
    }
    result = p._parse(response)
    assert "cached_tokens" not in result.usage


# --- object-based response (OpenAI SDK Pydantic model) ---

def test_extract_usage_openai_cached_tokens_obj():
    """prompt_tokens_details.cached_tokens from an SDK object response."""
    p = _provider()
    usage_obj = FakeUsage(
        prompt_tokens=2000,
        completion_tokens=300,
        total_tokens=2300,
        prompt_tokens_details=FakePromptDetails(cached_tokens=1200),
    )
    response = FakeUsage(choices=[_FakeChoice()], usage=usage_obj)
    result = p._parse(response)
    assert result.usage["cached_tokens"] == 1200


def test_extract_usage_deepseek_cached_tokens_obj():
    """prompt_cache_hit_tokens from a DeepSeek SDK object response."""
    p = _provider()
    usage_obj = FakeUsage(
        prompt_tokens=1500,
        completion_tokens=200,
        total_tokens=1700,
        prompt_cache_hit_tokens=1200,
    )
    response = FakeUsage(choices=[_FakeChoice()], usage=usage_obj)
    result = p._parse(response)
    assert result.usage["cached_tokens"] == 1200


def test_extract_usage_stepfun_top_level_cached_tokens_dict():
    """StepFun/Moonshot: usage.cached_tokens at top level (not nested)."""
    p = _provider()
    response = {
        "choices": [_DICT_CHOICE],
        "usage": {
            "prompt_tokens": 591,
            "completion_tokens": 120,
            "total_tokens": 711,
            "cached_tokens": 512,
        }
    }
    result = p._parse(response)
    assert result.usage["cached_tokens"] == 512


def test_extract_usage_stepfun_top_level_cached_tokens_obj():
    """StepFun/Moonshot: usage.cached_tokens as SDK object attribute."""
    p = _provider()
    usage_obj = FakeUsage(
        prompt_tokens=591,
        completion_tokens=120,
        total_tokens=711,
        cached_tokens=512,
    )
    response = FakeUsage(choices=[_FakeChoice()], usage=usage_obj)
    result = p._parse(response)
    assert result.usage["cached_tokens"] == 512


def test_extract_usage_priority_nested_over_top_level_dict():
    """When both nested and top-level cached_tokens exist, nested wins."""
    p = _provider()
    response = {
        "choices": [_DICT_CHOICE],
        "usage": {
            "prompt_tokens": 2000,
            "completion_tokens": 300,
            "total_tokens": 2300,
            "prompt_tokens_details": {"cached_tokens": 100},
            "cached_tokens": 500,
        }
    }
    result = p._parse(response)
    assert result.usage["cached_tokens"] == 100


def test_anthropic_maps_cache_fields_to_cached_tokens():
    """Anthropic's cache_read_input_tokens should map to cached_tokens."""
    from OriginAgent.providers.anthropic_provider import AnthropicProvider

    usage_obj = FakeUsage(
        input_tokens=800,
        output_tokens=200,
        cache_creation_input_tokens=300,
        cache_read_input_tokens=1200,
    )
    content_block = FakeUsage(type="text", text="hello")
    response = FakeUsage(
        id="msg_1",
        type="message",
        stop_reason="end_turn",
        content=[content_block],
        usage=usage_obj,
    )
    result = AnthropicProvider._parse_response(response)
    assert result.usage["cached_tokens"] == 1200
    assert result.usage["prompt_tokens"] == 2300
    assert result.usage["total_tokens"] == 2500
    assert result.usage["cache_creation_input_tokens"] == 300


def test_anthropic_no_cache_fields():
    """Anthropic response without cache fields should not have cached_tokens."""
    from OriginAgent.providers.anthropic_provider import AnthropicProvider

    usage_obj = FakeUsage(input_tokens=800, output_tokens=200)
    content_block = FakeUsage(type="text", text="hello")
    response = FakeUsage(
        id="msg_1",
        type="message",
        stop_reason="end_turn",
        content=[content_block],
        usage=usage_obj,
    )
    result = AnthropicProvider._parse_response(response)
    assert "cached_tokens" not in result.usage


# ---------------------------------------------------------------------------
# DeepSeek prompt-cache observability (supports_prompt_caching=True without
# explicit cache_control marker injection).
# ---------------------------------------------------------------------------


def test_deepseek_spec_supports_prompt_caching():
    """DeepSeek ProviderSpec must declare supports_prompt_caching=True.

    DeepSeek supports automatic prefix caching (no explicit cache_control
    markers needed). Setting this flag enables cache observability
    (cached_tokens normalization + logging) without triggering marker
    injection, which remains gated by the Anthropic/Claude model-name check
    in _build_kwargs.
    """
    deepseek_spec = find_by_name("deepseek")
    assert deepseek_spec is not None, "DeepSeek ProviderSpec not found in registry"
    assert deepseek_spec.supports_prompt_caching is True, (
        "DeepSeek should declare supports_prompt_caching=True to enable "
        "cached_tokens observability (automatic prefix caching)."
    )


def test_deepseek_does_not_inject_cache_control():
    """DeepSeek provider must NOT inject cache_control markers into messages.

    DeepSeek uses automatic prefix caching — no explicit cache_control
    markers are needed (unlike Anthropic). Only Anthropic/Claude models
    receive marker injection in _build_kwargs. This test guards against
    regressions that would accidentally inject markers for DeepSeek.
    """
    deepseek_spec = find_by_name("deepseek")
    assert deepseek_spec is not None
    provider = OpenAICompatProvider.__new__(OpenAICompatProvider)
    provider._spec = deepseek_spec
    provider.default_model = "deepseek-chat"
    provider._extra_body = {}
    provider.extra_headers = {}

    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "assistant", "content": "assistant turn"},
        {"role": "user", "content": "user turn"},
    ]
    kwargs = provider._build_kwargs(
        messages=messages,
        tools=None,
        model="deepseek-chat",
        max_tokens=128,
        temperature=0.7,
        reasoning_effort=None,
        tool_choice=None,
    )

    # No message in the resulting kwargs should carry a cache_control field.
    for msg in kwargs["messages"]:
        content = msg.get("content")
        if isinstance(content, str):
            assert "cache_control" not in msg, (
                f"DeepSeek must not inject cache_control on string-content "
                f"messages; got {msg}"
            )
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    assert "cache_control" not in block, (
                        f"DeepSeek must not inject cache_control on content "
                        f"blocks; got {block}"
                    )


@pytest.mark.asyncio
async def test_cached_tokens_logged_in_response():
    """event.llm.response log must include the normalized cached_tokens field.

    Verifies the end-to-end observability path: provider returns a response
    whose usage contains a provider-specific cache field (e.g. DeepSeek's
    ``prompt_cache_hit_tokens``); _extract_usage normalizes it to
    ``cached_tokens``; _log_llm_response emits it in the llm.response event.
    """
    from OriginAgent.agent.hook import AgentHook, AgentHookContext
    from OriginAgent.agent.runner import AgentRunner, AgentRunSpec
    from OriginAgent.config.schema import AgentDefaults
    from OriginAgent.providers.base import LLMResponse

    deepseek_spec = find_by_name("deepseek")
    assert deepseek_spec is not None

    provider = MagicMock()
    provider.supports_progress_deltas = False
    provider._spec = deepseek_spec
    # DeepSeek response: prompt_cache_hit_tokens should be normalized to cached_tokens.
    fake_response = LLMResponse(
        content="done",
        tool_calls=[],
        finish_reason="stop",
        usage={
            "prompt_tokens": 1500,
            "completion_tokens": 200,
            "total_tokens": 1700,
            "cached_tokens": 1200,
        },
    )
    provider.chat_with_retry = AsyncMock(return_value=fake_response)

    runner = AgentRunner(provider)
    tools = MagicMock()
    tools.get_definitions.return_value = []

    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        model="deepseek-chat",
        max_iterations=1,
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        session_key="test-session",
        llm_timeout_s=0,  # disable outer timeout; go through return-response path
    )
    hook = AgentHook()
    context = AgentHookContext(iteration=0, messages=spec.initial_messages)

    with patch("OriginAgent.agent.runner.log_event") as mock_log_event:
        await runner._request_model(provider, spec, spec.initial_messages, hook, context)

    # Find the llm.response event call.
    resp_call = None
    for call in mock_log_event.call_args_list:
        if call.args and call.args[0] == "llm.response":
            resp_call = call
            break
    assert resp_call is not None, "llm.response event was not logged"
    kwargs = resp_call.kwargs
    assert "cached_tokens" in kwargs, (
        "llm.response log must include cached_tokens field for observability"
    )
    assert kwargs["cached_tokens"] == 1200, (
        f"Expected cached_tokens=1200 (normalized from prompt_cache_hit_tokens), "
        f"got {kwargs['cached_tokens']}"
    )
