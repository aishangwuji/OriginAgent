from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from OpenHome.agent.auxiliary_llm import AuxiliaryLLMRouter, AuxiliaryTaskProvider
from OpenHome.agent.memory import ArchiveResult, Consolidator, Dream, MemoryStore
from OpenHome.config.schema import (
    AuxiliaryConfig,
    AuxiliaryTaskConfig,
    Config,
    InlineFallbackConfig,
)
from OpenHome.providers.base import LLMProvider, LLMResponse


EMPTY_FACT_PROPOSALS = json.dumps({
    "facts_to_upsert": [],
    "facts_to_deprecate": [],
    "memory_render_hints": [],
})


class FakeProvider(LLMProvider):
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
        response = await self.chat_with_retry(**kwargs)
        on_delta = kwargs.get("on_content_delta")
        if on_delta and response.content and response.finish_reason != "error":
            await on_delta(response.content)
        return response


class SlowProvider(FakeProvider):
    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        await asyncio.sleep(0.05)
        return _ok("too late")


def _ok(content: str = "ok") -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


def _payment_error() -> LLMResponse:
    return LLMResponse(
        content="insufficient quota",
        finish_reason="error",
        error_status_code=402,
        error_type="insufficient_quota",
    )


def _rate_limit_error() -> LLMResponse:
    return LLMResponse(
        content="rate limit",
        finish_reason="error",
        error_status_code=429,
        error_code="rate_limit_exceeded",
        error_should_retry=True,
    )


def _auth_error() -> LLMResponse:
    return LLMResponse(
        content="unauthorized",
        finish_reason="error",
        error_status_code=401,
        error_kind="authentication",
        error_should_retry=False,
    )


def _router(
    primary: FakeProvider,
    fallback: FakeProvider,
    *,
    task: str = "consolidation",
    task_config: AuxiliaryTaskConfig | None = None,
) -> AuxiliaryLLMRouter:
    cfg = AuxiliaryConfig(
        payment_cooldown_s=1800,
        transient_cooldown_s=60,
        tasks={
            task: task_config or AuxiliaryTaskConfig(
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


async def test_payment_error_falls_back_and_caches_primary_unhealthy() -> None:
    primary = FakeProvider("primary", [_payment_error(), _ok("primary-recovered")])
    fallback = FakeProvider("fallback", [_ok("fallback-1"), _ok("fallback-2")])
    router = _router(primary, fallback)

    first = await router.call_llm(
        "consolidation",
        messages=[{"role": "user", "content": "summarize"}],
    )
    second = await router.call_llm(
        "consolidation",
        messages=[{"role": "user", "content": "summarize again"}],
    )

    assert first.content == "fallback-1"
    assert second.content == "fallback-2"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 2


async def test_retryable_429_falls_back() -> None:
    primary = FakeProvider("primary", [_rate_limit_error()])
    fallback = FakeProvider("fallback", [_ok("fallback")])
    router = _router(primary, fallback)

    result = await router.call_llm(
        "consolidation",
        messages=[{"role": "user", "content": "summarize"}],
    )

    assert result.content == "fallback"
    assert len(fallback.calls) == 1


async def test_auth_error_does_not_cross_provider_fallback() -> None:
    primary = FakeProvider("primary", [_auth_error()])
    fallback = FakeProvider("fallback", [_ok("should-not-run")])
    router = _router(primary, fallback)

    result = await router.call_llm(
        "consolidation",
        messages=[{"role": "user", "content": "summarize"}],
    )

    assert result.finish_reason == "error"
    assert result.error_status_code == 401
    assert fallback.calls == []


async def test_task_model_override_and_inline_fallback_settings_apply() -> None:
    primary = FakeProvider("primary", [_payment_error()])
    fallback = FakeProvider("fallback", [_ok("fallback")])
    router = _router(
        primary,
        fallback,
        task="dream_phase1",
        task_config=AuxiliaryTaskConfig(
            model_override="task-model",
            fallback_models=[
                InlineFallbackConfig(
                    model="fallback-model",
                    provider="openrouter",
                    max_tokens=123,
                    temperature=0.2,
                    reasoning_effort="low",
                )
            ],
        ),
    )

    result = await router.call_llm(
        "dream_phase1",
        model="primary-model",
        messages=[{"role": "user", "content": "facts"}],
        max_tokens=999,
        temperature=0.8,
        reasoning_effort="high",
    )

    assert result.content == "fallback"
    assert primary.calls[0]["model"] == "task-model"
    assert fallback.calls[0]["model"] == "fallback-model"
    assert fallback.calls[0]["max_tokens"] == 123
    assert fallback.calls[0]["temperature"] == 0.2
    assert fallback.calls[0]["reasoning_effort"] == "low"


async def test_task_timeout_falls_back_to_next_candidate() -> None:
    primary = SlowProvider("primary", [])
    fallback = FakeProvider("fallback", [_ok("fallback after timeout")])
    router = _router(
        primary,
        fallback,
        task="dream_phase1",
        task_config=AuxiliaryTaskConfig(
            timeout_s=0.001,
            fallback_models=[
                InlineFallbackConfig(model="fallback-model", provider="openrouter")
            ],
        ),
    )

    result = await router.call_llm(
        "dream_phase1",
        messages=[{"role": "user", "content": "facts"}],
    )

    assert result.content == "fallback after timeout"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


async def test_configured_openrouter_key_adds_default_aggregator_fallback() -> None:
    primary = FakeProvider("primary", [_payment_error()])
    fallback = FakeProvider("fallback", [_ok("openrouter fallback")])
    created: list[tuple[str, str]] = []

    def factory(preset):
        created.append((preset.provider, preset.model))
        return fallback

    router = AuxiliaryLLMRouter(
        primary_provider=primary,
        primary_model="primary-model",
        auxiliary_config=AuxiliaryConfig(),
        config=Config(providers={"openrouter": {"api_key": "sk-or-test"}}),
        provider_factory=factory,
        primary_provider_name="deepseek",
    )

    result = await router.call_llm(
        "consolidation",
        messages=[{"role": "user", "content": "summarize"}],
    )

    assert result.content == "openrouter fallback"
    assert created == [("openrouter", "google/gemini-2.5-flash")]


async def test_consolidator_uses_auxiliary_fallback_before_raw_archive(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    primary = FakeProvider("primary", [_payment_error()])
    fallback = FakeProvider("fallback", [_ok("summary from fallback")])
    router = _router(primary, fallback)
    sessions = MagicMock()
    consolidator = Consolidator(
        store=store,
        provider=primary,
        model="primary-model",
        sessions=sessions,
        context_window_tokens=10_000,
        build_messages=MagicMock(return_value=[]),
        get_tool_definitions=MagicMock(return_value=[]),
        max_completion_tokens=100,
        auxiliary_router=router,
    )

    result = await consolidator.archive([{"role": "user", "content": "hello"}])

    assert result == ArchiveResult(summary="summary from fallback", history_cursor=1)
    entries = store.read_unprocessed_history(since_cursor=0)
    assert entries[0]["content"] == "summary from fallback"
    assert "[RAW]" not in entries[0]["content"]


async def test_consolidator_raw_archives_when_all_auxiliary_candidates_fail(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    primary = FakeProvider("primary", [_payment_error()])
    fallback = FakeProvider("fallback", [_rate_limit_error()])
    router = _router(primary, fallback)
    sessions = MagicMock()
    consolidator = Consolidator(
        store=store,
        provider=primary,
        model="primary-model",
        sessions=sessions,
        context_window_tokens=10_000,
        build_messages=MagicMock(return_value=[]),
        get_tool_definitions=MagicMock(return_value=[]),
        max_completion_tokens=100,
        auxiliary_router=router,
    )

    result = await consolidator.archive([{"role": "user", "content": "hello"}])

    assert result is None
    entries = store.read_unprocessed_history(since_cursor=0)
    assert "[RAW]" in entries[0]["content"]


async def test_dream_phase2_runner_provider_uses_auxiliary_fallback(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    primary = FakeProvider("primary", [_payment_error()])
    fallback = FakeProvider("fallback", [_ok("phase2 fallback")])
    router = _router(
        primary,
        fallback,
        task="dream_phase2",
        task_config=AuxiliaryTaskConfig(
            fallback_models=[
                InlineFallbackConfig(model="fallback-model", provider="openrouter")
            ],
        ),
    )
    dream = Dream(store=store, provider=primary, model="primary-model", auxiliary_router=router)

    assert isinstance(dream._runner.provider, AuxiliaryTaskProvider)
    result = await dream._runner.provider.chat_with_retry(
        messages=[{"role": "user", "content": "phase2"}],
        model="primary-model",
    )

    assert result.content == "phase2 fallback"
    assert len(fallback.calls) == 1


async def test_dream_phase1_uses_auxiliary_fallback_and_advances_cursor(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.append_history("User prefers concise answers")
    primary = FakeProvider("primary", [_payment_error()])
    fallback = FakeProvider("fallback", [_ok(EMPTY_FACT_PROPOSALS)])
    router = _router(
        primary,
        fallback,
        task="dream_phase1",
        task_config=AuxiliaryTaskConfig(
            fallback_models=[
                InlineFallbackConfig(model="fallback-model", provider="openrouter")
            ],
        ),
    )
    dream = Dream(store=store, provider=primary, model="primary-model", auxiliary_router=router)
    dream._runner.run = AsyncMock(return_value=MagicMock(stop_reason="completed", tool_events=[]))

    result = await dream.run()

    assert result is True
    assert store.get_last_dream_cursor() == 1
    assert len(fallback.calls) == 1


async def test_dream_phase1_invalid_fallback_json_keeps_cursor(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.append_history("User prefers concise answers")
    primary = FakeProvider("primary", [_payment_error()])
    fallback = FakeProvider("fallback", [_ok("{not-json")])
    router = _router(
        primary,
        fallback,
        task="dream_phase1",
        task_config=AuxiliaryTaskConfig(
            fallback_models=[
                InlineFallbackConfig(model="fallback-model", provider="openrouter")
            ],
        ),
    )
    dream = Dream(store=store, provider=primary, model="primary-model", auxiliary_router=router)
    dream._runner.run = AsyncMock()

    result = await dream.run()

    assert result is False
    assert store.get_last_dream_cursor() == 0
    dream._runner.run.assert_not_called()
