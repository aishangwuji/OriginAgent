"""Tool security classification — single source of truth for sensitivity.

Replaces three previously hardcoded lists (``_SENSITIVE_TOOL_LOG_NAMES`` in
loop.py, ``security_tools`` in schema.py, ``_CAPABILITY_REQUIRED_TOOL_NAMES``
in registry.py) with a unified enum that each ``Tool`` subclass declares on
itself.  Consumers read the classification from the tool's registration
metadata instead of maintaining parallel pattern-match lists.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from OriginAgent.agent.tools.base import Tool


class ToolSecurityClass(Enum):
    """Security classification for agent tools.

    Consumers (audit, capability snapshots, policy checks) read this
    from the tool's metadata rather than from hardcoded lists.
    """

    # No special treatment (default).
    STANDARD = auto()
    # Tool execution is logged in the security audit trail.
    SENSITIVE_AUDIT = auto()
    # Tool requires an explicit capability snapshot to execute.
    SENSITIVE_CAPABILITY = auto()
    # Both audit + capability required.
    SENSITIVE_ALL = auto()


# Tools classified as SENSITIVE_AUDIT or SENSITIVE_ALL are logged
# in the security audit trail.
SECURITY_AUDIT_CLASSES: frozenset[ToolSecurityClass] = frozenset({
    ToolSecurityClass.SENSITIVE_AUDIT,
    ToolSecurityClass.SENSITIVE_ALL,
})

# Tools classified as SENSITIVE_CAPABILITY or SENSITIVE_ALL require
# a capability snapshot.
CAPABILITY_REQUIRED_CLASSES: frozenset[ToolSecurityClass] = frozenset({
    ToolSecurityClass.SENSITIVE_CAPABILITY,
    ToolSecurityClass.SENSITIVE_ALL,
})

# Prefix-based fallback for MCP tools and device tools that are
# registered dynamically and cannot declare their class upfront.
_SENSITIVE_PREFIXES: tuple[str, ...] = (
    "originagent_device_",
    "mcp_",
)


def is_sensitive_tool(tool: Tool) -> bool:
    """Return True if *tool* should be treated as security-sensitive.

    Checks the tool's declared ``security_class`` first, then falls
    back to name-prefix matching for dynamically-registered tools.
    """
    cls = getattr(tool, "security_class", ToolSecurityClass.STANDARD)
    if cls in SECURITY_AUDIT_CLASSES:
        return True
    name = getattr(tool, "name", "") or ""
    return any(name.startswith(prefix) for prefix in _SENSITIVE_PREFIXES)
