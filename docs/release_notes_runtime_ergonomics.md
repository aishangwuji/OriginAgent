# Runtime Ergonomics Release Notes

This release closes the runtime/tools ergonomics track. The goal is a maintainable
agent runtime with clear boundaries, lower audit noise, and explicit authorization
for delegated high-capability work.

## Included

- Tool audit modes: `off`, `minimal`, and `security`.
- Capability guardrails for boundary-crossing tools.
- Redacted runtime explain tools for protected runtime state.
- Exec profiles: `secure`, `local_dev`, and `disabled`.
- Cron capability grants.
- Subagent capability grants bounded by parent-derived capability snapshots.
- Runtime profile presets: `default`, `household_safe`, `local_dev`, `automation`.
- Dry-run lighting device gateway UX and config demo.

## Tags

- `after-runtime-ergonomics-audit-capability`
- `after-runtime-status-explain-tools`
- `after-exec-security-profiles`
- `after-cron-capability-grants`
- `after-subagent-capability-grants`
- `after-delegated-capability-grants`
- `after-runtime-profile-presets`
- `after-device-gateway-ux-stabilization`
- `runtime-ergonomics-rc1`

## Known Limits

- Real device backend remains frozen.
- Grant management UI/admin APIs are not included.
- Grant summaries, path grants, and channel grants are not included.
- Device registry productization and household identity are not included.
