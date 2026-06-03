# Active Introspection Phase 3B Reminders Plan

Date: 2026-05-28
Status: Proposed
Scope: OriginAgent proactive reminder support only

## 1. Objective

Phase 3B adds a minimal durable reminder model on top of the phase-3 active
intent runtime.

This phase adds:

1. A small reminder record store with `due_at` semantics.
2. A reminder-to-active-intent bridge for idle-session delivery.
3. Clear boundaries between reminders, sustained goals, and cron.

The goal is to support obvious user-facing reminder scenarios without
introducing a second generalized task system or overloading `goal_state` with
time-based delivery semantics.

## 2. Why This Phase Exists

Phase 3 established the proactive runtime skeleton, but it intentionally
deferred `scheduled_reminder` because the codebase did not yet have a clean
durable model for due-time follow-up.

Without a dedicated reminder model:

- reminders would be awkwardly encoded into `goal_state`
- cron would be overused for one-shot user reminders
- proactive delivery would lack a durable, queryable trigger source

Phase 3B fills that gap with the smallest model that can safely support:

- one-shot reminders
- bounded redelivery rules
- durable completion/firing state

## 3. Non-Goals

This phase does not include:

- a full task manager or generalized personal todo system
- recurring reminders with arbitrary RRULE support
- replacing cron automation
- quiet hours or advanced notification preferences
- reminder UI beyond the normal chat delivery path
- reminder-driven subagent spawning
- calendar synchronization or external scheduler integrations

Those may come later, but they are explicitly out of scope for 3B.

## 4. Design Principles

### 4.1 Reminders are not goals

`goal_state` represents an active sustained objective. A reminder represents a
time-based delivery obligation. They may relate to each other, but they should
not share the same storage model.

### 4.2 One-shot first

The first reminder model should handle one-shot reminders correctly before
attempting recurring schedules.

### 4.3 Delivery must be durable

The system must be able to answer:

- what reminder existed
- when it became due
- whether it fired
- whether it was redelivered
- whether it was completed, cancelled, or expired

### 4.4 Active-intent runtime remains the only delivery path

Reminder delivery should reuse the phase-3 active-intent producer and normal
internal inbound message flow rather than create a parallel notification
pipeline.

## 5. Target Outcome

After phase 3B:

- the runtime can store durable one-shot reminders
- due reminders can emit proactive internal messages
- fired reminders are not re-emitted indefinitely
- reminders can be completed or cancelled
- operators can inspect reminder state and reminder delivery history

## 6. Functional Requirements

### 6.1 Reminder record model

Introduce a dedicated reminder record, tentatively named `ReminderRecord`.

Minimum fields:

- `reminder_id`
- `session_key`
- `channel`
- `chat_id`
- `content`
- `due_at`
- `status`
- `created_at`
- `updated_at`
- `fired_at` nullable
- `completed_at` nullable
- `cancelled_at` nullable
- `last_delivery_at` nullable
- `delivery_count`
- `source` such as `user_request` or `system`

Recommended statuses:

- `pending`
- `due`
- `fired`
- `completed`
- `cancelled`
- `expired`

### 6.2 Reminder storage

Phase 3B should use a small durable store under:

- `memory/active_intents/reminders.jsonl`, or
- `memory/reminders.jsonl`

Recommendation:

- keep reminder records under `memory/active_intents/` so proactive delivery
  artifacts stay co-located

The store should support:

- append or upsert by `reminder_id`
- list due reminders
- mark fired
- mark completed
- mark cancelled

### 6.3 Due-time evaluation

The active-intent loop should treat reminders as an additional candidate source.

Minimum logic:

- scan reminders with status `pending` or `due`
- treat reminder as due when `due_at <= now`
- emit at most one reminder active-intent per session per scan pass
- mark reminder as `fired` after successful emission

### 6.4 Delivery semantics

Phase 3B should treat a fired reminder as a proactive internal event that
enters the normal agent turn path.

Recommended shape:

- `injected_event = "active_intent"`
- `active_intent_type = "scheduled_reminder"`
- `active_intent_id = "reminder:<reminder_id>"`

Suggested reminder content:

- identify that this is a due reminder
- include the original reminder text
- instruct the agent to deliver or gently surface it, not reinterpret it into
  arbitrary new work

### 6.5 Redelivery policy

Phase 3B should define a simple bounded redelivery rule.

Recommended first rule:

- one-shot reminders fire once by default
- no automatic repeat after `fired`
- explicit later work may add `ack_pending` or bounded redelivery

This keeps first delivery semantics simple and avoids nag loops.

### 6.6 Completion and cancellation

The reminder system must support explicit terminal state transitions.

Minimum behaviors:

- user or tool can mark a reminder complete
- user or tool can cancel a reminder before it fires
- fired reminders may later be marked complete for bookkeeping clarity

### 6.7 Compatibility boundary with cron

Phase 3B must define when to use reminders versus cron.

Recommendation:

- one-shot, session-bound reminders belong in the reminder store
- detached recurring jobs and workspace automations still belong in cron

Practical distinction:

- reminder: "in this chat, remind me at 3 PM"
- cron: "every weekday at 9 AM check deployment health"

### 6.8 Compatibility boundary with `goal_state`

Phase 3B must not expand `goal_state` into a second reminder store.

Allowed relationship:

- reminder may optionally reference a goal

Disallowed phase-3B shortcut:

- storing `due_at` directly in `goal_state` and treating it as a reminder

## 7. Proposed Architecture Changes

### 7.1 Add reminder store module

Introduce a focused module, for example:

- `OriginAgent/agent/reminders.py`
- `OriginAgent/agent/active_reminders.py`

Suggested responsibilities:

- reminder record schema
- durable JSONL store
- due reminder queries
- state transitions

### 7.2 Extend active-intent service with reminder source

The existing `ActiveIntentService` should gain one new candidate source:

- `scheduled_reminder`

This should reuse existing session gating and cooldown logic where possible,
but reminder state transitions should remain reminder-store-driven.

### 7.3 Optional tool surface

If the repository already has a suitable natural place, phase 3B may add a
small tool for setting reminders.

Examples:

- `set_reminder`
- `complete_reminder`
- `cancel_reminder`

However, if a clean tool surface cannot be added without expanding scope too
much, phase 3B may initially expose only the store and delivery path and leave
tooling for a follow-up step.

Recommendation:

- if added, keep it to one-shot reminders only

### 7.4 Runtime introspection support

Reminder state should surface through runtime inspection at least minimally.

Useful fields:

- total reminders
- pending reminders
- due reminders
- last fired reminder timestamp

## 8. Workstreams

### Workstream A: Reminder data model

Deliverables:

- reminder schema
- durable reminder store
- state transition helpers

### Workstream B: Active-intent delivery integration

Deliverables:

- due reminder candidate generation
- reminder firing path
- reminder state update on emission

### Workstream C: Operator visibility and tests

Deliverables:

- reminder summary in runtime introspection
- regression tests for due/fired/cancelled behavior
- docs for reminder versus cron usage

## 9. File-Level Impact

Expected primary edit surface:

- new reminder store module
- `OriginAgent/agent/active_intents.py`
- `OriginAgent/agent/loop.py` if runtime summaries are surfaced
- possibly one reminder tool module
- tests under:
  - `tests/agent/`
  - `tests/tools/`

Expected secondary review surface:

- `OriginAgent/docs/runtime_profiles.md`
- `OriginAgent/docs/runtime_security.md`
- any runtime status or self-model surfaces

## 10. Risks

### 10.1 Overlap with cron

If the boundary is unclear, one-shot reminders and recurring automations will
blur together.

Mitigation:

- document the distinction clearly
- keep 3B reminder scope strictly session-bound and one-shot

### 10.2 Reminder spam or nag loops

If fired reminders are repeatedly scanned as due, the agent may redeliver them.

Mitigation:

- immediate transition to `fired`
- no auto-repeat in 3B
- delivery count and timestamps persisted durably

### 10.3 Tool surface scope creep

A reminder tool can easily expand into scheduling, recurrence, and calendar
features.

Mitigation:

- one-shot only
- no recurrence syntax in 3B
- no external integrations

### 10.4 Hidden state fragmentation

If reminders end up partially stored in goal metadata and partially in a
dedicated store, maintenance cost will rise quickly.

Mitigation:

- keep reminders in one dedicated store from day one

## 11. Implementation Sequence

### Step 1

Define the reminder record schema and durable store.

### Step 2

Add due-reminder scanning and `scheduled_reminder` candidate generation to the
active-intent service.

### Step 3

Mark reminders as fired on successful emission.

### Step 4

Add completion and cancellation helpers.

### Step 5

Add tests and runtime visibility.

### Step 6

Add a minimal reminder tool surface if still justified.

## 12. Testing Plan

Phase 3B requires tests for:

- due reminder emits exactly once
- future reminder does not emit early
- fired reminder does not re-emit
- cancelled reminder does not emit
- completed reminder does not emit
- busy session still suppresses reminder delivery
- reminder records survive process restart

## 13. Acceptance Criteria

Phase 3B is complete when all of the following are true:

1. One-shot reminders have a dedicated durable record model.
2. Due reminders can emit proactive internal messages through the existing
   active-intent path.
3. Fired reminders do not repeatedly redeliver by default.
4. Reminder versus cron boundaries are explicit and documented.
5. Reminder behavior is covered by regression tests.

## 14. Suggested Commit Boundaries

1. reminder schema and store
2. active-intent reminder integration
3. completion/cancellation helpers
4. tests and docs

## 15. Deferred Phase 4 Topics

- recurring reminders
- quiet hours
- cross-session reminder routing
- calendar-backed reminders
- acknowledgment-required delivery states
- user notification center

## 16. Final Decision Record

Phase 3B intentionally optimizes for:

- minimal durable reminder support
- clean separation from goals and cron
- low-risk extension of the active-intent runtime

It intentionally does not optimize for:

- rich scheduling
- recurrence
- full personal task management

That tradeoff is acceptable. The next step after phase-3 active introspection
is not more autonomy. It is giving the system one clean, durable, time-based
follow-up primitive that does not distort the rest of the runtime model.
