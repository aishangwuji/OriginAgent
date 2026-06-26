---
name: bug-hunt
description: "Use when the user wants to hunt bugs, find vulnerabilities, investigate security issues, audit code for defects, or search for exploitable weaknesses. Triggers: \"Find bugs in X\", \"Audit this for vulnerabilities\", \"Security holes?\", \"潜在漏洞\", \"找bug\", \"安全审计\". Skip for simple syntax questions or general code review."
---

# Bug Hunt — Dual-Agent Cross-Verified Analysis

## Overview

Context pollution from large code dumps causes hallucinations during bug hunts. This skill isolates analysis into **two independent sub-agents**, each using different GitNexus lenses. They report only key findings — never raw context. Cross-verification eliminates false positives: if both agree, it's likely real; if they disagree, a third agent tiebreaks.

**Core principle: Sub-agents extract and report. Main conversation stays clean.**

## REQUIRED BACKGROUND

- `gitnexus-debugging` — GitNexus tool workflow
- `gitnexus-impact-analysis` — blast-radius assessment
- `superpowers:dispatching-parallel-agents` — dual-agent coordination

## Core Workflow

```
1. CLARIFY target (symbol, module, endpoint, flow) and bug type
2. DISPATCH 2 sub-agents in parallel, each with a DIFFERENT lens
3. Each agent uses GitNexus → reports ONLY key findings (max 10)
4. CROSS-VERIFY: merge findings, flag agreements/discrepancies
5. TIEBREAK disputed findings with a 3rd verification agent
6. PRESENT confirmed findings to user
```

## Phase 1: Clarify Target

Establish **what** (file/function/module), **what kind** (injection/logic/race/auth), and **scope**. If vague ("find bugs in auth"), run `gitnexus_query` first to identify relevant symbols.

## Phase 2: Dual-Agent Dispatch (CRITICAL)

**Always 2 agents. Never 1.** Different lenses, different GitNexus entry points. Agents must not see each other's findings.

### Prompt Template

Build each sub-agent's prompt from this structure, swapping in the target and lens:

```
You are hunting [BUG_TYPE] in [TARGET].
Lens: [CHOSEN_LENS — unique per agent]

## GitNexus Steps (execute in order)
1. gitnexus_context({name: "[SYMBOL]"})
2. gitnexus_impact({target: "[SYMBOL]", direction: "upstream"})
3. [Lens-specific steps — see Lens Catalog]
4. Read source files to confirm

## Output Rules
- ONLY key findings. No file dumps. No raw context.
- Each finding: file:line, severity (CRITICAL/HIGH/MEDIUM/LOW), one-line description
- "No findings" if clean — never fabricate
- Max 10 findings
- Tag each as "confirmed" (read code) or "suspected" (inferred)

## Anti-Hallucination
- Never guess code you haven't read
- If GitNexus returns empty, report "no data" — don't invent
- Exact file:line citation for every finding
```

### Lens Catalog (pick 2 different ones)

| # | Lens | GitNexus Entry | Key Question |
|---|------|---------------|-------------|
| 1 | Data Flow | `context` → ACCESSES edges | Where does untrusted data enter and flow? |
| 2 | Control Flow | `context` → CALLS edges | What paths reach this code? Guards missing? |
| 3 | Caller Audit | `impact({direction: "upstream"})` | Are all callers safe? Inputs validated? |
| 4 | Callee Audit | `impact({direction: "downstream"})` | Are dependency outputs trusted blindly? |
| 5 | Process Trace | READ process resource | Full flow — where are gaps/assumptions? |
| 6 | Security Taint | `explain` | Known taint: SQLi, XSS, SSRF, cmd-injection |
| 7 | PDG Analysis | `pdg_query({mode: "controls"})` | What conditions gate this? Missing guards? |
| 8 | Change Impact | `detect_changes` | Recent edits introducing bugs? |
| 9 | Route Surface | `route_map` + `api_impact` | Auth middleware gaps? Exposed endpoints? |
| 10 | Error Paths | `context` → throw/catch sites | Swallowed errors? Silent fallbacks? |

### Minimal Example Pair

**Agent A** (Lens 3 — Caller Audit):
```
Hunt injection bugs in validate_url_target (OriginAgent/security/network.py).
Lens: Caller Audit. gitnexus_context + upstream impact → for each caller,
verify URL is sanitized before reaching validation. Read files to confirm.
Report only: bypass vectors, missing sanitization at call sites.
```

**Agent B** (Lens 6 — Security Taint):
```
Hunt SSRF bypasses in validate_url_target (OriginAgent/security/network.py).
Lens: Security Taint. gitnexus_explain → find known taint paths.
gitnexus_pdg_query({mode:"controls"}) → examine guards. Read source for
DNS rebinding / IPv6 bypass / redirect chain risks.
Report only: confirmed bypass vectors, missing enforcement.
```

## Phase 3: Cross-Verify

Build a matrix from both reports:

```
| Finding              | Agent A  | Agent B  | Verdict     |
|----------------------|----------|----------|-------------|
| XSS in /api/search   | ✓ HIGH   | ✓ HIGH   | CONFIRMED   |
| SQLi in login        | ✓ MEDIUM | ✗        | → TIEBREAK  |
```

- **Both agree → CONFIRMED** (report immediately)
- **Disagreement → TIEBREAK** (dispatch 3rd verification agent)
- **Both "no findings" → LIKELY CLEAN** (note scope limits)
- **Severity differs → use higher** (err on caution side)

## Phase 4: Tiebreak Disputed Findings

Dispatch a 3rd agent focused solely on the disputed finding:
```
"Agent A reported [FINDING] at [LOCATION]. Agent B checked [SCOPE] and didn't find it.
Verify: real bug or false positive? Read the code and confirm or refute with evidence."
```

The 3rd agent's verdict is final.

## Phase 5: Present Results

```markdown
## Bug Hunt: [TARGET]

### ✅ Confirmed (both agents agree)
| # | Severity | Finding | Location | Type |
|---|----------|---------|----------|------|

### ⚠️ Disputed (tiebreak needed)
| # | Severity | Finding | Agent A | Agent B |

### ✅ Likely Clean
(areas both checked, nothing found)

### Coverage: N symbols | N call sites | N taint paths
```

## GitNexus Tools Quick Reference

| Tool | Use for |
|------|---------|
| `gitnexus_query` | Find symbols/flows from description |
| `gitnexus_context` | 360° view: callers, callees, processes |
| `gitnexus_impact` | Blast radius: upstream/downstream dependents |
| `gitnexus_explain` | Known taint paths (SQLi, XSS, SSRF…) |
| `gitnexus_pdg_query` | Statement-level control/data dependence |
| `gitnexus_detect_changes` | Risk assessment of uncommitted changes |
| `gitnexus_api_impact` | Route consumers, middleware, mismatch risk |
| `gitnexus_trace` | Shortest call path between two symbols |
| `gitnexus_cypher` | Custom graph queries |

> If "Index is stale" → `npx gitnexus analyze`

## Non-Negotiable Rules

1. **2 agents per hunt.** Solo = no cross-check.
2. **Different lenses.** Same lens = waste.
3. **Findings only.** No raw code, no context dumps.
4. **Cite evidence.** file:line or tool result for every finding.
5. **Tag confidence.** "confirmed" vs "suspected".
6. **Never fabricate.** "No findings" is a valid result.

## Red Flags — Discard and Re-Dispatch

| Symptom | Problem |
|---------|---------|
| 20+ findings from one agent | Unfiltered noise |
| Identical findings, same order | Possible collusion |
| No file:line citations | Hallucination risk |
| All "might/could", zero confirmed | Agent didn't actually read code |
| Zero overlap between reports | Agents analyzed different things |
