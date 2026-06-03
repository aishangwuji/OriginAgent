from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from OriginAgent.agent.tools.audit import InMemoryToolAuditSink, ToolAuditConfig
from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.registry import ToolRegistry, _matches_security_tool
from OriginAgent.config.schema import ToolsConfig
from OriginAgent.security.capabilities import CapabilitySnapshot


class _FakeTool(Tool):
    def __init__(self, name: str, result: Any = "ok"):
        self._name = name
        self._result = result

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        return self._result if self._result != "params" else kwargs


def test_tool_audit_config_defaults_to_minimal() -> None:
    assert ToolAuditConfig().mode == "minimal"
    assert ToolsConfig().audit.mode == "minimal"


def test_tool_audit_config_rejects_invalid_mode() -> None:
    with pytest.raises(ValidationError):
        ToolsConfig.model_validate({"audit": {"mode": "verbose"}})


def test_default_security_tools_are_narrow() -> None:
    patterns = ToolAuditConfig().security_tools

    for name in (
        "exec",
        "message",
        "web_fetch",
        "content_read",
        "cron",
        "spawn",
        "originagent_device_lighting",
        "mcp_demo",
    ):
        assert _matches_security_tool(name, patterns)
    for name in ("read_file", "list_dir", "grep", "glob", "web_search"):
        assert not _matches_security_tool(name, patterns)


def test_security_tool_matching_only_supports_trailing_star_prefix() -> None:
    assert _matches_security_tool("mcp_demo", ("mcp_*",))
    assert not _matches_security_tool("x_mcp_demo", ("*mcp*",))
    assert not _matches_security_tool("mcp_demo", ("mcp_?",))


@pytest.mark.asyncio
async def test_minimal_success_omits_security_summary() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(audit_sink=sink)
    registry.set_audit_context(actor_id="alice", session_key="chat:home")
    registry.register(_FakeTool("example"))

    assert await registry.execute("example", {"secret": "value"}) == "ok"

    event = sink.events[0]
    assert event.status == "success"
    assert event.actor_id_hash is None
    assert event.session_key_hash is None
    assert event.target_kind is None
    assert event.target_hash is None
    assert event.policy_rule is None
    assert event.result_size is None
    assert "secret" not in str(event.to_dict())


@pytest.mark.asyncio
async def test_minimal_validation_error_omits_security_summary_for_ordinary_tool() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(audit_sink=sink)
    registry.set_audit_context(actor_id="alice", session_key="chat:home")
    registry.register(_FakeTool("example"))

    result = await registry.execute("example", ["bad"])

    assert "must be a JSON object" in result
    event = sink.events[0]
    assert event.status == "validation_error"
    assert event.actor_id_hash is None
    assert event.target_hash is None
    assert event.policy_rule is None


@pytest.mark.asyncio
async def test_policy_denied_uses_security_summary_even_in_minimal_mode() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(audit_sink=sink)
    registry.register(_FakeTool("exec"))

    result = await registry.execute("exec", {"command": "echo secret"})

    assert "requires an explicit capability snapshot" in result
    event = sink.events[0]
    assert event.status == "policy_denied"
    assert event.policy_rule == "capability_snapshot_required"
    assert event.target_kind == "command"
    assert event.target_hash
    assert "echo secret" not in str(event.to_dict())


@pytest.mark.asyncio
async def test_security_tool_success_uses_security_summary() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(
        audit_sink=sink,
        capability_snapshot=CapabilitySnapshot.user_turn(),
    )
    registry.register(_FakeTool("exec"))

    assert await registry.execute("exec", {"command": "echo ok"}) == "ok"

    event = sink.events[0]
    assert event.status == "success"
    assert event.target_kind == "command"
    assert event.target_hash
    assert event.result_size is not None
    assert "echo ok" not in str(event.to_dict())


@pytest.mark.asyncio
async def test_off_mode_records_no_tool_audit_events() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(audit_sink=sink, audit_config=ToolAuditConfig(mode="off"))
    registry.register(_FakeTool("example"))

    assert await registry.execute("example", {}) == "ok"

    assert sink.events == []


@pytest.mark.asyncio
async def test_security_mode_records_summary_for_ordinary_success() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(
        audit_sink=sink,
        audit_config=ToolAuditConfig(mode="security"),
    )
    registry.set_audit_context(actor_id="alice", session_key="chat:home")
    registry.register(_FakeTool("web_fetch"))

    assert await registry.execute("web_fetch", {"url": "https://example.com/private?q=secret"}) == "ok"

    event = sink.events[0]
    assert event.actor_id_hash
    assert event.session_key_hash
    assert event.target_kind == "url"
    assert event.target_hash
    assert event.result_size is not None
    assert "example.com" not in str(event.to_dict())
    assert "secret" not in str(event.to_dict())


@pytest.mark.asyncio
async def test_security_mode_records_subagent_audit_context() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(
        audit_sink=sink,
        audit_config=ToolAuditConfig(mode="security"),
        capability_snapshot=CapabilitySnapshot.user_turn(),
    )
    registry.set_audit_context(
        actor_id="subagent",
        session_key="system:subagent",
        subagent_task_id="sub-123",
        parent_session_key="slack:C123:1700.42",
        origin_channel="slack",
        origin_chat_id="C123",
    )
    registry.register(_FakeTool("read_file"))

    assert await registry.execute("read_file", {"path": "README.md"}) == "ok"

    event = sink.events[0]
    assert event.subagent_task_id == "sub-123"
    assert event.parent_session_key_hash is not None
    assert event.origin_channel == "slack"
    assert event.origin_chat_id_hash is not None
    serialized = str(event.to_dict())
    assert "slack:C123:1700.42" not in serialized
    assert "C123" not in serialized
