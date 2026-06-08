import httpx
import pytest

from OriginAgent.providers.model_fetch_contract import (
    ProviderModelFetchError,
    ProviderModelFetchHttpError,
    _clear_provider_models_cache,
    build_models_url_candidates,
    build_provider_model_fetch_request,
    fetch_provider_models,
    get_provider_model_catalog_kind,
    is_provider_model_fetch_supported,
)


@pytest.fixture(autouse=True)
def clear_provider_models_cache() -> None:
    _clear_provider_models_cache()


def test_openai_compat_provider_is_supported_for_phase1() -> None:
    assert is_provider_model_fetch_supported("openai") is True
    assert is_provider_model_fetch_supported("openrouter") is True
    assert is_provider_model_fetch_supported("deepseek") is True
    assert is_provider_model_fetch_supported("zhipu") is True
    assert is_provider_model_fetch_supported("dashscope") is True
    assert is_provider_model_fetch_supported("moonshot") is True
    assert is_provider_model_fetch_supported("groq") is True


def test_special_providers_are_not_supported_for_phase1() -> None:
    assert is_provider_model_fetch_supported("openai_codex") is False
    assert is_provider_model_fetch_supported("github_copilot") is False
    assert is_provider_model_fetch_supported("anthropic") is False
    assert is_provider_model_fetch_supported("azure_openai") is False
    assert is_provider_model_fetch_supported("bedrock") is False
    assert is_provider_model_fetch_supported("custom") is False
    assert is_provider_model_fetch_supported("huggingface") is False
    assert is_provider_model_fetch_supported("qianfan") is False


def test_provider_model_catalog_kind_explains_frontend_capabilities() -> None:
    assert get_provider_model_catalog_kind("openai") == "official"
    assert get_provider_model_catalog_kind("openrouter") == "catalog"
    assert get_provider_model_catalog_kind("custom") == "custom"
    assert get_provider_model_catalog_kind("ollama") == "local"
    assert get_provider_model_catalog_kind("anthropic") == "unsupported"
    assert get_provider_model_catalog_kind("missing-provider") == "unsupported"


def test_build_request_requires_provider_support_and_api_key() -> None:
    request = build_provider_model_fetch_request(
        "openrouter",
        api_key=" sk-or-test ",
        api_base=" https://openrouter.ai/api/v1 ",
    )

    assert request.provider == "openrouter"
    assert request.api_key == "sk-or-test"
    assert request.api_base == "https://openrouter.ai/api/v1"


def test_build_request_rejects_missing_key() -> None:
    try:
        build_provider_model_fetch_request("openrouter", api_key=" ", api_base=None)
    except ProviderModelFetchError as exc:
        assert str(exc) == "api_key is required"
        assert exc.reason == "api_key_required"
        assert exc.status == 400
    else:
        raise AssertionError("expected ProviderModelFetchError")


def test_build_request_rejects_unsupported_provider() -> None:
    try:
        build_provider_model_fetch_request("anthropic", api_key="secret", api_base=None)
    except ProviderModelFetchError as exc:
        assert str(exc) == "provider does not support automatic model discovery"
        assert exc.reason == "unsupported"
    else:
        raise AssertionError("expected ProviderModelFetchError")


def test_openai_request_uses_builtin_default_base_for_fetch() -> None:
    request = build_provider_model_fetch_request(
        "openai",
        api_key="sk-openai-test",
        api_base=None,
    )

    assert request.provider == "openai"
    assert request.api_base is None


def test_build_models_url_candidates_for_versioned_base() -> None:
    assert build_models_url_candidates("https://open.bigmodel.cn/api/paas/v4") == [
        "https://open.bigmodel.cn/api/paas/v4/models",
        "https://open.bigmodel.cn/api/paas/v4/v1/models",
    ]


def test_build_models_url_candidates_strip_compat_suffixes() -> None:
    assert build_models_url_candidates("https://api.deepseek.com/anthropic") == [
        "https://api.deepseek.com/anthropic/v1/models",
        "https://api.deepseek.com/v1/models",
        "https://api.deepseek.com/models",
    ]
    assert build_models_url_candidates("https://dashscope.aliyuncs.com/compatible-mode/v1") == [
        "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
        "https://dashscope.aliyuncs.com/v1/models",
        "https://dashscope.aliyuncs.com/models",
    ]


@pytest.mark.asyncio
async def test_fetch_provider_models_returns_sorted_models_from_first_success() -> None:
    request = build_provider_model_fetch_request(
        "openrouter",
        api_key="sk-or-test",
        api_base="https://openrouter.ai/api/v1",
    )

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/models"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "z-model", "owned_by": "vendor-z"},
                    {"id": "a-model", "owned_by": "vendor-a"},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await fetch_provider_models(request, http_client=client)

    assert response.provider == "openrouter"
    assert response.status == "available"
    assert response.catalog_kind == "catalog"
    assert response.source_url == "https://openrouter.ai/api/v1/models"
    assert response.model_count == 2
    assert response.fetched_at > 0
    assert response.cached is False
    assert [item.id for item in response.models] == ["a-model", "z-model"]


@pytest.mark.asyncio
async def test_fetch_provider_models_tries_fallback_candidate_after_404() -> None:
    request = build_provider_model_fetch_request(
        "deepseek",
        api_key="sk-deepseek",
        api_base="https://api.deepseek.com/anthropic",
    )

    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.path == "/anthropic/v1/models":
            return httpx.Response(404, text="not found")
        if req.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "deepseek-chat"}]})
        raise AssertionError(f"unexpected request to {req.url}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await fetch_provider_models(request, http_client=client)

    assert seen == [
        "https://api.deepseek.com/anthropic/v1/models",
        "https://api.deepseek.com/v1/models",
    ]
    assert [item.id for item in response.models] == ["deepseek-chat"]
    assert response.source_url == "https://api.deepseek.com/v1/models"
    assert response.catalog_kind == "official"


@pytest.mark.asyncio
async def test_fetch_provider_models_surfaces_auth_failures() -> None:
    request = build_provider_model_fetch_request(
        "openrouter",
        api_key="bad-key",
        api_base="https://openrouter.ai/api/v1",
    )

    transport = httpx.MockTransport(
        lambda _: httpx.Response(401, text="unauthorized"),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ProviderModelFetchHttpError, match="HTTP 401: authentication failed") as exc:
            await fetch_provider_models(request, http_client=client)

    assert exc.value.status == 401
    assert exc.value.reason == "auth_failed"


@pytest.mark.asyncio
async def test_fetch_provider_models_reports_missing_models_endpoint() -> None:
    request = build_provider_model_fetch_request(
        "openrouter",
        api_key="sk-or-test",
        api_base="https://openrouter.ai/api/v1",
    )

    transport = httpx.MockTransport(
        lambda _: httpx.Response(404, text="missing"),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ProviderModelFetchHttpError, match="All candidates failed") as exc:
            await fetch_provider_models(request, http_client=client)

    assert exc.value.status == 404
    assert exc.value.reason == "models_endpoint_missing"


@pytest.mark.asyncio
async def test_fetch_provider_models_reports_parse_failures() -> None:
    request = build_provider_model_fetch_request(
        "openrouter",
        api_key="sk-or-test",
        api_base="https://openrouter.ai/api/v1",
    )

    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"models": [{"id": "gpt-4o-mini"}]}),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ProviderModelFetchHttpError, match="Failed to parse response") as exc:
            await fetch_provider_models(request, http_client=client)

    assert exc.value.status == 502
    assert exc.value.reason == "parse_failed"


@pytest.mark.asyncio
async def test_fetch_provider_models_returns_cached_payload_on_repeat_call() -> None:
    request = build_provider_model_fetch_request(
        "openrouter",
        api_key="sk-or-test",
        api_base="https://openrouter.ai/api/v1",
    )

    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        first = await fetch_provider_models(request, http_client=client)
        second = await fetch_provider_models(request, http_client=client)

    assert call_count == 1
    assert first.cached is False
    assert second.cached is True
    assert second.fetched_at == first.fetched_at
    assert [item.id for item in second.models] == ["gpt-4o-mini"]


@pytest.mark.asyncio
async def test_fetch_provider_models_force_refresh_bypasses_cache() -> None:
    initial = build_provider_model_fetch_request(
        "openrouter",
        api_key="sk-or-test",
        api_base="https://openrouter.ai/api/v1",
    )
    force_refresh = build_provider_model_fetch_request(
        "openrouter",
        api_key="sk-or-test",
        api_base="https://openrouter.ai/api/v1",
        force_refresh=True,
    )

    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"data": [{"id": f"gpt-4o-mini-{call_count}"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        first = await fetch_provider_models(initial, http_client=client)
        second = await fetch_provider_models(force_refresh, http_client=client)

    assert call_count == 2
    assert first.cached is False
    assert second.cached is False
    assert [item.id for item in first.models] == ["gpt-4o-mini-1"]
    assert [item.id for item in second.models] == ["gpt-4o-mini-2"]
