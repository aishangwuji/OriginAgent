"""Delegated runtime policy for subagent execution.

Two built-in modes:
- ``normal``: broad tool access (write, web, exec allowed by parent config gate).
- ``restricted``: read-only tools only, no web, no exec.
"""

from __future__ import annotations

from dataclasses import dataclass

from OriginAgent.security.capabilities import CapabilitySnapshot

# 正常模式白名单 — 用户日常工具，开箱即用
_NORMAL_ALLOWED_TOOL_NAMES = frozenset({
    # 文件读写
    "read_file", "write_file", "edit_file", "list_dir", "glob", "grep",
    # 搜索
    "web_search", "web_fetch", "session_search",
    # 执行
    "exec",
    # 任务管理
    "long_task", "complete_goal",
    # 用户交互
    "ask_user",
    # 辅助
    "content_read", "notebook_edit",
    # 运行时/自省
    "runtime_status", "tool_audit_summary",
    "my",
    # 图片生成（实际可用性由父级 image_generation.enabled 控制）
    "generate_image",
})

# 限制模式白名单 — 只读文件 + 部分辅助
_RESTRICTED_ALLOWED_TOOL_NAMES = frozenset({
    "read_file", "list_dir", "glob", "grep",
    "ask_user",
})

# 默认排除的高风险工具
_EXCLUDED_TOOL_NAMES = frozenset({
    "spawn",       # 子代理嵌套子代理
    "cron",        # 持久化定时任务
    "message",     # 主动发消息
})

# 默认白名单（normal 模式）
_DEFAULT_ALLOWED_TOOL_NAMES = frozenset(
    _NORMAL_ALLOWED_TOOL_NAMES
)


@dataclass(frozen=True)
class SubagentPolicy:
    """Explicit delegated runtime policy for subagents."""

    capability_snapshot: CapabilitySnapshot
    allowed_tool_names: frozenset[str]
    allow_web: bool = True
    allow_nested_spawn: bool = False
    max_subagent_depth: int = 3
    max_children_per_subagent: int = 3
    child_allowed_tool_names: frozenset[str] | None = None

    @classmethod
    def default_for_parent(
        cls,
        parent_snapshot: CapabilitySnapshot | None,
    ) -> "SubagentPolicy":
        """Legacy alias — defaults to *normal* mode."""
        return cls.from_config(parent_snapshot, mode="normal")

    @classmethod
    def from_config(
        cls,
        parent_snapshot: CapabilitySnapshot | None,
        mode: str = "normal",
    ) -> "SubagentPolicy":
        """Build a policy from a parent capability snapshot and a mode string.

        Args:
            parent_snapshot: The parent agent's capability snapshot.
            mode: ``"normal"`` (default) or ``"restricted"``.

        Returns:
            A frozen ``SubagentPolicy`` instance.
        """
        parent = parent_snapshot or CapabilitySnapshot.system_default()
        snapshot = parent.derive_subagent()
        if mode == "restricted":
            allowed = _RESTRICTED_ALLOWED_TOOL_NAMES if snapshot.can_read_files else frozenset()
        else:
            allowed = _NORMAL_ALLOWED_TOOL_NAMES
        return cls(
            capability_snapshot=snapshot,
            allowed_tool_names=allowed,
            allow_web=mode != "restricted",
            allow_nested_spawn=False,
            max_subagent_depth=1,
            max_children_per_subagent=0,
            child_allowed_tool_names=None,
        )

    def allows(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tool_names

    def child_policy(self) -> "SubagentPolicy":
        """Return the downgraded policy for one nested delegated level."""
        child_snapshot = self.capability_snapshot.derive_subagent()
        child_tools = self.child_allowed_tool_names
        if child_tools is None:
            child_tools = self.allowed_tool_names if child_snapshot.can_read_files else frozenset()
        return SubagentPolicy(
            capability_snapshot=child_snapshot,
            allowed_tool_names=child_tools,
            allow_web=self.allow_web,
            allow_nested_spawn=self.allow_nested_spawn and self.max_subagent_depth > 1,
            max_subagent_depth=max(0, self.max_subagent_depth - 1),
            max_children_per_subagent=self.max_children_per_subagent,
            child_allowed_tool_names=self.child_allowed_tool_names,
        )
