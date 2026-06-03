# Runtime Profiles

Runtime profiles are conservative presets for common operating modes. They set
defaults only; they do not bypass policy, protected paths, capability snapshots,
tool audit rules, or real-mode device freeze behavior.

## Profiles

- `default`: current schema defaults.
- `household_safe`: minimal tool audit, secure exec, unsafe exec disabled, device
  tools disabled, dry-run mode retained as the only device default.
- `local_dev`: minimal tool audit, `exec.profile=local_dev`, but
  `allow_unsafe_exec=false`; unsafe local exec must still be explicitly enabled.
- `automation`: minimal tool audit, secure exec, device tools disabled. Cron and
  subagent grants can be used when explicitly bound by internal/admin paths, but
  no high-power grant is created by the profile.

## Guarantees

- Profiles do not expose `grant_id` to `CronTool` or `SpawnTool`.
- Profiles do not create cron or subagent grants.
- Profiles do not allow raw protected runtime state reads.
- Profiles do not enable real device mode.
- Profiles do not change `CapabilitySnapshot` or `RuntimeContext`.

## Example

```json
{
  "runtime": {
    "profile": "local_dev"
  },
  "tools": {
    "exec": {
      "allowUnsafeExec": true
    }
  }
}
```

The example still requires the normal capability gate before `exec` can run.
