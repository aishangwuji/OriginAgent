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

## Python / pytest Environment Notes

- On this Windows workspace, do not assume global `python` or `py -m pytest` works. They may be unavailable even when the repo is healthy.
- Prefer the repo venv for all validation commands: `.\.venv\Scripts\python.exe -m pytest ...`
- If you want a shorter shell workflow, activate the venv first: `.\.venv\Scripts\Activate.ps1`
- Treat missing global Python launcher bindings as a shell setup issue, not as proof that tests cannot run in this repo.

## High-Level Architecture

### Core Data Flow

Messages flow through an async `MessageBus` (`OriginAgent/bus/queue.py`) that decouples chat channels from the agent core:

1. **Channels** (`OriginAgent/channels/`) receive messages from external platforms and publish `InboundMessage` events to the bus.
2. **`AgentLoop`** (`OriginAgent/agent/loop.py`) acts as a **Facade** — it delegates inbound messages to `AgentRuntime` for processing, and delegates infrastructure management (MCP, BDI, provider) to `AgentHost`.
3. **`AgentRuntime`** (`OriginAgent/agent/agent_runtime.py`) is the **stateless message router**: it builds LLM context, drives the turn pipeline via `TurnOrchestrator`, manages meta-cognition and cognitive scheduling, and dispatches post-turn side effects.
4. **`AgentRunner`** (`OriginAgent/agent/runner.py`) handles the LLM conversation loop: sends messages to the provider, receives tool calls, executes tools, and streams responses.
5. Responses are published as `OutboundMessage` events back to the appropriate channel.

### Agent Core Decomposition (Internal Architecture)

The agent loop has been decomposed into three focused internal components to eliminate the original "God Object":

```
AgentLoop (Facade ~2434 lines)
├── AgentRuntime (~1187 lines)    ← Stateless message router
│   ├── Turn pipeline (build messages, run agent loop, assemble outbound)
│   ├── Meta-cognition (triggers, reflection, fast-path)
│   ├── Cognitive scheduling (candidates, events, working memory)
│   ├── Post-turn effects (background review, curator, nearline memory)
│   ├── Continuity & checkpoint
│   └── Message dispatch & persistence
├── AgentHost (~543 lines)        ← Infrastructure lifecycle
│   ├── MCP connection lifecycle
│   ├── Provider management (snapshot, preset switching)
│   ├── BDI deliberation engine
│   ├── Transcription provider
│   └── Background task scheduling
└── SessionStateHolder (~96 lines) ← Session-scoped state container
    └── Per-session _last_* scratchpad (thread-safe, TTL-based expiry)
```

### Key Subsystems

- **Agent Loop Facade** (`agent/loop.py`): Public API (`__init__`, `from_config`, `from_options`, `run()`, `stop()`, `process_direct()`) — delegates all logic to AgentHost + AgentRuntime. Does NOT contain business logic.
- **Agent Runtime** (`agent/agent_runtime.py`): Stateless message processor. Receives all context as explicit parameters, never stores mutable state on `self`. All `self.xxx` references resolve to `self._deps.xxx` (injected `RuntimeDependencies`).
- **Agent Host** (`agent/agent_host.py`): Infrastructure lifecycle manager. Owns MCP connections, BDI engine, provider swapping, and background task draining.
- **Session State** (`agent/session_state.py`): Thread-safe, session-keyed container for cross-turn scratchpad state. Uses double-checked locking. Drops stale entries after TTL.
- **Agent Runner** (`agent/runner.py`): Executes the multi-turn LLM conversation with tool execution.
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

- **AgentLoop is a Facade, not a God Object**: `loop.py` delegates to `AgentRuntime` (message routing), `AgentHost` (infrastructure lifecycle), and `SessionStateHolder` (session state). New message-routing logic goes into `agent_runtime.py`. New infrastructure goes into `agent_host.py`. New methods on AgentLoop must be <5 line compat shells that delegate.
- **AgentRuntime is stateless**: All context (session_key, session, messages) arrives as explicit parameters. Never store mutable state on `self` — use `session_state.py` if cross-turn state is needed. `self._deps.xxx` is the only allowed `self` member.
- **Favor repetition over premature abstraction**: Each channel and provider file is intentionally self-contained and independently readable. Do not extract shared base classes to eliminate similar code across channels.
- **Explicit over implicit**: All config must be declared as Pydantic models in `config/schema.py`. Errors must raise explicit exceptions rather than silently correcting invalid input.
- **Atomic session writes**: `agent/memory.py` uses temp-file + fsync + rename + dir-fsync. Never replace with plain `open(..., "w")`.
- **Template edits = code changes**: Agent system prompts and tool descriptions under `templates/` are Jinja2 Markdown files. Changes to them affect agent behavior identically to Python changes.
- **Heartbeat uses virtual tool calls**: The heartbeat injects a virtual `heartbeat` tool with `action: skip | run` rather than parsing free-text output. New periodic background checks follow this pattern.
- **Workspace restriction**: All filesystem tools must resolve paths through `_resolve_path` in `agent/tools/filesystem.py`, enforcing workspace boundaries.
- **SSRF protection**: All outbound HTTP from tools must pass through `validate_url_target` in `security/network.py` (blocks private IPs, link-local, cloud metadata).
- **Skills as extension point**: Built-in skills in `OriginAgent/skills/` use Markdown + YAML frontmatter. Agent capabilities that are knowledge-based (not code logic) should extend via skills, not hardcoded into the agent loop.
- **Compatibility first**: When moving methods from AgentLoop to AgentRuntime, always keep a compat shell with `hasattr(self, "_runtime")` fallback. Tests that use `AgentLoop.__new__` bypass `__init__` and must still work.

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

### Agent Core (Decomposed)

| File | Lines | Responsibility |
|------|-------|----------------|
| `OriginAgent/agent/loop.py` | ~2434 | **Facade** — public API, compat shells, factory methods. Delegates to AgentRuntime + AgentHost. |
| `OriginAgent/agent/agent_runtime.py` | ~1187 | **Stateless message router** — turn pipeline, meta-cognition, cognitive scheduling, continuity, outbound assembly. |
| `OriginAgent/agent/agent_host.py` | ~543 | **Infrastructure lifecycle** — MCP connections, BDI engine, provider management, transcription, background tasks. |
| `OriginAgent/agent/session_state.py` | ~96 | **Session-scoped state** — thread-safe container for cross-turn `_last_*` scratchpad, TTL-based expiry. |
| `OriginAgent/agent/runner.py` | — | LLM conversation loop (provider calls, tool execution, streaming). |

### Other Locations

- Config schema: `OriginAgent/config/schema.py`
- Provider base / registry: `OriginAgent/providers/base.py` / `registry.py`
- Channel base / manager: `OriginAgent/channels/base.py` / `manager.py`
- Tool registry: `OriginAgent/agent/tools/registry.py`
- Turn orchestration: `OriginAgent/agent/turn_orchestrator.py`
- Turn pipeline: `OriginAgent/agent/agent_turn_pipeline.py`
- Turn persistence: `OriginAgent/agent/agent_turn_persist.py`
- System turn handler: `OriginAgent/agent/system_turn_handler.py`
- Message dispatcher: `OriginAgent/agent/message_dispatcher.py`
- Agent cognitive runtime: `OriginAgent/agent/agent_cognitive_runtime.py`
- Memory / Dream: `OriginAgent/agent/memory.py`
- Session manager: `OriginAgent/session/manager.py`
- SSRF / security: `OriginAgent/security/network.py`
- Sandbox: `OriginAgent/agent/tools/sandbox.py`
- WebUI dev proxy config: `webui/vite.config.ts`
- Skills manifest: `skills-lock.json`

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **OriginAgent** (29177 symbols, 59846 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

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
