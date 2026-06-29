"""Tests for LoopOptions parameter object."""
import pytest
from pathlib import Path

from OriginAgent.agent.loop_options import (
    LoopOptions,
    ProviderOptions,
    ToolOptions,
    ChannelOptions,
    LearningOptions,
    RuntimeOptions,
)


def test_loop_options_accepts_grouped_config():
    opts = LoopOptions(
        provider=ProviderOptions(
            model="gpt-4",
            max_iterations=100,
            context_window_tokens=8192,
        ),
        tools=ToolOptions(
            restrict_to_workspace=True,
        ),
        channels=ChannelOptions(
            unified_session=False,
        ),
        learning=LearningOptions(
            consolidation_ratio=0.5,
        ),
    )
    assert opts.provider.model == "gpt-4"
    assert opts.tools.restrict_to_workspace is True
    assert opts.channels.unified_session is False
    assert opts.learning.consolidation_ratio == 0.5


def test_loop_options_provides_sensible_defaults():
    opts = LoopOptions()
    assert opts.provider.model is None
    assert opts.tools.restrict_to_workspace is False
    assert opts.channels.session_ttl_minutes == 0


def test_to_kwargs_flattens_groups():
    opts = LoopOptions(
        provider=ProviderOptions(model="claude-3", provider_retry_mode="persistent"),
        tools=ToolOptions(restrict_to_workspace=True),
        learning=LearningOptions(consolidation_ratio=0.8),
    )
    kwargs = opts.to_kwargs()
    assert kwargs.get("model") == "claude-3"
    assert kwargs.get("provider_retry_mode") == "persistent"
    assert kwargs.get("restrict_to_workspace") is True
    assert kwargs.get("consolidation_ratio") == 0.8


def test_to_kwargs_omits_none_values():
    opts = LoopOptions()
    kwargs = opts.to_kwargs()
    # None-valued fields should not appear in kwargs
    assert "model" not in kwargs


def test_runtime_options_defaults():
    opts = RuntimeOptions()
    assert opts.runtime_profile == "default"
    assert opts.hooks is None
    assert opts.disabled_skills is None
