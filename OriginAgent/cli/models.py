"""Model information helpers for the onboard wizard.

The model database is a static, curated list of common models across
OpenAI / Anthropic / OpenRouter (and a few other popular providers). It does
NOT perform live API calls — that is intentionally out of scope (rule 32:
minimal implementation). The static list restores autocomplete and
context-window auto-fill in the onboard wizard so users no longer have to
blindly type model names.

All public function signatures are preserved so callers continue to work
without changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ModelInfo:
    """Static metadata for a single model.

    Kept as a dataclass to match the project's existing style (see
    cli/onboard.py) and converted to a plain dict at the API boundary to
    preserve the ``dict[str, Any]`` return type of ``find_model_info``.
    """

    model_id: str
    display_name: str
    provider: str
    context_window: int
    description: str = ""


# Module-level constant (rule 17: no magic values scattered inside functions).
# Context windows reflect widely-published values at time of curation.
STATIC_MODELS: list[ModelInfo] = [
    # --- OpenAI ---
    ModelInfo(
        model_id="gpt-4o",
        display_name="GPT-4o",
        provider="openai",
        context_window=128000,
        description="OpenAI flagship multimodal model",
    ),
    ModelInfo(
        model_id="gpt-4o-mini",
        display_name="GPT-4o mini",
        provider="openai",
        context_window=128000,
        description="Cost-efficient small model",
    ),
    ModelInfo(
        model_id="gpt-4-turbo",
        display_name="GPT-4 Turbo",
        provider="openai",
        context_window=128000,
        description="GPT-4 with improved context and speed",
    ),
    ModelInfo(
        model_id="gpt-4",
        display_name="GPT-4",
        provider="openai",
        context_window=8192,
        description="Original GPT-4 base model",
    ),
    ModelInfo(
        model_id="gpt-3.5-turbo",
        display_name="GPT-3.5 Turbo",
        provider="openai",
        context_window=16385,
        description="Fast, inexpensive legacy model",
    ),
    # --- Anthropic ---
    ModelInfo(
        model_id="claude-3-5-sonnet-20241022",
        display_name="Claude 3.5 Sonnet",
        provider="anthropic",
        context_window=200000,
        description="Anthropic balanced flagship model",
    ),
    ModelInfo(
        model_id="claude-3-5-haiku-20241022",
        display_name="Claude 3.5 Haiku",
        provider="anthropic",
        context_window=200000,
        description="Anthropic fast lightweight model",
    ),
    ModelInfo(
        model_id="claude-3-opus-20240229",
        display_name="Claude 3 Opus",
        provider="anthropic",
        context_window=200000,
        description="Anthropic most capable Claude 3 model",
    ),
    # --- OpenRouter ---
    ModelInfo(
        model_id="openrouter/auto",
        display_name="OpenRouter Auto",
        provider="openrouter",
        context_window=128000,
        description="OpenRouter automatic model routing",
    ),
    ModelInfo(
        model_id="openrouter/anthropic/claude-3.5-sonnet",
        display_name="OpenRouter · Claude 3.5 Sonnet",
        provider="openrouter",
        context_window=200000,
        description="Claude 3.5 Sonnet via OpenRouter",
    ),
    ModelInfo(
        model_id="openrouter/openai/gpt-4o",
        display_name="OpenRouter · GPT-4o",
        provider="openrouter",
        context_window=128000,
        description="GPT-4o via OpenRouter",
    ),
    # --- Other common providers ---
    ModelInfo(
        model_id="gemini-1.5-pro",
        display_name="Gemini 1.5 Pro",
        provider="google",
        context_window=2000000,
        description="Google long-context multimodal model",
    ),
    ModelInfo(
        model_id="deepseek-chat",
        display_name="DeepSeek Chat",
        provider="deepseek",
        context_window=64000,
        description="DeepSeek conversational model",
    ),
    ModelInfo(
        model_id="qwen-max",
        display_name="Qwen Max",
        provider="alibaba",
        context_window=32768,
        description="Alibaba Qwen flagship model",
    ),
]


def get_all_models() -> list[str]:
    return [m.model_id for m in STATIC_MODELS]


def find_model_info(model_name: str) -> dict[str, Any] | None:
    if not model_name:
        return None
    needle = model_name.strip().lower()
    for model in STATIC_MODELS:
        if model.model_id.lower() == needle:
            return asdict(model)
    return None


def get_model_context_limit(model: str, provider: str = "auto") -> int | None:
    # provider is accepted for API compatibility but the static list is
    # keyed by model_id alone (rule 32: no speculative provider disambiguation).
    info = find_model_info(model)
    if info is None:
        return None
    return info.get("context_window")


def get_model_suggestions(partial: str, provider: str = "auto", limit: int = 20) -> list[str]:
    # Empty prefix would match everything; return nothing to avoid flooding the
    # completer before the user has typed anything meaningful.
    if not partial:
        return []
    needle = partial.strip().lower()
    matches = [
        m.model_id
        for m in STATIC_MODELS
        if needle in m.model_id.lower()
    ]
    return matches[:limit]


def format_token_count(tokens: int) -> str:
    """Format token count for display (e.g., 200000 -> '200,000')."""
    return f"{tokens:,}"
