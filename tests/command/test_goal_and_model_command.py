import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from OriginAgent.agent.loop import AgentLoop
from OriginAgent.bus.events import InboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.command.builtin import build_help_text, cmd_goal, cmd_model
from OriginAgent.command.router import CommandContext
from OriginAgent.config.schema import Config, ModelPresetConfig
from OriginAgent.providers.base import GenerationSettings


def _provider():
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings(max_tokens=1234, temperature=0.1)
    return provider


def _ctx(loop: AgentLoop, raw: str, *, args: str = "") -> CommandContext:
    msg = InboundMessage(channel="websocket", chat_id="chat-1", sender_id="u", content=raw)
    return CommandContext(
        msg=msg,
        session=loop.sessions.get_or_create(msg.session_key),
        key=msg.session_key,
        raw=raw,
        args=args,
        loop=loop,
    )


@pytest.mark.asyncio
async def test_goal_command_rewrites_to_agent_turn(tmp_path):
    loop = AgentLoop(bus=MessageBus(), provider=_provider(), workspace=tmp_path, model="test-model")
    ctx = _ctx(loop, "/goal ship it", args="ship it")

    result = await cmd_goal(ctx)

    assert result is None
    assert "long_task" in ctx.msg.content
    assert ctx.msg.metadata["original_command"] == "/goal"
    assert "goal_started_at" in ctx.msg.metadata
    assert "/goal <goal>" in build_help_text()


@pytest.mark.asyncio
async def test_model_command_switches_configured_preset(tmp_path):
    cfg = Config(
        agents={"defaults": {"model": "primary", "provider": "custom"}},
        providers={"custom": {"apiKey": "x"}},
        modelPresets={
            "fast": {
                "model": "fallback-model",
                "provider": "custom",
                "maxTokens": 2048,
                "contextWindowTokens": 32768,
            }
        },
    )
    provider = _provider()
    loop = AgentLoop.from_config(cfg, bus=MessageBus(), provider=provider)
    loop.model_presets = {
        **loop.model_presets,
        "fast": ModelPresetConfig(
            model="fallback-model",
            provider="custom",
            max_tokens=2048,
            context_window_tokens=32768,
        ),
    }
    ctx = _ctx(loop, "/model fast", args="fast")

    result = await cmd_model(ctx)

    assert result.content.startswith("Switched model preset")
    assert loop.model_preset == "fast"
    assert loop.model == "fallback-model"


def test_model_preset_allows_default_fallback_reference():
    cfg = Config(
        agents={"defaults": {"model": "primary", "provider": "custom"}},
        providers={"custom": {"apiKey": "x"}},
        modelPresets={
            "fast": {
                "model": "fallback-model",
                "provider": "custom",
                "fallbackModels": ["default"],
            }
        },
    )

    assert cfg.resolve_preset("fast").fallback_models == ["default"]


def test_model_presets_allow_large_context_window_tokens(tmp_path):
    cfg = Config(
        agents={
            "defaults": {
                "workspace": str(tmp_path),
                "model": "primary",
                "provider": "custom",
            }
        },
        providers={"custom": {"apiKey": "x"}},
        modelPresets={
            "large-128k": {
                "model": "fallback-model",
                "provider": "custom",
                "contextWindowTokens": 131072,
            },
            "large-200k": {
                "model": "fallback-model",
                "provider": "custom",
                "contextWindowTokens": 200000,
            },
        },
    )
    assert cfg.model_presets["large-128k"].context_window_tokens == 131072
    assert cfg.model_presets["large-200k"].context_window_tokens == 200000
    assert cfg.agents.defaults.context_window_tokens == 65_536

    provider = _provider()
    loop = AgentLoop.from_config(
        cfg,
        bus=MessageBus(),
        provider=provider,
        preset_snapshot_loader=lambda name: SimpleNamespace(
            provider=provider,
            model=cfg.model_presets[name].model,
            context_window_tokens=(
                cfg.model_presets[name].context_window_tokens
                or cfg.agents.defaults.context_window_tokens
            ),
            signature=("test", name, cfg.model_presets[name].context_window_tokens),
        ),
    )
    assert loop.context_window_tokens == 65_536
    assert loop.model_preset == "default"

    loop.set_model_preset("large-128k")
    assert loop.context_window_tokens == 131072

    loop.set_model_preset("large-200k")
    assert loop.context_window_tokens == 200000


def test_build_help_text_localizes_when_explicit_lang_passed():
    help_text = build_help_text("zh-CN")

    assert help_text.startswith("OriginAgent 命令：")
    assert "/help" in help_text
    assert "列出可用的斜杠命令" in help_text
