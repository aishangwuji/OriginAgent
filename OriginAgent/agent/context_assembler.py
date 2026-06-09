"""Phase 1 context assembler for continuity-aware prompt construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContextAssemblyResult:
    blocks: list[dict[str, Any]] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)


class ContextAssemblerV2:
    """Build the user-side context blocks for Phase 1 continuity."""

    def __init__(self, builder: Any) -> None:
        self._builder = builder

    def assemble(
        self,
        *,
        current_message: str | None,
        media: list[str] | None,
        channel: str | None,
        chat_id: str | None,
        sender_id: str | None,
        session_summary: str | None,
        session_metadata: dict[str, Any] | None,
        internal_event: tuple[str, str] | None,
        runtime_context: Any | None,
        session_key: str | None,
        include_current_message: bool = True,
    ) -> ContextAssemblyResult:
        user_content = (
            self._builder._build_user_content(current_message, media)
            if include_current_message
            else []
        )

        runtime_extra_lines = None
        if runtime_context is not None:
            runtime_extra_lines = [
                f"Identity user_id: {runtime_context.user_id}",
                f"Identity session_id: {runtime_context.session_id or ''}",
                f"Identity device_id: {runtime_context.device_id or ''}",
                f"Default scope: {runtime_context.default_scope}",
                f"Trigger: {runtime_context.trigger}",
            ]

        runtime_block = self._builder.build_runtime_context_block(
            channel,
            chat_id,
            self._builder.timezone,
            sender_id=sender_id,
            session_metadata=session_metadata,
            extra_lines=runtime_extra_lines,
        )
        continuity_blocks = self._builder.build_phase1_continuity_blocks(
            session_key=session_key,
            runtime_context=runtime_context,
        )
        reference_blocks = self._builder.build_reference_context_blocks(
            session_summary=session_summary,
            session_key=session_key,
            runtime_context=runtime_context,
        )

        merged: list[dict[str, Any]] = [
            runtime_block,
            *continuity_blocks,
            *reference_blocks,
        ]
        if internal_event is not None:
            source, content = internal_event
            if content:
                merged.append(self._builder.build_internal_event_block(source, content))
        merged.extend(user_content)

        audit = {
            "enabled": True,
            "session_key": session_key,
            "current_message_included": include_current_message,
            "current_message_preview": str(current_message or "").strip()[:200],
            "block_kinds": [
                block.get("_meta", {}).get("kind")
                for block in merged
                if isinstance(block, dict)
            ],
            "reference_sources": [
                block.get("_meta", {}).get("source")
                for block in reference_blocks
                if isinstance(block, dict)
            ],
            "internal_event_source": internal_event[0] if internal_event is not None else None,
            "runtime_context": (
                {
                    "actor_id": runtime_context.actor_id,
                    "user_id": runtime_context.user_id,
                    "session_id": runtime_context.session_id,
                    "device_id": runtime_context.device_id,
                    "trigger": runtime_context.trigger,
                    "source": runtime_context.source,
                    "scope": runtime_context.default_scope,
                }
                if runtime_context is not None
                else {}
            ),
            "working_memory_included": any(
                block.get("_meta", {}).get("kind") == self._builder.WORKING_MEMORY_CONTEXT_KIND
                for block in continuity_blocks
                if isinstance(block, dict)
            ),
            "world_state_included": any(
                block.get("_meta", {}).get("kind") == self._builder.WORLD_STATE_CONTEXT_KIND
                for block in continuity_blocks
                if isinstance(block, dict)
            ),
        }
        return ContextAssemblyResult(blocks=merged, audit=audit)
