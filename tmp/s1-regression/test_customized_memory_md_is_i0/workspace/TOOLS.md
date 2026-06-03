# Tool Usage Notes

Tool signatures are provided automatically via function calling. This file
records OpenHome-specific operating rules that should guide tool use.

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
- Use `HEARTBEAT.md` for periodic background checks that OpenHome should review.
