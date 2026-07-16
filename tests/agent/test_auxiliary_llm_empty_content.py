from __future__ import annotations

from typing import Any

from OriginAgent.agent.auxiliary_llm import AuxiliaryLLMRouter
from OriginAgent.config.schema import AuxiliaryConfig, AuxiliaryTaskConfig, InlineFallbackConfig
from OriginAgent.providers.base import LLMProvider, LLMResponse


class FakeProvider(LLMProvider):
    """记录调用并按顺序返回预设响应的 mock provider。"""

    def __init__(self, name: str, responses: list[LLMResponse | Exception]):
        super().__init__()
        self.name = name
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get_default_model(self) -> str:
        return f"{self.name}-model"

    async def chat(self, **kwargs: Any) -> LLMResponse:
        return await self.chat_with_retry(**kwargs)

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        return await self.chat_with_retry(**kwargs)


def _empty() -> LLMResponse:
    """空内容响应：finish_reason=stop 但 content 与 reasoning_content 均为空。"""
    return LLMResponse(content="", finish_reason="stop")


def _ok(content: str = "ok") -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


def _router(primary: FakeProvider, fallback: FakeProvider) -> AuxiliaryLLMRouter:
    cfg = AuxiliaryConfig(
        payment_cooldown_s=1800,
        transient_cooldown_s=60,
        tasks={
            "consolidation": AuxiliaryTaskConfig(
                fallback_models=[
                    InlineFallbackConfig(model="fallback-model", provider="openrouter")
                ],
            ),
        },
    )

    def factory(_preset):
        return fallback

    return AuxiliaryLLMRouter(
        primary_provider=primary,
        primary_model="primary-model",
        auxiliary_config=cfg,
        provider_factory=factory,
        primary_provider_name="deepseek",
    )


async def test_primary_empty_content_triggers_fallback() -> None:
    """primary 返回空内容时应触发 fallback，由 fallback 提供有效响应。"""
    primary = FakeProvider("primary", [_empty()])
    fallback = FakeProvider("fallback", [_ok("fallback-content")])
    router = _router(primary, fallback)

    result = await router.call_llm(
        "consolidation",
        messages=[{"role": "user", "content": "summarize"}],
    )

    assert result.content == "fallback-content"
    assert result.finish_reason == "stop"
    # primary 被调用一次但返回空，fallback 被调用一次
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1
    # primary 因空内容被标记为 unhealthy
    assert "primary" in router._unhealthy_until


async def test_all_candidates_empty_content_returns_error() -> None:
    """所有 candidate 都返回空内容时，应返回显式 error 响应而非空内容响应。

    空内容对调用方而言是实质失败（如 Dream Phase 1 拿到空 JSON 直接崩溃），
    必须以 finish_reason="error" + error_kind="empty_content" 暴露失败语义，
    让调用方走错误处理分支而非把空字符串喂给下游解析器。
    """
    primary = FakeProvider("primary", [_empty()])
    fallback = FakeProvider("fallback", [_empty()])
    router = _router(primary, fallback)

    result = await router.call_llm(
        "consolidation",
        messages=[{"role": "user", "content": "summarize"}],
    )

    # 两个 candidate 都被调用且都返回空，最终返回显式 error 响应
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1
    assert result.finish_reason == "error"
    assert result.error_kind == "empty_content"
