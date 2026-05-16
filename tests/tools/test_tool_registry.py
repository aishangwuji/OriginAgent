from __future__ import annotations

from typing import Any

import pytest

from OpenHome.agent.tools.base import Tool
from OpenHome.agent.tools.audit import InMemoryToolAuditSink
from OpenHome.agent.tools.registry import (
    DuplicateToolError,
    PolicyDeniedError,
    ToolRegistry,
)
from OpenHome.security.capabilities import CapabilitySnapshot


class _FakeTool(Tool):
    def __init__(self, name: str, result: Any | None = None, exc: Exception | None = None):
        self._name = name
        self._result = result
        self._exc = exc

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        if self._exc is not None:
            raise self._exc
        return self._result if self._result is not None else kwargs


def _tool_names(definitions: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for definition in definitions:
        fn = definition.get("function", {})
        names.append(fn.get("name", ""))
    return names


def test_get_definitions_orders_builtins_then_mcp_tools() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("mcp_git_status"))
    registry.register(_FakeTool("write_file"))
    registry.register(_FakeTool("mcp_fs_list"))
    registry.register(_FakeTool("read_file"))

    assert _tool_names(registry.get_definitions()) == [
        "read_file",
        "write_file",
        "mcp_fs_list",
        "mcp_git_status",
    ]


def test_prepare_call_read_file_rejects_non_object_params_with_actionable_hint() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("read_file"))

    tool, params, error = registry.prepare_call("read_file", ["foo.txt"])

    assert tool is None
    assert params == {}
    assert error is not None
    assert "must be a JSON object" in error
    assert "Use named parameters" in error


def test_prepare_call_other_tools_keep_generic_object_validation() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("grep"))

    tool, params, error = registry.prepare_call("grep", ["TODO"])

    assert tool is None
    assert params == {}
    assert error == "Error: Tool 'grep' parameters must be a JSON object, got list. Use named parameters."


def test_get_definitions_returns_cached_result() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("read_file"))
    first = registry.get_definitions()
    assert registry._cached_definitions is not None
    second = registry.get_definitions()
    assert first == second


def test_register_invalidates_cache() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("read_file"))
    first = registry.get_definitions()
    registry.register(_FakeTool("write_file"))
    second = registry.get_definitions()
    assert first is not second
    assert len(second) == 2


def test_register_duplicate_tool_raises() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("read_file"))

    try:
        registry.register(_FakeTool("read_file"))
    except DuplicateToolError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("expected duplicate registration to raise")


def test_unregister_invalidates_cache() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("read_file"))
    registry.register(_FakeTool("write_file"))
    first = registry.get_definitions()
    registry.unregister("write_file")
    second = registry.get_definitions()
    assert first is not second
    assert len(second) == 1


@pytest.mark.asyncio
async def test_execute_keeps_retry_hint_for_ordinary_tool_error() -> None:
    registry = ToolRegistry(capability_snapshot=CapabilitySnapshot.user_turn())
    registry.register(_FakeTool("grep", result="Error: invalid regex pattern: ["))

    result = await registry.execute("grep", {})

    assert "Error: invalid regex pattern" in result
    assert "try a different approach" in result


@pytest.mark.asyncio
async def test_execute_omits_retry_hint_for_policy_denial_text() -> None:
    registry = ToolRegistry(capability_snapshot=CapabilitySnapshot.user_turn())
    registry.register(
        _FakeTool(
            "read_file",
            result="Error: Path secret.txt is outside allowed directory /workspace",
        )
    )

    result = await registry.execute("read_file", {})

    assert result == "Error: Path secret.txt is outside allowed directory /workspace"
    assert "try a different approach" not in result


@pytest.mark.asyncio
async def test_execute_policy_text_matching_is_conservative() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("example", result="Error: resource is not accessible right now"))

    result = await registry.execute("example", {})

    assert "not accessible" in result
    assert "try a different approach" in result


@pytest.mark.asyncio
async def test_execute_omits_retry_hint_for_policy_denial_exception() -> None:
    registry = ToolRegistry()
    registry.register(
        _FakeTool(
            "example",
            exc=PolicyDeniedError(
                "hard policy boundary",
                code="x",
                boundary="test",
                policy_rule="test_rule",
            ),
        )
    )

    result = await registry.execute("example", {})

    assert result == "Error executing example: hard policy boundary"
    assert "try a different approach" not in result


@pytest.mark.asyncio
async def test_execute_keeps_retry_hint_for_ordinary_exception() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("example", exc=RuntimeError("temporary failure")))

    result = await registry.execute("example", {})

    assert "temporary failure" in result
    assert "try a different approach" in result


@pytest.mark.asyncio
async def test_execute_audits_success_without_params_or_result() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(audit_sink=sink)
    registry.register(_FakeTool("example", result="secret result"))

    result = await registry.execute("example", {"token": "secret"})

    assert result == "secret result"
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.tool_name == "example"
    assert event.status == "success"
    assert "secret" not in str(event.to_dict())


@pytest.mark.asyncio
async def test_execute_audits_validation_error() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(audit_sink=sink)
    registry.register(_FakeTool("grep"))

    result = await registry.execute("grep", ["TODO"])

    assert "must be a JSON object" in result
    assert sink.events[0].status == "validation_error"


@pytest.mark.asyncio
async def test_execute_audit_sink_failure_does_not_affect_tool_result() -> None:
    class BrokenSink:
        def record(self, event):
            raise RuntimeError("audit down")

    registry = ToolRegistry(audit_sink=BrokenSink())
    registry.register(_FakeTool("example", result="ok"))

    assert await registry.execute("example", {}) == "ok"


@pytest.mark.asyncio
async def test_execute_audits_policy_rule_for_structured_denial() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(audit_sink=sink)
    registry.register(
        _FakeTool(
            "example",
            exc=PolicyDeniedError(
                "denied",
                code="x",
                boundary="test",
                policy_rule="unit_policy",
            ),
        )
    )

    result = await registry.execute("example", {})

    assert result == "Error executing example: denied"
    assert sink.events[0].status == "policy_denied"
    assert sink.events[0].policy_rule == "unit_policy"


@pytest.mark.asyncio
async def test_high_capability_tool_requires_snapshot() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(audit_sink=sink)
    registry.register(_FakeTool("exec", result="ok"))

    result = await registry.execute("exec", {"command": "echo ok"})

    assert "requires an explicit capability snapshot" in result
    assert "try a different approach" not in result
    event = sink.events[0]
    assert event.status == "policy_denied"
    assert event.policy_rule == "capability_snapshot_required"
    assert event.target_kind == "command"
    assert event.target_hash
    assert "echo ok" not in str(event.to_dict())


@pytest.mark.asyncio
async def test_scheduled_snapshot_denies_exec_spawn_and_device_tools() -> None:
    registry = ToolRegistry(capability_snapshot=CapabilitySnapshot.scheduled_default())
    registry.register(_FakeTool("exec", result="ok"))
    registry.register(_FakeTool("spawn", result="ok"))
    registry.register(_FakeTool("openhome_device_lighting_set_power", result="ok"))

    assert "not allowed by the current capability snapshot" in await registry.execute("exec", {"command": "id"})
    assert "not allowed by the current capability snapshot" in await registry.execute("spawn", {"task": "x"})
    assert "Device tools are not allowed" in await registry.execute(
        "openhome_device_lighting_set_power",
        {"device_id": "lamp", "power": "on"},
    )


@pytest.mark.asyncio
async def test_audit_context_and_target_summary_are_hashed() -> None:
    sink = InMemoryToolAuditSink()
    registry = ToolRegistry(
        audit_sink=sink,
        capability_snapshot=CapabilitySnapshot.user_turn(),
    )
    registry.set_audit_context(actor_id="alice@example.com", session_key="slack:C123")
    registry.register(_FakeTool("web_fetch", result="page body"))

    result = await registry.execute("web_fetch", {"url": "https://example.com/private?q=secret"})

    assert result == "page body"
    event = sink.events[0]
    assert event.actor_id_hash
    assert event.session_key_hash
    assert event.target_kind == "url"
    assert event.target_hash
    serialized = str(event.to_dict())
    assert "alice@example.com" not in serialized
    assert "slack:C123" not in serialized
    assert "example.com" not in serialized
    assert "secret" not in serialized
