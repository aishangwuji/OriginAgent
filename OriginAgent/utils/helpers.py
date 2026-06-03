"""Utility functions for OriginAgent."""

import base64
import json
import re
import shutil
import time
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

import tiktoken
from loguru import logger


def strip_think(text: str) -> str:
    """Remove thinking blocks, unclosed trailing tags, and tokenizer-level
    template leaks occasionally emitted by some models (notably Gemma 4's
    Ollama renderer).

    Covers:
      1. Well-formed `<think>...</think>` and `<thought>...</thought>` blocks.
      2. Streaming prefixes where the block is never closed.
      3. *Malformed* opening tags missing the `>` — e.g. `<think广场…`. The
         model sometimes emits the tag name directly followed by user-facing
         content with no delimiter; without this step the literal `<think`
         leaks into the rendered message.
      4. Harmony-style channel markers like `<channel|>` / `<|channel|>`
         **at the start of the text** — conservative to avoid eating
         explanatory prose that mentions these tokens.
      5. Orphan closing tags `</think>` / `</thought>` **at the very start
         or end of the text** only, for the same reason.
      6. Trailing partial control tags split across stream chunks, such as
         `<thi`, `<thin`, or `<tho`.

    Since this is also applied before persisting to history (memory.py),
    the edge-only stripping of (4) and (5) is deliberate: stripping those
    tokens mid-text would silently rewrite any message where a user or the
    assistant discusses the tokens themselves.
    """
    # Well-formed blocks first.
    text = re.sub(r"<think>[\s\S]*?</think>", "", text)
    text = re.sub(r"^\s*<think>[\s\S]*$", "", text)
    text = re.sub(r"<thought>[\s\S]*?</thought>", "", text)
    text = re.sub(r"^\s*<thought>[\s\S]*$", "", text)
    # Malformed opening tags: `<think` / `<thought` where the next char is
    # NOT one that could continue a valid tag / identifier name. Explicitly
    # listing ASCII tag-name chars (letters, digits, `_`, `-`, `:`) plus
    # `>` / `/` — we can't use `\w` here because in Python's default
    # Unicode regex mode it matches CJK characters too, which would defeat
    # the primary fix for `<think广场…` leaks.
    text = re.sub(r"<think(?![A-Za-z0-9_\-:>/])", "", text)
    text = re.sub(r"<thought(?![A-Za-z0-9_\-:>/])", "", text)
    # Edge-only orphan closing tags (start or end of text).
    text = re.sub(r"^\s*</think>\s*", "", text)
    text = re.sub(r"\s*</think>\s*$", "", text)
    text = re.sub(r"^\s*</thought>\s*", "", text)
    text = re.sub(r"\s*</thought>\s*$", "", text)
    # Edge-only channel markers (harmony / Gemma 4 variant leaks).
    text = re.sub(r"^\s*<\|?channel\|?>\s*", "", text)
    # Stream chunks may end in the middle of a control tag. Strip only known
    # control-token prefixes at the very end.
    partial_control_tag = (
        r"</?(?:t|th|thi|thin|think|tho|thou|thoug|though|thought)>?"
        r"|<\|?(?:c|ch|cha|chan|chann|channe|channel)(?:\|?>?)?"
    )
    text = re.sub(rf"(?:{partial_control_tag})$", "", text)
    text = re.sub(r"^\s*<\|?$", "", text)
    return text.strip()


def extract_think(text: str) -> tuple[str | None, str]:
    """Extract closed inline thinking blocks and return cleaned text."""
    parts: list[str] = []
    for match in re.finditer(r"<think>([\s\S]*?)</think>", text):
        parts.append(match.group(1).strip())
    for match in re.finditer(r"<thought>([\s\S]*?)</thought>", text):
        parts.append(match.group(1).strip())
    thinking = "\n\n".join(parts) if parts else None
    return thinking, strip_think(text)


class IncrementalThinkExtractor:
    """Stateful inline ``<think>`` extractor for streaming buffers."""

    __slots__ = ("_emitted",)

    def __init__(self) -> None:
        self._emitted = ""

    def reset(self) -> None:
        self._emitted = ""

    async def feed(self, buf: str, emit: Any) -> bool:
        thinking, _ = extract_think(buf)
        if not thinking or thinking == self._emitted:
            return False
        new = thinking[len(self._emitted):].strip()
        self._emitted = thinking
        if not new:
            return False
        await emit(new)
        return True


def extract_reasoning(
    reasoning_content: str | None,
    thinking_blocks: list[dict[str, Any]] | None,
    content: str | None,
) -> tuple[str | None, str | None]:
    """Return ``(reasoning_text, cleaned_content)`` from one model response."""
    if isinstance(reasoning_content, str) and reasoning_content:
        return reasoning_content, strip_think(content) if content else content
    if isinstance(thinking_blocks, list) and thinking_blocks:
        parts = [
            block.get("thinking", "")
            for block in thinking_blocks
            if isinstance(block, dict) and block.get("type") == "thinking"
        ]
        joined = "\n\n".join(part for part in parts if part)
        return (joined or None), strip_think(content) if content else content
    if isinstance(content, str) and content:
        return extract_think(content)
    return None, content


def detect_image_mime(data: bytes) -> str | None:
    """Detect image MIME type from magic bytes, ignoring file extension."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def build_image_content_blocks(
    raw: bytes, mime: str, path: str, label: str
) -> list[dict[str, Any]]:
    """Build native image blocks plus a short text label."""
    b64 = base64.b64encode(raw).decode()
    return [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
            "_meta": {"path": path},
        },
        {"type": "text", "text": label},
    ]


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def timestamp() -> str:
    """Current ISO timestamp."""
    return datetime.now().isoformat()


def current_time_str(timezone: str | None = None) -> str:
    """Return the current time string."""
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(timezone) if timezone else None
    except (KeyError, Exception):
        tz = None

    now = datetime.now(tz=tz) if tz else datetime.now().astimezone()
    offset = now.strftime("%z")
    offset_fmt = f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else offset
    tz_name = timezone or (time.strftime("%Z") or "UTC")
    return f"{now.strftime('%Y-%m-%d %H:%M (%A)')} ({tz_name}, UTC{offset_fmt})"


_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')
_TOOL_RESULT_PREVIEW_CHARS = 1200
_TOOL_RESULTS_DIR = ".originagent/tool-results"
_TOOL_RESULT_RETENTION_SECS = 7 * 24 * 60 * 60
_TOOL_RESULT_MAX_BUCKETS = 32


def safe_filename(name: str) -> str:
    """Replace unsafe path characters with underscores."""
    return _UNSAFE_CHARS.sub("_", name).strip()


def image_placeholder_text(path: str | None, *, empty: str = "[image]") -> str:
    """Build an image placeholder string."""
    return f"[image: {path}]" if path else empty


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate text with a stable suffix."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (truncated)"


def find_legal_message_start(messages: list[dict[str, Any]]) -> int:
    """Find the first index whose tool results have matching assistant calls."""
    declared: set[str] = set()
    start = 0
    for i, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id"):
                    declared.add(str(tc["id"]))
        elif role == "tool":
            tid = msg.get("tool_call_id")
            if tid and str(tid) not in declared:
                start = i + 1
                declared.clear()
    return start


def stringify_text_blocks(content: list[dict[str, Any]]) -> str | None:
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            return None
        if block.get("type") != "text":
            return None
        text = block.get("text")
        if not isinstance(text, str):
            return None
        parts.append(text)
    return "\n".join(parts)


def _render_tool_result_reference(
    filepath: Path,
    *,
    original_size: int,
    preview: str,
    truncated_preview: bool,
) -> str:
    result = (
        f"[tool output persisted]\n"
        f"Full output saved to: {filepath}\n"
        f"Original size: {original_size} chars\n"
        f"Preview:\n{preview}"
    )
    if truncated_preview:
        result += "\n...\n(Read the saved file if you need the full output.)"
    return result


def _bucket_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _cleanup_tool_result_buckets(root: Path, current_bucket: Path) -> None:
    siblings = [path for path in root.iterdir() if path.is_dir() and path != current_bucket]
    cutoff = time.time() - _TOOL_RESULT_RETENTION_SECS
    for path in siblings:
        if _bucket_mtime(path) < cutoff:
            shutil.rmtree(path, ignore_errors=True)
    keep = max(_TOOL_RESULT_MAX_BUCKETS - 1, 0)
    siblings = [path for path in siblings if path.exists()]
    if len(siblings) <= keep:
        return
    siblings.sort(key=_bucket_mtime, reverse=True)
    for path in siblings[keep:]:
        shutil.rmtree(path, ignore_errors=True)


def _write_text_atomic(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def maybe_persist_tool_result(
    workspace: Path | None,
    session_key: str | None,
    tool_call_id: str,
    content: Any,
    *,
    max_chars: int,
) -> Any:
    """Persist oversized tool output and replace it with a stable reference string."""
    if workspace is None or max_chars <= 0:
        return content

    text_payload: str | None = None
    suffix = "txt"
    if isinstance(content, str):
        text_payload = content
    elif isinstance(content, list):
        text_payload = stringify_text_blocks(content)
        if text_payload is None:
            return content
        suffix = "json"
    else:
        return content

    if len(text_payload) <= max_chars:
        return content

    root = ensure_dir(workspace / _TOOL_RESULTS_DIR)
    bucket = ensure_dir(root / safe_filename(session_key or "default"))
    try:
        _cleanup_tool_result_buckets(root, bucket)
    except Exception:
        logger.exception("Failed to clean stale tool result buckets in {}", root)
    path = bucket / f"{safe_filename(tool_call_id)}.{suffix}"
    if not path.exists():
        if suffix == "json" and isinstance(content, list):
            _write_text_atomic(path, json.dumps(content, ensure_ascii=False, indent=2))
        else:
            _write_text_atomic(path, text_payload)

    preview = text_payload[:_TOOL_RESULT_PREVIEW_CHARS]
    return _render_tool_result_reference(
        path,
        original_size=len(text_payload),
        preview=preview,
        truncated_preview=len(text_payload) > _TOOL_RESULT_PREVIEW_CHARS,
    )


def split_message(content: str, max_len: int = 2000) -> list[str]:
    """
    Split content into chunks within max_len, preferring line breaks.

    Args:
        content: The text content to split.
        max_len: Maximum length per chunk (default 2000 for Discord compatibility).

    Returns:
        List of message chunks, each within max_len.
    """
    if not content:
        return []
    if len(content) <= max_len:
        return [content]
    chunks: list[str] = []
    while content:
        if len(content) <= max_len:
            chunks.append(content)
            break
        cut = content[:max_len]
        # Try to break at newline first, then space, then hard break
        pos = cut.rfind("\n")
        if pos <= 0:
            pos = cut.rfind(" ")
        if pos <= 0:
            pos = max_len
        chunks.append(content[:pos])
        content = content[pos:].lstrip()
    return chunks


def build_assistant_message(
    content: str | None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
    thinking_blocks: list[dict] | None = None,
) -> dict[str, Any]:
    """Build a provider-safe assistant message with optional reasoning fields."""
    msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if reasoning_content is not None or thinking_blocks:
        msg["reasoning_content"] = reasoning_content if reasoning_content is not None else ""
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
    return msg


def estimate_prompt_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Estimate prompt tokens with tiktoken.

    Counts all fields that providers send to the LLM: content, tool_calls,
    reasoning_content, tool_call_id, name, plus per-message framing overhead.
    """
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        parts: list[str] = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        txt = part.get("text", "")
                        if txt:
                            parts.append(txt)

            tc = msg.get("tool_calls")
            if tc:
                parts.append(json.dumps(tc, ensure_ascii=False))

            rc = msg.get("reasoning_content")
            if isinstance(rc, str) and rc:
                parts.append(rc)

            for key in ("name", "tool_call_id"):
                value = msg.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)

        if tools:
            parts.append(json.dumps(tools, ensure_ascii=False))

        per_message_overhead = len(messages) * 4
        return len(enc.encode("\n".join(parts))) + per_message_overhead
    except Exception:
        return 0


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate prompt tokens contributed by one persisted message."""
    content = message.get("content")
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                if text:
                    parts.append(text)
            else:
                parts.append(json.dumps(part, ensure_ascii=False))
    elif content is not None:
        parts.append(json.dumps(content, ensure_ascii=False))

    for key in ("name", "tool_call_id"):
        value = message.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    if message.get("tool_calls"):
        parts.append(json.dumps(message["tool_calls"], ensure_ascii=False))

    rc = message.get("reasoning_content")
    if isinstance(rc, str) and rc:
        parts.append(rc)

    payload = "\n".join(parts)
    if not payload:
        return 4
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return max(4, len(enc.encode(payload)) + 4)
    except Exception:
        return max(4, len(payload) // 4 + 4)


def estimate_prompt_tokens_chain(
    provider: Any,
    model: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    """Estimate prompt tokens via provider counter first, then tiktoken fallback."""
    provider_counter = getattr(provider, "estimate_prompt_tokens", None)
    if callable(provider_counter):
        with suppress(Exception):
            tokens, source = provider_counter(messages, tools, model)
            if isinstance(tokens, (int, float)) and tokens > 0:
                return int(tokens), str(source or "provider_counter")
    estimated = estimate_prompt_tokens(messages, tools)
    if estimated > 0:
        return int(estimated), "tiktoken"
    return 0, "none"


def build_status_content(
    *,
    version: str,
    model: str,
    start_time: float,
    last_usage: dict[str, int],
    context_window_tokens: int,
    session_msg_count: int,
    context_tokens_estimate: int,
    search_usage_text: str | None = None,
    active_task_count: int = 0,
    max_completion_tokens: int = 8192,
) -> str:
    """Build a human-readable runtime status snapshot.

    Args:
        search_usage_text: Optional pre-formatted web search usage string
                           (produced by SearchUsageInfo.format()). When provided
                           it is appended as an extra section.
    """
    uptime_s = int(time.time() - start_time)
    uptime = (
        f"{uptime_s // 3600}h {(uptime_s % 3600) // 60}m"
        if uptime_s >= 3600
        else f"{uptime_s // 60}m {uptime_s % 60}s"
    )
    last_in = last_usage.get("prompt_tokens", 0)
    last_out = last_usage.get("completion_tokens", 0)
    cached = last_usage.get("cached_tokens", 0)
    ctx_total = max(context_window_tokens, 0)
    # Budget mirrors Consolidator formula: ctx_window - max_completion - _SAFETY_BUFFER
    ctx_budget = max(ctx_total - int(max_completion_tokens) - 1024, 1)
    ctx_pct = min(int((context_tokens_estimate / ctx_budget) * 100), 999) if ctx_budget > 0 else 0
    ctx_used_str = (
        f"{context_tokens_estimate // 1000}k"
        if context_tokens_estimate >= 1000
        else str(context_tokens_estimate)
    )
    ctx_total_str = f"{ctx_total // 1000}k" if ctx_total > 0 else "n/a"
    token_line = f"\U0001f4ca Tokens: {last_in} in / {last_out} out"
    if cached and last_in:
        token_line += f" ({cached * 100 // last_in}% cached)"
    lines = [
        f"OriginAgent v{version}",
        f"\U0001f9e0 Model: {model}",
        token_line,
        f"\U0001f4da Context: {ctx_used_str}/{ctx_total_str} ({ctx_pct}% of input budget)",
        f"\U0001f4ac Session: {session_msg_count} messages",
        f"\u23f1 Uptime: {uptime}",
        f"\u26a1 Tasks: {active_task_count} active",
    ]
    if search_usage_text:
        lines.append(search_usage_text)
    return "\n".join(lines)


_LEGACY_DEFAULT_TEMPLATES: dict[str, str] = {
    "AGENTS.md": """# Agent Instructions

## OriginAgent Role

You are OriginAgent for this workspace: a practical local AI home assistant.
Keep the household context in mind when interpreting short commands such as
"turn it off", "good night", "too hot", or "is everything OK?".

## Scheduled Reminders

Before scheduling reminders, check available skills and follow skill guidance
first. Use the built-in `cron` tool to create, list, and remove jobs. Do not
call `originagent cron` through `exec`.

Get USER_ID and CHANNEL from the current session when a reminder needs to be
delivered back to the user.

Do not just write reminders to MEMORY.md; that will not trigger notifications.

## Heartbeat Tasks

`HEARTBEAT.md` is checked on the configured heartbeat interval. Use file tools
to manage periodic tasks:

- Add: append new tasks with `edit_file`.
- Remove: delete completed tasks with `edit_file`.
- Rewrite: replace all tasks with `write_file`.

When the user asks for a recurring background check, update `HEARTBEAT.md`
instead of creating a one-time reminder.
""",
    "SOUL.md": """# Soul

I am OriginAgent, a local AI home assistant for the user's household.

OriginAgent is meant to run close to the home environment, usually on a NAS,
home server, or trusted local machine. My job is to help the user understand,
coordinate, and safely operate their home systems through conversation.

## Core Principles

- Be useful in the home first: rooms, devices, routines, comfort, energy use,
  maintenance, notifications, and family context matter more than generic chat.
- Protect privacy. Treat household state, logs, routines, names, rooms, and
  device data as sensitive local context.
- Be calm and conservative around real-world actions. Prefer reversible,
  low-risk actions; ask before ambiguous, disruptive, expensive, or safety
  relevant actions.
- Keep replies brief and practical unless the user asks for detail.
- State uncertainty clearly. If a device, room, or intent is ambiguous, resolve
  it before acting.

## Execution Rules

- Act immediately on simple, low-risk requests when the target is clear.
- For multi-step tasks, summarize the plan before executing.
- Before controlling devices, identify the intended entity or room as precisely
  as available context allows.
- For risky actions such as locks, alarms, cameras, appliances, HVAC extremes,
  security modes, destructive automation edits, or anything affecting people
  at home, ask for confirmation unless the user has given an explicit rule.
- Use available MCP tools for home systems instead of inventing API calls.
- After an action, report the result and any important device feedback.
- If a tool call fails, explain the likely cause in plain language and suggest
  the next concrete check.
""",
    "TOOLS.md": """# Tool Usage Notes

Tool signatures are provided automatically via function calling. This file
records OriginAgent-specific operating rules that should guide tool use.

## Home System Tools

- Prefer configured MCP tools for Home Assistant or other home systems.
- Search or inspect entity state before controlling a device when the user's
  wording does not map cleanly to one known entity.
- Do not guess entity IDs. If several matches are plausible, ask a short
  clarification question.
- Use the smallest effective service call. Do not bundle unrelated device
  changes into one action unless the user asked for a scene or routine.
- Treat locks, alarms, cameras, ovens, heaters, high-power devices, and security
  modes as sensitive. Ask for confirmation unless a trusted household rule
  already covers the action.

## Files and Workspace

- Read before writing. Do not assume a file exists or contains expected content.
- Keep household notes, rules, and durable preferences in the workspace files
  instead of scattering them through transient chat.
- Do not expose secrets such as tokens, webhook URLs, or home network details in
  user-facing replies.

## exec

- Commands have a configurable timeout.
- Dangerous commands are blocked.
- `restrictToWorkspace` can limit file access to the active workspace.
- Prefer dedicated tools or MCP tools over shell commands for home automation.

## cron and Heartbeat

- Use cron for scheduled one-time or recurring reminders.
- Use `HEARTBEAT.md` for periodic background checks that OriginAgent should review.
""",
    "USER.md": """# Home Profile

Edit this file to teach OriginAgent about the household.

## Household

- Home name:
- Primary users:
- Preferred language:
- Timezone:

## Rooms

- Living room:
- Bedrooms:
- Kitchen:
- Bathroom:
- Study / office:
- Balcony / outdoor:

## Devices

- Home Assistant URL:
- Lighting:
- Switches / plugs:
- Climate:
- Sensors:
- Media devices:
- Security devices:

## Preferences

- Preferred temperature:
- Quiet hours:
- Notification style:
- Energy-saving preferences:
- Rooms or devices that should not be controlled automatically:

## Safety Notes

- Require confirmation before:
- Never control:
- People, pets, or conditions to consider:

## Useful Phrases

- "Turn on the living room lights" means:
- "Good night" means:
- "I'm leaving" means:
""",
    "HEARTBEAT.md": """# Heartbeat Tasks

This file is checked periodically by OriginAgent.

Use it for background household checks such as "tell me if the front door stays
open for too long" or "watch for unusually high temperature in the study".

If this file has no active tasks, OriginAgent will skip the heartbeat.

## Active Tasks

<!-- Add periodic household checks below this line. -->


## Completed

<!-- Move completed checks here or delete them. -->
""",
    "memory/MEMORY.md": """# Long-term Memory

This file stores durable household context that should persist across sessions.

## Household Facts

- 

## Device Map

- 

## Room Preferences

- 

## Routines and Scenes

- 

## Notification Preferences

- 

## Safety Boundaries

- 

## Maintenance Notes

- 

---

OriginAgent may update this file when it learns important long-term household
facts, preferences, or safety boundaries.
""",
}


def _normalize_template_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _is_legacy_default_template(rel_path: str, content: str) -> bool:
    legacy = _LEGACY_DEFAULT_TEMPLATES.get(rel_path.replace("\\", "/"))
    if legacy is None:
        return False
    return _normalize_template_text(content) == _normalize_template_text(legacy)


def sync_workspace_templates(workspace: Path, silent: bool = False) -> list[str]:
    """Sync bundled templates to workspace.

    Missing templates are created. Known old default templates are upgraded, but
    customized workspace files are never overwritten.
    """
    from importlib.resources import files as pkg_files

    try:
        tpl = pkg_files("OriginAgent") / "templates"
    except Exception:
        return []
    if not tpl.is_dir():
        return []

    added: list[str] = []
    updated: list[str] = []

    def _write(src, dest: Path):
        rel_path = str(dest.relative_to(workspace)).replace("\\", "/")
        if dest.exists():
            if src is not None:
                try:
                    current = dest.read_text(encoding="utf-8")
                except OSError:
                    return
                if _is_legacy_default_template(rel_path, current):
                    new_content = src.read_text(encoding="utf-8")
                    if _normalize_template_text(current) != _normalize_template_text(new_content):
                        dest.write_text(new_content, encoding="utf-8")
                        updated.append(rel_path)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8") if src else "", encoding="utf-8")
        added.append(rel_path)

    for item in tpl.iterdir():
        if item.name.endswith(".md") and not item.name.startswith("."):
            _write(item, workspace / item.name)
    _write(tpl / "memory" / "MEMORY.md", workspace / "memory" / "MEMORY.md")
    _write(None, workspace / "memory" / "history.jsonl")
    (workspace / "skills").mkdir(exist_ok=True)

    if (added or updated) and not silent:
        from rich.console import Console

        console = Console()
        for name in added:
            console.print(f"  [dim]Created {name}[/dim]")
        for name in updated:
            console.print(f"  [dim]Updated default template {name}[/dim]")

    # Initialize git for memory version control
    try:
        from OriginAgent.utils.gitstore import GitStore

        gs = GitStore(
            workspace,
            tracked_files=[
                "SOUL.md",
                "USER.md",
                "memory/MEMORY.md",
            ],
        )
        gs.init()
    except Exception:
        logger.exception("Failed to initialize git store for {}", workspace)

    return added
