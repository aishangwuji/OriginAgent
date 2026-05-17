# Device Gateway

Device tools are disabled by default. In this release, OpenHome exposes only
dry-run lighting tools when explicitly configured with the fake backend:

- `openhome_device_lighting_set_power`
- `openhome_device_lighting_set_brightness`
- `openhome_device_lighting_set_color_temperature`

Explicitly not enabled: locks, security systems, cameras, gas, presence,
appliances, group actions, natural-language device discovery, and autonomous
real execution by default.

The safety chain is: tool schema, `TypedDeviceAction`,
`DeviceActionExecutor.submit_typed()`, schema validation, `ActionSafetyGate`,
`PermissionResolver`, `SafeActionExecutor`, backend, audit.

Runtime defaults are conservative: `tools.device.enabled=false`,
`lighting_enabled=false`, `mode=dry_run`, and `backend=none`. Real mode is
frozen in this release and does not register device tools.

Dry-run fake config:

```json
{
  "tools": {
    "device": {
      "enabled": true,
      "lightingEnabled": true,
      "mode": "dry_run",
      "backend": "fake"
    }
  }
}
```

Dry-run tool responses use stable user-facing messages such as
`Lighting action accepted in dry-run mode.` Dry-run does not control real
devices.

Privacy boundaries:

- Tool audit does not store raw params or result text.
- Device tool responses do not include raw backend results.
- Action audit sanitizes device scopes so raw device IDs are not written.
- Generic file and shell tools cannot modify protected runtime audit/state.

Testing matrix:

- Default config registers no device tools.
- Dry-run lighting config registers exactly three tools.
- Real mode remains frozen and registers no device tools.
- Schema failures do not enter gate, permission, confirmation, or backend.
- Backend still receives resolved raw device IDs after registry resolution.
- `scripts/run_device_gateway_config_demo.py` checks config wiring and redaction.
