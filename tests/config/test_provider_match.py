"""Tests for priority-based provider auto-detection."""
from OriginAgent.config.schema import Config, ProvidersConfig, ProviderConfig


def test_exact_prefix_wins_over_keyword():
    """Exact provider-prefix match should beat a generic keyword match."""
    cfg = Config()
    cfg.providers.openai = ProviderConfig(api_key="sk-openai")
    cfg.providers.aihubmix = ProviderConfig(api_key="sk-ahm")
    provider, name = cfg._match_provider("openai/gpt-4")
    assert name == "openai", f"Expected 'openai', got {name!r}"


def test_keyword_match_works():
    """Keyword matching in model name should find the right provider."""
    cfg = Config()
    cfg.providers.groq = ProviderConfig(api_key="gsk-xxx")
    cfg.providers.ollama = ProviderConfig(api_base="http://localhost:11434")
    provider, name = cfg._match_provider("groq/llama-70b")
    assert name == "groq", f"Expected 'groq', got {name!r}"


def test_keyword_match_via_ollama():
    """Keyword matching via ollama's 'nemotron' keyword."""
    cfg = Config()
    cfg.providers.ollama = ProviderConfig(api_base="http://localhost:11434")
    provider, name = cfg._match_provider("nemotron-4-340b")
    assert name == "ollama", f"Expected 'ollama', got {name!r}"


def test_return_none_when_no_provider_configured():
    """Without any configured provider, match returns (None, None)."""
    cfg = Config()
    provider, name = cfg._match_provider("unknown-model")
    assert provider is None
    assert name is None


def test_forced_provider_takes_precedence():
    """When provider is forced (not 'auto'), it overrides auto-detection."""
    cfg = Config()
    cfg.agents.defaults.provider = "deepseek"
    cfg.providers.deepseek = ProviderConfig(api_key="sk-ds")
    provider, name = cfg._match_provider("gpt-4")
    assert name == "deepseek", f"Expected 'deepseek', got {name!r}"
