"""Contracts and support checks for provider model-list fetching."""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal

import httpx

from OriginAgent.providers.registry import find_by_name

if TYPE_CHECKING:
    from OriginAgent.providers.registry import ProviderSpec


FETCH_TIMEOUT_SECONDS = 15.0
ERROR_BODY_MAX_CHARS = 300
MODEL_FETCH_CACHE_TTL_SECONDS = 12 * 60 * 60
OPENAI_DEFAULT_API_BASE = "https://api.openai.com/v1"
SUPPORTED_PROVIDER_NAMES = frozenset({
    "openai",
    "openrouter",
    "deepseek",
    "zhipu",
    "dashscope",
    "moonshot",
    "groq",
})
KNOWN_COMPAT_SUFFIXES: tuple[str, ...] = (
    "/compatible-mode/v1",
    "/compatible-mode",
    "/api/anthropic",
    "/anthropic",
)
CATALOG_PROVIDER_NAMES = frozenset({
    "openrouter",
})
OFFICIAL_PROVIDER_NAMES = SUPPORTED_PROVIDER_NAMES - CATALOG_PROVIDER_NAMES
ProviderModelCatalogKind = Literal["official", "catalog", "local", "custom", "unsupported"]


@dataclass(frozen=True)
class _ProviderModelsCacheEntry:
    response: "ProviderModelFetchResponse"
    cached_at: float


_provider_models_cache: dict[str, _ProviderModelsCacheEntry] = {}
_provider_models_cache_lock = Lock()


@dataclass(frozen=True)
class FetchedProviderModel:
    """One provider-reported model candidate for the settings UI."""

    id: str
    owned_by: str | None = None


@dataclass(frozen=True)
class ProviderModelFetchRequest:
    """Validated settings-surface request for model-list discovery."""

    provider: str
    api_key: str
    api_base: str | None
    force_refresh: bool = False


@dataclass(frozen=True)
class ProviderModelFetchResponse:
    """Settings-surface contract returned to the WebUI."""

    provider: str
    status: Literal["available"]
    catalog_kind: ProviderModelCatalogKind
    models: tuple[FetchedProviderModel, ...]
    model_count: int
    fetched_at: float
    source_url: str | None = None
    cached: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "catalog_kind": self.catalog_kind,
            "models": [asdict(model) for model in self.models],
            "model_count": self.model_count,
            "fetched_at": self.fetched_at,
            "source_url": self.source_url,
            "cached": self.cached,
        }


class ProviderModelFetchError(ValueError):
    """Raised when the request is invalid for model discovery."""

    def __init__(self, reason: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.reason = reason
        self.status = status
        self.phase = "contract"

    def to_json(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "phase": self.phase,
            "reason": self.reason,
        }


class ProviderModelFetchHttpError(RuntimeError):
    """Raised when upstream model discovery fails after validation."""

    def __init__(self, reason: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.reason = reason
        self.status = status
        self.phase = "fetch"

    def to_json(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "phase": self.phase,
            "reason": self.reason,
        }


def is_provider_model_fetch_supported(provider_name: str) -> bool:
    """Return True when the provider is in the V1 auto-fetch scope."""

    spec = find_by_name(provider_name)
    if spec is None:
        return False
    if spec.name not in SUPPORTED_PROVIDER_NAMES:
        return False
    if spec.backend != "openai_compat":
        return False
    if spec.is_oauth or spec.is_local or spec.is_direct:
        return False
    return True


def get_provider_model_catalog_kind(provider_name: str) -> ProviderModelCatalogKind:
    """Classify provider model-discovery behavior for the settings UI."""

    spec = find_by_name(provider_name)
    if spec is None:
        return "unsupported"
    if spec.name == "custom":
        return "custom"
    if spec.is_local:
        return "local"
    if is_provider_model_fetch_supported(spec.name):
        if spec.name in CATALOG_PROVIDER_NAMES:
            return "catalog"
        return "official"
    return "unsupported"


def build_provider_model_fetch_request(
    provider_name: str,
    *,
    api_key: str | None,
    api_base: str | None,
    force_refresh: bool = False,
) -> ProviderModelFetchRequest:
    """Normalize and validate a model-fetch request from WebUI query params."""

    provider = provider_name.strip()
    if not provider:
        raise ProviderModelFetchError("provider_required", "provider is required")
    if not is_provider_model_fetch_supported(provider):
        raise ProviderModelFetchError(
            "unsupported",
            "provider does not support automatic model discovery",
        )

    normalized_key = (api_key or "").strip()
    if not normalized_key:
        raise ProviderModelFetchError("api_key_required", "api_key is required")

    normalized_base = (api_base or "").strip() or None
    return ProviderModelFetchRequest(
        provider=provider,
        api_key=normalized_key,
        api_base=normalized_base,
        force_refresh=force_refresh,
    )


def resolve_provider_models_base_url(request: ProviderModelFetchRequest) -> str:
    """Resolve the provider base URL used for model discovery."""

    if request.api_base:
        return request.api_base
    spec = _get_supported_provider_spec(request.provider)
    default_api_base = _get_provider_default_api_base(request.provider, spec)
    if not default_api_base:
        raise ProviderModelFetchError("api_base_required", "api_base is required")
    return default_api_base


def build_models_url_candidates(base_url: str) -> list[str]:
    """Build ordered `/models` endpoint candidates from an OpenAI-style base URL."""

    trimmed = base_url.strip().rstrip("/")
    if not trimmed:
        raise ProviderModelFetchError("api_base_required", "api_base is required")

    candidates: list[str] = []
    if _ends_with_version_segment(trimmed):
        candidates.append(f"{trimmed}/models")
        if not trimmed.endswith("/v1"):
            candidates.append(f"{trimmed}/v1/models")
    else:
        candidates.append(f"{trimmed}/v1/models")

    stripped = _strip_compat_suffix(trimmed)
    if stripped and stripped != trimmed:
        if _ends_with_version_segment(stripped):
            candidates.append(f"{stripped}/models")
        else:
            candidates.append(f"{stripped}/v1/models")
        candidates.append(f"{stripped}/models")

    unique: list[str] = []
    for url in candidates:
        if url not in unique:
            unique.append(url)
    return unique


async def fetch_provider_models(
    request: ProviderModelFetchRequest,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> ProviderModelFetchResponse:
    """Fetch provider models from the first compatible upstream endpoint."""

    base_url = resolve_provider_models_base_url(request)
    cache_key = _provider_models_cache_key(request.provider, base_url, request.api_key)
    if not request.force_refresh:
        cached_response = _get_cached_provider_models(cache_key)
        if cached_response is not None:
            return cached_response

    candidates = build_models_url_candidates(base_url)
    client = http_client
    close_client = False
    if client is None:
        client = httpx.AsyncClient(
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            trust_env=True,
        )
        close_client = True

    last_endpoint_error: str | None = None
    try:
        for url in candidates:
            try:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {request.api_key}"},
                )
            except httpx.TimeoutException as exc:
                raise ProviderModelFetchHttpError(
                    "timeout",
                    f"Request timed out: {exc}",
                    status=504,
                ) from exc
            except httpx.RequestError as exc:
                raise ProviderModelFetchHttpError(
                    "network_failed",
                    f"Request failed: {exc}",
                    status=502,
                ) from exc

            if response.status_code in {401, 403}:
                raise ProviderModelFetchHttpError(
                    "auth_failed",
                    f"HTTP {response.status_code}: authentication failed",
                    status=response.status_code,
                )
            if response.status_code in {404, 405}:
                body = _truncate_body(response.text)
                last_endpoint_error = f"HTTP {response.status_code}: {body or 'endpoint not found'}"
                continue
            if response.is_error:
                body = _truncate_body(response.text)
                raise ProviderModelFetchHttpError(
                    "upstream_error",
                    f"HTTP {response.status_code}: {body or response.reason_phrase}",
                    status=response.status_code,
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderModelFetchHttpError(
                    "parse_failed",
                    f"Failed to parse response: {exc}",
                    status=502,
                ) from exc

            models = _parse_models_payload(payload)
            response_payload = ProviderModelFetchResponse(
                provider=request.provider,
                status="available",
                catalog_kind=get_provider_model_catalog_kind(request.provider),
                models=tuple(sorted(models, key=lambda model: model.id)),
                model_count=len(models),
                fetched_at=time.time(),
                source_url=url,
                cached=False,
            )
            _store_cached_provider_models(cache_key, response_payload)
            return response_payload
    finally:
        if close_client:
            await client.aclose()

    raise ProviderModelFetchHttpError(
        "models_endpoint_missing",
        f"All candidates failed: {last_endpoint_error or 'no compatible /models endpoint found'}",
        status=404,
    )


def _get_supported_provider_spec(provider_name: str) -> "ProviderSpec":
    spec = find_by_name(provider_name)
    if spec is None or not is_provider_model_fetch_supported(provider_name):
        raise ProviderModelFetchError(
            "unsupported",
            "provider does not support automatic model discovery",
        )
    return spec


def _get_provider_default_api_base(provider_name: str, spec: "ProviderSpec") -> str:
    if provider_name == "openai":
        return OPENAI_DEFAULT_API_BASE
    return (spec.default_api_base or "").strip()


def _provider_models_cache_key(provider_name: str, base_url: str, api_key: str) -> str:
    key_digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    return f"{provider_name}|{base_url.rstrip('/')}|{key_digest}"


def _get_cached_provider_models(cache_key: str) -> ProviderModelFetchResponse | None:
    now = time.time()
    with _provider_models_cache_lock:
        entry = _provider_models_cache.get(cache_key)
        if entry is None:
            return None
        if now - entry.cached_at > MODEL_FETCH_CACHE_TTL_SECONDS:
            _provider_models_cache.pop(cache_key, None)
            return None
        return ProviderModelFetchResponse(
            provider=entry.response.provider,
            status=entry.response.status,
            catalog_kind=entry.response.catalog_kind,
            models=entry.response.models,
            model_count=entry.response.model_count,
            fetched_at=entry.response.fetched_at,
            source_url=entry.response.source_url,
            cached=True,
        )


def _store_cached_provider_models(
    cache_key: str,
    response: ProviderModelFetchResponse,
) -> None:
    with _provider_models_cache_lock:
        _provider_models_cache[cache_key] = _ProviderModelsCacheEntry(
            response=response,
            cached_at=time.time(),
        )


def _clear_provider_models_cache() -> None:
    with _provider_models_cache_lock:
        _provider_models_cache.clear()


def _ends_with_version_segment(url: str) -> bool:
    tail = url.rsplit("/", 1)[-1]
    digits = tail[1:] if tail.startswith("v") else ""
    return bool(digits) and digits.isdigit()


def _strip_compat_suffix(base_url: str) -> str | None:
    for suffix in KNOWN_COMPAT_SUFFIXES:
        if base_url.endswith(suffix):
            return base_url[: -len(suffix)].rstrip("/")
    return None


def _truncate_body(body: str) -> str:
    trimmed = body.strip()
    if len(trimmed) <= ERROR_BODY_MAX_CHARS:
        return trimmed
    return f"{trimmed[:ERROR_BODY_MAX_CHARS]}..."


def _parse_models_payload(payload: Any) -> list[FetchedProviderModel]:
    if not isinstance(payload, dict):
        raise ProviderModelFetchHttpError(
            "parse_failed",
            "Failed to parse response: expected object payload",
            status=502,
        )
    data = payload.get("data")
    if not isinstance(data, list):
        raise ProviderModelFetchHttpError(
            "parse_failed",
            "Failed to parse response: expected data[]",
            status=502,
        )

    models: list[FetchedProviderModel] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        model_id = row.get("id")
        if not isinstance(model_id, str):
            continue
        owned_by = row.get("owned_by")
        models.append(
            FetchedProviderModel(
                id=model_id,
                owned_by=owned_by if isinstance(owned_by, str) else None,
            )
        )
    return models
