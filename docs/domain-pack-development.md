# Domain Pack Development Guide

## What is a Domain Pack?

A Domain Pack is a self-contained, pluggable module that extends OriginAgent with
domain-specific capabilities. Each pack has:

- A `domain_pack.yaml` manifest describing its identity, capabilities, tools, and runtime
- A `CAPABILITIES.md` that gets injected into the agent's system prompt when the pack is active
- Optional `tools/`, `runtime/`, `skills/`, `workflows/`, `evals/`, and `tests/` directories

Domain packs are either **builtin** (shipped with OriginAgent in `OriginAgent/domain_packs/`)
or **workspace** (installed by users into `<workspace>/domain_packs/`).

## Quick Start: 5-Minute Scaffold

### Prerequisites

- OriginAgent installed (`pip install -e .`)
- Working directory is your project or workspace root

### Step 1: Scaffold a New Pack

```bash
originagent domain init my_pack --name "My Pack" --description "My domain extension"
```

This creates:

```
domain_packs/my_pack/
├── domain_pack.yaml         # Manifest — edit this first
├── CAPABILITIES.md          # System prompt capabilities
├── __init__.py
├── tools/
│   ├── __init__.py
│   └── example_tool.py      # Tool implementation
├── runtime/
│   ├── __init__.py
│   └── contribution.py      # Runtime injection
├── skills/
│   └── example-skill/
│       └── SKILL.md
├── workflows/
│   └── example-workflow/
│       └── workflow.yaml
├── evals/
│   └── example_eval.py
└── tests/
    ├── __init__.py
    ├── test_domain_pack.py
    └── test_tools.py
```

> **Tip:** Use `--no-tools`, `--no-runtime`, `--no-skills`, `--no-workflows`,
> `--no-evals`, `--no-tests` to exclude components you don't need.

### Step 2: Edit the Manifest

Open `domain_packs/my_pack/domain_pack.yaml` and fill in:

```yaml
id: my_pack              # Unique ID — must match ^[a-z0-9_-]+$
name: My Pack            # Human-readable name
version: 0.1.0           # Semver
enabled: true
description: "My domain extension"

capabilities:
  - my_special_capability

tools:
  - id: my_pack_my_action
    module: tools.example_tool
    class: MyPackTool
    permissions:
      - read_files
    audit: minimal

runtime:
  module: runtime.contribution
  factory: build_runtime_contribution
```

### Step 3: Implement Your Tool

Edit `tools/example_tool.py`:

```python
from typing import Any
from OriginAgent.agent.tools.base import Tool, tool_parameters
from OriginAgent.agent.tools.schema import StringSchema, tool_parameters_schema

@tool_parameters(tool_parameters_schema(...))
class MyPackTool(Tool):
    name = "my_pack_my_action"

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls()

    @property
    def description(self) -> str:
        return "Description shown to the LLM."

    async def execute(self, ...) -> dict[str, Any]:
        return {"status": "success"}
```

### Step 4: Validate

```bash
originagent domain validate domain_packs/my_pack
```

### Step 5: Install

```bash
originagent domain install domain_packs/my_pack
```

This copies the pack into your workspace's `domain_packs/` directory and registers it.

### Step 6: Activate

```bash
originagent domain activate my_pack
```

The pack is now active — its capabilities will appear in the agent's system prompt
and its tools will be available.

> **To deactivate:** `originagent domain deactivate my_pack`
> **To uninstall:** `originagent domain uninstall my_pack`

## Registration Flow Diagram

```
┌──────────────────────────────────────────────────────────┐
│ 1. originagent domain init my_pack                        │
│    → Creates domain_packs/my_pack/ from template           │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│ 2. Edit domain_pack.yaml + CAPABILITIES.md                │
│    → Define id, name, capabilities, tools, runtime         │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│ 3. Implement tools/ + runtime/ + skills/                  │
│    → Write actual Python code and markdown                 │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│ 4. originagent domain validate .                          │
│    → DomainPackValidator checks manifest + files           │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│ 5. originagent domain install <path>                      │
│    → Copies into workspace/domain_packs/<id>/              │
│    → GovernanceService validates with strict mode          │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│ 6. originagent domain activate <id>                       │
│    → Adds to config → pack becomes active                  │
│    → Tools registered, skills injected into prompt         │
└──────────────────────────────────────────────────────────┘
```

## Manifest Reference

### Top-Level Fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `id` | yes | string | Unique identifier, `^[a-z0-9_-]+$` |
| `name` | yes | string | Human-readable name |
| `version` | no | string | Semver, default `0.1.0` |
| `enabled` | no | bool | Default `true` |
| `description` | no | string | One-line description |
| `capabilities` | no | string[] | Capability tags for system prompt |
| `skills` | no | string[] | Skill names, correspond to `skills/<name>/SKILL.md` |
| `workflows` | no | string[] | Workflow names, correspond to `workflows/<name>/workflow.yaml` |
| `tools` | no | ToolDeclaration[] | Tool definitions |
| `runtime` | no | RuntimeDeclaration | Runtime contribution config |
| `activation.triggers` | no | string[] | Keyword triggers for future auto-activation |
| `requires.bins` | no | string[] | Required CLI binaries |
| `requires.env` | no | string[] | Required environment variables |
| `evals` | no | EvalDeclaration[] | Self-evaluation checks |

### ToolDeclaration

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `id` | yes | string | `^[a-z0-9_]{1,64}$` — must match the Tool's `.name` |
| `module` | yes | string | Dotted path under `tools/`, e.g. `tools.device` |
| `class` | yes | string | Python class name in that module |
| `permissions` | yes | string[] | Permission grants — must be explicit even if empty |
| `audit` | no | string | `"minimal"` or `"security"` |

### RuntimeDeclaration

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `module` | yes | string | Dotted path under `runtime/`, e.g. `runtime.contribution` |
| `factory` | no | string | Function name, default `build_runtime_contribution` |

## Directory Structure Reference

```
domain_packs/<pack_id>/
├── domain_pack.yaml          # REQUIRED: Manifest
├── CAPABILITIES.md           # REQUIRED: Capabilities text injected into system prompt
├── __init__.py               # Python package marker
├── tools/                    # Tool implementations
│   ├── __init__.py
│   └── <name>.py
├── runtime/                  # Runtime hooks
│   ├── __init__.py
│   └── contribution.py       # build_runtime_contribution(context) → DomainRuntimeContribution
├── skills/                   # Agent skills (markdown knowledge)
│   └── <name>/
│       └── SKILL.md
├── workflows/                # Workflow definitions
│   └── <name>/
│       └── workflow.yaml
├── evals/                    # Self-evaluation scripts
│   └── <name>.py
└── tests/                    # Pack tests
    ├── __init__.py
    ├── test_domain_pack.py   # Structure checks
    └── test_tools.py         # Tool integration tests
```

## Best Practices

1. **Start small** — Use `originagent domain init` with only the components you need
2. **Validate early** — Run `originagent domain validate .` while developing
3. **Test in isolation** — Pack tests use `DomainPackManager` pointing at a temp workspace
4. **CAPABILITIES.md is your contract** — This text is injected into the agent's prompt. Be precise about capabilities and limitations
5. **Tools need permissions** — Read-only tools can have empty `permissions: []`; write tools need appropriate grants
6. **Runtime is for wiring** — Use `build_runtime_contribution` to inject objects (executors, registries) into your tools, don't create them inside tools themselves

## Debugging

```bash
# Validate a pack (works for both builtin and workspace)
originagent domain validate domain_packs/my_pack

# List all packs and their status
originagent domain list

# Get detailed info on a specific pack
originagent domain info my_pack

# Check runtime tool registration status
# Look for "Domain tool ... registered" or "skipped" in logs
```

## Reference: Builtin smart_home Pack

The `OriginAgent/domain_packs/smart_home/` directory is the reference builtin
implementation. Key patterns to follow:

- **Manifest** uses `id: smart_home` with explicit capability tags
- **Tools** extend a shared base class (`_LightingToolBase`) for common behavior
- **Runtime** builds a `DeviceActionExecutor` via `build_runtime_contribution`
- **Safety gates** are contributed as part of `DomainRuntimeContribution.safety_gates`
- **Permissions** use `device:*` prefixed permission strings

See `OriginAgent/domain_packs/smart_home/` for the complete reference.

## Contributing a New Builtin Pack

1. Create your pack using `originagent domain init` in the OriginAgent repo root
2. Move it to `OriginAgent/domain_packs/<your_pack>/`
3. Add tests in `tests/agent/` following the existing patterns
4. Ensure the architecture boundary test still passes (core must not import domain packs)
5. Submit a PR targeting the `nightly` branch
