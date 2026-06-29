"""Verify tiered routing: web_search → economy tier."""
from __future__ import annotations

import pytest

from OriginAgent.agent.auxiliary_llm import AuxiliaryLLMRouter
from OriginAgent.config.schema import (
    AuxiliaryConfig,
    ModelTierConfig,
    TieredRouterConfig,
)


class _MockProvider:
    """Minimal LLMProvider stub — just enough to construct the router."""
    generation = None

    def __init__(self, name: str = "mock"):
        self._name = name

    def get_default_model(self) -> str:
        return f"{self._name}-default-model"

    @property
    def name(self) -> str:
        return self._name


def test_tiered_routing_resolves_economy_for_web_search():
    """web_search should resolve to the economy tier model."""
    primary = _MockProvider("claude")
    tiered = TieredRouterConfig(
        enabled=True,
        default_tier="economy",
        tiers={
            "economy": ModelTierConfig(
                provider="openrouter",
                model="deepseek/deepseek-chat",
                max_tokens=16384,
            ),
            "standard": ModelTierConfig(
                provider="openrouter",
                model="anthropic/claude-sonnet-4-6",
                max_tokens=8192,
            ),
            "premium": ModelTierConfig(
                provider="anthropic",
                model="claude-opus-4-8-20250514",
                max_tokens=4096,
            ),
        },
        task_tier_mapping={
            "web_search": "economy",
            "background_review": "standard",
            "heartbeat": "premium",
        },
    )

    router = AuxiliaryLLMRouter(
        primary_provider=primary,
        primary_model=primary.get_default_model(),
        auxiliary_config=AuxiliaryConfig(enabled=True),
        tiered_config=tiered,
    )

    # web_search should resolve to economy → deepseek
    model = router._resolve_model_from_tier("web_search", None)
    assert model == "deepseek/deepseek-chat", (
        f"Expected deepseek/deepseek-chat, got {model}"
    )


def test_tiered_routing_falls_back_to_default_tier():
    """Unknown task should use default_tier."""
    primary = _MockProvider("claude")
    tiered = TieredRouterConfig(
        enabled=True,
        default_tier="economy",
        tiers={
            "economy": ModelTierConfig(
                provider="openrouter", model="deepseek/deepseek-chat",
            ),
            "premium": ModelTierConfig(
                provider="anthropic", model="claude-opus-4-8",
            ),
        },
        task_tier_mapping={"web_search": "economy"},
    )

    router = AuxiliaryLLMRouter(
        primary_provider=primary,
        primary_model=primary.get_default_model(),
        auxiliary_config=AuxiliaryConfig(enabled=True),
        tiered_config=tiered,
    )

    # "unknown_task" is not in mapping → defaults to "economy"
    model = router._resolve_model_from_tier("unknown_task", None)
    assert model == "deepseek/deepseek-chat", (
        f"Expected default economy model, got {model}"
    )


def test_tiered_routing_explicit_override_takes_priority():
    """Explicitly requested model should bypass tiered config."""
    primary = _MockProvider("claude")
    tiered = TieredRouterConfig(
        enabled=True,
        default_tier="economy",
        tiers={
            "economy": ModelTierConfig(
                provider="openrouter", model="deepseek/deepseek-chat",
            ),
        },
        task_tier_mapping={"web_search": "economy"},
    )

    router = AuxiliaryLLMRouter(
        primary_provider=primary,
        primary_model=primary.get_default_model(),
        auxiliary_config=AuxiliaryConfig(enabled=True),
        tiered_config=tiered,
    )

    # Explicit model should win over tiered resolution
    model = router._resolve_model_from_tier("web_search", "claude-sonnet-4-6")
    assert model == "claude-sonnet-4-6", (
        f"Expected explicit override, got {model}"
    )


def test_tiered_routing_disabled_returns_none():
    """When tiered config is disabled, _resolve_model_from_tier returns None."""
    primary = _MockProvider("claude")
    tiered = TieredRouterConfig(
        enabled=False,  # ← disabled
        tiers={
            "economy": ModelTierConfig(
                provider="openrouter", model="deepseek/deepseek-chat",
            ),
        },
        task_tier_mapping={"web_search": "economy"},
    )

    router = AuxiliaryLLMRouter(
        primary_provider=primary,
        primary_model=primary.get_default_model(),
        auxiliary_config=AuxiliaryConfig(enabled=True),
        tiered_config=tiered,
    )

    model = router._resolve_model_from_tier("web_search", None)
    assert model is None, f"Expected None when disabled, got {model}"


def test_tiered_routing_no_tiered_config_returns_none():
    """When no tiered config is provided, _resolve_model_from_tier returns None."""
    primary = _MockProvider("claude")

    router = AuxiliaryLLMRouter(
        primary_provider=primary,
        primary_model=primary.get_default_model(),
        auxiliary_config=AuxiliaryConfig(enabled=True),
        # no tiered_config
    )

    model = router._resolve_model_from_tier("web_search", None)
    assert model is None, f"Expected None when no tiered_config, got {model}"


def test_tiered_routing_candidates_use_tiered_model():
    """_candidates should include the tier-resolved model."""
    primary = _MockProvider("claude")
    tiered = TieredRouterConfig(
        enabled=True,
        default_tier="economy",
        tiers={
            "economy": ModelTierConfig(
                provider="openrouter", model="deepseek/deepseek-chat",
            ),
        },
        task_tier_mapping={"web_search": "economy"},
    )

    router = AuxiliaryLLMRouter(
        primary_provider=primary,
        primary_model=primary.get_default_model(),
        auxiliary_config=AuxiliaryConfig(enabled=True),
        tiered_config=tiered,
    )

    candidates = router._candidates("web_search", None)
    # First candidate should carry the tier-resolved model
    assert candidates, "Expected at least one candidate"
    # The primary candidate should now use deepseek model
    assert candidates[0].model == "deepseek/deepseek-chat", (
        f"Expected deepseek model in candidates, got {candidates[0].model}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
