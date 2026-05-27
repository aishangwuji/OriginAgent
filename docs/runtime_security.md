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

## Exec Profiles

Exec uses an explicit security profile:

- `secure`: default; workspace-restricted exec requires a supported sandbox and fails closed if the sandbox is unavailable.
- `local_dev`: explicit unsafe fallback for local development; unsandboxed execution requires `allow_unsafe_exec=true` and is marked in the tool result.
- `disabled`: the exec tool is not registered.

Audit mode does not change exec policy, and the capability snapshot still controls whether exec can run. `local_dev` unsafe mode does not provide sandbox isolation and should not be treated as a protected runtime boundary.

## Runtime Explain Tools

Protected runtime state remains unavailable to generic file tools. Use the runtime explain tools for redacted observability:

- `originagent_runtime_status`: runtime counts and configured audit mode.
- `originagent_tool_audit_summary`: aggregate tool audit status and policy counts.
- `originagent_cron_summary`: aggregate cron job and capability-summary counts.
- `originagent_confirmation_summary`: aggregate confirmation kind/status/risk counts.

These tools do not return raw audit events, hash-chain values, target hashes, session data, cron prompts, confirmation reasons, action payloads, device IDs, commands, paths, URLs, or messages.

## Cron and Subagent Grants

Cron and subagent delegated work defaults to low privilege. High-capability
delegated work requires an internal/admin-bound capability grant.

- Cron jobs can bind `grant_id` internally; `CronTool` cannot create or select grants.
- Subagents can bind `grant_id` internally; `SpawnTool` exposes only `task` and `label`.
- Missing, expired, or revoked grants fail closed before the delegated turn runs.
- Subagent grants are intersected with the parent-derived snapshot and cannot expand parent authority.

Grant IDs and raw grant metadata are not returned in user-facing errors.

## Runtime Profiles

Runtime profiles provide conservative defaults for `default`, `household_safe`,
`local_dev`, and `automation`. They do not bypass capability gates, protected
paths, grant enforcement, tool audit behavior, or device real-mode freeze.

## Governed Evolution

Self-evolution controls are documented in
[`governed_evolution.md`](./governed_evolution.md). The important security
boundary is that evolution can observe, propose, evaluate, trial, verify, and
record feedback, but it does not automatically activate workflows or skills.
Trial execution is isolated and read-only, and evolution control-plane writes
through `my` require `learning.evolution.allow_manual_override=true`.
