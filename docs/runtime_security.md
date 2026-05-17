# Runtime Security Notes

## Tool Audit Modes

Tool call audit is separate from action and device audit.

- `off`: do not write generic tool call audit events.
- `minimal`: default; record tool name, status, timing, and tool flags. Security summaries are recorded only for policy denials and configured high-capability tools.
- `security`: record privacy-preserving actor/session hashes, target hashes, policy rule, and result size for every tool call.

No tool audit mode records raw params, results, paths, URLs, commands, messages, or device IDs.

## Capability Scope Boundary

Capability snapshots are intended for tools that cross persistence, filesystem, network, delegation, external-provider, messaging, or real-world-action boundaries. They are not a general-purpose permission requirement for pure helper tools.

Changing the tool audit mode does not change capability enforcement, action audit, device audit, or real-mode device behavior.
