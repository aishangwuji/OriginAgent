"""Session management for conversation history."""

import asyncio
import inspect
import json
import os
import re
import shutil
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from OriginAgent.config.paths import get_legacy_sessions_dir
from OriginAgent.utils.helpers import (
    ensure_dir,
    estimate_message_tokens,
    find_legal_message_start,
    image_placeholder_text,
    safe_filename,
)
from OriginAgent.utils.subagent_channel_display import scrub_subagent_announce_body

FILE_MAX_MESSAGES = 2000
_MESSAGE_TIME_PREFIX_RE = re.compile(r"^\[Message Time: [^\]]+\]\n?")
_LOCAL_IMAGE_BREADCRUMB_RE = re.compile(r"^\[image: (?:/|~)[^\]]+\]\s*$")
_TOOL_CALL_ECHO_RE = re.compile(r'^\s*(?:generate_image|message)\([^)]*\)\s*$')
_SESSION_PREVIEW_MAX_CHARS = 120
_CONTINUITY_RUNTIME_IDENTITY_KEY = "continuity_runtime_identity_v1"


def tenant_workspace(workspace: Path, tenant_id: str) -> Path:
    """Return the per-tenant workspace directory.

    Workspace layout:
        workspace/
          sessions/           -> per-tenant sessions (legacy compat)
          tenants/
            {tenant_id}/
              memory/         -> facts, BDI desires, foresights
              sessions/       -> this tenant's history
              bdi/
          shared/
            memory/           -> shared facts, shared devices
    """
    return workspace / "tenants" / tenant_id


def _serialize_episodes(episodes: list["Episode"]) -> list[dict[str, Any]]:
    """Serialize episode list for JSON persistence."""
    result: list[dict[str, Any]] = []
    for ep in episodes:
        d: dict[str, Any] = {
            "episode_id": ep.episode_id,
            "label": ep.label,
            "msg_start": ep.msg_start,
            "msg_end": ep.msg_end,
            "status": ep.status,
        }
        if ep.started_at is not None:
            d["started_at"] = ep.started_at.isoformat()
        if ep.ended_at is not None:
            d["ended_at"] = ep.ended_at.isoformat()
        result.append(d)
    return result


def _deserialize_episodes(data: list[dict[str, Any]]) -> list["Episode"]:
    """Deserialize episode list from JSON data."""
    episodes: list[Episode] = []
    for d in data:
        started_at = None
        ended_at = None
        if d.get("started_at"):
            try:
                started_at = datetime.fromisoformat(d["started_at"])
            except (ValueError, TypeError):
                pass
        if d.get("ended_at"):
            try:
                ended_at = datetime.fromisoformat(d["ended_at"])
            except (ValueError, TypeError):
                pass
        episodes.append(Episode(
            episode_id=str(d.get("episode_id", "")),
            label=str(d.get("label", "")),
            started_at=started_at,
            ended_at=ended_at,
            msg_start=int(d.get("msg_start", 0)),
            msg_end=int(d.get("msg_end", 0)),
            status=str(d.get("status", "active")),
        ))
    return episodes


def _call_archive_callback(
    callback: Any,
    messages: list[dict[str, Any]],
    *,
    session_key: str,
    reason: str,
) -> None:
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        callback(messages, session_key=session_key, reason=reason)
        return
    parameters = signature.parameters
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_kwargs or {"session_key", "reason"}.issubset(parameters):
        callback(messages, session_key=session_key, reason=reason)
    else:
        callback(messages)


def _sanitize_assistant_replay_text(content: str) -> str:
    """Remove internal replay artifacts that the model may have copied before.

    These strings are useful as runtime/session metadata, but when they appear
    in assistant examples they become demonstrations for the model to repeat.
    """
    content = _MESSAGE_TIME_PREFIX_RE.sub("", content, count=1)
    lines = [
        line
        for line in content.splitlines()
        if not _LOCAL_IMAGE_BREADCRUMB_RE.match(line)
        and not _TOOL_CALL_ECHO_RE.match(line)
    ]
    return "\n".join(lines).strip()


def _text_preview(content: Any) -> str:
    """Return compact display text for session lists."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                value = block.get("text")
                if isinstance(value, str):
                    parts.append(value)
        text = " ".join(parts)
    else:
        return ""
    text = _sanitize_assistant_replay_text(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > _SESSION_PREVIEW_MAX_CHARS:
        text = text[: _SESSION_PREVIEW_MAX_CHARS - 1].rstrip() + "..."
    return text


def _message_preview_text(message: dict[str, Any]) -> str:
    content: Any = message.get("content")
    if message.get("injected_event") == "subagent_result" and isinstance(content, str):
        content = scrub_subagent_announce_body(content)
    return _text_preview(content)


def _continuity_identity_summary(metadata: Any) -> tuple[str | None, str | None, str | None]:
    if not isinstance(metadata, dict):
        return None, None, None
    raw = metadata.get(_CONTINUITY_RUNTIME_IDENTITY_KEY)
    if not isinstance(raw, dict):
        return None, None, None
    user_id = str(raw.get("user_id")).strip() if raw.get("user_id") else None
    device_id = str(raw.get("device_id")).strip() if raw.get("device_id") else None
    updated_at = str(raw.get("updated_at")).strip() if raw.get("updated_at") else None
    return user_id, device_id, updated_at


@dataclass
class Episode:
    """A contiguous topic-block within a session."""

    episode_id: str
    label: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    msg_start: int = 0   # index into session.messages (inclusive)
    msg_end: int = 0     # index into session.messages (exclusive)
    status: str = "active"  # "active" | "closed"


@dataclass
class Session:
    """A conversation session."""

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    episodes: list[Episode] = field(default_factory=list)
    active_episode_index: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files

    def __post_init__(self) -> None:
        """Backfill: create a catch-all episode if messages exist but no episodes."""
        if self.messages and not self.episodes:
            episode_id = str(uuid.uuid4())
            end = len(self.messages)
            episode = Episode(
                episode_id=episode_id,
                label="",
                started_at=self._first_message_timestamp(),
                msg_start=0,
                msg_end=end,
                status="active",
            )
            self.episodes.append(episode)
            self.active_episode_index = 0
            for msg in self.messages:
                msg.setdefault("episode_id", episode_id)

    def _first_message_timestamp(self) -> datetime | None:
        """Return the timestamp of the first user message, or None."""
        for msg in self.messages:
            ts = msg.get("timestamp")
            if ts:
                try:
                    return datetime.fromisoformat(ts)
                except (ValueError, TypeError):
                    continue
        return None

    @property
    def active_episode(self) -> Episode | None:
        """Return the current active episode, or None if no episodes exist."""
        if not self.episodes:
            return None
        idx = self.active_episode_index
        if idx < 0 or idx >= len(self.episodes):
            return None
        ep = self.episodes[idx]
        return ep if ep.status == "active" else None

    def ensure_active_episode(self) -> Episode:
        """Ensure there is an open episode, creating one if needed.

        Returns the active episode.
        """
        if not self.episodes:
            episode_id = str(uuid.uuid4())
            episode = Episode(
                episode_id=episode_id,
                started_at=datetime.now(),
                msg_start=len(self.messages),
                msg_end=len(self.messages),
                status="active",
            )
            self.episodes.append(episode)
            self.active_episode_index = 0
            return episode

        active = self.episodes[self.active_episode_index]
        if active.status == "active":
            return active

        # The current active episode is closed; start a new one.
        episode_id = str(uuid.uuid4())
        episode = Episode(
            episode_id=episode_id,
            started_at=datetime.now(),
            msg_start=len(self.messages),
            msg_end=len(self.messages),
            status="active",
        )
        self.metadata["_active_episode_label"] = ""
        self.metadata["_episode_count"] = len(self.episodes)
        self.episodes.append(episode)
        self.active_episode_index = len(self.episodes) - 1
        return episode

    @staticmethod
    def _analyze_episode_tone(messages: list[dict[str, Any]]) -> str:
        """Analyze the tone of an episode using simple heuristics.

        No LLM call — uses message-level signals (questions, exclamation,
        length, emoji) to classify tone.
        """
        user_texts: list[str] = [
            m.get("content", "")
            for m in messages
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]
        if not user_texts:
            return "neutral"
        total = len(user_texts)
        questions = sum(1 for t in user_texts if "?" in t or "？" in t)
        exclamations = sum(1 for t in user_texts if "!" in t or "！" in t)
        long_msgs = sum(1 for t in user_texts if len(t) > 150)
        short_msgs = sum(1 for t in user_texts if len(t) < 30)
        has_emoji = any("\U0001F000" <= c <= "\U0001FFFF" or "✀" <= c <= "➿" for t in user_texts for c in t)

        if total >= 3 and all(len(t) < 20 for t in user_texts):
            return "terse"
        if questions / total > 0.5:
            return "inquisitive / asking questions"
        if exclamations / total > 0.3:
            return "emphatic / excited"
        if long_msgs / total > 0.4:
            return "detailed / explanatory"
        if has_emoji:
            return "casual / playful"
        if short_msgs / total > 0.7 and total >= 3:
            return "brief / direct"
        return "neutral / conversational"

    @staticmethod
    def _extract_key_quotes(
        messages: list[dict[str, Any]],
        max_quotes: int = 3,
    ) -> list[str]:
        """Extract notable verbatim user quotes from an episode.

        Picks the most semantically dense user messages (longest ones
        that aren't purely procedural).
        """
        procedural_prefixes = ("ok", "okay", "yes", "no", "thanks", "ty", "thx",
                               "好的", "嗯", "对", "是", "不", "谢谢", "明白了")
        candidates: list[str] = []
        for m in messages:
            if m.get("role") != "user":
                continue
            text = str(m.get("content", "")).strip()
            if not text or len(text) < 10:
                continue
            is_procedural = any(text.lower().startswith(p) for p in procedural_prefixes)
            if is_procedural and len(text) < 30:
                continue
            candidates.append(text)
        # Return the longest (most content-rich) quotes, limited.
        candidates.sort(key=len, reverse=True)
        return candidates[:max_quotes]

    def _build_episode_preview(self, episode: Episode) -> str:
        """Build a compact text preview of an episode for summary metadata.

        For Phase 2 this is a simple first/last-message preview; Phase 4
        adds tone notes and verbatim quotes.
        """
        msgs = self.messages[episode.msg_start:episode.msg_end]
        user_msgs = [m.get("content", "") for m in msgs if m.get("role") == "user"]
        if not user_msgs:
            return f"{len(msgs)} messages, no user text"
        first = str(user_msgs[0])[:100]
        if len(user_msgs) > 1:
            last = str(user_msgs[-1])[:100]
            return f"{len(msgs)} msgs: \"{first}\" ... \"{last}\""
        return f"{len(msgs)} msgs: \"{first}\""

    def _store_episode_summary(
        self, episode: Episode, *, working_memory: dict[str, Any] | None = None
    ) -> None:
        """Store a summary of a closed episode in session metadata.

        Phase 4: includes tone analysis and key verbatim quotes alongside
        the text preview.

        Phase 6 (coupling fix B): optionally archives a snapshot of the
        working memory (current_goal, current_plan, open_loops, etc.) at
        the time the episode was closed, bridging the gap between the
        working memory system and the episode system.
        """
        msgs = self.messages[episode.msg_start:episode.msg_end]
        tone = self._analyze_episode_tone(msgs)
        quotes = self._extract_key_quotes(msgs)

        summaries: list[dict[str, Any]] = list(self.metadata.get("_episode_summaries", []))
        entry: dict[str, Any] = {
            "episode_id": episode.episode_id,
            "label": episode.label,
            "preview": self._build_episode_preview(episode),
            "message_count": episode.msg_end - episode.msg_start,
            "tone": tone,
            "key_quotes": quotes,
            "status": episode.status,
        }
        if episode.started_at is not None:
            entry["started_at"] = episode.started_at.isoformat()
        if episode.ended_at is not None:
            entry["ended_at"] = episode.ended_at.isoformat()
        if working_memory:
            wm_snapshot: dict[str, Any] = {}
            for key in ("current_goal", "current_plan", "open_loops", "active_constraints"):
                val = working_memory.get(key)
                if val:
                    wm_snapshot[key] = val
            if wm_snapshot:
                entry["working_memory_snapshot"] = wm_snapshot
        # Keep most recent summaries at the front, limit to 5.
        summaries.insert(0, entry)
        self.metadata["_episode_summaries"] = summaries[:5]

    def start_new_episode(
        self, label: str = "", *, working_memory: dict[str, Any] | None = None
    ) -> Episode:
        """Close the current active episode and start a new one.

        Args:
            label: Optional human-readable label for the new episode.
            working_memory: Optional snapshot of working memory state
                (goal, plan, open_loops, constraints) to archive.

        Returns:
            The new active episode.
        """
        if self.episodes:
            active = self.episodes[self.active_episode_index]
            if active.status == "active":
                active.status = "closed"
                active.ended_at = datetime.now()
                self._store_episode_summary(active, working_memory=working_memory)

        episode_id = str(uuid.uuid4())
        episode = Episode(
            episode_id=episode_id,
            label=label,
            started_at=datetime.now(),
            msg_start=len(self.messages),
            msg_end=len(self.messages),
            status="active",
        )
        self.metadata["_active_episode_label"] = label or ""
        self.metadata["_episode_count"] = len(self.episodes)
        self.episodes.append(episode)
        self.active_episode_index = len(self.episodes) - 1
        return episode

    def close_active_episode(
        self, *, working_memory: dict[str, Any] | None = None
    ) -> None:
        """Close the current active episode without opening a new one.

        Args:
            working_memory: Optional snapshot of working memory state
                to archive in the episode's summary.

        The next ``add_message()`` call will automatically create a new episode.
        """
        if self.episodes:
            active = self.episodes[self.active_episode_index]
            if active.status == "active":
                active.status = "closed"
                active.ended_at = datetime.now()
                self._store_episode_summary(active, working_memory=working_memory)

    def get_episode_history(
        self,
        episode_id: str,
        *,
        max_tokens: int = 0,
        include_timestamps: bool = False,
    ) -> list[dict[str, Any]]:
        """Return raw messages for a specific episode.

        Args:
            episode_id: The episode to retrieve messages for.
            max_tokens: Maximum token budget (0 = unlimited).
            include_timestamps: Whether to annotate user turns with timestamps.

        Returns:
            List of message dicts scoped to the episode.
        """
        episode = next(
            (ep for ep in self.episodes if ep.episode_id == episode_id),
            None,
        )
        if episode is None:
            return []

        raw = self.messages[episode.msg_start:episode.msg_end]

        out: list[dict[str, Any]] = []
        for message in raw:
            if message.get("_command"):
                continue
            content = message.get("content", "")
            role = message.get("role")
            if role == "assistant" and isinstance(content, str):
                content = _sanitize_assistant_replay_text(content)
            media = message.get("media")
            if role == "user" and isinstance(media, list) and media and isinstance(content, str):
                breadcrumbs = "\n".join(
                    image_placeholder_text(p) for p in media if isinstance(p, str) and p
                )
                content = f"{content}\n{breadcrumbs}" if content else breadcrumbs
            if include_timestamps:
                content = self._annotate_message_time(message, content)
            if role == "assistant" and isinstance(content, str) and not content.strip():
                if not any(key in message for key in ("tool_calls", "reasoning_content", "thinking_blocks")):
                    continue
            entry: dict[str, Any] = {"role": message["role"], "content": content}
            for key in ("tool_calls", "tool_call_id", "name", "reasoning_content", "thinking_blocks"):
                if key in message:
                    entry[key] = message[key]
            out.append(entry)

        if max_tokens > 0 and out:
            kept: list[dict[str, Any]] = []
            used = 0
            for message in reversed(out):
                tokens = estimate_message_tokens(message)
                if kept and used + tokens > max_tokens:
                    break
                kept.append(message)
                used += tokens
            kept.reverse()

            # Keep history aligned to the first visible user turn.
            first_user = next((i for i, m in enumerate(kept) if m.get("role") == "user"), None)
            if first_user is not None:
                kept = kept[first_user:]
            else:
                # If no user turn in the token-budget tail, recover the
                # nearest user turn from the full unscoped output.
                recovered_user = next(
                    (i for i in range(len(out) - 1, -1, -1) if out[i].get("role") == "user"),
                    None,
                )
                if recovered_user is not None:
                    kept = out[recovered_user:]

            out = kept

        return out

    def _rebuild_episode_indices(self) -> None:
        """Reconcile episode indices after messages have been trimmed.

        Removes episodes whose messages have been entirely trimmed away,
        and adjusts ``msg_start`` / ``msg_end`` for remaining episodes
        to stay within the bounds of ``self.messages``.
        """
        total = len(self.messages)
        surviving: list[Episode] = []
        index_map: dict[str, int] = {}

        for ep in self.episodes:
            clamped_start = min(ep.msg_start, total)
            clamped_end = min(ep.msg_end, total)
            if clamped_start >= clamped_end or clamped_start >= total:
                # This episode has been fully trimmed away.
                continue
            new_ep = Episode(
                episode_id=ep.episode_id,
                label=ep.label,
                started_at=ep.started_at,
                ended_at=ep.ended_at,
                msg_start=clamped_start,
                msg_end=clamped_end,
                status=ep.status,
            )
            index_map[ep.episode_id] = len(surviving)
            surviving.append(new_ep)

        self.episodes = surviving

        # Adjust active_episode_index if the active episode was removed.
        active_id = (
            self.episodes[self.active_episode_index].episode_id
            if self.episodes and self.active_episode_index < len(self.episodes)
            else None
        )
        if active_id and active_id in index_map:
            self.active_episode_index = index_map[active_id]
        else:
            self.active_episode_index = 0

        # Ensure at least one episode exists if there are messages.
        if self.messages and not self.episodes:
            episode_id = str(uuid.uuid4())
            self.episodes.append(Episode(
                episode_id=episode_id,
                label="",
                started_at=self._first_message_timestamp(),
                msg_start=0,
                msg_end=total,
                status="active",
            ))
            self.active_episode_index = 0

    @staticmethod
    def _annotate_message_time(message: dict[str, Any], content: Any) -> Any:
        """Expose persisted turn timestamps to the model for relative-date reasoning.

        Annotating *every* assistant turn trains the model (via in-context
        demonstrations) to start its own replies with the same
        ``[Message Time: ...]`` prefix, which leaks metadata back to the user.
        We therefore only annotate user turns. User-side stamps are enough to
        pin adjacent assistant replies for relative-time reasoning, including
        proactive messages the user replies to later.
        """
        timestamp = message.get("timestamp")
        if not timestamp or not isinstance(content, str):
            return content
        role = message.get("role")
        if role != "user":
            return content
        return f"[Message Time: {timestamp}]\n{content}"

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session, tagging it with the active episode."""
        episode = self.ensure_active_episode()
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "episode_id": episode.episode_id,
            **kwargs
        }
        self.messages.append(msg)
        episode.msg_end = len(self.messages)
        self.updated_at = datetime.now()

    def get_history(
        self,
        max_messages: int = 120,
        *,
        max_tokens: int = 0,
        include_timestamps: bool = False,
    ) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input.

        History is sliced by message count first (``max_messages``), then by
        token budget from the tail (``max_tokens``) when provided.
        """
        unconsolidated = self.messages[self.last_consolidated:]
        max_messages = max_messages if max_messages > 0 else 120
        sliced = unconsolidated[-max_messages:]

        # Avoid starting mid-turn when possible, except for proactive
        # assistant deliveries that the user may be replying to.
        for i, message in enumerate(sliced):
            if message.get("role") == "user":
                start = i
                if i > 0 and sliced[i - 1].get("_channel_delivery"):
                    start = i - 1
                sliced = sliced[start:]
                break

        # Drop orphan tool results at the front.
        start = find_legal_message_start(sliced)
        if start:
            sliced = sliced[start:]

        out: list[dict[str, Any]] = []
        for message in sliced:
            if message.get("_command"):
                continue
            content = message.get("content", "")
            role = message.get("role")
            if role == "assistant" and isinstance(content, str):
                content = _sanitize_assistant_replay_text(content)
            # Synthesize an ``[image: path]`` breadcrumb from the persisted
            # ``media`` kwarg so LLM replay still sees *something* where the
            # image used to be. Without this, an image-only user turn
            # replays as an empty user message — the assistant's reply then
            # looks like it's responding to nothing.
            media = message.get("media")
            if role == "user" and isinstance(media, list) and media and isinstance(content, str):
                breadcrumbs = "\n".join(
                    image_placeholder_text(p) for p in media if isinstance(p, str) and p
                )
                content = f"{content}\n{breadcrumbs}" if content else breadcrumbs
            if include_timestamps:
                content = self._annotate_message_time(message, content)
            if role == "assistant" and isinstance(content, str) and not content.strip():
                if not any(key in message for key in ("tool_calls", "reasoning_content", "thinking_blocks")):
                    continue
            entry: dict[str, Any] = {"role": message["role"], "content": content}
            for key in ("tool_calls", "tool_call_id", "name", "reasoning_content", "thinking_blocks"):
                if key in message:
                    entry[key] = message[key]
            out.append(entry)

        if max_tokens > 0 and out:
            kept: list[dict[str, Any]] = []
            used = 0
            for message in reversed(out):
                tokens = estimate_message_tokens(message)
                if kept and used + tokens > max_tokens:
                    break
                kept.append(message)
                used += tokens
            kept.reverse()

            # Keep history aligned to the first visible user turn.
            first_user = next((i for i, m in enumerate(kept) if m.get("role") == "user"), None)
            if first_user is not None:
                kept = kept[first_user:]
            else:
                # Tight token budgets can otherwise leave assistant-only tails.
                # If a user turn exists in the unsliced output, recover the
                # nearest one even if it slightly exceeds the token budget.
                recovered_user = next(
                    (i for i in range(len(out) - 1, -1, -1) if out[i].get("role") == "user"),
                    None,
                )
                if recovered_user is not None:
                    kept = out[recovered_user:]

            # And keep a legal tool-call boundary at the front.
            start = find_legal_message_start(kept)
            if start:
                kept = kept[start:]
            out = kept
        return out

    def _find_hot_start_idx(self, max_turns: int = 50) -> int:
        """定位热区起点在 ``self.messages`` 中的索引（按 user turn 边界对齐）。

        与 ``get_hot_history`` 的边界对齐逻辑一致，但只返回起点 index，
        不实际切片 / 处理消息内容。供需要"截取超出热区部分"的调用方复用，
        避免边界对齐逻辑重复实现导致行为漂移。

        - 总轮数 <= ``max_turns`` 时返回 0（全部消息都属于热区）；
        - 否则返回倒数第 ``max_turns`` 个 user 消息的位置，并按 tool_call
          完整性向前扩展；
        - 最后丢弃前端仍无法配对的孤儿 tool_result。
        """
        if not self.messages:
            return 0

        max_turns = max_turns if max_turns > 0 else 50

        # 一轮 = 一条 user 消息开始，到下一条 user 消息之前的全部内容。
        # 从尾部向前数 user 消息，定位第 max_turns 轮的起点。
        user_indices = [i for i, m in enumerate(self.messages) if m.get("role") == "user"]

        if not user_indices or len(user_indices) <= max_turns:
            # 不足 max_turns 轮：全部消息都属于热区。
            return 0

        start = user_indices[-max_turns]
        # 主动下发的 assistant 消息（_channel_delivery）与其后的 user 回复
        # 属于同一轮，需一并保留，与 get_history 行为一致。
        if start > 0 and self.messages[start - 1].get("_channel_delivery"):
            start -= 1

        sliced = self.messages[start:]

        # 边界对齐：若截断点落在 tool_call 序列中间（前方存在孤儿 tool_result，
        # 即声明它的 assistant 被截断），向前扩展到上一个 user 轮，使每个
        # tool_call 与其 tool_result 保持完整配对。
        while start > 0:
            legal = find_legal_message_start(sliced)
            if legal == 0:
                break
            prev_user = None
            for i in range(start - 1, -1, -1):
                if self.messages[i].get("role") == "user":
                    prev_user = i
                    break
            if prev_user is None:
                break
            start = prev_user
            sliced = self.messages[start:]

        # 仍无法配对的孤儿 tool_result（父 assistant 不存在）直接丢弃，
        # 让 start 前移越过它们。
        legal = find_legal_message_start(sliced)
        if legal:
            start += legal

        return start

    def get_hot_history(
        self,
        max_turns: int = 50,
        *,
        max_tokens: int = 0,
        include_timestamps: bool = False,
    ) -> list[dict[str, Any]]:
        """Return the most recent turns for LLM input, bounded by turn count.

        A "turn" = one user message + its corresponding assistant response(s)
        (including tool_calls and tool_results). Boundary aligns to user
        message, never truncating mid-tool_call sequence.

        Unlike ``get_history`` (which slices by raw message count over the
        unconsolidated tail), this method counts *user turns* across the full
        ``self.messages`` log, so recently consolidated turns are still
        eligible as long as they fall inside the turn window.
        """
        if not self.messages:
            return []

        max_turns = max_turns if max_turns > 0 else 50

        # 一轮 = 一条 user 消息开始，到下一条 user 消息之前的全部内容。
        # 从尾部向前数 user 消息，定位第 max_turns 轮的起点。
        user_indices = [i for i, m in enumerate(self.messages) if m.get("role") == "user"]

        if not user_indices:
            start = 0
        elif len(user_indices) <= max_turns:
            # 不足 max_turns 轮：从首条消息起全部返回（保留首个 user 之前的
            # 主动下发消息）。
            start = 0
        else:
            start = user_indices[-max_turns]
            # 主动下发的 assistant 消息（_channel_delivery）与其后的 user 回复
            # 属于同一轮，需一并保留，与 get_history 行为一致。
            if start > 0 and self.messages[start - 1].get("_channel_delivery"):
                start -= 1

        sliced = self.messages[start:]

        # 边界对齐：若截断点落在 tool_call 序列中间（前方存在孤儿 tool_result，
        # 即声明它的 assistant 被截断），向前扩展到上一个 user 轮，使每个
        # tool_call 与其 tool_result 保持完整配对。
        while start > 0:
            legal = find_legal_message_start(sliced)
            if legal == 0:
                break
            prev_user = None
            for i in range(start - 1, -1, -1):
                if self.messages[i].get("role") == "user":
                    prev_user = i
                    break
            if prev_user is None:
                break
            start = prev_user
            sliced = self.messages[start:]

        # 仍无法配对的孤儿 tool_result（父 assistant 不存在）直接丢弃。
        legal = find_legal_message_start(sliced)
        if legal:
            sliced = sliced[legal:]

        out: list[dict[str, Any]] = []
        for message in sliced:
            if message.get("_command"):
                continue
            content = message.get("content", "")
            role = message.get("role")
            if role == "assistant" and isinstance(content, str):
                content = _sanitize_assistant_replay_text(content)
            media = message.get("media")
            if role == "user" and isinstance(media, list) and media and isinstance(content, str):
                breadcrumbs = "\n".join(
                    image_placeholder_text(p) for p in media if isinstance(p, str) and p
                )
                content = f"{content}\n{breadcrumbs}" if content else breadcrumbs
            if include_timestamps:
                content = self._annotate_message_time(message, content)
            if role == "assistant" and isinstance(content, str) and not content.strip():
                if not any(key in message for key in ("tool_calls", "reasoning_content", "thinking_blocks")):
                    continue
            entry: dict[str, Any] = {"role": message["role"], "content": content}
            for key in ("tool_calls", "tool_call_id", "name", "reasoning_content", "thinking_blocks"):
                if key in message:
                    entry[key] = message[key]
            out.append(entry)

        if max_tokens > 0 and out:
            kept: list[dict[str, Any]] = []
            used = 0
            for message in reversed(out):
                tokens = estimate_message_tokens(message)
                if kept and used + tokens > max_tokens:
                    break
                kept.append(message)
                used += tokens
            kept.reverse()

            first_user = next((i for i, m in enumerate(kept) if m.get("role") == "user"), None)
            if first_user is not None:
                kept = kept[first_user:]
            else:
                recovered_user = next(
                    (i for i in range(len(out) - 1, -1, -1) if out[i].get("role") == "user"),
                    None,
                )
                if recovered_user is not None:
                    kept = out[recovered_user:]

            legal = find_legal_message_start(kept)
            if legal:
                kept = kept[legal:]
            out = kept
        return out

    def clear(self) -> None:
        """Clear all messages, episodes, and reset session to initial state."""
        self.messages = []
        self.episodes = []
        self.active_episode_index = 0
        self.last_consolidated = 0
        self.updated_at = datetime.now()
        self.metadata.pop("_recent_summaries", None)
        self.metadata.pop("_last_summary", None)
        self.metadata.pop("_episode_summaries", None)

    def retain_recent_legal_suffix(self, max_messages: int) -> None:
        """Keep a legal recent suffix constrained by a hard message cap."""
        if max_messages <= 0:
            self.clear()
            return
        if len(self.messages) <= max_messages:
            return

        retained = self._recent_legal_suffix(max_messages)
        dropped = len(self.messages) - len(retained)
        self.messages = retained
        self.last_consolidated = max(0, self.last_consolidated - dropped)
        self.updated_at = datetime.now()
        self._rebuild_episode_indices()

    def _recent_legal_suffix(self, max_messages: int) -> list[dict[str, Any]]:
        if max_messages <= 0:
            return []
        retained = list(self.messages[-max_messages:])
        # Prefer starting at a user turn when one exists within the tail.
        first_user = next((i for i, m in enumerate(retained) if m.get("role") == "user"), None)
        if first_user is not None:
            retained = retained[first_user:]
        else:
            # If the tail is assistant/tool-only, anchor to the latest user in
            # the full session and take a capped forward window from there.
            latest_user = next(
                (i for i in range(len(self.messages) - 1, -1, -1)
                 if self.messages[i].get("role") == "user"),
                None,
            )
            if latest_user is not None:
                retained = list(self.messages[latest_user: latest_user + max_messages])

        # Mirror get_history(): avoid persisting orphan tool results at the front.
        start = find_legal_message_start(retained)
        if start:
            retained = retained[start:]

        # Hard-cap guarantee: never keep more than max_messages.
        if len(retained) > max_messages:
            retained = retained[-max_messages:]
            start = find_legal_message_start(retained)
            if start:
                retained = retained[start:]
        return retained

    def enforce_file_cap(
        self,
        on_archive: Any = None,
        limit: int = FILE_MAX_MESSAGES,
    ) -> None:
        """Bound session message growth by archiving and trimming old prefixes."""
        if limit <= 0 or len(self.messages) <= limit:
            return

        before = list(self.messages)
        before_last_consolidated = self.last_consolidated
        before_count = len(before)
        retained = self._recent_legal_suffix(limit)
        dropped_count = before_count - len(retained)
        if dropped_count <= 0:
            return

        dropped = before[:dropped_count]
        already_consolidated = min(before_last_consolidated, dropped_count)
        archive_chunk = dropped[already_consolidated:]
        if archive_chunk and on_archive:
            _call_archive_callback(
                on_archive,
                archive_chunk,
                session_key=self.key,
                reason="session_file_cap",
            )
        self.messages = retained
        self.last_consolidated = max(0, before_last_consolidated - dropped_count)
        self.updated_at = datetime.now()
        self._rebuild_episode_indices()
        logger.info(
            "Session file cap hit for {}: dropped {}, raw-archived {}, kept {}",
            self.key,
            dropped_count,
            len(archive_chunk),
            len(self.messages),
        )


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = get_legacy_sessions_dir()
        self._cache: dict[str, Session] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """Return a per-session asyncio.Lock, creating one if needed."""
        return self._locks.setdefault(session_key, asyncio.Lock())

    @staticmethod
    def safe_key(key: str) -> str:
        """Public helper used by HTTP handlers to map an arbitrary key to a stable filename stem."""
        return safe_filename(key.replace(":", "_"))

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        return self.sessions_dir / f"{self.safe_key(key)}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path (~/.OriginAgent/sessions/)."""
        return self.legacy_sessions_dir / f"{self.safe_key(key)}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        return session

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    shutil.move(str(legacy_path), str(path))
                    logger.info("Migrated session {} from legacy path", key)
                except Exception:
                    logger.exception("Failed to migrate session {}", key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            updated_at = None
            last_consolidated = 0
            episodes_raw = None
            active_episode_index = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        updated_at = datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
                        last_consolidated = data.get("last_consolidated", 0)
                        episodes_raw = data.get("episodes")
                        active_episode_index = int(data.get("active_episode_index", 0))
                    else:
                        messages.append(data)

            episodes = _deserialize_episodes(episodes_raw) if episodes_raw else []

            return Session(
                key=key,
                messages=messages,
                episodes=episodes,
                active_episode_index=active_episode_index,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info("Recovered session {} from corrupt file ({} messages)", key, len(repaired.messages))
            return repaired

    def _repair(self, key: str) -> Session | None:
        """Attempt to recover a session from a corrupt JSONL file."""
        path = self._get_session_path(key)
        if not path.exists():
            return None

        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at: datetime | None = None
            updated_at: datetime | None = None
            last_consolidated = 0
            episodes_raw = None
            active_episode_index = 0
            skipped = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        skipped += 1
                        continue

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        if data.get("created_at"):
                            with suppress(ValueError, TypeError):
                                created_at = datetime.fromisoformat(data["created_at"])
                        if data.get("updated_at"):
                            with suppress(ValueError, TypeError):
                                updated_at = datetime.fromisoformat(data["updated_at"])
                        last_consolidated = data.get("last_consolidated", 0)
                        episodes_raw = data.get("episodes")
                        active_episode_index = int(data.get("active_episode_index", 0))
                    else:
                        messages.append(data)

            if skipped:
                logger.warning("Skipped {} corrupt lines in session {}", skipped, key)

            if not messages and not metadata:
                return None

            episodes = _deserialize_episodes(episodes_raw) if episodes_raw else []

            return Session(
                key=key,
                messages=messages,
                episodes=episodes,
                active_episode_index=active_episode_index,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Repair failed for session {}: {}", key, e)
            return None

    @staticmethod
    def _session_payload(session: Session) -> dict[str, Any]:
        return {
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "messages": session.messages,
        }

    # Maximum retries for os.replace on Windows PermissionError (WinError 5).
    # Windows can briefly lock the target file when another process (antivirus,
    # search indexer, concurrent reader) has it open. This is transient —
    # retrying with backoff resolves it without crashing the turn.
    # Rule 11: classified as retryable (transient file lock), not permanent.
    _REPLACE_MAX_RETRIES = 3
    _REPLACE_BACKOFF_SECONDS = (0.01, 0.05, 0.2)

    def save(self, session: Session, *, fsync: bool = False) -> None:
        """Save a session to disk atomically.

        When *fsync* is ``True`` the final file and its parent directory are
        explicitly flushed to durable storage.  This is intentionally off by
        default (the OS page-cache is sufficient for normal operation) but
        should be enabled during graceful shutdown so that filesystems with
        write-back caching (e.g. rclone VFS, NFS, FUSE mounts) do not lose
        the most recent writes.

        On Windows, ``os.replace`` may fail with ``PermissionError``
        (WinError 5) when the target file is briefly locked by another
        process (antivirus, search indexer, concurrent reader). This is
        retried up to ``_REPLACE_MAX_RETRIES`` times with exponential
        backoff before giving up.
        """
        path = self._get_session_path(session.key)
        tmp_path = path.with_suffix(".jsonl.tmp")

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                metadata_line = {
                    "_type": "metadata",
                    "key": session.key,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                    "metadata": session.metadata,
                    "last_consolidated": session.last_consolidated,
                    "episodes": _serialize_episodes(session.episodes),
                    "active_episode_index": session.active_episode_index,
                }
                f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
                for msg in session.messages:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                if fsync:
                    f.flush()
                    os.fsync(f.fileno())

            self._atomic_replace(tmp_path, path)

            if fsync:
                # fsync the directory so the rename is durable.
                # On Windows, opening a directory with O_RDONLY raises
                # PermissionError — skip the dir sync there (NTFS
                # journals metadata synchronously).
                with suppress(PermissionError):
                    fd = os.open(str(path.parent), os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        self._cache[session.key] = session

    def _atomic_replace(self, tmp_path: Path, target_path: Path) -> None:
        """Atomically replace *target_path* with *tmp_path*, retrying on WinError 5.

        On Windows, ``os.replace`` can fail with ``PermissionError`` when
        the target file is briefly locked by another process. This is a
        transient condition (rule 11: retryable error) — we retry with
        backoff before giving up. Non-PermissionError exceptions are not
        retried (e.g. OSError from disk full is not transient).
        """
        last_error: PermissionError | None = None
        for attempt in range(self._REPLACE_MAX_RETRIES):
            try:
                os.replace(tmp_path, target_path)
                return
            except PermissionError as exc:
                last_error = exc
                if attempt < self._REPLACE_MAX_RETRIES - 1:
                    time.sleep(self._REPLACE_BACKOFF_SECONDS[attempt])
                    logger.debug(
                        "Session save retry {}/{} for {} (WinError 5)",
                        attempt + 1, self._REPLACE_MAX_RETRIES,
                        target_path.name,
                    )
        if last_error is not None:
            raise last_error

    def flush_all(self) -> int:
        """Re-save every cached session with fsync for durable shutdown.

        Returns the number of sessions flushed.  Errors on individual
        sessions are logged but do not prevent other sessions from being
        flushed.
        """
        flushed = 0
        for key, session in list(self._cache.items()):
            try:
                self.save(session, fsync=True)
                flushed += 1
            except Exception:
                logger.warning("Failed to flush session {}", key, exc_info=True)
        return flushed

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def delete_session(self, key: str) -> bool:
        """Remove a session from disk and the in-memory cache.

        Returns True if a JSONL file was found and unlinked.
        """
        path = self._get_session_path(key)
        self.invalidate(key)
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except OSError as e:
            logger.warning("Failed to delete session file {}: {}", path, e)
            return False

    def read_session_file(self, key: str) -> dict[str, Any] | None:
        """Load a session from disk without caching; intended for read-only HTTP endpoints.

        Returns ``{"key", "created_at", "updated_at", "metadata", "messages"}`` or
        ``None`` when the session file does not exist or fails to parse.
        """
        path = self._get_session_path(key)
        if not path.exists():
            return None
        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at: str | None = None
            updated_at: str | None = None
            stored_key: str | None = None
            episodes_raw = None
            active_episode_index = 0
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = data.get("created_at")
                        updated_at = data.get("updated_at")
                        stored_key = data.get("key")
                        episodes_raw = data.get("episodes")
                        active_episode_index = int(data.get("active_episode_index", 0))
                    else:
                        messages.append(data)
            episodes = _deserialize_episodes(episodes_raw) if episodes_raw else []
            return {
                "key": stored_key or key,
                "created_at": created_at,
                "updated_at": updated_at,
                "metadata": metadata,
                "messages": messages,
                "episodes": episodes,
                "active_episode_index": active_episode_index,
            }
        except Exception as e:
            logger.warning("Failed to read session {}: {}", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info("Recovered read-only session view {} from corrupt file", key)
                return self._session_payload(repaired)
            return None

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            fallback_key = path.stem.replace("_", ":", 1)
            try:
                # Read the metadata line and a small preview for WebUI/session lists.
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            metadata = data.get("metadata", {})
                            title = metadata.get("title") if isinstance(metadata, dict) else None
                            user_id, device_id, identity_updated_at = _continuity_identity_summary(metadata)
                            preview = ""
                            fallback_preview = ""
                            for line in f:
                                if not line.strip():
                                    continue
                                item = json.loads(line)
                                if item.get("_type") == "metadata":
                                    continue
                                text = _message_preview_text(item)
                                if not text:
                                    continue
                                if item.get("role") == "user":
                                    preview = text
                                    break
                                if not fallback_preview and item.get("role") == "assistant":
                                    fallback_preview = text
                            preview = preview or fallback_preview
                            # Derive episode info from metadata.
                            episode_count = int(metadata.get("_episode_count", 0)) if isinstance(metadata, dict) else 0
                            active_label = str(metadata.get("_active_episode_label", "")) if isinstance(metadata, dict) else ""
                            sessions.append({
                                "key": key,
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "title": title if isinstance(title, str) else "",
                                "preview": preview,
                                "path": str(path),
                                "user_id": user_id,
                                "device_id": device_id,
                                "continuity_identity_updated_at": identity_updated_at,
                                "episode_count": episode_count,
                                "active_episode_label": active_label,
                            })
            except Exception:
                repaired = self._repair(fallback_key)
                if repaired is not None:
                    user_id, device_id, identity_updated_at = _continuity_identity_summary(repaired.metadata)
                    ep_count = int(repaired.metadata.get("_episode_count", 0))
                    active_label = str(repaired.metadata.get("_active_episode_label", ""))
                    sessions.append({
                        "key": repaired.key,
                        "created_at": repaired.created_at.isoformat(),
                        "updated_at": repaired.updated_at.isoformat(),
                        "title": (
                            repaired.metadata.get("title")
                            if isinstance(repaired.metadata.get("title"), str)
                            else ""
                        ),
                        "preview": next(
                            (
                                text
                                for msg in repaired.messages
                                if (text := _message_preview_text(msg))
                            ),
                            "",
                        ),
                        "path": str(path),
                        "user_id": user_id,
                        "device_id": device_id,
                        "continuity_identity_updated_at": identity_updated_at,
                        "episode_count": ep_count,
                        "active_episode_label": active_label,
                    })
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
