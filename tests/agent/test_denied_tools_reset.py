"""方案 A: 用户消息进入时清空 _denied_tools 测试。

cron 拒绝工具时会把工具加入 session._denied_tools 短路列表。
若 cron 和用户共享 session_key（如 tenant:guest），用户消息进入时
必须清空 _denied_tools，否则用户的合法工具调用也会被短路阻塞。
"""
from __future__ import annotations

from typing import Any

import pytest

from OriginAgent.agent.agent_turn_pipeline import AgentTurnPipeline


class _FakeSession:
    def __init__(self, denied_tools: list[str] | None = None) -> None:
        self.metadata: dict[str, Any] = {"_denied_tools": list(denied_tools or [])}
        self.saved = False

    def save(self) -> None:
        self.saved = True


class _FakeSessions:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def get_or_create(self, session_key: str) -> _FakeSession:
        return self._session

    def save(self, session: _FakeSession) -> None:
        session.saved = True


class _FakeRuntimeContext:
    def __init__(self, trigger: str) -> None:
        self.trigger = trigger
        self.actor_id = "user:alice"
        self.identity = None


class _FakeDeps:
    """最小化的依赖 stub，只为测试 _clear_denied_tools_for_user_turn。"""

    def __init__(self, sessions: _FakeSessions) -> None:
        self.sessions = sessions


def test_clear_denied_tools_for_user_initiated_turn_clears_cron_pollution() -> None:
    """用户消息进入时，session._denied_tools 应被清空。"""
    session = _FakeSession(denied_tools=["exec", "read_file", "cron"])
    sessions = _FakeSessions(session)
    deps = _FakeDeps(sessions)
    runtime_context = _FakeRuntimeContext(trigger="user_initiated")

    # 调用待实施的方法
    AgentTurnPipeline._clear_denied_tools_for_user_turn(
        deps,  # type: ignore[arg-type]
        session=session,
        session_key="tenant:guest",
        trigger=runtime_context.trigger,
    )

    assert session.metadata["_denied_tools"] == []
    assert session.saved is True


def test_clear_denied_tools_skipped_for_cron_trigger() -> None:
    """cron 触发时不应清空 _denied_tools（避免 cron 自己绕过自己拒绝的工具）。"""
    session = _FakeSession(denied_tools=["exec"])
    sessions = _FakeSessions(session)
    deps = _FakeDeps(sessions)

    AgentTurnPipeline._clear_denied_tools_for_user_turn(
        deps,  # type: ignore[arg-type]
        session=session,
        session_key="tenant:guest",
        trigger="scheduled",
    )

    # cron 触发时不应清空
    assert session.metadata["_denied_tools"] == ["exec"]
    assert session.saved is False


def test_clear_denied_tools_noop_when_already_empty() -> None:
    """_denied_tools 已经为空时不应触发 save（避免无谓的磁盘写入）。"""
    session = _FakeSession(denied_tools=[])
    sessions = _FakeSessions(session)
    deps = _FakeDeps(sessions)

    AgentTurnPipeline._clear_denied_tools_for_user_turn(
        deps,  # type: ignore[arg-type]
        session=session,
        session_key="tenant:guest",
        trigger="user_initiated",
    )

    assert session.metadata["_denied_tools"] == []
    assert session.saved is False  # 没有变化，不应 save
