# Subagent Phase 1 Hardening Plan

Date: 2026-05-28
Status: Proposed
Scope: OriginAgent subagent phase 1 only

## 1. Objective

Phase 1 combines two changes into one bounded refactor:

1. Security hardening for subagents.
2. Runtime/tooling assembly unification between the main agent and subagents.

The goal is not to make subagents fully isolated workers yet. The goal is to
turn the current subagent path from a special-case background runner into a
standardized, least-privilege delegated runtime that is easier to reason about,
extend, and audit.

## 2. Why This Phase Exists

Current subagent behavior is functional but structurally fragile:

- Subagents build a hand-rolled tool registry in `agent/subagent.py`.
- Tool registration logic has already drifted from the main runtime.
- Web access is controlled by global config, not by a subagent-specific
  capability/profile boundary.
- Subagent tool activity is tracked in memory but not persisted as a first-class
  audit stream.
- Future enhancements would require changing both the main tool assembly path
  and the subagent-specific assembly path.

This phase fixes those issues without introducing containers, separate
credential pools, or per-subagent process isolation.

## 3. Non-Goals

This phase does not include:

- Separate provider instances or credential pools for subagents.
- Container, VM, or per-process sandboxing for subagents.
- Temporary overlay workspaces or copy-on-write filesystems.
- New user-facing grant management APIs or UI.
- Multi-stage trust scoring for subagent results.
- Rich orchestration features such as fan-out planning or structured task DAGs.

Those items remain valid future directions, but they are explicitly out of
scope for phase 1.

## 4. Design Principles

### 4.1 Least privilege by default

Subagents should default to a read-oriented profile. Write, exec, messaging,
cron, spawn, and network access must not be implicitly inherited from the main
agent runtime.

### 4.2 One assembly path

Main-agent and subagent tool registration should share the same assembly logic.
Subagents may apply stricter filtering, but they should not maintain a separate
hand-written registry definition.

### 4.3 Explicit policy, not accidental capability

Whether a subagent can use a tool should be determined by an explicit subagent
policy/profile layer, not by whichever global tools happen to be enabled.

### 4.4 Auditable delegation

Delegated work must leave durable traces that answer:

- Which parent session spawned the subagent.
- Which subagent ran.
- Which tools it attempted to call.
- Which calls succeeded, failed, or were denied by policy.

## 5. Target Outcome

After phase 1:

- Subagents use a dedicated policy/profile object.
- Subagents default to read-only, no-exec, no-network behavior.
- Web tools are gated by subagent policy, not only by global web config.
- Subagents reuse the standard tool assembly path.
- Tool filtering happens through an explicit allowlist/profile layer.
- Subagent tool execution is persistently auditable.
- Adding a new core tool no longer requires remembering to duplicate logic in
  `agent/subagent.py`.

## 6. Functional Requirements

### 6.1 Subagent policy model

Introduce a first-class subagent policy object, tentatively named
`SubagentPolicy`, `DelegatedRuntimeProfile`, or equivalent.

Minimum phase 1 responsibilities:

- Define default delegated behavior.
- Express allowed tool names and/or tool classes.
- Express coarse capability flags needed by assembly.
- Be constructed centrally, not ad hoc inside `_run_subagent`.

Recommended default phase 1 policy:

- allow file read tools
- allow directory/search tools
- deny write tools
- deny exec
- deny web tools
- deny message
- deny cron
- deny spawn
- deny domain tools unless explicitly approved by policy

### 6.2 Default delegated profile

The default parent-to-subagent downgrade should be strict.

Phase 1 default:

- `can_read_files = True` only if parent had file read
- `can_write_files = False`
- `can_exec = False`
- `can_create_cron = False`
- `can_spawn = False`
- `can_send_cross_target = False`
- subagent web access disabled by default
- device and high-impact domain permissions disabled by default

Important: web access must become part of delegated policy enforcement even if
it is represented outside `CapabilitySnapshot` at first.

### 6.3 Unified tool assembly

Subagents should stop manually registering tools in `agent/subagent.py`.

Instead:

- reuse `agent_tool_setup.py` as the single assembly source
- add a delegated-runtime assembly mode or filtered registration path
- apply subagent filtering after or during standard registration
- ensure the resulting registry does not expose forbidden tools even if they are
  globally enabled

The unification should preserve the ability to omit tools that are inherently
unsafe for subagents.

### 6.4 Tool filtering model

Phase 1 should support explicit filtering by tool name.

A minimal mechanism is acceptable:

- build a standard registry
- remove or skip tools not allowed by the subagent policy

A better mechanism is preferred if it stays small:

- shared tool assembly accepts a predicate or profile
- each tool is registered only when the delegated profile allows it

The filtering outcome must be deterministic and testable.

### 6.5 Persistent audit trail

Subagent tool calls should be recorded using the same durability expectations as
main-agent tool audit events.

Phase 1 minimum fields:

- timestamp
- subagent task id
- parent session key
- origin channel/chat id when available
- tool name
- outcome
- denied-by-policy or executed
- redacted arguments preview
- redacted result preview or error summary

Preferred implementation direction:

- reuse the existing tool audit sink path if feasible
- otherwise introduce a dedicated durable subagent audit log with compatible
  semantics

### 6.6 Compatibility constraints

Phase 1 must preserve:

- current spawn tool external schema: `task`, optional `label`
- current subagent result injection contract: `injected_event="subagent_result"`
- current session routing behavior using `session_key_override`
- current `/stop` cancellation semantics

## 7. Proposed Architecture Changes

### 7.1 Add a delegated runtime profile layer

Create a small policy layer near runtime/capability assembly rather than inside
`SubagentManager._run_subagent`.

Suggested responsibilities:

- derive delegated defaults from parent snapshot
- merge delegated policy overrides
- expose:
  - effective capability snapshot
  - allowed tool names
  - web enabled flag
  - audit context metadata

This can initially live under:

- `OriginAgent/agent/subagent_policy.py`, or
- `OriginAgent/security/delegation.py`

### 7.2 Extend tool assembly for delegated mode

Refactor `register_default_tools()` or add a sibling helper so the same assembly
code can be used for:

- main runtime
- delegated runtime
- future cron/runtime variants if needed

Likely changes:

- add optional filtering hook or profile object
- allow toggling specific tool families off
- separate "tool available globally" from "tool allowed in this runtime"

### 7.3 Remove hand-written subagent tool registration

`SubagentManager._run_subagent()` should no longer manually decide:

- which file tools to register
- whether to register web tools
- whether to register exec

It should instead:

- build a delegated runtime profile
- request a filtered registry from shared assembly
- run the subagent with that registry

### 7.4 Add web gating to delegated policy

Web access currently depends on `self.web_config.enable`.

Phase 1 should add a delegated gate so that:

- global web enabled + delegated web disabled = no web tools
- global web enabled + delegated web enabled = web tools allowed
- global web disabled = no web tools regardless of delegated policy

This can be modeled as:

- a new capability field in `CapabilitySnapshot`, or
- a subagent policy field if changing the snapshot schema now is too broad

Recommendation for phase 1: use the smallest change that gives explicit
delegated web denial, then consider snapshot schema expansion in a later pass.

### 7.5 Durable audit integration

Extend subagent execution so tool activity is persisted through the existing
audit path where practical.

Possible implementation options:

1. Reuse `ToolRegistry` audit sink with subagent-specific audit context.
2. Add a `SubagentAuditRecorder` fed by `_SubagentHook`.

Recommendation:

- prefer reusing registry audit plumbing if it can distinguish subagent context
  cleanly
- avoid building a second audit format unless necessary

## 8. Workstreams

### Workstream A: Policy and capability boundary

Deliverables:

- delegated runtime policy object
- default read-only delegated profile
- explicit web denial for subagents by default
- tests for effective policy derivation

### Workstream B: Shared tool assembly

Deliverables:

- refactored shared tool registration path
- delegated mode or filtering support
- removal of hand-written tool registration from `agent/subagent.py`
- tests proving subagent registry comes from shared assembly

### Workstream C: Persistent audit

Deliverables:

- durable subagent tool audit records
- task/session correlation fields
- tests for executed and denied tool attempts

### Workstream D: Regression protection

Deliverables:

- coverage for default denied tools
- coverage for allowed read-only tools
- coverage for spawn compatibility and result injection
- coverage for `/stop` and session cancellation behavior

## 9. File-Level Impact

Expected primary edit surface:

- `OriginAgent/agent/subagent.py`
- `OriginAgent/agent/agent_tool_setup.py`
- `OriginAgent/security/capabilities.py`
- new delegated policy module
- possibly `OriginAgent/agent/tools/registry.py` or audit-related helpers
- tests under:
  - `tests/agent/tools/`
  - `tests/tools/`
  - `tests/agent/`

Expected secondary review surface:

- `OriginAgent/agent/agent_runtime_context.py`
- `OriginAgent/docs/runtime_security.md`
- `OriginAgent/docs/runtime_profiles.md`
- any runtime status/introspection tools that expose subagent state

## 10. Risks

### 10.1 Breaking background tasks that relied on web search

Some existing prompts may implicitly expect subagents to do research.
Phase 1 intentionally changes that default. This is acceptable, but should be
called out in release notes and tests.

### 10.2 Over-coupling delegated policy to current tool names

If allowlists are purely string-based, future tool renames become brittle.
Phase 1 can still start with names, but should centralize the mapping in one
place.

### 10.3 Shared assembly complexity

If the shared registration function becomes too branch-heavy, the unification
may become harder to maintain than the duplication it replaces.

Mitigation:

- keep the delegated filter model narrow
- avoid turning `register_default_tools()` into a policy engine

### 10.4 Audit noise or sensitive data leakage

Persisting more subagent activity must not store raw secrets, full command
payloads, or high-risk user data without existing redaction rules.

Mitigation:

- reuse existing audit redaction behavior where possible
- log previews, not full raw payloads

## 11. Implementation Sequence

### Step 1

Introduce delegated policy/profile primitives and tests without changing
subagent runtime behavior yet.

### Step 2

Refactor shared tool assembly to accept delegated filtering/profile input.

### Step 3

Switch `SubagentManager` to shared assembly and remove manual registration.

### Step 4

Enable default delegated denials for web/exec/write/message/cron/spawn.

### Step 5

Persist subagent tool audit records and add regression tests.

### Step 6

Update docs and release notes to reflect the new delegated runtime boundary.

## 12. Testing Plan

Phase 1 requires new or updated tests for the following:

### Policy derivation

- subagent default profile is read-only
- parent write/exec/web authority does not automatically flow through
- explicit delegated allowlist enables only expected tools

### Tool assembly

- subagent registry uses shared registration path
- globally enabled web tools are absent in delegated default mode
- write/edit/exec/message/cron/spawn tools are absent by default
- read/list/glob/grep remain available when parent allows file read

### Audit

- allowed tool execution produces durable audit records
- denied tool attempts produce durable denied records
- audit entries include subagent id and parent session correlation

### Runtime behavior

- spawn tool schema remains unchanged
- result injection still uses `subagent_result`
- `/stop` still cancels running subagents
- session-bound waiting/injection behavior still works

### Regression

- main agent tool registry remains unchanged in normal runtime
- cron behavior is not unintentionally modified by delegated policy changes

## 13. Acceptance Criteria

Phase 1 is complete when all of the following are true:

1. Subagents no longer hand-build their tool registry in `agent/subagent.py`.
2. Subagents default to a read-only, no-exec, no-network runtime.
3. Web tools are explicitly denied for subagents unless policy allows them.
4. Shared assembly code is the source of truth for both main and delegated
   tool registration.
5. Delegated tool filtering is explicit and covered by tests.
6. Subagent tool actions are durably auditable with subagent/session
   correlation.
7. Existing spawn/result routing compatibility is preserved.

## 14. Suggested Commit Boundaries

To keep reviewable stages and follow repo git discipline, implement phase 1 in
separate commits:

1. delegated policy primitives and tests
2. shared tool assembly refactor
3. subagent runtime switch to shared assembly
4. persistent subagent audit integration
5. docs and release note updates

## 15. Deferred Phase 2 Topics

The following should be evaluated after phase 1 stabilizes:

- subagent-specific credential pools
- separate provider cooldown and rate limiting
- separate session ids and parent session links for delegated work
- structured subagent result payloads instead of text-first templates
- per-subagent temporary workspaces
- separate process or container isolation
- trust-level wrappers for low-trust delegated results

## 16. Final Decision Record

Phase 1 intentionally optimizes for:

- stronger delegated safety
- less implementation drift
- lower future maintenance cost

It intentionally does not optimize for:

- maximum isolation
- maximum feature richness
- immediate parity with more complex multi-agent runtimes

That tradeoff is acceptable. The current bottleneck is not lack of orchestration
power. It is the lack of a clean, explicit, auditable delegated runtime
boundary.
