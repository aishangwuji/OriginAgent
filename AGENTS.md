<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **OriginAgent** (29508 symbols, 58927 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

> Operational note: GitNexus is usable in this repo, but duplicate indexed `OriginAgent` entries can make repo-agnostic commands fail with `Multiple repositories indexed`. In this workspace, prefer `npx gitnexus status` first, and use CLI commands with `--repo OriginAgent` such as `npx gitnexus impact --repo OriginAgent ...` and `npx gitnexus detect-changes --repo OriginAgent`. If the current Codex session does not expose GitNexus MCP tools, fall back to the CLI instead of treating GitNexus as unavailable.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. In this repo, if MCP is unavailable or repo resolution is ambiguous, run `npx gitnexus detect-changes --repo OriginAgent`.
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
