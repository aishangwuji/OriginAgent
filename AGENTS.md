## Python / pytest 环境（Windows）

- 不要把 `py -m pytest` 失败误判成“当前环境没有可用 Python”。本仓库优先使用项目自带虚拟环境。
- 运行测试时默认使用 `.\.venv\Scripts\python.exe -m pytest ...`，不要依赖全局 `python` 或 `py`。
- 如果需要临时简化命令，可先执行 `.\.venv\Scripts\Activate.ps1`，之后再运行 `python -m pytest ...`。
- 只有在 `.\.venv\Scripts\python.exe` 本身不可用时，才把它视为真实环境阻塞；单独的全局 Python / `py.exe` 异常不是阻塞事故。

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **OriginAgent** (67920 symbols, 132461 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.
> In this workspace, GitNexus-related CLI commands are approved to run outside the sandbox when needed. Prefer escalating `npx gitnexus ...` directly instead of re-asking for the same approval.

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
