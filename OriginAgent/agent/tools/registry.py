"""Tool registry for dynamic tool management."""

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

from OriginAgent.agent.confirmation import ConfirmationManager
from OriginAgent.agent.tools.audit import ToolAuditConfig, ToolAuditSink, ToolCallAuditEvent
from OriginAgent.agent.tools.base import Tool
from OriginAgent.agent.tools.security import (
    CAPABILITY_REQUIRED_CLASSES,
    SECURITY_AUDIT_CLASSES,
    ToolSecurityClass,
)
from OriginAgent.security.capabilities import CapabilitySnapshot, intersect_capability_snapshots
from OriginAgent.security.grants import CapabilityGrant, CapabilityGrantStore
from OriginAgent.security.policy import PolicyDeniedError


class DuplicateToolError(ValueError):
    """Raised when a tool registration would overwrite an existing tool."""


_RETRY_HINT = "\n\n[Analyze the error above and try a different approach.]"
_POLICY_DENIAL_PHRASES = (
    "outside allowed directory",
    "hard policy boundary",
    "private url",
    "internal/private url",
    "workspace-boundary",
    "path traversal",
    "safety guard",
    "protected and cannot be modified",
    "read-only and cannot be modified",
    "capability snapshot",
    "capability_snapshot_required",
    "not allowed by the current capability snapshot",
)


@dataclass(frozen=True)
class ToolAuditContext:
    actor_id_hash: str | None = None
    session_key_hash: str | None = None
    subagent_task_id: str | None = None
    parent_session_key_hash: str | None = None
    origin_channel: str | None = None
    origin_chat_id_hash: str | None = None


@dataclass(frozen=True)
class ToolRuntimeContext:
    actor_id: str | None = None
    session_key: str | None = None
    trigger: str | None = None
    channel: str | None = None
    chat_id: str | None = None


class ToolExecutionObserver(Protocol):
    def on_tool_result(
        self,
        *,
        name: str,
        params: dict[str, Any],
        status: str,
        start: float,
        error_kind: str | None = None,
        policy_rule: str | None = None,
        result: Any = None,
    ) -> None:
        ...



_DOMAIN_PERMISSION_POLICY_RULES = {
    "read_files": ("can_read_files", "capability_domain_read_files_denied"),
    "write_files": ("can_write_files", "capability_domain_write_files_denied"),
    "exec": ("can_exec", "capability_domain_exec_denied"),
    "send_cross_target": (
        "can_send_cross_target",
        "capability_domain_send_cross_target_denied",
    ),
    "create_cron": ("can_create_cron", "capability_domain_create_cron_denied"),
    "spawn": ("can_spawn", "capability_domain_spawn_denied"),
}


def is_policy_denial_text(text: str) -> bool:
    """Return True for explicit safety-boundary error strings.

    Keep this intentionally conservative.  Do not match broad words such as
    "protected", "read-only", or "not accessible" by themselves.
    """
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped.lower().startswith("error"):
        return False
    lowered = stripped.lower()
    return any(phrase in lowered for phrase in _POLICY_DENIAL_PHRASES)


def is_policy_denial_exc(exc: Exception) -> bool:
    if isinstance(exc, PolicyDeniedError):
        return True
    if isinstance(exc, PermissionError):
        return is_policy_denial_text(f"Error: {exc}")
    return False


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(
        self,
        audit_sink: ToolAuditSink | None = None,
        audit_config: ToolAuditConfig | None = None,
        capability_snapshot: CapabilitySnapshot | None = None,
        execution_observer: ToolExecutionObserver | None = None,
        confirmation_manager: ConfirmationManager | None = None,
        grant_store: CapabilityGrantStore | None = None,
    ):
        self._tools: dict[str, Tool] = {}
        self._cached_definitions: list[dict[str, Any]] | None = None
        self._audit_sink = audit_sink
        self._audit_config = ToolAuditConfig.from_config(audit_config)
        self._capability_snapshot = capability_snapshot
        self._audit_context = ToolAuditContext()
        self._execution_observer = execution_observer
        self._confirmation_manager = confirmation_manager
        self._grant_store = grant_store
        self._runtime_context = ToolRuntimeContext()

    def set_capability_snapshot(self, snapshot: CapabilitySnapshot | None) -> None:
        self._capability_snapshot = snapshot
        for tool in self._tools.values():
            if hasattr(tool, "set_capability_snapshot"):
                tool.set_capability_snapshot(snapshot)

    def set_runtime_context(
        self,
        *,
        actor_id: str | None = None,
        session_key: str | None = None,
        trigger: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        self._runtime_context = ToolRuntimeContext(
            actor_id=actor_id,
            session_key=session_key,
            trigger=trigger,
            channel=channel,
            chat_id=chat_id,
        )

    @property
    def runtime_context(self) -> ToolRuntimeContext:
        return self._runtime_context

    def set_audit_context(
        self,
        *,
        actor_id: str | None = None,
        session_key: str | None = None,
        subagent_task_id: str | None = None,
        parent_session_key: str | None = None,
        origin_channel: str | None = None,
        origin_chat_id: str | None = None,
    ) -> None:
        self._audit_context = ToolAuditContext(
            actor_id_hash=_safe_hash(actor_id),
            session_key_hash=_safe_hash(session_key),
            subagent_task_id=subagent_task_id,
            parent_session_key_hash=_safe_hash(parent_session_key),
            origin_channel=origin_channel,
            origin_chat_id_hash=_safe_hash(origin_chat_id),
        )

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        if tool.name in self._tools:
            raise DuplicateToolError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    @staticmethod
    def _schema_name(schema: dict[str, Any]) -> str:
        """Extract a normalized tool name from either OpenAI or flat schemas."""
        fn = schema.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str):
                return name
        name = schema.get("name")
        return name if isinstance(name, str) else ""

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get tool definitions with stable ordering for cache-friendly prompts.

        Built-in tools are sorted first as a stable prefix, then MCP tools are
        sorted and appended.  The result is cached until the next
        register/unregister call.
        """
        if self._cached_definitions is not None:
            return self._cached_definitions

        definitions = [tool.to_schema() for tool in self._tools.values()]
        builtins: list[dict[str, Any]] = []
        mcp_tools: list[dict[str, Any]] = []
        for schema in definitions:
            name = self._schema_name(schema)
            if name.startswith("mcp_"):
                mcp_tools.append(schema)
            else:
                builtins.append(schema)

        builtins.sort(key=self._schema_name)
        mcp_tools.sort(key=self._schema_name)
        self._cached_definitions = builtins + mcp_tools
        return self._cached_definitions

    def prepare_call(
        self,
        name: str,
        params: dict[str, Any],
    ) -> tuple[Tool | None, dict[str, Any], str | None]:
        """Resolve, cast, and validate one tool call."""
        tool = self._tools.get(name)
        if not tool:
            return None, params, (
                f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"
            )

        if not isinstance(params, dict):
            return None, {}, (
                f"Error: Tool '{name}' parameters must be a JSON object, "
                f"got {type(params).__name__}. Use named parameters."
            )

        cast_params = tool.cast_params(params)
        errors = tool.validate_params(cast_params)
        if errors:
            return tool, cast_params, (
                f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            )
        try:
            self._assert_capability(tool, cast_params)
        except PolicyDeniedError as exc:
            return tool, cast_params, f"Error: {exc}"
        return tool, cast_params, None

    async def execute(self, name: str, params: dict[str, Any]) -> Any:
        """Execute a tool by name with given parameters."""
        start = time.monotonic()
        tool, params, error = self.prepare_call(name, params)
        if error:
            status = "policy_denied" if is_policy_denial_text(error) else "validation_error"
            await self.audit_tool_result_async(
                name=name,
                tool=tool,
                status=status,
                start=start,
                error_kind="validation_error",
                policy_rule=policy_rule_from_error_text(error),
                params=params if isinstance(params, dict) else {},
            )
            return error if is_policy_denial_text(error) else error + _RETRY_HINT

        try:
            assert tool is not None  # guarded by prepare_call()
            result = await tool.execute(**params)
            if isinstance(result, str) and result.startswith("Error"):
                policy_denied = is_policy_denial_text(result)
                await self.audit_tool_result_async(
                    name=name,
                    tool=tool,
                    status="policy_denied" if policy_denied else "error",
                    start=start,
                    error_kind="tool_error",
                    policy_rule=policy_rule_from_error_text(result),
                    params=params,
                )
                return result if policy_denied else result + _RETRY_HINT
            await self.audit_tool_result_async(
                name=name,
                tool=tool,
                status="success",
                start=start,
                params=params,
                result=result,
            )
            return result
        except BaseException as e:
            if type(e).__name__ == "AskUserInterrupt":
                await self.audit_tool_result_async(
                    name=name,
                    tool=tool,
                    status="interrupted",
                    start=start,
                    error_kind=type(e).__name__,
                    params=params,
                )
                raise
            if not isinstance(e, Exception):
                raise
            error_text = f"Error executing {name}: {str(e)}"
            policy_denied = is_policy_denial_exc(e)
            policy_rule = e.policy_rule if isinstance(e, PolicyDeniedError) else None
            await self.audit_tool_result_async(
                name=name,
                tool=tool,
                status="policy_denied" if policy_denied else "error",
                start=start,
                error_kind=type(e).__name__,
                policy_rule=policy_rule,
                params=params,
            )
            return error_text if policy_denied else error_text + _RETRY_HINT

    def _assert_capability(self, tool: Tool, params: dict[str, Any] | None = None) -> None:
        snapshot = self._capability_snapshot
        name = tool.name
        if snapshot is None:
            if _requires_capability_snapshot(name, self) or tuple(
                getattr(tool, "_domain_tool_permissions", ()) or ()
            ):
                raise PolicyDeniedError(
                    f"Tool '{name}' requires an explicit capability snapshot",
                    code="capability_snapshot_missing",
                    boundary="capability",
                    policy_rule="capability_snapshot_required",
                )
            return
        snapshot = self._effective_snapshot_for_tool(snapshot, name)
        if hasattr(tool, "set_capability_snapshot"):
            tool.set_capability_snapshot(snapshot)
        _assert_domain_tool_capability(tool, snapshot)
        if name == "exec" and not snapshot.can_exec:
            self._raise_or_request_tool_approval(
                tool=tool,
                params=params or {},
                message="Tool 'exec' is not allowed by the current capability snapshot",
                policy_rule="capability_exec_denied",
                grant_flags={"can_exec": True},
            )
        if name in {"read_file", "session_search", "list_dir", "glob", "grep", "notebook_read"} and not snapshot.can_read_files:
            raise PolicyDeniedError(
                f"Tool '{name}' cannot read files under the current capability snapshot",
                code="capability_denied",
                boundary="capability",
                policy_rule="capability_file_read_denied",
            )
        if name in {"write_file", "edit_file", "notebook_edit"} and not snapshot.can_write_files:
            raise PolicyDeniedError(
                f"Tool '{name}' cannot write files under the current capability snapshot",
                code="capability_denied",
                boundary="capability",
                policy_rule="capability_file_write_denied",
            )
        if name == "message" and not snapshot.can_send_cross_target:
            if self._is_cross_target_message(params or {}):
                if bool(getattr(tool, "has_cross_target_grant", lambda: False)()):
                    return
                self._raise_or_request_tool_approval(
                    tool=tool,
                    params=params or {},
                    message="Cross-target message sends require a runtime grant",
                    policy_rule="message_cross_target_grant_required",
                    grant_flags={"can_send_cross_target": True},
                )
            return
        if name == "cron" and not snapshot.can_create_cron:
            self._raise_or_request_tool_approval(
                tool=tool,
                params=params or {},
                message="Creating cron jobs is not allowed by the current capability snapshot",
                policy_rule="capability_cron_denied",
                grant_flags={"can_create_cron": True},
            )
        if name == "spawn" and not snapshot.can_spawn:
            self._raise_or_request_tool_approval(
                tool=tool,
                params=params or {},
                message="Spawning subagents is not allowed by the current capability snapshot",
                policy_rule="capability_spawn_denied",
                grant_flags={"can_spawn": True},
            )
        if name.startswith("originagent_device_"):
            allowed = snapshot.allowed_device_domains
            if not allowed:
                raise PolicyDeniedError(
                    "Device tools are not allowed by the current capability snapshot",
                    code="capability_denied",
                    boundary="capability",
                    policy_rule="capability_device_denied",
                )
        if name.startswith("mcp_"):
            if hasattr(tool, "set_capability_snapshot"):
                tool.set_capability_snapshot(snapshot)
            if "read" not in snapshot.allowed_mcp_scopes:
                raise PolicyDeniedError(
                    "MCP tools are not allowed by the current capability snapshot",
                    code="capability_denied",
                    boundary="capability",
                    policy_rule="capability_mcp_denied",
                )
            return

    def _raise_or_request_tool_approval(
        self,
        *,
        tool: Tool,
        params: dict[str, Any],
        message: str,
        policy_rule: str,
        grant_flags: dict[str, Any],
    ) -> None:
        grant = self._existing_matching_grant(tool.name, policy_rule)
        if grant is not None:
            return
        confirmation = self._create_tool_approval(tool=tool, params=params, grant_flags=grant_flags)
        if confirmation is not None:
            raise PolicyDeniedError(
                (
                    f"{message}. Approval required: {confirmation.prompt} "
                    f"(confirmation_id={confirmation.confirmation_id})"
                ),
                code="tool_approval_required",
                boundary="capability",
                policy_rule=policy_rule,
            )
        raise PolicyDeniedError(
            message,
            code="capability_denied",
            boundary="capability",
            policy_rule=policy_rule,
        )

    def _existing_matching_grant(
        self,
        tool_name: str,
        policy_rule: str,
    ) -> CapabilityGrant | None:
        store = self._grant_store
        session_key = self._runtime_context.session_key
        if store is None or not session_key:
            return None
        grant = store.latest_active_for_session(session_key, tool_name=tool_name)
        if grant is None:
            return None
        metadata = dict(grant.metadata or {})
        if metadata.get("policy_rule") and metadata.get("policy_rule") != policy_rule:
            return None
        return grant

    def _create_tool_approval(
        self,
        *,
        tool: Tool,
        params: dict[str, Any],
        grant_flags: dict[str, Any],
    ):
        manager = self._confirmation_manager
        session_key = self._runtime_context.session_key
        if manager is None or not session_key:
            return None
        prompt = _tool_approval_prompt(tool.name, params)
        decision_reason = f"tool '{tool.name}' requires explicit approval before use"
        idempotency_key = _tool_approval_idempotency_key(
            session_key=session_key,
            tool_name=tool.name,
            params=params,
            grant_flags=grant_flags,
        )
        metadata = {
            "session_key": session_key,
            "tool_name": tool.name,
        }
        return manager.create_tool_approval(
            tool_name=tool.name,
            prompt=prompt,
            decision_reason=decision_reason,
            requested_by=self._runtime_context.actor_id,
            trigger=self._runtime_context.trigger,
            session_key=session_key,
            metadata=metadata | {"policy_rule": _policy_rule_for_tool_approval(tool.name, grant_flags)},
            action_payload={
                "tool_name": tool.name,
                "params_summary": _tool_param_summary(params),
                "grant_flags": json.dumps(grant_flags, ensure_ascii=False, sort_keys=True),
            },
            idempotency_key=idempotency_key,
        )

    def _effective_snapshot_for_tool(
        self,
        snapshot: CapabilitySnapshot,
        tool_name: str,
    ) -> CapabilitySnapshot:
        grant = self._grant_for_tool(tool_name)
        if grant is None:
            return snapshot
        grant_snapshot = grant.to_snapshot(trigger=snapshot.trigger)
        return CapabilitySnapshot(
            version=snapshot.version,
            source=snapshot.source,
            trigger=snapshot.trigger,
            can_exec=snapshot.can_exec or grant_snapshot.can_exec,
            can_read_files=snapshot.can_read_files or grant_snapshot.can_read_files,
            can_write_files=snapshot.can_write_files or grant_snapshot.can_write_files,
            can_send_cross_target=(
                snapshot.can_send_cross_target or grant_snapshot.can_send_cross_target
            ),
            can_create_cron=snapshot.can_create_cron or grant_snapshot.can_create_cron,
            can_spawn=snapshot.can_spawn or grant_snapshot.can_spawn,
            allowed_device_domains=tuple(
                sorted(set(snapshot.allowed_device_domains) | set(grant_snapshot.allowed_device_domains))
            ),
            allowed_mcp_scopes=tuple(
                sorted(set(snapshot.allowed_mcp_scopes) | set(grant_snapshot.allowed_mcp_scopes))
            ),
        )

    def _grant_for_tool(self, tool_name: str) -> CapabilityGrant | None:
        store = self._grant_store
        session_key = self._runtime_context.session_key
        if store is None or not session_key:
            return None
        return store.latest_active_for_session(session_key, tool_name=tool_name)

    def _is_cross_target_message(self, params: dict[str, Any]) -> bool:
        current_channel = self._runtime_context.channel
        current_chat_id = self._runtime_context.chat_id
        if not current_channel or not current_chat_id:
            return False
        target_channel = str(params.get("channel") or current_channel)
        target_chat_id = str(params.get("chat_id") or current_chat_id)
        return target_channel != current_channel or target_chat_id != current_chat_id

    def _audit_tool_call(
        self,
        *,
        name: str,
        tool: Tool | None,
        status: str,
        start: float,
        error_kind: str | None = None,
        policy_rule: str | None = None,
        target_kind: str | None = None,
        target_hash: str | None = None,
        result_size: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        if self._audit_sink is None:
            return
        tier = self._audit_tier_for(name, status)
        if tier == "off":
            return
        try:
            summarized_kind: str | None = None
            summarized_hash: str | None = None
            if tier == "security":
                summarized_kind, summarized_hash = summarize_tool_target(name, params or {})
            self._audit_sink.record(
                ToolCallAuditEvent(
                    tool_name=name,
                    status=status,  # type: ignore[arg-type]
                    duration_ms=max(0, int((time.monotonic() - start) * 1000)),
                    read_only=bool(tool.read_only) if tool is not None else False,
                    exclusive=bool(tool.exclusive) if tool is not None else False,
                    error_kind=error_kind,
                    actor_id_hash=self._audit_context.actor_id_hash if tier == "security" else None,
                    session_key_hash=self._audit_context.session_key_hash if tier == "security" else None,
                    subagent_task_id=self._audit_context.subagent_task_id if tier == "security" else None,
                    parent_session_key_hash=(
                        self._audit_context.parent_session_key_hash if tier == "security" else None
                    ),
                    origin_channel=self._audit_context.origin_channel if tier == "security" else None,
                    origin_chat_id_hash=(
                        self._audit_context.origin_chat_id_hash if tier == "security" else None
                    ),
                    policy_rule=policy_rule if tier == "security" else None,
                    target_kind=(target_kind or summarized_kind) if tier == "security" else None,
                    target_hash=(target_hash or summarized_hash) if tier == "security" else None,
                    result_size=result_size if tier == "security" else None,
                )
            )
        except Exception:
            pass

    async def _audit_tool_call_async(
        self,
        **kwargs: Any,
    ) -> None:
        await asyncio.to_thread(self._audit_tool_call, **kwargs)

    def _audit_tier_for(
        self,
        name: str,
        status: str,
    ) -> Literal["off", "minimal", "security"]:
        config = self._audit_config
        if config.mode == "off":
            return "off"
        if config.mode == "security":
            return "security"
        tool = self._tools.get(name)
        if getattr(tool, "_domain_tool_audit", None) == "security":
            return "security"
        if status == "policy_denied" and config.security_on_policy_denial:
            return "security"
        if _matches_security_tool(name, config.security_tools):
            return "security"
        return "minimal"

    def audit_tool_result(
        self,
        *,
        name: str,
        tool: Tool | None,
        params: dict[str, Any],
        status: str,
        start: float,
        error_kind: str | None = None,
        policy_rule: str | None = None,
        result: Any = None,
    ) -> None:
        self._audit_tool_call(
            name=name,
            tool=tool,
            status=status,
            start=start,
            error_kind=error_kind,
            policy_rule=policy_rule,
            params=params,
            result_size=_safe_result_size(result) if status == "success" else None,
        )
        self._notify_execution_observer(
            name=name,
            params=params,
            status=status,
            start=start,
            error_kind=error_kind,
            policy_rule=policy_rule,
            result=result,
        )

    async def audit_tool_result_async(
        self,
        *,
        name: str,
        tool: Tool | None,
        params: dict[str, Any],
        status: str,
        start: float,
        error_kind: str | None = None,
        policy_rule: str | None = None,
        result: Any = None,
    ) -> None:
        await self._audit_tool_call_async(
            name=name,
            tool=tool,
            status=status,
            start=start,
            error_kind=error_kind,
            policy_rule=policy_rule,
            params=params,
            result_size=_safe_result_size(result) if status == "success" else None,
        )
        await asyncio.to_thread(
            self._notify_execution_observer,
            name=name,
            params=params,
            status=status,
            start=start,
            error_kind=error_kind,
            policy_rule=policy_rule,
            result=result,
        )

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def _notify_execution_observer(
        self,
        *,
        name: str,
        params: dict[str, Any],
        status: str,
        start: float,
        error_kind: str | None = None,
        policy_rule: str | None = None,
        result: Any = None,
    ) -> None:
        observer = self._execution_observer
        if observer is None:
            return
        try:
            observer.on_tool_result(
                name=name,
                params=params,
                status=status,
                start=start,
                error_kind=error_kind,
                policy_rule=policy_rule,
                result=result,
            )
        except Exception:
            pass


def _safe_hash(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _requires_capability_snapshot(name: str, tools: "ToolRegistry | None" = None) -> bool:
    """Check if a tool requires a capability snapshot.

    Uses the tool's declared ``security_class`` if the tool is registered,
    falls back to name-based matching for dynamic/prepare-call scenarios.
    """
    if tools is not None:
        tool = tools.get(name)
        if tool is not None:
            cls = getattr(tool, "security_class", None)
            if cls is not None:
                return cls in CAPABILITY_REQUIRED_CLASSES
    # Fallback: name-based matching for tools not yet registered or unclassified.
    return (
        name in _CAPABILITY_REQUIRED_TOOL_NAME_FALLBACK
        or name.startswith("originagent_device_")
        or name.startswith("mcp_")
    )


# Minimal name-based fallback set for the prepare_call path
# where the tool may not be registered yet.
_CAPABILITY_REQUIRED_TOOL_NAME_FALLBACK: set[str] = {
    "exec", "read_file", "list_dir", "glob", "grep",
    "notebook_read", "write_file", "edit_file", "notebook_edit",
    "message", "cron", "spawn",
}


def _assert_domain_tool_capability(tool: Tool, snapshot: CapabilitySnapshot) -> None:
    evolution_snapshot = getattr(tool, "_evolution_capability_snapshot", None)
    if isinstance(evolution_snapshot, CapabilitySnapshot):
        snapshot = intersect_capability_snapshots(
            snapshot,
            evolution_snapshot,
            source=snapshot.source,
            trigger=snapshot.trigger,
        )
    permissions = tuple(getattr(tool, "_domain_tool_permissions", ()) or ())
    if not permissions:
        return
    name = tool.name
    for permission in permissions:
        if permission in _DOMAIN_PERMISSION_POLICY_RULES:
            attr, policy_rule = _DOMAIN_PERMISSION_POLICY_RULES[permission]
            if not bool(getattr(snapshot, attr, False)):
                raise PolicyDeniedError(
                    f"Tool '{name}' is not allowed by the current capability snapshot ({policy_rule})",
                    code="capability_denied",
                    boundary="capability",
                    policy_rule=policy_rule,
                )
            continue
        if permission.startswith("device:"):
            domain = permission.split(":", 1)[1]
            if domain not in snapshot.allowed_device_domains:
                raise PolicyDeniedError(
                    "Tool "
                    f"'{name}' is not allowed by the current capability snapshot "
                    f"(capability_domain_device_{domain}_denied)",
                    code="capability_denied",
                    boundary="capability",
                    policy_rule=f"capability_domain_device_{domain}_denied",
                )
            continue
        if permission == "mcp:read":
            if "read" not in snapshot.allowed_mcp_scopes:
                raise PolicyDeniedError(
                    "Tool "
                    f"'{name}' is not allowed by the current capability snapshot "
                    "(capability_domain_mcp_read_denied)",
                    code="capability_denied",
                    boundary="capability",
                    policy_rule="capability_domain_mcp_read_denied",
                )


def _matches_security_tool(name: str, patterns: tuple[str, ...]) -> bool:
    return any(
        name == pattern
        or (pattern.endswith("*") and name.startswith(pattern[:-1]))
        for pattern in patterns
    )


def policy_rule_from_error_text(text: str | None) -> str | None:
    """Infer a structured policy rule from legacy string-only denials."""

    if not text:
        return None
    lowered = text.lower()
    if "capability_snapshot_required" in lowered or "requires an explicit capability snapshot" in lowered:
        return "capability_snapshot_required"
    if "tool 'exec' is not allowed by the current capability snapshot" in lowered:
        return "capability_exec_denied"
    if "cannot read files under the current capability snapshot" in lowered:
        return "capability_file_read_denied"
    if "cannot write files under the current capability snapshot" in lowered:
        return "capability_file_write_denied"
    if "creating cron jobs is not allowed by the current capability snapshot" in lowered:
        return "capability_cron_denied"
    if "spawning subagents is not allowed by the current capability snapshot" in lowered:
        return "capability_spawn_denied"
    for rule in (
        "capability_domain_read_files_denied",
        "capability_domain_write_files_denied",
        "capability_domain_exec_denied",
        "capability_domain_send_cross_target_denied",
        "capability_domain_create_cron_denied",
        "capability_domain_spawn_denied",
        "capability_domain_device_lighting_denied",
        "capability_domain_mcp_read_denied",
    ):
        if rule in lowered:
            return rule
    if "device tools are not allowed by the current capability snapshot" in lowered:
        return "capability_device_denied"
    if "mcp tools are not allowed by the current capability snapshot" in lowered:
        return "capability_mcp_denied"
    if "protected runtime state and cannot be read" in lowered:
        return "protected_path_read"
    if "protected runtime state and cannot be modified" in lowered:
        return "protected_path_write"
    if "read-only tool root and cannot be modified" in lowered:
        return "read_only_root_write"
    if "internal/private url" in lowered or "private/internal address" in lowered:
        return "ssrf_denied"
    if is_policy_denial_text(text):
        return "policy_denied"
    return None


def _safe_result_size(result: Any) -> int | None:
    if result is None:
        return 0
    if isinstance(result, (str, bytes, bytearray)):
        return len(result)
    try:
        return len(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        return None


def summarize_tool_target(name: str, params: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return a privacy-preserving target kind and hash for audit events."""

    target: Any | None = None
    kind: str | None = None
    if name in {"read_file", "write_file", "edit_file", "notebook_read", "notebook_edit"}:
        kind = "file"
        target = params.get("path") or params.get("file_path") or params.get("notebook_path")
    elif name in {"list_dir", "glob", "grep"}:
        kind = "file"
        target = params.get("path") or params.get("directory") or params.get("root")
    elif name in {"web_fetch", "content_read"}:
        kind = "url"
        url = str(params.get("url") or "")
        parsed = urlparse(url)
        target = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.netloc else url
    elif name == "message":
        kind = "channel"
        target = f"{params.get('channel') or ''}:{params.get('chat_id') or ''}"
    elif name == "cron":
        kind = "cron"
        target = params.get("action") or params.get("name") or params.get("id")
    elif name == "exec":
        kind = "command"
        command = str(params.get("command") or "")
        target = _command_shape(command)
    elif name.startswith("originagent_device_"):
        kind = "device"
        target = params.get("device_ref") or params.get("device_id") or params.get("room")
    elif name.startswith("mcp_"):
        kind = "mcp"
        target = name
    if kind is None or target in (None, ""):
        return kind, None
    return kind, _safe_hash(target)


def _command_shape(command: str) -> str:
    words = command.strip().split()
    if not words:
        return ""
    operators = "".join(ch for ch in command if ch in "|&;<>")
    return f"{words[0]}:{len(words)}:{operators[:16]}"


def _tool_param_summary(params: dict[str, Any]) -> str:
    if not isinstance(params, dict) or not params:
        return "{}"
    summary: dict[str, str] = {}
    for key, value in sorted(params.items()):
        if value is None:
            continue
        text = str(value)
        if len(text) > 80:
            text = text[:77] + "..."
        summary[str(key)] = text
    return json.dumps(summary, ensure_ascii=False, sort_keys=True)


def _tool_approval_prompt(tool_name: str, params: dict[str, Any]) -> str:
    summary = _tool_param_summary(params)
    return (
        f"The agent wants to use high-risk tool '{tool_name}'. "
        f"Approve this operation for the current session? Parameters: {summary}"
    )


def _tool_approval_idempotency_key(
    *,
    session_key: str,
    tool_name: str,
    params: dict[str, Any],
    grant_flags: dict[str, Any],
) -> str:
    payload = json.dumps(
        {
            "session_key": session_key,
            "tool_name": tool_name,
            "params": params,
            "grant_flags": grant_flags,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"tool_approval:{tool_name}:{digest}"


def _policy_rule_for_tool_approval(tool_name: str, grant_flags: dict[str, Any]) -> str:
    if tool_name == "exec" or grant_flags.get("can_exec"):
        return "capability_exec_denied"
    if tool_name == "cron" or grant_flags.get("can_create_cron"):
        return "capability_cron_denied"
    if tool_name == "spawn" or grant_flags.get("can_spawn"):
        return "capability_spawn_denied"
    if tool_name == "message" or grant_flags.get("can_send_cross_target"):
        return "message_cross_target_grant_required"
    return "tool_approval_required"
