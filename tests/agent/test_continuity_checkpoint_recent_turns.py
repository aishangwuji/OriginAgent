"""Tests for continuity checkpoint recent turns summary.

continuity_checkpoint_v1 应包含 recent_turns_summary 字段，
记录最近 2 轮对话摘要，使 websocket 重连后 Agent 能看到具体对话内容。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from OriginAgent.agent.agent_runtime import AgentRuntime, RuntimeDependencies
from OriginAgent.agent.context import ContextBuilder
from OriginAgent.config.schema import ContextConfig


def _build_runtime_for_checkpoint() -> AgentRuntime:
    """构建用于 checkpoint 测试的 AgentRuntime"""
    working = MagicMock()
    working.current_goal = "test goal"
    working.current_plan = ["step 1"]
    working.open_loops = []
    working.active_constraints = []

    working_memory = MagicMock()
    working_memory.load.return_value = working

    # _save_continuity_checkpoint 会读取 context._context_config.recent_turns_summary_max_chars，
    # 这里用真实 ContextConfig 提供默认值（800），避免 AttributeError。
    context = MagicMock()
    context._context_config = ContextConfig()

    deps = RuntimeDependencies(
        working_memory=working_memory,
        nearline_memory=None,
        context=context,
    )
    return AgentRuntime(deps)


def _make_session_with_messages(messages: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        key="cli:test",
        metadata={},
        messages=messages,
    )


def test_save_checkpoint_includes_recent_turns_summary():
    """_save_continuity_checkpoint 应包含 recent_turns_summary 且长度为 4"""
    runtime = _build_runtime_for_checkpoint()
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
        {"role": "user", "content": "What can you do?"},
        {"role": "assistant", "content": "I can help with many things."},
    ]
    session = _make_session_with_messages(messages)

    checkpoint = runtime._save_continuity_checkpoint(session)

    assert "recent_turns_summary" in checkpoint
    assert len(checkpoint["recent_turns_summary"]) == 4
    assert checkpoint["recent_turns_summary"][0]["role"] == "user"
    assert checkpoint["recent_turns_summary"][0]["content"] == "Hello"
    assert checkpoint["recent_turns_summary"][3]["role"] == "assistant"


def test_load_checkpoint_returns_recent_turns_summary():
    """_load_continuity_checkpoint 应返回 recent_turns_summary 字段"""
    session = SimpleNamespace(
        key="cli:test",
        metadata={
            "continuity_checkpoint_v1": {
                "session_key": "cli:test",
                "current_goal": "goal",
                "current_plan": [],
                "open_loops": [],
                "active_constraints": [],
                "pending_confirmation_refs": [],
                "recent_turns_summary": [
                    {"role": "user", "content": "Hi"},
                    {"role": "assistant", "content": "Hello"},
                ],
                "updated_at": "2026-01-01T00:00:00Z",
            }
        },
    )

    result = AgentRuntime._load_continuity_checkpoint(session)

    assert result is not None
    assert "recent_turns_summary" in result
    assert len(result["recent_turns_summary"]) == 2
    assert result["recent_turns_summary"][0]["role"] == "user"
    assert result["recent_turns_summary"][0]["content"] == "Hi"


def test_build_recovered_continuity_includes_recent_turns():
    """build_recovered_continuity_context 应在输出中渲染 Recent Turns Summary"""
    snapshot = {
        "session_key": "cli:test",
        "current_goal": "goal",
        "recent_turns_summary": [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ],
    }

    block = ContextBuilder.build_recovered_continuity_context(snapshot)

    assert "Recent Turns Summary" in block["text"]
    assert "[user] Hi" in block["text"]
    assert "[assistant] Hello" in block["text"]
