"""Tests for onboard command overwrite protection (Task B5).

Verifies that the onboard command does not silently overwrite existing
non-default configuration in non-wizard mode, per rule 23 (destructive
operations must be explicitly confirmed by the user).
"""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from OriginAgent.cli.commands import app
from OriginAgent.config.schema import Config

runner = CliRunner()


def _make_custom_config() -> Config:
    """Create a Config with a non-default API key set."""
    config = Config()
    config.providers.deepseek.api_key = "sk-existing-test-key"
    return config


@pytest.fixture
def isolated_env(monkeypatch):
    """Isolate config/workspace to a local temp dir and mock channel discovery.

    Allows tests to control what load_config returns via env["load_holder"].
    Tracks all save_config calls in env["saved"].
    """
    base_dir = Path("./test_onboard_overwrite_data")
    if base_dir.exists():
        shutil.rmtree(base_dir)
    base_dir.mkdir()

    config_file = base_dir / "config.json"
    workspace_dir = base_dir / "workspace"

    saved: list[Config] = []
    load_holder: dict[str, Any] = {"config": None}

    def _fake_load_config(_config_path=None):
        if load_holder["config"] is not None:
            return load_holder["config"].model_copy(deep=True)
        return Config()

    def _fake_save_config(config, config_path=None):
        saved.append(config)
        target = config_path or config_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(config.model_dump(by_alias=True)), encoding="utf-8"
        )

    monkeypatch.setattr(
        "OriginAgent.config.loader.get_config_path", lambda: config_file
    )
    monkeypatch.setattr("OriginAgent.config.loader.load_config", _fake_load_config)
    monkeypatch.setattr("OriginAgent.config.loader.save_config", _fake_save_config)
    monkeypatch.setattr(
        "OriginAgent.cli.commands.get_workspace_path", lambda _: workspace_dir
    )
    monkeypatch.setattr("OriginAgent.channels.registry.discover_all", lambda: {})

    yield {
        "config_file": config_file,
        "workspace_dir": workspace_dir,
        "saved": saved,
        "load_holder": load_holder,
    }

    if base_dir.exists():
        shutil.rmtree(base_dir)


class TestHasCustomConfig:
    """Tests for _has_custom_config detection (B5.1)."""

    def test_default_config_has_no_custom(self):
        """A fresh default Config should not be flagged as custom."""
        from OriginAgent.cli.commands import _has_custom_config

        assert _has_custom_config(Config()) is False

    def test_config_with_api_key_is_custom(self):
        """Any provider with an API key set means the config is customized."""
        from OriginAgent.cli.commands import _has_custom_config

        config = Config()
        config.providers.deepseek.api_key = "sk-test"
        assert _has_custom_config(config) is True

    def test_config_with_custom_model_is_custom(self):
        """A non-default model name indicates customization."""
        from OriginAgent.cli.commands import _has_custom_config

        config = Config()
        config.agents.defaults.model = "gpt-4o"
        assert _has_custom_config(config) is True

    def test_config_with_custom_provider_is_custom(self):
        """A non-default provider indicates customization."""
        from OriginAgent.cli.commands import _has_custom_config

        config = Config()
        config.agents.defaults.provider = "openai"
        assert _has_custom_config(config) is True

    def test_config_with_api_key_in_other_provider(self):
        """API key in any provider (not just deepseek) counts as custom."""
        from OriginAgent.cli.commands import _has_custom_config

        config = Config()
        config.providers.openai.api_key = "sk-openai"
        assert _has_custom_config(config) is True


class TestOnboardOverwriteProtection:
    """Tests for onboard overwrite protection in non-wizard mode (B5.2, B5.3)."""

    def test_no_config_no_prompt(self, isolated_env):
        """Test 1: No existing config file → onboard runs normally without overwrite prompt."""
        env = isolated_env
        # config_file does NOT exist yet

        result = runner.invoke(app, ["onboard"])

        assert result.exit_code == 0
        assert "Created config" in result.stdout
        assert "Overwrite" not in result.stdout

    def test_default_config_no_prompt(self, isolated_env):
        """Config file exists but is all defaults → no overwrite prompt, just refresh."""
        env = isolated_env
        env["config_file"].write_text("{}")
        # load_config returns default Config() via fixture

        result = runner.invoke(app, ["onboard"])

        assert result.exit_code == 0
        assert "Config already exists" in result.stdout
        assert "Overwrite" not in result.stdout
        assert "Config refreshed" in result.stdout

    def test_custom_config_prompts_overwrite(self, isolated_env):
        """Test 2: Custom config exists → prompts user to confirm overwrite."""
        env = isolated_env
        env["config_file"].write_text("{}")
        env["load_holder"]["config"] = _make_custom_config()

        result = runner.invoke(app, ["onboard"], input="n\n")

        assert result.exit_code == 0
        assert "Config already exists" in result.stdout
        assert "Overwrite" in result.stdout

    def test_decline_does_not_overwrite(self, isolated_env):
        """Test 3: User inputs N → config is NOT overwritten."""
        env = isolated_env
        env["config_file"].write_text("{}")
        env["load_holder"]["config"] = _make_custom_config()

        result = runner.invoke(app, ["onboard"], input="n\n")

        assert result.exit_code == 0
        # No save should have occurred (user cancelled)
        assert len(env["saved"]) == 0
        assert "preserved" in result.stdout.lower()

    def test_confirm_overwrites_config(self, isolated_env):
        """Test 4: User inputs y → config is overwritten with defaults."""
        env = isolated_env
        env["config_file"].write_text("{}")
        env["load_holder"]["config"] = _make_custom_config()

        result = runner.invoke(app, ["onboard"], input="y\n")

        assert result.exit_code == 0
        assert "Config reset to defaults" in result.stdout
        # A config should have been saved (the default one)
        assert len(env["saved"]) == 1
        saved_config = env["saved"][0]
        # The overwritten config should NOT have the old API key
        assert saved_config.providers.deepseek.api_key is None

    def test_enter_key_declines_overwrite(self, isolated_env):
        """Pressing Enter (empty input) → declines overwrite (default=N).

        This is the core of the fix: the old code defaulted to y (overwrite),
        the new code must default to N (preserve).
        """
        env = isolated_env
        env["config_file"].write_text("{}")
        env["load_holder"]["config"] = _make_custom_config()

        result = runner.invoke(app, ["onboard"], input="\n")

        assert result.exit_code == 0
        assert len(env["saved"]) == 0
        assert "preserved" in result.stdout.lower()
