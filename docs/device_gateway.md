# Device Gateway

Device tools are disabled by default. When enabled for v1, OpenHome exposes only
three single-device lighting actions:

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
`lighting_enabled=false`, `mode=dry_run`, and `backend=none`. Real mode requires
explicit backend configuration and registry-backed device references; it does
not expose raw device IDs in the tool schema.

Privacy boundaries:

- Tool audit does not store raw params or result text.
- Device tool responses do not include raw backend results.
- Action audit sanitizes device scopes so raw device IDs are not written.
- Generic file and shell tools cannot modify protected runtime audit/state.

Testing matrix:

- Default config registers no device tools.
- Dry-run lighting config registers exactly three tools.
- Real mode uses `device_ref` schema.
- Schema failures do not enter gate, permission, confirmation, or backend.
- Backend still receives resolved raw device IDs after registry resolution.

