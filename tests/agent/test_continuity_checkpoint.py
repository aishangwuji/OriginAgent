"""Tests for _extract_recent_turns_summary max_chars configurability.

P2 修复任务：原实现将每条消息硬截断到 500 字符（content = str(content)[:500]），
跨 session 恢复时长对话关键信息丢失。本测试验证 max_chars 参数可配置截断长度，
并覆盖多模态 content 的拼接后截断行为。

验证先行（规则34）：本文件先于实现编写，预期在实现前用例会因缺少 max_chars 参数而失败。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from OriginAgent.agent.agent_runtime import AgentRuntime, RuntimeDependencies
from OriginAgent.config.schema import ContextConfig


def _make_session_with_messages(messages: list[dict]) -> SimpleNamespace:
    """构建带 messages 属性的 mock session，与生产 Session 类的接口一致"""
    return SimpleNamespace(
        key="cli:test",
        metadata={},
        messages=messages,
    )


def _make_long_text(length: int) -> str:
    """生成长度为 length 的字符串（以 10 字符为基本单元重复）"""
    return "abcdefghij" * (length // 10)


def test_default_max_chars_800():
    """默认 max_chars=800：超过 800 字符的消息被截断到 800"""
    long_content = _make_long_text(1000)  # 1000 字符
    messages = [
        {"role": "user", "content": long_content},
        {"role": "assistant", "content": "reply"},
    ]
    session = _make_session_with_messages(messages)

    # 不传 max_chars，使用默认值 800
    summary = AgentRuntime._extract_recent_turns_summary(session)

    assert len(summary) == 2
    assert summary[0]["role"] == "user"
    assert len(summary[0]["content"]) == 800
    assert summary[0]["content"] == long_content[:800]


def test_max_chars_1200():
    """max_chars=1200：超过 1200 字符的消息被截断到 1200"""
    long_content = _make_long_text(1500)  # 1500 字符
    messages = [
        {"role": "user", "content": long_content},
        {"role": "assistant", "content": "reply"},
    ]
    session = _make_session_with_messages(messages)

    summary = AgentRuntime._extract_recent_turns_summary(session, max_chars=1200)

    assert len(summary) == 2
    assert len(summary[0]["content"]) == 1200
    assert summary[0]["content"] == long_content[:1200]


def test_max_chars_300():
    """max_chars=300：超过 300 字符的消息被截断到 300"""
    long_content = _make_long_text(600)  # 600 字符
    messages = [
        {"role": "user", "content": long_content},
        {"role": "assistant", "content": "reply"},
    ]
    session = _make_session_with_messages(messages)

    summary = AgentRuntime._extract_recent_turns_summary(session, max_chars=300)

    assert len(summary) == 2
    assert len(summary[0]["content"]) == 300
    assert summary[0]["content"] == long_content[:300]


def test_short_message_not_truncated():
    """消息短于 max_chars 时不截断，原样保留"""
    short_content = "Hello, world!"
    messages = [
        {"role": "user", "content": short_content},
        {"role": "assistant", "content": "Hi there"},
    ]
    session = _make_session_with_messages(messages)

    summary = AgentRuntime._extract_recent_turns_summary(session, max_chars=800)

    assert len(summary) == 2
    assert summary[0]["content"] == short_content
    assert summary[1]["content"] == "Hi there"


def test_multimodal_content_truncated():
    """多模态 content（list 类型）被正确拼接后截断

    content 为 list 时，提取 type=text 的 part.text 与裸 str part，
    以空格拼接后再按 max_chars 截断。
    """
    long_text_a = _make_long_text(500)  # 500 字符
    long_text_b = _make_long_text(500)  # 500 字符，拼接后总长度 1001（含空格）
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": long_text_a},
                {"type": "text", "text": long_text_b},
            ],
        },
        {"role": "assistant", "content": "reply"},
    ]
    session = _make_session_with_messages(messages)

    summary = AgentRuntime._extract_recent_turns_summary(session, max_chars=300)

    assert len(summary) == 2
    # 拼接后总长度 1001（500 + 1 空格 + 500），截断到 300
    assert len(summary[0]["content"]) == 300
    expected_joined = f"{long_text_a} {long_text_b}"
    assert summary[0]["content"] == expected_joined[:300]


# ── Task 4: checkpoint 保存/加载事件日志（验证先行） ──────────────────


def _build_runtime_for_checkpoint() -> AgentRuntime:
    """构建用于 checkpoint 测试的 AgentRuntime（与 test_continuity_checkpoint_recent_turns 同模式）。

    _save_continuity_checkpoint 会读取 context._context_config.recent_turns_summary_max_chars，
    这里用真实 ContextConfig 提供默认值（800），避免 AttributeError。
    """
    working = MagicMock()
    working.current_goal = "test goal"
    working.current_plan = ["step 1"]
    working.open_loops = []
    working.active_constraints = []

    working_memory = MagicMock()
    working_memory.load.return_value = working

    context = MagicMock()
    context._context_config = ContextConfig()

    deps = RuntimeDependencies(
        working_memory=working_memory,
        nearline_memory=None,
        context=context,
    )
    return AgentRuntime(deps)


@patch("OriginAgent.agent.agent_runtime.log_event")
def test_checkpoint_saved_logs_field_counts(mock_log_event):
    """_save_continuity_checkpoint 应记录 continuity.checkpoint.saved 事件，
    attrs 包含 field_count/recent_turns_count/cold_indices_count/current_goal_present。"""
    runtime = _build_runtime_for_checkpoint()
    session = SimpleNamespace(key="cli:test", metadata={}, messages=[])

    runtime._save_continuity_checkpoint(session)

    mock_log_event.assert_called_once()
    call_args = mock_log_event.call_args
    # 首个位置参数为事件名
    assert call_args.args[0] == "continuity.checkpoint.saved"
    attrs = call_args.kwargs
    # 必备 attrs 必须存在
    assert "field_count" in attrs
    assert "recent_turns_count" in attrs
    assert "cold_indices_count" in attrs
    assert "current_goal_present" in attrs
    # session_key 透传
    assert attrs.get("session_key") == "cli:test"
    # working.current_goal = "test goal" → current_goal_present 为 True
    assert attrs["current_goal_present"] is True
    # 空 messages → recent_turns_summary 为空；无 workspace → cold_indices 为空
    assert attrs["recent_turns_count"] == 0
    assert attrs["cold_indices_count"] == 0
    # field_count 为非空字段计数，current_goal/updated_at 等非空 → 至少 1
    assert isinstance(attrs["field_count"], int)
    assert attrs["field_count"] >= 1


@patch("OriginAgent.agent.agent_runtime.log_event")
def test_checkpoint_loaded_logs_source_and_counts(mock_log_event):
    """_load_continuity_checkpoint 应记录 continuity.checkpoint.loaded 事件，
    attrs 包含 source/field_count/recent_turns_count/cold_indices_count。"""
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
                ],
                "cold_indices": [],
                "updated_at": "2026-01-01T00:00:00Z",
            }
        },
    )

    # workspace=None：_read_cold_indices 返回空 → 回退到 checkpoint_embedded
    result = AgentRuntime._load_continuity_checkpoint(session)

    assert result is not None
    mock_log_event.assert_called_once()
    call_args = mock_log_event.call_args
    assert call_args.args[0] == "continuity.checkpoint.loaded"
    attrs = call_args.kwargs
    assert "source" in attrs
    assert "field_count" in attrs
    assert "recent_turns_count" in attrs
    assert "cold_indices_count" in attrs
    # workspace=None → 冷区索引回退到 checkpoint 内嵌 → source=checkpoint_embedded
    assert attrs["source"] == "checkpoint_embedded"
    assert attrs.get("session_key") == "cli:test"
    assert attrs["recent_turns_count"] == 1
    assert attrs["cold_indices_count"] == 0
    assert isinstance(attrs["field_count"], int)
    assert attrs["field_count"] >= 1


# ── Task 1.1: 重复 save 应覆盖旧 checkpoint（修复 setdefault 语义 bug） ──


@patch("OriginAgent.agent.agent_runtime.log_event")
def test_repeated_save_updates_checkpoint(mock_log_event):
    """_save_continuity_checkpoint 多次调用必须覆盖旧 checkpoint。

    回归 bug：原实现使用 ``session.metadata.setdefault("continuity_checkpoint_v1", checkpoint)``，
    ``setdefault`` 仅当 key 不存在时才写入——首次写入后 checkpoint 永不更新，
    导致 ``recent_turns_summary`` saved 4 条但 loaded 0 条（答非所问、上下文缺失）。
    本测试验证第二次 save 覆盖第一次，且 saved 事件被触发两次。
    """
    runtime = _build_runtime_for_checkpoint()
    session = SimpleNamespace(key="cli:test", metadata={}, messages=[])

    # 第一次保存：messages 为空 → recent_turns_summary 为空
    checkpoint1 = runtime._save_continuity_checkpoint(session)
    assert session.metadata["continuity_checkpoint_v1"] is checkpoint1

    # 修改 session 状态：增加一条 user message → recent_turns_summary 将包含 1 条
    session.messages.append({"role": "user", "content": "Hello after first save"})

    # 第二次保存：必须覆盖第一次的 checkpoint
    checkpoint2 = runtime._save_continuity_checkpoint(session)

    # metadata 中存储的是 checkpoint2（不是 checkpoint1）——setdefault 在此处会失败
    assert session.metadata["continuity_checkpoint_v1"] is checkpoint2
    assert session.metadata["continuity_checkpoint_v1"] is not checkpoint1

    # checkpoint2 的 recent_turns_summary 与 checkpoint1 不同
    assert checkpoint1["recent_turns_summary"] == []
    assert len(checkpoint2["recent_turns_summary"]) == 1
    assert checkpoint2["recent_turns_summary"] != checkpoint1["recent_turns_summary"]

    # continuity.checkpoint.saved 事件被触发了两次
    saved_calls = [
        call for call in mock_log_event.call_args_list
        if call.args and call.args[0] == "continuity.checkpoint.saved"
    ]
    assert len(saved_calls) == 2
