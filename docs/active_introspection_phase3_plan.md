# Active Introspection Phase 3 Plan

Date: 2026-05-28
Status: Proposed
Scope: OriginAgent active introspection phase 3 only

## 1. Objective

Phase 3 introduces bounded, auditable agent-initiated behavior without turning
the runtime into a fully autonomous planner.

This phase adds:

1. A background active-intent producer for idle sessions.
2. A minimal persistent model for agent-initiated nudges and reminders.
3. Guardrails so proactive behavior remains understandable, rate-limited, and
   user-controllable.

The goal is to move the agent from purely reactive turn handling toward
carefully scoped proactive assistance while preserving the existing turn model,
session routing, and operational safety boundaries.

## 2. Why This Phase Exists

Current runtime behavior is user-driven only:

- The agent wakes up only when a user or system event arrives.
- Sustained goals exist, but they do not independently re-surface when a
  session is idle.
- Pending confirmations and due reminders can remain latent unless a user
  returns and asks again.
- The runtime already has the primitives for internal events and durable
  session metadata, but no dedicated producer for proactive follow-up.

This phase addresses those gaps without introducing a new orchestration layer
or embedding autonomous behavior into the main state machine.

## 3. Non-Goals

This phase does not include:

- redesigning the main `TurnState` state machine around `ACTIVE` or `WAITING`
- fully autonomous multi-step planning without a triggering policy
- free-form unsolicited research or open-ended outbound outreach
- new end-user UI surfaces such as a notification center
- trust scoring for self-generated proactive messages
- distributed schedulers, queue backends, or workflow engines
- replacing cron, long-task, or subagent systems

Those may become later directions, but they are intentionally out of scope for
phase 3.

## 4. Design Principles

### 4.1 Keep the main loop simple

Proactive behavior should be implemented as an independent producer of internal
messages, not as a new branch of the primary turn state machine.

### 4.2 Default off, explicit enablement

Agent-initiated messages change the interaction contract with the user. They
must be gated behind an explicit runtime setting and remain easy to disable.

### 4.3 Low-noise, low-frequency, high-intent

Phase 3 should focus on obvious, high-value proactive cases such as due
reminders, pending confirmations, and sustained-goal nudges. It should not
attempt broad speculative outreach.

### 4.4 Durable and explainable

Every proactive action should be reconstructable:

- why it fired
- which session it targeted
- which source condition triggered it
- when it last fired
- whether it was suppressed by cooldown or policy

### 4.5 No privilege expansion by stealth

Active introspection may generate messages that trigger normal turns, but it
must not silently bypass existing capability, confirmation, or tool policies.

## 5. Target Outcome

After phase 3:

- the runtime can periodically inspect idle sessions for bounded proactive work
- proactive follow-up is implemented as internal inbound messages
- the main turn-processing path remains the single execution path
- sustained goals can resurface after idle periods
- pending confirmations can be politely re-raised
- due reminders can fire without waiting for a new user turn
- proactive behavior is rate-limited, auditable, and configurable

## 6. Functional Requirements

### 6.1 Independent active-intent producer

Introduce a dedicated background service, tentatively named:

- `ActiveIntentService`
- `ActiveIntentLoop`
- `IdleSessionIntrospector`

Minimum phase 3 responsibilities:

- periodically scan sessions while the runtime is idle
- detect proactive opportunities
- suppress duplicate or noisy follow-up
- publish internal inbound messages back into the main bus

This service should run independently from the main agent turn state machine.

### 6.2 Internal proactive message model

Proactive follow-up should be represented as normal internal inbound messages.

Recommended shape:

- `channel="system"`
- `sender_id="agent_active"`
- `session_key_override=<target session>`
- `metadata["injected_event"] = "active_intent"`
- `metadata["_from_active"] = True`
- `metadata["active_intent_type"] = <intent type>`
- `metadata["active_intent_id"] = <stable dedupe key>`

These messages should enter the normal `_process_message()` flow rather than a
special execution path.

### 6.3 Initial supported proactive intent types

Phase 3 should start with a narrow set of high-confidence intent types:

1. `scheduled_reminder`
2. `goal_nudge`
3. `pending_confirmation_nudge`

Anything beyond these should be deferred unless it reuses the same policy and
cooldown model cleanly.

### 6.4 Goal-state integration

Phase 3 should reuse existing sustained-goal metadata before introducing a new
general task schema.

Minimum behavior:

- inspect `goal_state` in session metadata
- detect active goals with no recent progress
- produce at most one gentle nudge per cooldown window

This should reuse the helpers in `session/goal_state.py` where practical.

### 6.5 Pending confirmation integration

Phase 3 should be able to resurface outstanding confirmation work.

Minimum behavior:

- inspect durable pending confirmations and/or memory facts in
  `pending_confirmation` state
- generate a bounded reminder only when the session is idle
- avoid re-prompting repeatedly without new evidence or elapsed cooldown

### 6.6 Reminder scheduling support

Phase 3 should support a minimal reminder-oriented proactive flow.

Acceptable first step:

- define a small durable reminder record
- detect records with `due_at <= now`
- emit a proactive internal message
- mark the reminder as fired or completed to avoid duplicate delivery

This may live alongside goal metadata or in a dedicated small store if that is
cleaner.

### 6.7 Idle-session eligibility rules

The active-intent producer must not fire indiscriminately.

Phase 3 minimum gating rules:

- no currently running foreground turn for the target session
- no running subagents for that session
- no unresolved foreground confirmation flow that would make the nudge noisy
- no recent active-intent emission for the same session and intent key
- runtime-level `allow_agent_initiated_messages` enabled

### 6.8 Cooldown and dedupe

Phase 3 must include strong anti-loop and anti-spam controls.

Minimum controls:

- per-session cooldown
- per-intent-key cooldown
- max one proactive message per scan interval per session
- suppression when the triggering condition was just emitted recently
- metadata marker so active-intent messages do not recursively trigger
  themselves immediately

### 6.9 Compatibility constraints

Phase 3 must preserve:

- current `TurnState` model and transition table
- current `_process_message()` entrypoint contract
- current session locking and routing behavior
- current subagent result injection flow
- current `/stop` cancellation behavior
- current channel delivery semantics

## 7. Proposed Architecture Changes

### 7.1 Add an active-intent service

Introduce a new service under a focused module, for example:

- `OriginAgent/agent/active_intents.py`
- `OriginAgent/agent/active_introspection.py`

Suggested responsibilities:

- enumerate eligible sessions
- inspect session metadata and durable stores
- derive zero or one active intent per session per pass
- publish an internal inbound message when appropriate
- record emission and suppression decisions

### 7.2 Start a dedicated background loop from `AgentLoop.run()`

Rather than extending `TurnState`, `AgentLoop.run()` should start a dedicated
background coroutine that wakes up on a fixed cadence.

Recommended behavior:

- sleep for a configurable interval, such as 30 seconds
- scan session keys known to the session manager
- skip busy sessions
- publish internal messages for eligible sessions

The loop should be tracked in the same background task set used for other
runtime-maintained background work.

### 7.3 Reuse the normal inbound bus path

Proactive follow-up should be published through `MessageBus.publish_inbound()`
rather than inserted directly into temporary pending queues.

Reason:

- pending queues exist only while a session turn is actively running
- bus injection cleanly wakes idle sessions using existing dispatch logic
- normal routing, persistence, and channel behavior remain centralized

### 7.4 Add a small durable active-intent ledger

Phase 3 needs durable cooldown and audit state.

Preferred implementation direction:

- a JSONL or compact structured record store under `memory/active_intents/`

Minimum record families:

- emission record
- suppression record
- optional reminder record if reminders are introduced in this phase

Minimum fields:

- timestamp
- session_key
- intent_type
- intent_id
- source_type
- source_reference
- outcome: emitted / suppressed / skipped
- suppression_reason when applicable

### 7.5 Add runtime configuration surface

Introduce a minimal runtime config for proactive behavior.

Suggested fields:

- `allow_agent_initiated_messages: bool = False`
- `active_intent_interval_seconds: int = 30`
- `active_intent_session_cooldown_seconds: int = 300`
- `active_intent_intent_cooldown_seconds: int = 300`
- `active_intent_max_messages_per_session_per_pass: int = 1`

This should remain intentionally small in phase 3.

## 8. Workstreams

### Workstream A: Active-intent runtime skeleton

Deliverables:

- active-intent service module
- background loop startup and shutdown wiring
- session eligibility checks
- internal message publishing path

### Workstream B: Initial intent sources

Deliverables:

- sustained-goal nudges
- pending-confirmation nudges
- minimal due reminder support or equivalent durable reminder source

### Workstream C: Cooldown and durability

Deliverables:

- durable active-intent ledger
- cooldown and dedupe rules
- emission/suppression audit records

### Workstream D: Regression and operator controls

Deliverables:

- tests for idle-session gating
- tests for default-off behavior
- tests for non-recursive proactive handling
- docs and operator-facing configuration notes

## 9. File-Level Impact

Expected primary edit surface:

- `OriginAgent/agent/loop.py`
- new active-intent service module
- possibly new active-intent record store module
- `OriginAgent/config/schema.py`
- possibly `OriginAgent/agent/tools/long_task.py`
- possibly `OriginAgent/agent/confirmation.py`
- tests under:
  - `tests/agent/`
  - `tests/command/`
  - `tests/tools/`

Expected secondary review surface:

- `OriginAgent/session/goal_state.py`
- `OriginAgent/agent/context.py`
- `OriginAgent/docs/runtime_profiles.md`
- `OriginAgent/docs/runtime_security.md`
- websocket or status metadata publishers if proactive state is surfaced

## 10. Risks

### 10.1 User surprise and interruption cost

Unsolicited proactive messages can feel noisy or intrusive.

Mitigation:

- default off
- narrow initial intent types
- aggressive cooldown
- explicit metadata and channel labeling

### 10.2 Recursive self-triggering

An active-intent message could trigger a turn that produces new state and then
immediately retriggers another active-intent pass.

Mitigation:

- `_from_active` marker
- per-intent cooldown
- per-session cooldown
- max one message per pass

### 10.3 Hidden complexity through partial state duplication

If phase 3 introduces a new active-goal schema alongside `goal_state`, the
system could drift into multiple sources of truth.

Mitigation:

- reuse `goal_state` first
- introduce a separate reminder record only when there is a clear due-time use
  case

### 10.4 Operational opacity

If proactive events are emitted silently, operators will struggle to explain
why a message appeared.

Mitigation:

- durable emission/suppression ledger
- runtime status/introspection hooks
- clear metadata on generated messages

### 10.5 Priority inversion with busy sessions

If the active-intent loop injects work too aggressively, it could contend with
foreground user traffic.

Mitigation:

- skip sessions with active tasks
- respect existing session locks and global concurrency gating
- emit at most one proactive message per session per pass

## 11. Implementation Sequence

### Step 1

Add runtime config and an active-intent service skeleton with default-off
behavior.

### Step 2

Start and stop the active-intent background loop from `AgentLoop`.

### Step 3

Implement bus-based internal active-intent injection for idle sessions.

### Step 4

Add goal-state and pending-confirmation intent sources.

### Step 5

Add durable cooldown/emission records and suppression tracking.

### Step 6

Add reminder-oriented due-time intent support if phase-3 scope still permits.

### Step 7

Add regression coverage, docs, and operator notes.

## 12. Testing Plan

Phase 3 requires new or updated tests for the following:

### Runtime skeleton

- active-intent loop does not start when proactive behavior is disabled
- active-intent loop starts and stops cleanly with the runtime
- idle sessions can receive internal proactive messages through the normal bus

### Eligibility and gating

- busy sessions are skipped
- sessions with running subagents are skipped
- cooldown suppresses repeated nudges
- `_from_active` messages do not recursively retrigger immediate follow-up

### Goal-state integration

- active sustained goal can produce a nudge when idle
- completed or inactive goals do not produce nudges

### Pending confirmation integration

- pending confirmations can produce a bounded proactive reminder
- recently reminded confirmations are suppressed

### Compatibility

- standard user turns remain unchanged
- subagent injection remains unchanged
- `/stop` still cancels foreground work normally
- active-intent messages use the normal processing path

### Regression

- default runtime behavior is unchanged when proactive mode is off
- no extra outbound traffic is produced in disabled mode
- proactive messages carry stable metadata for auditing

## 13. Acceptance Criteria

Phase 3 is complete when all of the following are true:

1. Proactive behavior is implemented without expanding the main turn state
   machine.
2. A dedicated active-intent background service can inspect idle sessions.
3. Proactive follow-up is published as internal inbound messages through the
   normal bus path.
4. Sustained goals and pending confirmations can produce bounded proactive
   nudges.
5. Proactive behavior is disabled by default and explicitly configurable.
6. Cooldown and dedupe behavior are durable and covered by tests.
7. Operators can explain why a proactive message was emitted or suppressed.

## 14. Suggested Commit Boundaries

To keep reviewable stages and preserve clear git history, implement phase 3 in
separate commits:

1. active-intent config and service skeleton
2. `AgentLoop` background loop integration
3. initial goal-state and pending-confirmation intent sources
4. durable cooldown/emission ledger
5. reminder support if included
6. docs and release note updates

## 15. Deferred Phase 4 Topics

The following should be evaluated only after phase 3 stabilizes:

- user-visible proactive inbox or notification center
- per-user quiet hours and schedule preferences
- richer reminder/task models beyond `goal_state`
- self-initiated research or information gathering
- semantic prioritization across multiple proactive intents
- trust wrappers or special rendering for proactive content
- proactive subagent spawning

## 16. Final Decision Record

Phase 3 intentionally optimizes for:

- minimal intrusion into the current runtime architecture
- bounded proactive value
- operational explainability

It intentionally does not optimize for:

- maximum autonomy
- complex planner-style self-direction
- broad unsolicited outreach

That tradeoff is acceptable. The current opportunity is not to make the agent
fully autonomous. It is to let the runtime surface obvious follow-up obligations
in a controlled, durable, and maintainable way.
