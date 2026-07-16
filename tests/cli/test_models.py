"""Tests for the static model database in cli.models.

The model database is a static curated list (no live API calls). These tests
verify the list is non-empty and that lookup/suggestion helpers behave as
expected for the onboard wizard's autocomplete and context-window auto-fill.
"""

from OriginAgent.cli import models


def test_get_all_models_returns_non_empty_list():
    """get_all_models() must return a non-empty list so autocomplete works."""
    result = models.get_all_models()
    assert isinstance(result, list)
    assert len(result) > 0


def test_find_model_info_returns_info_for_known_model():
    """find_model_info() must return a dict with expected keys for a known model."""
    info = models.find_model_info("gpt-4o")
    assert info is not None
    assert isinstance(info, dict)
    # Required fields per SubTask A6.1
    assert "model_id" in info
    assert "display_name" in info
    assert "provider" in info
    assert "context_window" in info
    assert info["model_id"] == "gpt-4o"


def test_find_model_info_returns_none_for_unknown_model():
    """find_model_info() must return None for a model not in the static list."""
    result = models.find_model_info("nonexistent-model-xyz")
    assert result is None


def test_get_model_context_limit_returns_correct_window():
    """get_model_context_limit() must return the context window for a known model."""
    limit = models.get_model_context_limit("gpt-4o")
    assert limit is not None
    assert isinstance(limit, int)
    assert limit > 0


def test_get_model_suggestions_for_gpt_prefix():
    """get_model_suggestions('gpt') must include common GPT models."""
    suggestions = models.get_model_suggestions("gpt")
    assert isinstance(suggestions, list)
    assert "gpt-4o" in suggestions
    # Should also match other gpt-* models
    assert any(s.startswith("gpt-4") for s in suggestions)


def test_get_model_suggestions_for_claude_prefix():
    """get_model_suggestions('claude') must include Claude models."""
    suggestions = models.get_model_suggestions("claude")
    assert isinstance(suggestions, list)
    assert len(suggestions) > 0
    # At least one claude-* model should be present
    assert any(s.startswith("claude") for s in suggestions)


def test_get_model_suggestions_for_nonexistent_prefix_returns_empty():
    """get_model_suggestions() with an unknown prefix must return an empty list."""
    suggestions = models.get_model_suggestions("nonexistent")
    assert isinstance(suggestions, list)
    assert suggestions == []
