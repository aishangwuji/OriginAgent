"""Runtime context helpers for AgentLoop.

Responsibility boundary (spec 1.10 / tech-debt Batch C1):
    Pure, stateless utility functions providing runtime-context plumbing for
    the AgentLoop: ``runtime_chat_id`` (chat-id resolution for runtime
    metadata), ``snapshot_for_trigger`` (capability snapshot selection by
    trigger kind), ``set_tool_context`` (propagate routing info / capability
    snapshot to the tool registry and individual tools), and bus
    progress/retry-wait callbacks.

    Kept as a dedicated module rather than merged into ``context.py``: its
    concern is *per-turn runtime routing & tool wiring*, which is distinct
    from *prompt content assembly* (ContextBuilder) and *token budget
    trimming* (ContextBudgetManager).  The functions are small, cohesive and
    stateless, so an independent module preserves a clean
    single-responsibility boundary — context.py owns the prompt, this module
    owns the per-turn runtime plumbing.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from OriginAgent.agent.identity import RuntimeContext
from OriginAgent.agent.tools.context import RequestContext
from OriginAgent.bus.events import InboundMessage, OutboundMessage
from OriginAgent.bus.queue import MessageBus
from OriginAgent.security.capabilities import CapabilitySnapshot

logger = logging.getLogger(__name__)

# Canonical capability flag keys. A well-formed ``payload_snapshot`` dict
# should contain at least one of these so that ``CapabilitySnapshot.from_dict``
# produces a meaningful snapshot rather than an all-default one. Used by
# ``snapshot_for_trigger`` to detect malformed payloads and fall back safely.
_CAPABILITY_FLAG_KEYS: frozenset[str] = frozenset({
    "can_exec",
    "can_read_files",
    "can_write_files",
    "can_send_cross_target",
    "can_create_cron",
    "can_spawn",
})


def runtime_chat_id(msg: InboundMessage) -> str:
    """Return the chat id shown in runtime metadata for the model."""
    return str(msg.metadata.get("context_chat_id") or msg.chat_id)


def snapshot_for_trigger(
    trigger: str | None,
    payload_snapshot: dict | None = None,
) -> CapabilitySnapshot:
    """Select a ``CapabilitySnapshot`` for the given trigger.

    Resolution order (spec P0-1):
    1. If ``payload_snapshot`` is a non-empty dict, reconstruct a
       ``CapabilitySnapshot`` from it (overriding trigger-based selection).
       - If the dict contains no recognized capability flag keys, log a
         warning and fall back to ``scheduled_default()`` (fail-safe: invalid
         input must not silently grant capabilities — rule 18).
       - If ``CapabilitySnapshot.from_dict`` raises (KeyError/TypeError/
         ValueError), log a warning and fall through to trigger-based logic.
    2. Else, fall back to the existing trigger-based mapping
       (``scheduled_default()`` for ``"scheduled"``, ``user_turn()`` for user,
       etc.).
    """
    if payload_snapshot:
        try:
            if not (_CAPABILITY_FLAG_KEYS & set(payload_snapshot)):
                logger.warning(
                    "payload_snapshot contains no recognized capability flag "
                    "keys; falling back to scheduled_default(); keys=%s",
                    list(payload_snapshot.keys()),
                )
                return CapabilitySnapshot.scheduled_default()
            return CapabilitySnapshot.from_dict(payload_snapshot)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Failed to reconstruct CapabilitySnapshot from payload_snapshot "
                "(%s); falling back to trigger=%r selection",
                exc, trigger,
            )
    if trigger == "scheduled":
        return CapabilitySnapshot.scheduled_default()
    if trigger == "automation":
        return CapabilitySnapshot.automation_lighting()
    if trigger == "subagent":
        return CapabilitySnapshot.system_default().derive_subagent()
    if trigger == "system":
        return CapabilitySnapshot.system_default()
    return CapabilitySnapshot.user_turn()


def set_tool_context(
    tools: Any,
    *,
    channel: str,
    chat_id: str,
    message_id: str | None = None,
    metadata: dict | None = None,
    session_key: str | None = None,
    actor_id: str | None = None,
    trigger: str | None = None,
    capability_snapshot: CapabilitySnapshot | None = None,
    runtime_context: RuntimeContext | None = None,
    unified_session: bool = False,
    unified_session_key: str = "unified:default",
    turn_id: str | None = None,
) -> None:
    """Update context for all tools that need routing info."""
    if runtime_context is not None:
        channel = runtime_context.channel
        chat_id = runtime_context.chat_id
        session_key = runtime_context.session_key
        actor_id = runtime_context.actor_id
        trigger = runtime_context.trigger

    if session_key is not None:
        effective_key = session_key
    elif unified_session:
        effective_key = unified_session_key
    else:
        effective_key = f"{channel}:{chat_id}"

    raw_tools = getattr(tools, "_tools", None)
    if isinstance(raw_tools, dict):
        context_tool_names = [
            name
            for name, tool in raw_tools.items()
            if hasattr(tool, "set_context") or hasattr(tool, "set_capability_snapshot")
        ]
    else:
        candidates = list(getattr(tools, "tool_names", ()) or ())
        if not candidates:
            candidates = ["spawn", "cron", "long_task", "complete_goal", "message", "my"]
        context_tool_names = []
        for name in dict.fromkeys(candidates):
            tool = tools.get(name)
            if tool is not None and (
                hasattr(tool, "set_context") or hasattr(tool, "set_capability_snapshot")
            ):
                context_tool_names.append(name)

    request_ctx = RequestContext(
        channel=channel,
        chat_id=chat_id,
        message_id=message_id,
        session_key=effective_key,
        metadata=metadata or {},
        actor_id=actor_id,
        trigger=trigger,
        capability_snapshot=capability_snapshot,
        runtime_context=runtime_context,
    )
    if hasattr(tools, "set_capability_snapshot"):
        tools.set_capability_snapshot(capability_snapshot)
    if hasattr(tools, "set_runtime_context"):
        tools.set_runtime_context(
            actor_id=actor_id,
            session_key=effective_key,
            trigger=trigger,
            channel=channel,
            chat_id=chat_id,
            turn_id=turn_id,
        )
    if hasattr(tools, "set_audit_context"):
        tools.set_audit_context(actor_id=actor_id, session_key=effective_key)
    for name in context_tool_names:
        if tool := tools.get(name):
            permissions = tuple(getattr(tool, "_domain_tool_permissions", ()) or ())
            if hasattr(tool, "set_capability_snapshot"):
                tool.set_capability_snapshot(capability_snapshot)
            if hasattr(tool, "set_context"):
                if any(permission.startswith("device:") for permission in permissions):
                    try:
                        tool.set_context(request_ctx)
                    except TypeError:
                        if actor_id is not None and trigger is not None:
                            tool.set_context(actor_id, trigger, session_key=effective_key)
                        else:
                            tool.set_context(channel, chat_id)
                elif name == "spawn":
                    tool.set_context(channel, chat_id, effective_key=effective_key)
                    if hasattr(tool, "set_origin_message_id"):
                        tool.set_origin_message_id(message_id)
                elif name == "cron":
                    tool.set_context(channel, chat_id, metadata=metadata, session_key=session_key)
                elif name in {"long_task", "complete_goal"}:
                    tool.set_context(channel, chat_id, session_key=effective_key)
                elif name == "message":
                    tool.set_context(channel, chat_id, message_id, metadata=metadata)
                elif name == "my":
                    tool.set_context(channel, chat_id)
                else:
                    try:
                        tool.set_context(request_ctx)
                    except TypeError:
                        tool.set_context(channel, chat_id)


async def build_bus_progress_callback(
    bus: MessageBus,
    msg: InboundMessage,
) -> Callable[..., Awaitable[None]]:
    """Build a progress callback that publishes to the message bus."""

    async def _bus_progress(
        content: str,
        *,
        tool_hint: bool = False,
        tool_events: list[dict[str, Any]] | None = None,
        reasoning: bool = False,
        reasoning_end: bool = False,
    ) -> None:
        meta = dict(msg.metadata or {})
        meta["_progress"] = True
        meta["_tool_hint"] = tool_hint
        if reasoning:
            meta["_reasoning_delta"] = True
        if reasoning_end:
            meta["_reasoning_end"] = True
        if tool_events:
            meta["_tool_events"] = tool_events
        await bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=content,
                metadata=meta,
            )
        )

    return _bus_progress


async def build_retry_wait_callback(
    bus: MessageBus,
    msg: InboundMessage,
) -> Callable[[str], Awaitable[None]]:
    """Build a retry-wait callback that publishes to the message bus."""

    async def _on_retry_wait(content: str) -> None:
        meta = dict(msg.metadata or {})
        meta["_retry_wait"] = True
        await bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=content,
                metadata=meta,
            )
        )

    return _on_retry_wait
