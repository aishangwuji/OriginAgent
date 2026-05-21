"""Tests for AnthropicProvider._tool_result_block image_url conversion.

Regression for: tool results containing OpenAI-format image_url blocks
(e.g. from read_file on an image file, via build_image_content_blocks)
were passed to Anthropic unconverted, causing silent image drops with a
"Non-transient LLM error with image content, retrying without images"
warning.
"""

from OriginAgent.providers.anthropic_provider import AnthropicProvider


def test_tool_result_block_converts_image_url_in_list_content():
    """image_url blocks inside tool_result list content must be translated
    to Anthropic-native image blocks; sibling text blocks pass through."""
    msg = {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,AAAA"},
                "_meta": {"path": "/tmp/x.png"},
            },
            {"type": "text", "text": "(Image file: /tmp/x.png)"},
        ],
    }
    block = AnthropicProvider._tool_result_block(msg)

    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "call_1"
    content = block["content"]
    assert isinstance(content, list)
    assert content[0] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "AAAA",
        },
    }
    assert content[1] == {"type": "text", "text": "(Image file: /tmp/x.png)"}


def test_tool_result_block_preserves_string_content():
    """String content must be passed through unchanged; the image-conversion
    path for lists must not affect the string path."""
    msg = {
        "role": "tool",
        "tool_call_id": "call_2",
        "content": "plain tool output",
    }
    block = AnthropicProvider._tool_result_block(msg)

    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "call_2"
    assert block["content"] == "plain tool output"


def test_build_kwargs_strips_meta_without_mutating_messages():
    """Provider request construction must remove internal block metadata on a copy."""
    provider = AnthropicProvider(api_key="test-key")
    messages = [{
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "hi",
                "_meta": {"kind": "runtime_context"},
            },
        ],
    }]

    kwargs = provider._build_kwargs(
        messages=messages,
        tools=None,
        model="claude-sonnet-4-20250514",
        max_tokens=128,
        temperature=0.0,
        reasoning_effort=None,
        tool_choice=None,
        supports_caching=False,
    )

    assert "_meta" in messages[0]["content"][0]
    assert kwargs["messages"][0]["content"] == [{"type": "text", "text": "hi"}]
    assert "_meta" not in str(kwargs)
