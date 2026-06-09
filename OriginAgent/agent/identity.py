"""Runtime actor identity resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from OriginAgent.agent.scope import IdentityDescriptor, ScopeResolver


RuntimeTrigger = Literal["user_initiated", "scheduled", "system", "subagent"]
RuntimeSource = Literal["user_turn", "cron", "system", "subagent"]


@dataclass(frozen=True)
class RuntimeContext:
    actor_id: str
    user_id: str
    session_id: str | None
    device_id: str | None
    trigger: RuntimeTrigger
    channel: str
    chat_id: str
    session_key: str | None = None
    source: RuntimeSource = "user_turn"
    default_scope: str = "session"

    @property
    def identity(self) -> IdentityDescriptor:
        return IdentityDescriptor(
            actor_id=self.actor_id,
            user_id=self.user_id,
            session_id=self.session_id,
            device_id=self.device_id,
        )


class ActorResolver:
    def resolve(
        self,
        *,
        channel: str,
        chat_id: str,
        sender_id: str,
        metadata: dict,
    ) -> str:
        return self.resolve_runtime_context(
            channel=channel,
            chat_id=chat_id,
            sender_id=sender_id,
            metadata=metadata,
        ).actor_id

    def resolve_runtime_context(
        self,
        *,
        channel: str,
        chat_id: str,
        sender_id: str,
        metadata: dict,
        session_key: str | None = None,
        routing_channel: str | None = None,
        routing_chat_id: str | None = None,
    ) -> RuntimeContext:
        sender = str(sender_id or "").strip()
        actor_id = sender or "unknown"
        normalized_channel = str(channel or "").strip()
        context_channel = str(routing_channel or normalized_channel or "").strip()
        context_chat_id = str(routing_chat_id if routing_chat_id is not None else chat_id or "")
        metadata = metadata if isinstance(metadata, dict) else {}
        user_id = self._resolve_user_id(sender=sender, metadata=metadata, channel=normalized_channel)
        session_id = str(session_key or "").strip() or None
        device_id = self._resolve_device_id(metadata=metadata, channel=normalized_channel, chat_id=context_chat_id)
        if sender == "subagent" or metadata.get("injected_event") == "subagent_result":
            trigger: RuntimeTrigger = "subagent"
            source: RuntimeSource = "subagent"
        elif normalized_channel == "cron":
            trigger = "scheduled"
            source = "cron"
        elif normalized_channel == "system":
            trigger = "system"
            source = "system"
        else:
            trigger = "user_initiated"
            source = "user_turn"
        return RuntimeContext(
            actor_id=actor_id,
            user_id=user_id,
            session_id=session_id,
            device_id=device_id,
            trigger=trigger,
            channel=context_channel,
            chat_id=context_chat_id,
            session_key=session_key,
            source=source,
            default_scope=self._default_scope(trigger=trigger, metadata=metadata),
        )

    @staticmethod
    def _resolve_user_id(*, sender: str, metadata: dict, channel: str) -> str:
        for key in ("user_id", "from_user_id", "open_id"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
        if sender:
            return sender
        if channel in {"system", "cron"}:
            return channel
        return "unknown"

    @staticmethod
    def _resolve_device_id(*, metadata: dict, channel: str, chat_id: str) -> str | None:
        for key in ("device_id", "context_device_id"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
        if channel in {"websocket", "matrix"} and chat_id:
            return f"{channel}:{chat_id}"
        return None

    @staticmethod
    def _default_scope(*, trigger: RuntimeTrigger, metadata: dict) -> str:
        if metadata.get("scope") is not None:
            return ScopeResolver.normalize_scope(metadata.get("scope"))
        if trigger == "subagent":
            return "task"
        return "session"
