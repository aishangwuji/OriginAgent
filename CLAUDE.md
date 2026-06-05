# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OriginAgent is a lightweight, open-source AI agent framework (Python 3.11+, asyncio) with a React/TypeScript WebUI. It centers around a small agent loop that receives messages from chat channels, invokes an LLM provider, executes tools, and manages session memory.

## Development Commands

```bash
# Install with dev dependencies (prefer uv — consistent with CI)
uv sync --all-extras
pip install -e ".[dev]"       # fallback without uv

# Optional channel extras (needed for channel-specific tests)
pip install -e ".[discord,wecom,matrix]"

# Python: run full suite / single test / coverage / lint
pytest
pytest tests/test_openai_api.py::test_function -v
pytest --cov=OriginAgent --cov-report=term
ruff check OriginAgent/

# NEVER run `ruff format` — it destroys git blame. Use `ruff check` only.

# WebUI: dev server (proxies API/WS to gateway), build, test
# Build output: ../OriginAgent/web/dist (bundled into the Python wheel)
cd webui && bun install
cd webui && bun run dev      # or ORIGINAGENT_API_URL=http://... bun run dev
cd webui && bun run build
cd webui && bun run test

# TypeScript bridge (WhatsApp)
cd bridge && npm install && npm run build

# Gateway (required for WebSocket channel, API, and WebUI)
originagent gateway
```

## High-Level Architecture

### Core Data Flow

Messages flow through an async `MessageBus` (`OriginAgent/bus/queue.py`) that decouples chat channels from the agent core:

1. **Channels** (`OriginAgent/channels/`) receive messages from external platforms and publish `InboundMessage` events to the bus.
2. **`AgentLoop`** (`OriginAgent/agent/loop.py`) consumes inbound messages, builds context, and coordinates each turn.
3. **`AgentRunner`** (`OriginAgent/agent/runner.py`) handles the LLM conversation loop: sends messages to the provider, receives tool calls, executes tools, and streams responses.
4. Responses are published as `OutboundMessage` events back to the appropriate channel.

### Key Subsystems

- **Agent Loop** (`agent/loop.py`, `agent/runner.py`): Core processing engine. `AgentLoop` manages session keys, hooks, and context building. `AgentRunner` executes the multi-turn LLM conversation with tool execution.
- **LLM Providers** (`providers/`): Provider implementations (Anthropic, OpenAI-compatible, Azure, GitHub Copilot, Bedrock, etc.) built on a common base (`base.py`). Adding a new provider requires only: (1) add a `ProviderSpec` to `registry.py`, (2) add a field to `ProvidersConfig` in `config/schema.py`. `factory.py` handles instantiation and model discovery. Provider auto-detection matches model names against provider keywords.
- **Channels** (`channels/`): Platform integrations (Telegram, Discord, Slack, Feishu/飞书, Matrix, WhatsApp, QQ, WeChat/微信, DingTalk, MSTeams, Email, WebSocket). `manager.py` discovers and coordinates them. Channels are auto-discovered via `pkgutil` scan + entry-point plugins.
- **Tools** (`agent/tools/`): Agent capabilities registered in `ToolRegistry` (`registry.py`): filesystem (read/write/edit/list), shell execution with sandbox support, web search/fetch, MCP servers, cron scheduling, notebook editing, subagent spawning, image generation, and `MyTool` for self-inspection.
- **Memory** (`agent/memory.py`): Dream two-phase memory consolidation with atomic writes (temp-file + fsync + rename + dir-fsync) for crash-safe durability.
- **Session Management** (`session/manager.py`): Per-session history persistence in `history.jsonl`, context compaction, TTL-based auto-compaction.
- **Config** (`config/schema.py`, `loader.py`): Pydantic-based configuration loaded from `~/.originagent/config.json`. Supports `${ENV_VAR}` placeholder interpolation and `ORIGINAGENT__` env var overrides with `__` delimiter (e.g. `ORIGINAGENT__PROVIDERS__OPENROUTER__API_KEY`).
- **API** (`api/server.py`): OpenAI-compatible `/v1/chat/completions` and `/v1/models` endpoints served via aiohttp.
- **Gateway** (`web/`, channels `websocket.py`): Bundled HTTP server on port 18790 that serves the WebUI SPA, a WebSocket multiplex protocol, REST endpoints under `/api` and `/webui`, and token-based auth under `/auth`.
- **Templates** (`templates/`): Jinja2 Markdown files for agent system prompts (identity, heartbeat, soul, tool descriptions). Changes to templates affect agent behavior identically to Python code changes.
- **Bridge** (`bridge/`): TypeScript WhatsApp bridge using Baileys, bundled into the Python wheel.
- **WebUI** (`webui/`): Vite + React 18 + TypeScript + Tailwind 3 + shadcn/ui SPA. Talks to the gateway over WebSocket multiplex. The dev server proxies `/api`, `/webui`, `/auth`, and WebSocket traffic to the gateway. Build output goes to `OriginAgent/web/dist/`.

### Entry Points

- **CLI**: `OriginAgent/cli/commands.py` (typer app, command: `OriginAgent`)
- **Python SDK**: `OriginAgent/OriginAgent.py` (`OriginAgent` class with `RunResult`)

## Design Rules

These are from `.agent/design.md`, `.agent/security.md`, and `.agent/gotchas.md` (all in Chinese — translate as needed):

- **Keep the core lean**: `agent/loop.py` and `agent/runner.py` are critical hot-path files. New functionality goes through channels, tools, skills, or MCP — not into the agent main loop.
- **Favor repetition over premature abstraction**: Each channel and provider file is intentionally self-contained and independently readable. Do not extract shared base classes to eliminate similar code across channels.
- **Explicit over implicit**: All config must be declared as Pydantic models in `config/schema.py`. Errors must raise explicit exceptions rather than silently correcting invalid input.
- **Atomic session writes**: `agent/memory.py` uses temp-file + fsync + rename + dir-fsync. Never replace with plain `open(..., "w")`.
- **Template edits = code changes**: Agent system prompts and tool descriptions under `templates/` are Jinja2 Markdown files. Changes to them affect agent behavior identically to Python changes.
- **Heartbeat uses virtual tool calls**: The heartbeat injects a virtual `heartbeat` tool with `action: skip | run` rather than parsing free-text output. New periodic background checks follow this pattern.
- **Workspace restriction**: All filesystem tools must resolve paths through `_resolve_path` in `agent/tools/filesystem.py`, enforcing workspace boundaries.
- **SSRF protection**: All outbound HTTP from tools must pass through `validate_url_target` in `security/network.py` (blocks private IPs, link-local, cloud metadata).
- **Skills as extension point**: Built-in skills in `OriginAgent/skills/` use Markdown + YAML frontmatter. Agent capabilities that are knowledge-based (not code logic) should extend via skills, not hardcoded into the agent loop.

## Branching Strategy

Two-branch model: `main` (stable releases) and `nightly` (experimental).

| Your Change | Target Branch |
|-------------|---------------|
| Bug fix, docs, minor tweak | `main` |
| New feature, refactoring, API change | `nightly` |
| Unsure | `nightly` |

Stable features are cherry-picked from `nightly` into `main` (~weekly), not merged wholesale.

## CI

- GitHub Actions: PRs run on `ubuntu-latest` with Python 3.11 + 3.14; pushes to `main`/`nightly` add `windows-latest` and Python 3.12 + 3.13.
- Uses `uv` for dependency management (`uv sync --all-extras`).
- Lint check: `ruff check OriginAgent --select F` (fatal errors only).
- Tests: `uv run pytest tests/`.

## Code Style

- Python 3.11+, asyncio throughout.
- Line length: 100.
- Linting: `ruff` with rules E, F, I, N, W (E501 ignored). Never run `ruff format`.
- pytest with `asyncio_mode = "auto"`.
- Dependency management: prefer `uv` (consistent with CI).
- WebUI requires `bun` (not npm/yarn). Bridge requires `npm` and Node >= 20.

## Key File Locations

- Config schema: `OriginAgent/config/schema.py`
- Provider base / registry: `OriginAgent/providers/base.py` / `registry.py`
- Channel base / manager: `OriginAgent/channels/base.py` / `manager.py`
- Tool registry: `OriginAgent/agent/tools/registry.py`
- Agent loop core: `OriginAgent/agent/loop.py` / `runner.py`
- Memory / Dream: `OriginAgent/agent/memory.py`
- Session manager: `OriginAgent/session/manager.py`
- SSRF / security: `OriginAgent/security/network.py`
- Sandbox: `OriginAgent/agent/tools/sandbox.py`
- WebUI dev proxy config: `webui/vite.config.ts`
- Skills manifest: `skills-lock.json`

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **OriginAgent** (27601 symbols, 55654 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/OriginAgent/context` | Codebase overview, check index freshness |
| `gitnexus://repo/OriginAgent/clusters` | All functional areas |
| `gitnexus://repo/OriginAgent/processes` | All execution flows |
| `gitnexus://repo/OriginAgent/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
