"""Priority-based provider matching for auto-detection.

Replaces sequential-first-match with priority-scored matching:
exact prefix match > gateway catch-all > keyword match > local fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from OriginAgent.providers.registry import ProviderSpec


class ProviderMatch:
    """A matching candidate with its priority score."""

    def __init__(self, spec: ProviderSpec, score: int, reason: str):
        self.spec = spec
        self.score = score
        self.reason = reason


def score_provider_match(
    spec: ProviderSpec,
    model: str,
    model_lower: str,
    model_normalized: str,
    model_prefix: str,
    normalized_prefix: str,
    *,
    api_base: str | None = None,
) -> ProviderMatch | None:
    """Score how well *spec* matches *model*.

    *api_base* is the provider's configured base URL, used for Level 3
    (``detect_by_base_keyword`` match).

    Returns ``None`` if the spec does not match at all.
    Higher scores = better match.
    """
    # Level 1: Exact prefix match (e.g. "openai/" prefix -> openai provider)
    if model_prefix and normalized_prefix == spec.name:
        return ProviderMatch(spec, 100, f"exact prefix match: {model_prefix} == {spec.name}")

    # Level 2: Keyword match in model name
    for kw in spec.keywords:
        if kw in model_lower or kw in model_normalized:
            return ProviderMatch(spec, 60, f"keyword match: {kw} in {model}")

    # Level 3: detect_by_base_keyword — matches when the keyword appears in the
    # provider's API base URL (e.g. ollama's "11434" in "http://localhost:11434").
    if spec.detect_by_base_keyword and api_base and spec.detect_by_base_keyword in api_base:
        return ProviderMatch(spec, 40, f"base keyword {spec.detect_by_base_keyword} in {api_base}")

    return None


def best_provider_match(
    model: str | None,
    get_provider_config: Any,
    providers_list: list[Any],
) -> tuple[Any, Any, str] | None:
    """Find the best provider for *model* using priority scoring.

    *get_provider_config* is a callable ``(spec_name) -> ProviderConfig | None``.
    *providers_list* is the ``PROVIDERS`` list from ``registry.py``.

    Returns ``(config, spec, name)`` or ``None``.
    """
    if not model:
        return None

    model_lower = model.lower()
    model_normalized = model_lower.replace("-", "_")
    model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
    normalized_prefix = model_prefix.replace("-", "_")

    candidates: list[ProviderMatch] = []
    for spec in providers_list:
        config = get_provider_config(spec.name)
        if config is None:
            continue
        if spec.is_oauth:
            continue
        if not spec.is_local and not spec.is_direct and not (config and config.api_key):
            continue

        api_base = getattr(config, "api_base", None) if config else None
        match = score_provider_match(
            spec, model, model_lower, model_normalized,
            model_prefix, normalized_prefix,
            api_base=api_base,
        )
        if match is not None:
            candidates.append(match)

    if not candidates:
        return None

    candidates.sort(key=lambda m: (-m.score, providers_list.index(m.spec)))
    best = candidates[0]
    config = get_provider_config(best.spec.name)
    return (config, best.spec, best.spec.name)


def match_provider_name(
    model: str | None,
    get_provider_config: Any,
    providers_list: list[Any],
) -> str | None:
    """Return the best provider name for *model*, or ``None``."""
    result = best_provider_match(model, get_provider_config, providers_list)
    return result[2] if result else None
