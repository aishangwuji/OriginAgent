"""Runtime actor identity resolution."""

from __future__ import annotations


class ActorResolver:
    def resolve(
        self,
        *,
        channel: str,
        chat_id: str,
        sender_id: str,
        metadata: dict,
    ) -> str:
        sender = str(sender_id or "").strip()
        if sender:
            return sender
        return "unknown"

