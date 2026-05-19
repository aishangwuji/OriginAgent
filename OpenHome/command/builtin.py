"""Built-in slash command handlers."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from contextlib import suppress
from dataclasses import dataclass

from OpenHome import __version__
from OpenHome.bus.events import OutboundMessage
from OpenHome.command.router import CommandContext, CommandRouter
from OpenHome.utils.helpers import build_status_content
from OpenHome.utils.restart import set_restart_notice_to_env


@dataclass(frozen=True)
class BuiltinCommandSpec:
    command: str
    title: str
    description: str
    icon: str
    arg_hint: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "command": self.command,
            "title": self.title,
            "description": self.description,
            "icon": self.icon,
            "arg_hint": self.arg_hint,
        }


BUILTIN_COMMAND_SPECS: tuple[BuiltinCommandSpec, ...] = (
    BuiltinCommandSpec(
        "/new",
        "New chat",
        "Stop the current task and start a fresh conversation.",
        "square-pen",
    ),
    BuiltinCommandSpec(
        "/stop",
        "Stop current task",
        "Cancel the active agent turn for this chat.",
        "square",
    ),
    BuiltinCommandSpec(
        "/restart",
        "Restart OpenHome",
        "Restart the bot process in place.",
        "rotate-cw",
    ),
    BuiltinCommandSpec(
        "/status",
        "Show status",
        "Display runtime, provider, and channel status.",
        "activity",
    ),
    BuiltinCommandSpec(
        "/goal",
        "Start long-running goal",
        "Treat the request as a sustained goal until complete_goal is called.",
        "target",
        "<goal>",
    ),
    BuiltinCommandSpec(
        "/model",
        "Switch model",
        "Show or switch the active model preset.",
        "badge",
        "[preset]",
    ),
    BuiltinCommandSpec(
        "/pairing",
        "Manage pairing",
        "List, approve, deny, or revoke DM pairing requests.",
        "key-round",
        "[list|approve|deny|revoke]",
    ),
    BuiltinCommandSpec(
        "/mcp",
        "Show MCP servers",
        "List configured MCP servers and registered capabilities.",
        "server",
    ),
    BuiltinCommandSpec(
        "/skill",
        "Show skills",
        "List available agent skills and where they come from.",
        "graduation-cap",
    ),
    BuiltinCommandSpec(
        "/domain",
        "Show domain packs",
        "List installed domain packs and their availability.",
        "boxes",
    ),
    BuiltinCommandSpec(
        "/history",
        "Show conversation history",
        "Print the last N persisted conversation messages.",
        "history",
        "[n]",
    ),
    BuiltinCommandSpec(
        "/reviews",
        "Show learning proposals",
        "List and review background learning proposals.",
        "list-checks",
        "[n|show|apply|reject]",
    ),
    BuiltinCommandSpec(
        "/dream",
        "Run Dream",
        "Manually trigger memory consolidation.",
        "sparkles",
    ),
    BuiltinCommandSpec(
        "/dream-log",
        "Show Dream log",
        "Show what the last Dream consolidation changed.",
        "book-open",
    ),
    BuiltinCommandSpec(
        "/dream-restore",
        "Restore memory",
        "Revert memory to a previous Dream snapshot.",
        "undo-2",
    ),
    BuiltinCommandSpec(
        "/help",
        "Show help",
        "List available slash commands.",
        "circle-help",
    ),
)


def builtin_command_palette() -> list[dict[str, str]]:
    """Return structured command metadata for UI command palettes."""
    return [spec.as_dict() for spec in BUILTIN_COMMAND_SPECS]


async def cmd_stop(ctx: CommandContext) -> OutboundMessage:
    """Cancel all active tasks and subagents for the session."""
    loop = ctx.loop
    msg = ctx.msg
    total = await loop._cancel_active_tasks(ctx.key)
    content = f"Stopped {total} task(s)." if total else "No active task to stop."
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content=content,
        metadata=dict(msg.metadata or {})
    )


async def cmd_restart(ctx: CommandContext) -> OutboundMessage:
    """Restart the process in-place via os.execv."""
    msg = ctx.msg
    set_restart_notice_to_env(
        channel=msg.channel,
        chat_id=msg.chat_id,
        metadata=dict(msg.metadata or {}),
    )

    async def _do_restart():
        await asyncio.sleep(1)
        os.execv(sys.executable, [sys.executable, "-m", "OpenHome"] + sys.argv[1:])

    asyncio.create_task(_do_restart())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Restarting...",
        metadata=dict(msg.metadata or {})
    )


async def cmd_status(ctx: CommandContext) -> OutboundMessage:
    """Build an outbound status message for a session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    ctx_est = 0
    with suppress(Exception):
        ctx_est, _ = loop.consolidator.estimate_session_prompt_tokens(session)
    if ctx_est <= 0:
        ctx_est = loop._last_usage.get("prompt_tokens", 0)

    # Fetch web search provider usage (best-effort, never blocks the response)
    search_usage_text: str | None = None
    # Never let usage fetch break /status
    with suppress(Exception):
        from OpenHome.utils.searchusage import fetch_search_usage
        web_cfg = getattr(loop, "web_config", None)
        search_cfg = getattr(web_cfg, "search", None) if web_cfg else None
        if search_cfg is not None:
            provider = getattr(search_cfg, "provider", "duckduckgo")
            api_key = getattr(search_cfg, "api_key", "") or None
            usage = await fetch_search_usage(provider=provider, api_key=api_key)
            search_usage_text = usage.format()
    active_tasks = loop._active_tasks.get(ctx.key, [])
    task_count = sum(1 for t in active_tasks if not t.done())
    with suppress(Exception):
        task_count += loop.subagents.get_running_count_by_session(ctx.key)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_status_content(
            version=__version__, model=loop.model,
            start_time=loop._start_time, last_usage=loop._last_usage,
            context_window_tokens=loop.context_window_tokens,
            session_msg_count=len(session.get_history(max_messages=0)),
            context_tokens_estimate=ctx_est,
            search_usage_text=search_usage_text,
            active_task_count=task_count,
            max_completion_tokens=getattr(
                getattr(loop.provider, "generation", None), "max_tokens", 8192
            ),
        ),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_new(ctx: CommandContext) -> OutboundMessage:
    """Stop active task and start a fresh session."""
    loop = ctx.loop
    await loop._cancel_active_tasks(ctx.key)
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    snapshot = session.messages[session.last_consolidated:]
    session.clear()
    loop.sessions.save(session)
    loop.sessions.invalidate(session.key)
    if snapshot:
        loop._schedule_background(loop.consolidator.archive(snapshot))
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content="New session started.",
        metadata=dict(ctx.msg.metadata or {})
    )


_GOAL_PROMPT_TEMPLATE = """The user invoked `/goal` to start a sustained objective.

Inspect or clarify if needed, then call `long_task` with the refined objective and optional short `ui_summary`. Work proceeds as normal assistant turns using ordinary tools. When the objective is fully done and verified, call `complete_goal` with a brief recap. If the user later cancels or changes direction, still call `complete_goal` with an honest recap before starting a replacement goal. Do not use `long_task` / `complete_goal` for trivial one-shot answers.

Goal:
{goal}
"""


async def cmd_goal(ctx: CommandContext) -> OutboundMessage | None:
    """Rewrite /goal into a normal agent turn that nudges long_task use."""
    goal = ctx.args.strip()
    if not goal:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Usage: /goal <long-running task description>",
            metadata=dict(ctx.msg.metadata or {}),
        )

    active_tasks = ctx.loop._active_tasks.get(ctx.key, [])
    if any(not task.done() for task in active_tasks):
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=(
                "A task is already running in this chat. "
                "Use `/stop` first, then send `/goal <long-running task description>` again."
            ),
            metadata=dict(ctx.msg.metadata or {}),
        )

    ctx.msg.metadata.update(
        {
            "original_command": "/goal",
            "original_content": ctx.raw,
            "goal_started_at": time.time(),
        }
    )
    ctx.msg.content = _GOAL_PROMPT_TEMPLATE.format(goal=goal)
    return None


def _model_preset_names(loop) -> list[str]:
    names = set(getattr(loop, "model_presets", {}) or {})
    if names:
        names.add("default")
    return sorted(names)


def _format_model_status(loop) -> str:
    names = _model_preset_names(loop)
    active = getattr(loop, "model_preset", None) or "default"
    if not names:
        return f"Current model: `{loop.model}`.\n\nNo model presets are configured."
    lines = [f"Current preset: `{active}`", f"Current model: `{loop.model}`", "", "Available presets:"]
    for name in names:
        marker = "*" if name == active else "-"
        lines.append(f"{marker} `{name}`")
    return "\n".join(lines)


async def cmd_model(ctx: CommandContext) -> OutboundMessage:
    """Show or switch runtime model preset."""
    name = ctx.args.strip()
    if not name:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=_format_model_status(ctx.loop),
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )
    try:
        ctx.loop.set_model_preset(name)
    except Exception as exc:
        names = ", ".join(f"`{n}`" for n in _model_preset_names(ctx.loop)) or "(none)"
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=f"Could not switch model preset: {exc}\n\nAvailable presets: {names}",
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=f"Switched model preset to `{ctx.loop.model_preset}`.\nCurrent model: `{ctx.loop.model}`",
        metadata={
            **dict(ctx.msg.metadata or {}),
            "render_as": "text",
            "model_preset": ctx.loop.model_preset,
            "model": ctx.loop.model,
        },
    )


def _pairing_config(ctx: CommandContext):
    config = getattr(ctx.loop, "pairing_config", None)
    if config is not None:
        return config
    with suppress(Exception):
        from OpenHome.config.loader import load_config

        return load_config().security.pairing
    from OpenHome.config.schema import PairingConfig

    return PairingConfig()


def _pairing_command_allowed(ctx: CommandContext, subcommand: str) -> bool:
    config = _pairing_config(ctx)
    if str(ctx.msg.channel) in set(config.approval_channels):
        return True
    if config.allow_self_approve:
        return True
    return False


async def cmd_pairing(ctx: CommandContext) -> OutboundMessage:
    """List, approve, deny or revoke pairing requests when pairing is enabled."""
    from OpenHome.pairing import PAIRING_COMMAND_META_KEY, handle_pairing_command

    config = _pairing_config(ctx)
    meta = {**dict(ctx.msg.metadata or {}), PAIRING_COMMAND_META_KEY: True, "render_as": "text"}
    if not config.enabled:
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Pairing is disabled. Enable `security.pairing.enabled` to use `/pairing`.",
            metadata=meta,
        )
    subcommand = (ctx.args.strip().split() or ["list"])[0]
    if not _pairing_command_allowed(ctx, subcommand):
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Pairing approval commands are restricted to trusted approval channels.",
            metadata=meta,
        )
    reply = handle_pairing_command(ctx.msg.channel, ctx.args)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=reply,
        metadata=meta,
    )


def _format_mcp_capabilities(items: list[dict], *, limit: int = 10) -> str:
    registered = [item for item in items if item.get("status") == "registered"]
    names = [str(item.get("wrapped_name") or item.get("name") or "").strip() for item in registered]
    names = [name for name in names if name]
    if not names:
        return "none"
    shown = names[:limit]
    suffix = f", +{len(names) - limit} more" if len(names) > limit else ""
    return ", ".join(f"`{name}`" for name in shown) + suffix


def _format_mcp_status(loop) -> str:
    configured = getattr(loop, "_mcp_servers", {}) or {}
    snapshot = getattr(loop, "_mcp_snapshot", {}) or {}
    connected = getattr(loop, "_mcp_connected", False)
    connecting = getattr(loop, "_mcp_connecting", False)

    if not configured:
        return (
            "## MCP Servers\n\n"
            "No MCP servers are configured.\n\n"
            "Add entries under `tools.mcp_servers` in the OpenHome config, then restart the gateway."
        )

    lines = [
        "## MCP Servers",
        "",
        f"- Configured: {len(configured)}",
        f"- Connected: {len(getattr(loop, '_mcp_stacks', {}) or {})}",
        f"- State: {'connecting' if connecting else 'connected' if connected else 'not connected yet'}",
        "",
    ]

    for name in sorted(configured):
        cfg = configured[name]
        snap = snapshot.get(name, {})
        status = snap.get("status") or ("connected" if name in getattr(loop, "_mcp_stacks", {}) else "pending")
        transport = snap.get("transport") or getattr(cfg, "type", "") or ("stdio" if getattr(cfg, "command", "") else "streamableHttp" if getattr(cfg, "url", "") else "unknown")
        tools = snap.get("tools") or []
        resources = snap.get("resources") or []
        prompts = snap.get("prompts") or []
        registered_count = snap.get("registered_count")
        if registered_count is None:
            registered_count = sum(
                1
                for item in [*tools, *resources, *prompts]
                if isinstance(item, dict) and item.get("status") == "registered"
            )

        lines.extend(
            [
                f"### `{name}`",
                f"- Status: {status}",
                f"- Transport: {transport}",
                f"- Registered capabilities: {registered_count}",
                f"- Tools ({sum(1 for item in tools if item.get('status') == 'registered')}/{len(tools)}): {_format_mcp_capabilities(tools)}",
                f"- Resources ({sum(1 for item in resources if item.get('status') == 'registered')}/{len(resources)}): {_format_mcp_capabilities(resources)}",
                f"- Prompts ({sum(1 for item in prompts if item.get('status') == 'registered')}/{len(prompts)}): {_format_mcp_capabilities(prompts)}",
            ]
        )
        error = str(snap.get("error") or "").strip()
        if error:
            lines.append(f"- Error: {error}")
        lines.append("")

    return "\n".join(lines).rstrip()


async def cmd_mcp(ctx: CommandContext) -> OutboundMessage:
    """List configured MCP servers and registered capabilities."""
    loop = ctx.loop
    with suppress(Exception):
        await loop._connect_mcp()
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=_format_mcp_status(loop),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def _format_skill_status(loop) -> str:
    loader = loop.context.skills
    all_skills = sorted(
        loader.list_skills(filter_unavailable=False),
        key=lambda item: (item.get("source", ""), item.get("name", "")),
    )
    if not all_skills:
        return "## Skills\n\nNo skills are available."

    lines = ["## Skills", ""]
    available_count = 0
    workspace_count = 0
    builtin_count = 0
    rows: list[str] = []

    for entry in all_skills:
        name = entry["name"]
        source = entry.get("source", "unknown")
        if source == "workspace":
            workspace_count += 1
        elif source == "builtin":
            builtin_count += 1
        meta = loader._get_skill_meta(name)
        available = loader._check_requirements(meta)
        if available:
            available_count += 1
        desc = loader._get_skill_description(name)
        missing = loader._get_missing_requirements(meta) if not available else ""
        suffix = f" unavailable: {missing}" if missing else " unavailable" if not available else "available"
        rows.append(f"- `{name}` [{source}] — {desc} ({suffix})")

    always = loader.get_always_skills()
    lines.extend(
        [
            f"- Total: {len(all_skills)}",
            f"- Available: {available_count}",
            f"- Workspace: {workspace_count}",
            f"- Built-in: {builtin_count}",
        ]
    )
    if always:
        lines.append(f"- Always loaded: {', '.join(f'`{name}`' for name in always)}")
    lines.extend(["", *rows])
    return "\n".join(lines)


async def cmd_skill(ctx: CommandContext) -> OutboundMessage:
    """List available skills."""
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=_format_skill_status(ctx.loop),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def _format_domain_status(loop) -> str:
    context = getattr(loop, "context", None)
    manager = getattr(loop, "domain_packs", None) or getattr(context, "domain_packs", None)
    if manager is None:
        return "## Domain Packs\n\nNo domain pack manager is configured."
    packs = manager.list_packs()
    if not packs:
        return "## Domain Packs\n\nNo domain packs are installed."

    lines = ["## Domain Packs", ""]
    available_count = sum(1 for pack in packs if pack.status == "available")
    active_count = sum(1 for pack in packs if pack.active)
    invalid_count = sum(1 for pack in packs if pack.status == "invalid")
    lines.extend(
        [
            f"- Total: {len(packs)}",
            f"- Available: {available_count}",
            f"- Active: {active_count}",
            f"- Invalid: {invalid_count}",
            "",
        ]
    )
    for pack in packs:
        active = "active" if pack.active else "inactive"
        reason = f"; reason: {pack.unavailable_reason}" if pack.unavailable_reason else ""
        lines.append(
            f"- `{pack.id}` [{pack.source}] — {pack.name} "
            f"(status: {pack.status}, {active}{reason})"
        )
        if getattr(pack, "skills", ()):
            available_skills = [skill for skill in pack.skills if skill.status == "available"]
            skipped_skills = [skill for skill in pack.skills if skill.status == "skipped"]
            available_names = [
                skill.virtual_id or skill.id
                for skill in available_skills
            ]
            available_suffix = (
                " — " + ", ".join(f"`{name}`" for name in available_names[:5])
                if available_names
                else ""
            )
            if len(available_names) > 5:
                available_suffix += f", +{len(available_names) - 5} more"
            lines.append(
                f"  Skills: declared {len(pack.skills)}, available {len(available_skills)}, "
                f"skipped {len(skipped_skills)}{available_suffix}"
            )
            for skill in skipped_skills[:3]:
                lines.append(f"  - skipped skill `{skill.id}`: {skill.unavailable_reason}")
        if getattr(pack, "tools", ()):
            manifest_skipped = [tool for tool in pack.tools if tool.status == "skipped"]
            runtime_records = (
                manager.domain_tool_runtime_records(pack.id)
                if hasattr(manager, "domain_tool_runtime_records")
                else []
            )
            registered = [record for record in runtime_records if record.status == "registered"]
            runtime_skipped = [record for record in runtime_records if record.status == "skipped"]
            skipped_count = len(manifest_skipped) + len(runtime_skipped)
            lines.append(
                f"  Tools: declared {len(pack.tools)}, registered {len(registered)}, "
                f"skipped {skipped_count}"
            )
            for tool in manifest_skipped[:3]:
                lines.append(f"  - skipped tool `{tool.id}`: {tool.unavailable_reason}")
            for record in runtime_skipped[:3]:
                lines.append(f"  - skipped tool `{record.tool_id}`: {record.reason}")
    return "\n".join(lines)


async def cmd_domain(ctx: CommandContext) -> OutboundMessage:
    """List installed domain packs."""
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=_format_domain_status(ctx.loop),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_dream(ctx: CommandContext) -> OutboundMessage:
    """Manually trigger a Dream consolidation run."""
    import time

    loop = ctx.loop
    msg = ctx.msg

    async def _run_dream():
        t0 = time.monotonic()
        try:
            did_work = await loop.dream.run()
            elapsed = time.monotonic() - t0
            if did_work:
                content = f"Dream completed in {elapsed:.1f}s."
            else:
                content = "Dream: nothing to process."
        except Exception as e:
            elapsed = time.monotonic() - t0
            content = f"Dream failed after {elapsed:.1f}s: {e}"
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    asyncio.create_task(_run_dream())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Dreaming...",
    )


def _extract_changed_files(diff: str) -> list[str]:
    """Extract changed file paths from a unified diff."""
    files: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[3]
        if path.startswith("b/"):
            path = path[2:]
        if path in seen:
            continue
        seen.add(path)
        files.append(path)
    return files


def _format_changed_files(diff: str) -> str:
    files = _extract_changed_files(diff)
    if not files:
        return "No tracked memory files changed."
    return ", ".join(f"`{path}`" for path in files)


def _format_dream_log_content(commit, diff: str, *, requested_sha: str | None = None) -> str:
    files_line = _format_changed_files(diff)
    lines = [
        "## Dream Update",
        "",
        "Here is the selected Dream memory change." if requested_sha else "Here is the latest Dream memory change.",
        "",
        f"- Commit: `{commit.sha}`",
        f"- Time: {commit.timestamp}",
        f"- Changed files: {files_line}",
    ]
    if diff:
        lines.extend([
            "",
            f"Use `/dream-restore {commit.sha}` to undo this change.",
            "",
            "```diff",
            diff.rstrip(),
            "```",
        ])
    else:
        lines.extend([
            "",
            "Dream recorded this version, but there is no file diff to display.",
        ])
    return "\n".join(lines)


def _format_dream_restore_list(commits: list) -> str:
    lines = [
        "## Dream Restore",
        "",
        "Choose a Dream memory version to restore. Latest first:",
        "",
    ]
    for c in commits:
        lines.append(f"- `{c.sha}` {c.timestamp} - {c.message.splitlines()[0]}")
    lines.extend([
        "",
        "Preview a version with `/dream-log <sha>` before restoring it.",
        "Restore a version with `/dream-restore <sha>`.",
    ])
    return "\n".join(lines)


async def cmd_dream_log(ctx: CommandContext) -> OutboundMessage:
    """Show what the last Dream changed.

    Default: diff of the latest commit (HEAD~1 vs HEAD).
    With /dream-log <sha>: diff of that specific commit.
    """
    store = ctx.loop.consolidator.store
    git = store.git

    if not git.is_initialized():
        if store.get_last_dream_cursor() == 0:
            msg = "Dream has not run yet. Run `/dream`, or wait for the next scheduled Dream cycle."
        else:
            msg = "Dream history is not available because memory versioning is not initialized."
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content=msg, metadata={"render_as": "text"},
        )

    args = ctx.args.strip()

    if args:
        # Show diff of a specific commit
        sha = args.split()[0]
        result = git.show_commit_diff(sha)
        if not result:
            content = (
                f"Couldn't find Dream change `{sha}`.\n\n"
                "Use `/dream-restore` to list recent versions, "
                "or `/dream-log` to inspect the latest one."
            )
        else:
            commit, diff = result
            content = _format_dream_log_content(commit, diff, requested_sha=sha)
    else:
        # Default: show the latest commit's diff
        commits = git.log(max_entries=1)
        result = git.show_commit_diff(commits[0].sha) if commits else None
        if result:
            commit, diff = result
            content = _format_dream_log_content(commit, diff)
        else:
            content = "Dream memory has no saved versions yet."

    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
    )


async def cmd_dream_restore(ctx: CommandContext) -> OutboundMessage:
    """Restore memory files from a previous dream commit.

    Usage:
        /dream-restore          — list recent commits
        /dream-restore <sha>    — revert a specific commit
    """
    store = ctx.loop.consolidator.store
    git = store.git
    if not git.is_initialized():
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content="Dream history is not available because memory versioning is not initialized.",
        )

    args = ctx.args.strip()
    if not args:
        # Show recent commits for the user to pick
        commits = git.log(max_entries=10)
        if not commits:
            content = "Dream memory has no saved versions to restore yet."
        else:
            content = _format_dream_restore_list(commits)
    else:
        sha = args.split()[0]
        result = git.show_commit_diff(sha)
        changed_files = _format_changed_files(result[1]) if result else "the tracked memory files"
        new_sha = git.revert(sha)
        if new_sha:
            content = (
                f"Restored Dream memory to the state before `{sha}`.\n\n"
                f"- New safety commit: `{new_sha}`\n"
                f"- Restored files: {changed_files}\n\n"
                f"Use `/dream-log {new_sha}` to inspect the restore diff."
            )
        else:
            content = (
                f"Couldn't restore Dream change `{sha}`.\n\n"
                "It may not exist, or it may be the first saved version with no earlier state to restore."
            )
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
    )


_HISTORY_DEFAULT_COUNT = 10
_HISTORY_MAX_COUNT = 50
_HISTORY_MAX_CONTENT_CHARS = 200


def _format_history_message(msg: dict) -> str | None:
    """Format a single history message for display. Returns None to skip."""
    role = msg.get("role")
    if role not in ("user", "assistant"):
        return None
    content = msg.get("content") or ""
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        content = " ".join(parts)
    content = str(content).strip()
    if not content:
        return None
    if len(content) > _HISTORY_MAX_CONTENT_CHARS:
        content = content[:_HISTORY_MAX_CONTENT_CHARS] + "…"
    label = "👤 You" if role == "user" else "🤖 Bot"
    return f"{label}: {content}"


async def cmd_history(ctx: CommandContext) -> OutboundMessage:
    """Show the last N messages of the current session (default 10, max 50).

    Usage: /history [count]
    """
    count = _HISTORY_DEFAULT_COUNT
    if ctx.args.strip():
        try:
            count = max(1, min(int(ctx.args.strip()), _HISTORY_MAX_COUNT))
        except ValueError:
            return OutboundMessage(
                channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
                content="Usage: /history [count] — e.g. /history 5 (default: 10, max: 50)",
                metadata=dict(ctx.msg.metadata or {}),
            )

    session = ctx.session or ctx.loop.sessions.get_or_create(ctx.key)
    history = session.get_history(max_messages=0)
    visible = [_format_history_message(m) for m in history]
    visible = [m for m in visible if m is not None]
    recent = visible[-count:]

    if not recent:
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content="No conversation history yet.",
            metadata=dict(ctx.msg.metadata or {}),
        )

    header = f"Last {len(recent)} message(s):\n"
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=header + "\n".join(recent),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


_REVIEWS_DEFAULT_COUNT = 10
_REVIEWS_MAX_COUNT = 50


def _reviews_store(ctx: CommandContext):
    service = getattr(ctx.loop, "background_review", None)
    return getattr(service, "store", None), service


def _format_review_record(record: dict) -> str:
    proposal_id = str(record.get("id") or "unknown")
    proposal_type = str(record.get("proposal_type") or record.get("type") or "unknown")
    domain_id = str(record.get("domain_id") or "core")
    status = str(record.get("status") or "pending")
    title = str(record.get("title") or "(untitled)")
    content = str(record.get("content") or "").strip()
    if len(content) > 260:
        content = content[:260] + "..."
    created_at = str(record.get("created_at") or "")
    confidence = record.get("confidence")
    confidence_text = f", confidence={confidence}" if confidence is not None else ""
    lines = [
        f"- `{proposal_id}` [{status}] {proposal_type}/{domain_id}: {title}",
        f"  created={created_at}{confidence_text}",
    ]
    if content:
        lines.append(f"  {content}")
    return "\n".join(lines)


def _format_review_detail(record: dict) -> str:
    lines = [
        "## Background Review Proposal",
        "",
        f"- ID: `{record.get('id') or 'unknown'}`",
        f"- Status: {record.get('status') or 'pending'}",
        f"- Type: {record.get('proposal_type') or record.get('type') or 'unknown'}",
        f"- Domain: {record.get('domain_id') or 'core'}",
        f"- Created: {record.get('created_at') or ''}",
        f"- Session: {record.get('session_key') or ''}",
        "",
        f"### {record.get('title') or '(untitled)'}",
        "",
        str(record.get("content") or "").strip() or "(empty)",
    ]
    rationale = str(record.get("rationale") or "").strip()
    if rationale:
        lines.extend(["", "### Rationale", "", rationale])
    evidence = record.get("evidence")
    if isinstance(evidence, list) and evidence:
        lines.extend(["", "### Evidence", ""])
        lines.extend(f"- {item}" for item in evidence if str(item).strip())
    review_reason = str(record.get("review_reason") or "").strip()
    if review_reason:
        lines.extend(["", "### Review Reason", "", review_reason])
    fact_id = record.get("applied_fact_id")
    if isinstance(fact_id, str) and fact_id:
        lines.extend(["", f"Applied fact: `{fact_id}`"])
    skill_path = record.get("applied_skill_path")
    if isinstance(skill_path, str) and skill_path:
        lines.extend(["", f"Applied skill: `{skill_path}`"])
    return "\n".join(lines)


def _format_review_result(result: object) -> str:
    if hasattr(result, "to_json"):
        data = result.to_json()
    elif isinstance(result, dict):
        data = result
    else:
        data = {"ok": False, "message": str(result)}
    lines = [
        f"Review `{data.get('proposal_id') or ''}`: {data.get('message') or data.get('status')}",
        f"- Status: {data.get('status') or 'unknown'}",
    ]
    fact_id = data.get("fact_id")
    if fact_id:
        lines.append(f"- Fact: `{fact_id}`")
    artifact = data.get("artifact")
    if isinstance(artifact, dict):
        skill_name = artifact.get("skill_name")
        skill_path = artifact.get("path")
        if skill_name:
            lines.append(f"- Skill: `{skill_name}`")
        if skill_path:
            lines.append(f"- Path: `{skill_path}`")
    error = data.get("error")
    if error:
        lines.append(f"- Error: {error}")
    return "\n".join(lines)


async def cmd_reviews(ctx: CommandContext) -> OutboundMessage:
    """Show and manage pending background review proposals.

    Usage:
      /reviews [count]
      /reviews show <proposal_id>
      /reviews apply|approve <proposal_id>
      /reviews reject|defer <proposal_id> [reason]
    """
    raw_args = ctx.args.strip()
    args = raw_args.split(maxsplit=2)
    store, service = _reviews_store(ctx)
    if store is None:
        content = "Background review proposal store is not available."
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=content,
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )

    if args and args[0] in {"show", "apply", "approve", "reject", "defer"}:
        action = args[0]
        proposal_id = args[1] if len(args) >= 2 else ""
        reason = args[2] if len(args) >= 3 else ""
        if not proposal_id:
            content = (
                "Usage: /reviews show <proposal_id>, "
                "/reviews apply <proposal_id>, "
                "/reviews reject <proposal_id> [reason], "
                "or /reviews defer <proposal_id> [reason]"
            )
        elif action == "show":
            record = store.get(proposal_id)
            content = _format_review_detail(record) if record else "Review proposal was not found."
        elif action in {"apply", "approve"}:
            content = _format_review_result(store.apply(proposal_id, reason=reason))
        elif action == "reject":
            content = _format_review_result(store.reject(proposal_id, reason=reason))
        else:
            content = _format_review_result(store.defer(proposal_id, reason=reason))
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content=content,
            metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
        )

    count = _REVIEWS_DEFAULT_COUNT
    if raw_args:
        try:
            count = max(1, min(int(raw_args), _REVIEWS_MAX_COUNT))
        except ValueError:
            return OutboundMessage(
                channel=ctx.msg.channel,
                chat_id=ctx.msg.chat_id,
                content=(
                    "Usage: /reviews [count], /reviews show <proposal_id>, "
                    "/reviews apply <proposal_id>, /reviews reject <proposal_id> [reason]"
                ),
                metadata=dict(ctx.msg.metadata or {}),
            )

    records = store.recent(count)
    if not records:
        enabled = bool(getattr(service, "enabled", False))
        suffix = " It is currently disabled." if not enabled else ""
        content = "No background review proposals yet." + suffix
    else:
        stats = store.stats()
        lines = [
            "## Background Review Proposals",
            "",
            f"- Showing: {len(records)}",
            f"- Total stored: {stats.get('proposal_count', 0)}",
            f"- Pending: {stats.get('pending_count', 0)}",
            "",
        ]
        lines.extend(_format_review_record(record) for record in records)
        content = "\n".join(lines)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_help(ctx: CommandContext) -> OutboundMessage:
    """Return available slash commands."""
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_help_text(),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def build_help_text() -> str:
    """Build canonical help text shared across channels."""
    lines = ["OpenHome commands:"]
    for spec in BUILTIN_COMMAND_SPECS:
        command = spec.command
        if spec.arg_hint:
            command = f"{command} {spec.arg_hint}"
        lines.append(f"{command} — {spec.description}")
    return "\n".join(lines)


def register_builtin_commands(router: CommandRouter) -> None:
    """Register the default set of slash commands."""
    router.priority("/stop", cmd_stop)
    router.priority("/restart", cmd_restart)
    router.priority("/status", cmd_status)
    router.exact("/new", cmd_new)
    router.exact("/status", cmd_status)
    router.exact("/goal", cmd_goal)
    router.prefix("/goal ", cmd_goal)
    router.exact("/model", cmd_model)
    router.prefix("/model ", cmd_model)
    router.exact("/pairing", cmd_pairing)
    router.prefix("/pairing ", cmd_pairing)
    router.exact("/mcp", cmd_mcp)
    router.exact("/skill", cmd_skill)
    router.exact("/skills", cmd_skill)
    router.exact("/domain", cmd_domain)
    router.exact("/domains", cmd_domain)
    router.exact("/history", cmd_history)
    router.prefix("/history ", cmd_history)
    router.exact("/reviews", cmd_reviews)
    router.prefix("/reviews ", cmd_reviews)
    router.exact("/dream", cmd_dream)
    router.exact("/dream-log", cmd_dream_log)
    router.prefix("/dream-log ", cmd_dream_log)
    router.exact("/dream-restore", cmd_dream_restore)
    router.prefix("/dream-restore ", cmd_dream_restore)
    router.exact("/help", cmd_help)
