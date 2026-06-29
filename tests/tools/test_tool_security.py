"""Tests for tool security classification."""
import pytest
from enum import auto

from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.security import (
    ToolSecurityClass,
    is_sensitive_tool,
    SECURITY_AUDIT_CLASSES,
    CAPABILITY_REQUIRED_CLASSES,
)
from OriginAgent.agent.tools.registry import ToolRegistry


class _SensitiveTool(Tool):
    security_class = ToolSecurityClass.SENSITIVE_AUDIT

    @property
    def name(self) -> str:
        return "test_sensitive"

    @property
    def description(self) -> str:
        return "test"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "ok"


class _StandardTool(Tool):
    security_class = ToolSecurityClass.STANDARD

    @property
    def name(self) -> str:
        return "test_normal"

    @property
    def description(self) -> str:
        return "test"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "ok"


class _CapabilityTool(Tool):
    security_class = ToolSecurityClass.SENSITIVE_CAPABILITY

    @property
    def name(self) -> str:
        return "test_capability"

    @property
    def description(self) -> str:
        return "test"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "ok"


class _AllTool(Tool):
    security_class = ToolSecurityClass.SENSITIVE_ALL

    @property
    def name(self) -> str:
        return "test_all"

    @property
    def description(self) -> str:
        return "test"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "ok"


class _UnclassifiedTool(Tool):
    @property
    def name(self) -> str:
        return "unclassified"

    @property
    def description(self) -> str:
        return "test"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "ok"


def test_is_sensitive_tool_returns_true_for_sensitive_class():
    assert is_sensitive_tool(_SensitiveTool()) is True


def test_is_sensitive_tool_returns_false_for_standard_class():
    assert is_sensitive_tool(_StandardTool()) is False


def test_is_sensitive_tool_returns_false_for_unclassified():
    assert is_sensitive_tool(_UnclassifiedTool()) is False


def test_security_classes_contain_expected_enums():
    assert ToolSecurityClass.SENSITIVE_AUDIT in SECURITY_AUDIT_CLASSES
    assert ToolSecurityClass.SENSITIVE_ALL in SECURITY_AUDIT_CLASSES
    assert ToolSecurityClass.SENSITIVE_CAPABILITY in CAPABILITY_REQUIRED_CLASSES
    assert ToolSecurityClass.SENSITIVE_ALL in CAPABILITY_REQUIRED_CLASSES
    assert ToolSecurityClass.STANDARD not in SECURITY_AUDIT_CLASSES
    assert ToolSecurityClass.STANDARD not in CAPABILITY_REQUIRED_CLASSES


def test_tool_registry_propagates_security_class():
    reg = ToolRegistry()
    tool = _SensitiveTool()
    reg.register(tool)
    retrieved = reg.get("test_sensitive")
    assert retrieved is not None
    assert retrieved.security_class == ToolSecurityClass.SENSITIVE_AUDIT


@pytest.mark.asyncio
async def test_real_tools_have_expected_security_classes():
    """Verify that built-in tools declare the expected security_class."""
    from OriginAgent.agent.tools.shell import ExecTool
    from OriginAgent.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool

    assert ExecTool.security_class == ToolSecurityClass.SENSITIVE_ALL
    assert ReadFileTool.security_class == ToolSecurityClass.SENSITIVE_CAPABILITY
    assert WriteFileTool.security_class == ToolSecurityClass.SENSITIVE_ALL
    assert EditFileTool.security_class == ToolSecurityClass.SENSITIVE_ALL
    assert ListDirTool.security_class == ToolSecurityClass.SENSITIVE_CAPABILITY
