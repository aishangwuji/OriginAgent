# Robot Capabilities

- Provide preview-only robot action planning proposals when the robot domain pack is explicitly enabled.
- Keep real robot execution fail-closed in Phase 4A.
- Expose simulator-style previews without registering autonomous writeback.

## P5B+ G1 Integration Placeholders

- G1 MCP/SSE endpoint wiring is reserved for P5B+ and remains disabled by default.
- Real Unitree backends and PERCEIVE-stage perception are future wiring points, not active Phase 5A behavior.
- Do not add manifest-only G1 fields here; the robot domain pack must keep loading with the current schema.
