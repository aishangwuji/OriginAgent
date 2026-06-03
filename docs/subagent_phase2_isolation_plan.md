# Subagent Phase 2 Isolation Plan

Date: 2026-05-28
Status: Proposed
Scope: OriginAgent subagent phase 2 only

## 1. Objective

Phase 2 builds on the phase-1 delegated runtime boundary and introduces three
new capabilities:

1. Durable subagent audit records plus independent task records.
2. Provider and credential-pool isolation for delegated work.
3. Stronger execution isolation for subagents.

The goal is to move subagents from "safe default delegated runtime" toward
"independently traceable and resource-bounded delegated workers" without
jumping directly to a full multi-process orchestration platform.

## 2. Why This Phase Exists

Phase 1 addresses least-privilege defaults and shared tool assembly, but it
does not fully solve three operational gaps:

- Durable audit is still too close to generic tool-call logging and not yet a
  complete subagent lifecycle record.
- Subagents still share the primary provider path, rate limits, and credential
  surface with the parent runtime.
- Subagents still execute inside the same process and workspace namespace,
  which limits fault containment and environment isolation.

Phase 2 addresses these gaps in a staged way:

- first make delegated work fully reconstructable
- then separate resource/provider concerns
- then strengthen runtime isolation

## 3. Non-Goals

This phase does not include:

- redesigning the public `spawn` tool schema
- DAG scheduling, planner fan-out, or subagent swarms
- trust-ranking or semantic scoring of subagent outputs
- distributed worker fleets
- external queue backends
- end-user policy UI for advanced subagent routing
- full production-grade container orchestration

Those may become later tracks, but they are intentionally outside phase 2.

## 4. Design Principles

### 4.1 Durable before clever

Before making delegated work more powerful, the system must be able to explain
exactly what happened during a subagent run from start to finish.

### 4.2 Resource separation must be explicit

Subagents should not borrow provider, credential, or runtime resources
accidentally. Any sharing should be deliberate, observable, and configurable.

### 4.3 Isolation is incremental

Phase 2 should improve containment in layers:

- record isolation
- provider isolation
- runtime isolation

It should not assume the final answer is immediately "containers everywhere".

### 4.4 Compatibility must degrade gracefully

If a stronger isolation backend is unavailable on a host, the system should
fall back safely and clearly instead of silently widening privilege.

## 5. Target Outcome

After phase 2:

- every subagent run has a durable task record
- every subagent tool action can be correlated back to that task record
- subagents can use an isolated provider/credential selection path
- parent and child delegated work no longer necessarily compete on the same
  credential slot or cooldown lane
- subagents can run inside stronger isolation modes, starting with temporary
  workspace isolation and progressing toward separate process/container options
- the runtime can explain which isolation level was used for each subagent run

## 6. Phase Breakdown

### Phase 2A

Durable audit records and independent task records.

### Phase 2B

Provider and credential-pool isolation.

### Phase 2C

Stronger execution isolation.

These should be implemented in that order.

## 7. Functional Requirements

### 7.1 Durable subagent task record

Each subagent run must produce a durable task record that is separate from
generic tool-call audit entries.

Minimum phase 2A fields:

- `subagent_id`
- `parent_session_key`
- `origin_channel`
- `origin_chat_id`
- `origin_message_id` when available
- task label
- task prompt/description summary
- effective delegated policy/profile summary
- tool allowlist/profile summary
- provider identity summary
- start time
- end time
- terminal status
- stop reason
- failure summary when applicable

This record should survive process restarts and should not depend on in-memory
status maps.

### 7.2 Durable subagent tool record

Phase 1 already improves security-tier audit context, but phase 2A should
define an explicit subagent-oriented record model.

Minimum fields:

- `subagent_id`
- `parent_session_key`
- tool name
- start time
- end time or duration
- executed / denied / failed / interrupted
- redacted parameter summary
- redacted result summary
- policy rule when denied

The system must be able to answer:

- which tools did subagent X try to use
- which ones were denied by policy
- which tool caused failure

### 7.3 Independent task lifecycle record

Subagent records should not only capture tool events. They must also capture
lifecycle transitions.

Minimum transitions:

- spawned
- running
- waiting_for_tools or equivalent
- completed
- failed
- interrupted
- cancelled

If the current status model remains in memory, phase 2A should mirror the
meaningful states into durable storage.

### 7.4 Provider isolation

Subagents should be able to use a provider path distinct from the parent
runtime.

Phase 2B minimum expectations:

- support selecting a subagent-specific provider config or provider alias
- support a subagent-specific cooldown lane
- support a subagent-specific credential or credential pool
- preserve a safe fallback if an isolated provider is not configured

The parent runtime should be able to choose one of these modes:

- inherit parent provider path
- use delegated provider alias
- use delegated credential pool

### 7.5 Credential pool separation

If a credential pool exists or is introduced, subagents should not consume the
same slot blindly as the parent.

Minimum behavior:

- subagent worker can ask for a delegated credential selection
- parent and child usage/cooldown can be tracked separately
- exhaustion or throttling in delegated work should not immediately poison the
  parent lane unless configured to share state

### 7.6 Stronger execution isolation

Phase 2C should support stronger runtime isolation than the shared-process
model.

Recommended progression:

1. temporary delegated workspace
2. separate child process
3. optional container or VM backend

The first acceptable step is a temporary subagent workspace or isolated overlay
with controlled file ingress/egress.

### 7.7 Isolation mode reporting

Each subagent run should record which isolation mode was used.

Examples:

- `shared_process`
- `shared_process_temp_workspace`
- `child_process`
- `container`

This must appear in the durable task record.

### 7.8 Compatibility constraints

Phase 2 must preserve:

- existing `spawn` tool schema
- existing session routing and `subagent_result` injection contract
- existing `/stop` user behavior
- current ability to run locally without container dependencies

## 8. Proposed Architecture Changes

### 8.1 Subagent record store

Introduce a dedicated durable store for subagent task records.

Possible locations:

- `OriginAgent/agent/subagent_records.py`
- `OriginAgent/agent/subagent_store.py`
- `OriginAgent/session/subagent_store.py`

Preferred storage shape:

- append-only JSONL for event records, or
- structured JSON/SQLite if querying becomes important

Recommendation:

- start with append-only JSONL or structured JSONL event stream
- add projection helpers for status summaries

### 8.2 Task record and tool record schemas

Define explicit schemas rather than overloading generic tool audit events.

Recommended record families:

- `SubagentTaskRecord`
- `SubagentToolRecord`
- optional `SubagentLifecycleEvent`

These may still reuse the existing redaction helpers and hash-chain approach.

### 8.3 Provider selection strategy for delegated work

Add a dedicated delegated provider selection layer.

Suggested responsibilities:

- resolve delegated provider policy
- choose provider alias or credential pool
- enforce delegated cooldown and retry behavior
- expose provider summary into the task record

This can initially live near:

- `OriginAgent/agent/subagent_provider.py`, or
- `OriginAgent/providers/delegation.py`

### 8.4 Credential pool abstraction

If the codebase does not yet expose a first-class credential pool abstraction,
phase 2B may need to create a minimal one.

Minimum behaviors:

- select
- mark temporary cooldown
- mark exhausted
- query health/availability

The interface should be small and provider-agnostic.

### 8.5 Isolation backend abstraction

Phase 2C should not hard-code a single execution backend.

Suggested abstraction:

- `SubagentIsolationBackend`
- `SharedProcessBackend`
- `TempWorkspaceBackend`
- `ChildProcessBackend`
- `ContainerBackend` as optional future backend

The `SubagentManager` should request an isolation backend rather than directly
deciding how to execute the worker.

### 8.6 Workspace ingress/egress rules

If temporary workspaces are introduced, phase 2C must define:

- which files are copied in
- whether the workspace is read-only or writable
- which artifacts are copied back
- how cleanup is guaranteed

Recommendation for initial phase 2C:

- copy in only the minimum readable workspace surface
- deny write-back by default
- explicitly export only structured result artifacts

## 9. Workstreams

### Workstream A: Durable task and audit records

Deliverables:

- durable subagent task store
- durable tool records linked by `subagent_id`
- lifecycle transition records
- query/projection helpers for runtime status and debugging

### Workstream B: Delegated provider and credential isolation

Deliverables:

- delegated provider policy
- delegated provider selection path
- delegated credential or credential-pool support
- separate cooldown/accounting for delegated usage

### Workstream C: Stronger runtime isolation

Deliverables:

- isolation backend abstraction
- at least one stronger-than-shared-process backend
- cleanup guarantees
- isolation mode surfaced in records and runtime explain output

### Workstream D: Regression and operability

Deliverables:

- tests for durable records
- tests for delegated provider fallback behavior
- tests for cleanup on success, failure, and cancellation
- docs/runbook updates

## 10. File-Level Impact

Expected primary edit surface:

- `OriginAgent/agent/subagent.py`
- new subagent record store module
- new delegated provider selection module
- provider factory/routing modules
- possibly runtime introspection/status modules
- possibly session or memory audit surfaces
- tests under:
  - `tests/agent/`
  - `tests/tools/`
  - `tests/providers/`

Expected secondary review surface:

- `OriginAgent/agent/tools/runtime_status.py`
- `OriginAgent/docs/runtime_security.md`
- `OriginAgent/docs/runtime_profiles.md`
- `OriginAgent/agent/loop.py`
- provider retry/cooldown helpers

## 11. Risks

### 11.1 Too many record formats

If phase 2 introduces separate task logs, lifecycle logs, and generic tool
audit logs without a clear relationship, observability may become harder rather
than easier.

Mitigation:

- define canonical ids and correlation rules up front
- keep record types small and explicit

### 11.2 Provider isolation complexity

Credential pools and delegated provider routing can become a cross-cutting
system quickly.

Mitigation:

- keep the delegated provider contract narrow
- start with opt-in configuration
- provide a safe inherit-parent fallback

### 11.3 Stronger isolation portability

Child-process or container isolation may behave differently across Windows,
Linux, Docker, and local dev environments.

Mitigation:

- design isolation backends as optional capabilities
- surface selected backend in runtime status and task records
- require graceful fallback behavior

### 11.4 Cleanup leakage

Temporary workspaces, processes, or browser/tool sessions may leak if cleanup
paths are not robust.

Mitigation:

- make cleanup a first-class tested requirement
- test success, failure, timeout, and cancellation paths

### 11.5 Sensitive data retention

Durable subagent task records can easily become too verbose.

Mitigation:

- log summaries, not raw prompts/results where unnecessary
- reuse redaction and hashing helpers
- define which fields are safe to persist verbatim

## 12. Implementation Sequence

### Step 1

Introduce durable task and tool record schemas plus storage.

### Step 2

Write lifecycle events and correlate them with tool records.

### Step 3

Expose subagent task summaries through runtime introspection/status helpers.

### Step 4

Add delegated provider policy and provider selection hooks.

### Step 5

Introduce credential-pool separation for delegated work.

### Step 6

Add stronger isolation backend abstraction and implement the first non-shared
backend.

### Step 7

Add cleanup guarantees and failure/cancellation regression coverage.

### Step 8

Update docs, runbooks, and release notes.

## 13. Testing Plan

Phase 2 requires new or updated tests for the following:

### Durable records

- task record created on spawn
- lifecycle state transitions persisted
- tool records linked to `subagent_id`
- cancellation writes terminal state
- failure writes redacted summary

### Provider isolation

- delegated provider selection uses configured alias when present
- fallback to parent provider remains safe
- delegated cooldown does not incorrectly poison parent lane
- credential exhaustion rotates delegated pool correctly

### Isolation backend behavior

- temp workspace backend cleans up on success
- temp workspace backend cleans up on failure
- child process backend reports status and exit failures correctly
- unavailable stronger backend falls back safely and visibly

### Runtime compatibility

- `spawn` tool contract remains unchanged
- `subagent_result` injection remains intact
- `/stop` still cancels delegated work
- first-phase read-only delegated policy remains enforceable under stronger
  isolation modes

## 14. Acceptance Criteria

Phase 2 is complete when all of the following are true:

1. Every subagent run has a durable task record with lifecycle data.
2. Every subagent tool action can be correlated to a durable `subagent_id`.
3. Delegated work can use a provider path or credential lane separate from the
   parent runtime.
4. Delegated provider/accounting behavior is covered by tests.
5. At least one stronger-than-shared-process isolation backend is implemented.
6. Cleanup behavior is reliable across success, failure, and cancellation.
7. Runtime explain/status surfaces can summarize recent delegated work without
   exposing unsafe raw data.

## 15. Suggested Commit Boundaries

To keep reviewable stages and preserve clean git boundaries, implement phase 2
in separate commits:

1. durable subagent task/tool record schemas and store
2. lifecycle persistence and runtime status projection
3. delegated provider selection and fallback path
4. delegated credential pool separation
5. stronger isolation backend abstraction and first backend
6. docs and release note updates

## 16. Deferred Phase 3 Topics

The following should be evaluated after phase 2 stabilizes:

- fully structured subagent outputs rather than text-first summaries
- richer task-level trust policies
- explicit parent-to-child result verification workflows
- planner-managed subagent ensembles
- distributed worker execution
- operator/admin tooling for task inspection and replay

## 17. Final Decision Record

Phase 2 intentionally optimizes for:

- durable observability
- resource separation
- stronger containment

It intentionally does not optimize for:

- maximum orchestration sophistication
- immediate full containerization
- total removal of the shared-process fallback path

That tradeoff is acceptable. Once phase 1 establishes a clean delegated
runtime boundary, the next bottleneck is no longer tool assembly. It is the
ability to reconstruct, resource-bound, and contain delegated work as an
independent operational unit.
