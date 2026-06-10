"""Prompt-side context budget management for continuity-aware turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from OriginAgent.utils.helpers import estimate_message_tokens, find_legal_message_start


@dataclass(frozen=True)
class ContextBudgetResult:
    messages: list[dict[str, Any]]
    audit: dict[str, Any] = field(default_factory=dict)


class ContextBudgetManager:
    """Trim prompt inputs without mutating persisted session state."""

    def __init__(self, *, safety_buffer_tokens: int = 1024) -> None:
        self._safety_buffer_tokens = max(0, int(safety_buffer_tokens))

    def apply(
        self,
        messages: list[dict[str, Any]],
        *,
        context_window_tokens: int | None,
        max_completion_tokens: int | None,
    ) -> ContextBudgetResult:
        if not messages:
            return ContextBudgetResult(messages=list(messages), audit={"applied": False, "reason": "empty"})

        ctx_total = int(context_window_tokens or 0)
        completion = int(max_completion_tokens or 0)
        budget = ctx_total - completion - self._safety_buffer_tokens
        if budget <= 0:
            return ContextBudgetResult(
                messages=[self._clone_message(message) for message in messages],
                audit={"applied": False, "reason": "no_budget", "budget_tokens": budget},
            )

        trimmed = [self._clone_message(message) for message in messages]
        initial_tokens = self._estimate_messages(trimmed)
        audit: dict[str, Any] = {
            "applied": True,
            "budget_tokens": budget,
            "initial_tokens": initial_tokens,
            "trimmed_history_messages": 0,
            "removed_recent_history_blocks": 0,
            "removed_retrieval_blocks": 0,
            "removed_continuity_reference_blocks": 0,
        }
        if initial_tokens <= budget:
            audit["final_tokens"] = initial_tokens
            audit["reason"] = "within_budget"
            return ContextBudgetResult(messages=trimmed, audit=audit)

        current_index = self._last_user_index(trimmed)
        if current_index is None:
            audit["final_tokens"] = initial_tokens
            audit["reason"] = "no_user_message"
            return ContextBudgetResult(messages=trimmed, audit=audit)

        trimmed, removed_history = self._trim_history(trimmed, current_index, budget)
        audit["trimmed_history_messages"] = removed_history
        current_index = self._last_user_index(trimmed)
        if current_index is None:
            audit["final_tokens"] = self._estimate_messages(trimmed)
            audit["reason"] = "history_trim_exhausted"
            return ContextBudgetResult(messages=trimmed, audit=audit)

        content = trimmed[current_index].get("content")
        if isinstance(content, list):
            content, recent_removed = self._drop_recent_history_blocks(content, budget, trimmed)
            trimmed[current_index]["content"] = content
            audit["removed_recent_history_blocks"] = recent_removed
            if self._estimate_messages(trimmed) > budget:
                content, retrieval_removed = self._drop_retrieval_blocks(content, budget, trimmed)
                trimmed[current_index]["content"] = content
                audit["removed_retrieval_blocks"] = retrieval_removed
            if self._estimate_messages(trimmed) > budget:
                content, continuity_removed = self._drop_continuity_reference_blocks(content, budget, trimmed)
                trimmed[current_index]["content"] = content
                audit["removed_continuity_reference_blocks"] = continuity_removed

        audit["final_tokens"] = self._estimate_messages(trimmed)
        audit["reason"] = "trimmed" if audit["final_tokens"] < initial_tokens else "unchanged"
        return ContextBudgetResult(messages=trimmed, audit=audit)

    @staticmethod
    def _clone_message(message: dict[str, Any]) -> dict[str, Any]:
        cloned = dict(message)
        content = message.get("content")
        if isinstance(content, list):
            cloned["content"] = [dict(block) if isinstance(block, dict) else block for block in content]
        return cloned

    @staticmethod
    def _estimate_messages(messages: list[dict[str, Any]]) -> int:
        return sum(estimate_message_tokens(message) for message in messages)

    @staticmethod
    def _last_user_index(messages: list[dict[str, Any]]) -> int | None:
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "user":
                return index
        return None

    def _trim_history(
        self,
        messages: list[dict[str, Any]],
        current_index: int,
        budget: int,
    ) -> tuple[list[dict[str, Any]], int]:
        if current_index <= 0:
            return messages, 0
        system_messages = [message for message in messages[:current_index] if message.get("role") == "system"]
        history_messages = [message for message in messages[:current_index] if message.get("role") != "system"]
        current_message = messages[current_index]
        trailing_messages = messages[current_index + 1 :]
        if not history_messages:
            return messages, 0

        reserved = self._estimate_messages(system_messages + [current_message] + trailing_messages)
        history_budget = max(0, budget - reserved)
        kept: list[dict[str, Any]] = []
        used = 0
        for message in reversed(history_messages):
            tokens = estimate_message_tokens(message)
            if kept and used + tokens > history_budget:
                break
            kept.append(message)
            used += tokens
        kept.reverse()
        if kept:
            start = find_legal_message_start(kept)
            if start:
                kept = kept[start:]
        removed = max(0, len(history_messages) - len(kept))
        return [*system_messages, *kept, current_message, *trailing_messages], removed

    def _drop_recent_history_blocks(
        self,
        content: list[dict[str, Any]],
        budget: int,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        kept: list[dict[str, Any]] = []
        removed = 0
        for block in content:
            if self._estimate_messages(messages) <= budget:
                kept.append(block)
                continue
            source = self._block_source(block)
            if source == "recent_history":
                removed += 1
                continue
            kept.append(block)
        return kept, removed

    def _drop_retrieval_blocks(
        self,
        content: list[dict[str, Any]],
        budget: int,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        removable = [
            index
            for index, block in enumerate(content)
            if self._block_kind(block) == "reference_context"
            and self._block_source(block) not in {"recent_history", "user_profile", "archived_session_summary"}
        ]
        removed = 0
        kept = list(content)
        for index in reversed(removable):
            if self._estimate_messages(messages) <= budget:
                break
            kept.pop(index)
            messages[-1]["content"] = kept
            removed += 1
        return kept, removed

    def _drop_continuity_reference_blocks(
        self,
        content: list[dict[str, Any]],
        budget: int,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        removable = [
            index
            for index, block in enumerate(content)
            if self._block_kind(block) == "reference_context"
            and self._block_source(block) in {"user_profile", "archived_session_summary"}
        ]
        removed = 0
        kept = list(content)
        for index in reversed(removable):
            if self._estimate_messages(messages) <= budget:
                break
            kept.pop(index)
            messages[-1]["content"] = kept
            removed += 1
        return kept, removed

    @staticmethod
    def _block_kind(block: Any) -> str:
        if not isinstance(block, dict):
            return ""
        meta = block.get("_meta")
        if not isinstance(meta, dict):
            return ""
        return str(meta.get("kind") or "")

    @staticmethod
    def _block_source(block: Any) -> str:
        if not isinstance(block, dict):
            return ""
        meta = block.get("_meta")
        if not isinstance(meta, dict):
            return ""
        return str(meta.get("source") or "")
