"""WarmSummarizer 单元测试：覆盖正常总结、空内容/非法 JSON 降级、字段完整性与热区上下文注入。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from OriginAgent.agent.warm_summarizer import WarmSummarizer
from OriginAgent.providers.base import LLMResponse


def _router(response: LLMResponse) -> MagicMock:
    """构造一个 auxiliary_router mock，其 call_llm 返回给定响应。"""
    router = MagicMock()
    router.call_llm = AsyncMock(return_value=response)
    return router


def _summarizer(tmp_path: Path, response: LLMResponse, *, output_language: str | None = None) -> WarmSummarizer:
    return WarmSummarizer(
        auxiliary_router=_router(response),
        workspace=tmp_path,
        output_language=output_language,
    )


def _msg(role: str, content: str, ts: str = "2025-01-01T00:00:00Z") -> dict:
    return {"role": role, "content": content, "timestamp": ts}


WARM_MESSAGES = [
    _msg("user", "帮我设计背题提醒系统"),
    _msg("assistant", "好的，采用 cron 实现定时提醒"),
]
# 热区包含独特标记，便于断言其进入了 prompt
HOT_MESSAGES = [
    _msg("user", "记得 1:30 提醒我背期末题"),
    _msg("assistant", "已设置 cron 幂等任务"),
]

VALID_SUMMARY = json.dumps(
    {
        "turn_range": "51-100",
        "summary": "讨论了背题提醒系统的实现",
        "commitments": ["提醒用户 1:30 背期末题"],
        "decisions": ["采用 cron 实现"],
        "open_questions": ["cron 幂等性测试是否通过"],
        "key_entities": ["背题", "cron"],
        "timestamp_range": {"start": "2025-01-01T00:00:00Z", "end": "2025-01-01T00:01:00Z"},
    },
    ensure_ascii=False,
)


@pytest.mark.asyncio
async def test_summarize_normal_returns_dict(tmp_path: Path) -> None:
    """LLM 返回合法 JSON 时应正常解析为 dict。"""
    summarizer = _summarizer(tmp_path, LLMResponse(content=VALID_SUMMARY, finish_reason="stop"))

    result = await summarizer.summarize(
        WARM_MESSAGES, HOT_MESSAGES, turn_range="51-100", session_key="cli:direct"
    )

    assert result is not None
    assert result["turn_range"] == "51-100"
    assert result["summary"] == "讨论了背题提醒系统的实现"


@pytest.mark.asyncio
async def test_summarize_empty_content_returns_none(tmp_path: Path) -> None:
    """LLM 返回空内容时应优雅降级返回 None，而非抛异常。"""
    summarizer = _summarizer(tmp_path, LLMResponse(content="", finish_reason="stop"))

    result = await summarizer.summarize(
        WARM_MESSAGES, HOT_MESSAGES, turn_range="51-100", session_key="cli:direct"
    )

    assert result is None


@pytest.mark.asyncio
async def test_summarize_invalid_json_returns_none(tmp_path: Path) -> None:
    """LLM 返回非法 JSON 时应优雅降级返回 None。"""
    summarizer = _summarizer(tmp_path, LLMResponse(content="not json", finish_reason="stop"))

    result = await summarizer.summarize(
        WARM_MESSAGES, HOT_MESSAGES, turn_range="51-100", session_key="cli:direct"
    )

    assert result is None


@pytest.mark.asyncio
async def test_summarize_code_fence_json_still_parsed(tmp_path: Path) -> None:
    """LLM 返回带 markdown code fence 的 JSON 时仍应被正确解析。"""
    fenced = f"```json\n{VALID_SUMMARY}\n```"
    summarizer = _summarizer(tmp_path, LLMResponse(content=fenced, finish_reason="stop"))

    result = await summarizer.summarize(
        WARM_MESSAGES, HOT_MESSAGES, turn_range="51-100", session_key="cli:direct"
    )

    assert result is not None
    assert result["turn_range"] == "51-100"


@pytest.mark.asyncio
async def test_summarize_field_completeness(tmp_path: Path) -> None:
    """解析结果应包含全部必需字段且类型正确。"""
    summarizer = _summarizer(tmp_path, LLMResponse(content=VALID_SUMMARY, finish_reason="stop"))

    result = await summarizer.summarize(
        WARM_MESSAGES, HOT_MESSAGES, turn_range="51-100", session_key="cli:direct"
    )

    assert result is not None
    required = {
        "turn_range",
        "summary",
        "commitments",
        "decisions",
        "open_questions",
        "key_entities",
        "timestamp_range",
    }
    assert required.issubset(result.keys())
    assert isinstance(result["commitments"], list)
    assert isinstance(result["decisions"], list)
    assert isinstance(result["open_questions"], list)
    assert isinstance(result["key_entities"], list)
    assert isinstance(result["timestamp_range"], dict)
    assert {"start", "end"}.issubset(result["timestamp_range"].keys())


@pytest.mark.asyncio
async def test_summarize_prompt_includes_hot_context(tmp_path: Path) -> None:
    """调用 LLM 的 prompt 中必须包含热区消息内容，确保总结结合热区上下文。"""
    router = _router(LLMResponse(content=VALID_SUMMARY, finish_reason="stop"))
    summarizer = WarmSummarizer(auxiliary_router=router, workspace=tmp_path)

    await summarizer.summarize(
        WARM_MESSAGES, HOT_MESSAGES, turn_range="51-100", session_key="cli:direct"
    )

    assert router.call_llm.await_count == 1
    messages = router.call_llm.call_args.kwargs["messages"]
    user_prompt = "\n".join(m["content"] for m in messages if m.get("role") == "user")
    # 热区独有内容应出现在 prompt 中
    assert "1:30" in user_prompt
    assert "背期末题" in user_prompt
    # 温区内容也应出现，确保两者都被注入
    assert "背题提醒系统" in user_prompt


@pytest.mark.asyncio
async def test_summarize_error_response_returns_none(tmp_path: Path) -> None:
    """LLM 返回 error 响应时应降级返回 None。"""
    summarizer = _summarizer(
        tmp_path,
        LLMResponse(content="Error calling LLM: timeout", finish_reason="error", error_kind="timeout"),
    )

    result = await summarizer.summarize(
        WARM_MESSAGES, HOT_MESSAGES, turn_range="51-100", session_key="cli:direct"
    )

    assert result is None
