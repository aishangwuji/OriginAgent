# OriginAgent / nanobot 0.2.0 Alignment

OriginAgent now adopts selected nanobot 0.2.0 runtime patterns while keeping OriginAgent-specific safety, device, audit, and WebUI behavior as the source of truth.

## Pairing

Pairing is opt-in. The default `security.pairing.enabled: false` preserves OriginAgent's existing behavior: an empty `allowFrom` denies every sender.

When enabled, authorization order is:

1. `allowFrom: ["*"]`
2. Exact sender match in `allowFrom`
3. Approved sender in the pairing store

Only unauthorized direct messages receive a pairing code. Group messages and WebSocket traffic do not create ordinary pairing codes. `/pairing` commands are restricted to configured approval channels unless `security.pairing.allowSelfApprove` is explicitly enabled.

## Tool Plugins

OriginAgent core tools are still registered by `_register_default_tools()`. The nanobot-style `ToolLoader` runs afterward as an external plugin layer and uses the `originagent.tools` entry point group.

Plugins cannot replace already registered OriginAgent core tools. Tool classes may declare `config_key`, `_plugin_discoverable`, `_scopes`, `enabled(ctx)`, and `create(ctx)`.

## Attachment Staging

Session replay staging copies trusted local attachments into the signed WebUI media area. The default limits are:

- Maximum single file size: 25 MB
- Maximum files per turn: 20
- Allowed sources: workspace, OriginAgent media root, and OriginAgent runtime temp output
- Rejected sources: URLs, missing files, symlink paths, files outside trusted roots, and oversized files

The WebUI continues to use signed media URLs; this alignment does not add a public static file route.

## MCP Probe

HTTP and SSE MCP transports are probed before connection. Unreachable endpoints are skipped cleanly. Stdio transports are not probed. OriginAgent's SSRF URL policy still runs first and remains a hard security boundary.
