# Governed Evolution Controls

OriginAgent self-evolution is a governed pipeline, not an unrestricted self-modifying loop. The runtime may observe repeated patterns, generate signals, prepare review proposals, evaluate risk, run isolated read-only trials, and record health trends. Long-term activation remains conservative and review-driven.

## Configuration

Evolution settings live under `learning.evolution`.

```yaml
learning:
  evolution:
    mode: conservative
    dry_run: true
    allow_manual_override: false
    outcome_retention_days: 90
    dependency_stale_cleanup_enabled: true
    health_history_retention_days: 90
    max_health_history_snapshots: 100

    trial:
      enabled: true
      isolated_workspace: true
      read_only_tools_only: true
      blocked_tools: ["write_file", "edit_file", "exec", "message", "cron", "spawn"]
      temp_dir: ""
      max_step_output_chars: 2000
      max_retained_trial_logs: 10
      trial_log_retention_days: 30
```

`mode=conservative` and `dry_run=true` are the safe defaults. They allow observability without automatically filling the review queue.

## Control Plane Writes

The `my` tool exposes a small evolution control plane:

- `suppress_signal`
- `resume_signal`
- `run_maintenance`
- `force_cleanup`
- `run_feedback_calibration`
- `retry_trial`

These are write actions. They are disabled unless `learning.evolution.allow_manual_override=true`.

When disabled, the tool returns:

```text
Evolution manual override is disabled. Set evolution.allow_manual_override=true in config to enable.
```

This protects the evolution state from accidental mutation by ordinary task execution or by the model itself. Administrators should enable manual override only for an intentional maintenance session, then disable it again.

## Operator Loop

v1.3 adds an operator loop on top of the governed evolution pipeline. It does not expand what the agent may activate automatically. It turns existing signals, gates, trials, health scores, and feedback into reviewable operator summaries.

Auto-evolution proposals include:

- `operator_insights.trial_summary`: compact sandbox or retry-trial status.
- `operator_insights.risk_summary`: gate decisions, issue counts, and high-signal issue messages.
- `operator_insights.health_impact`: estimated direction and score impact.
- `operator_insights.recommended_action`: `review_required`, `auto_apply`, or `reject`.
- `operator_insights.why_not_auto_active`: policy reminders explaining why verified artifacts still do not become active.

`originagent_runtime_status.evolution.operator_recommendations` reports bounded operational suggestions such as:

- health trend is degrading.
- stale dependencies should be cleaned by maintenance.
- sandbox failures need inspection or retry.
- pending auto-evolution proposals should be reviewed.
- repeated negative feedback suggests suppressing a signal.

These recommendations are redacted summaries. They do not expose raw trial output or raw evidence text.

The `my` tool also exposes read-only operator views:

- `inspect_signal` with `key=<opportunity_id>`.
- `inspect_evolution_proposal` with `key=<proposal_id>`.
- `explain_evolution_health`.
- `list_evolution_recommendations`.

These read actions do not require `allow_manual_override`. The write action `retry_trial` does require `allow_manual_override=true` and only applies to pending auto-evolution workflow proposals. Retry trial re-runs the read-only isolated trial with optional fixtures, updates the proposal payload with compact trial evidence, and writes a `trial_retried` outcome event.

## Trial Isolation

Trial mode is for evaluating verified evolution artifacts without touching the real workspace.

The trial runner enforces these rules:

- It calls `SandboxEvaluator.evaluate_trial_workflow_payload()` before executing any step.
- It creates an isolated temporary workspace for each trial.
- It does not read from the real workspace.
- It only seeds files explicitly passed as trial fixtures.
- It only implements `read_file`, `glob`, and `grep`.
- It intersects configured read-only tools with the built-in read-only set.
- It blocks or fails side-effecting tools before execution.

The configured `blocked_tools` should include `write_file`, `edit_file`, `exec`, `message`, `cron`, and `spawn`. If a workflow or skill references these during trial, the result is marked `blocked` or `failed`; the tool is not run.

`trial.temp_dir` can point to an administrator-controlled parent directory for trial temporary workspaces. If unset, the system uses the OS temporary directory.

## Trial Logs

Trial logs are stored in:

```text
memory/evolution_trial_logs.jsonl
```

Each step output is capped by `learning.evolution.trial.max_step_output_chars` and records:

- `output`: truncated step output.
- `output_chars`: original redacted output length.
- `output_truncated`: whether truncation occurred.
- `output_summary`: short redacted summary.

Retention is bounded by both count and age:

- `max_retained_trial_logs`: default `10`.
- `trial_log_retention_days`: default `30`.

Evolution maintenance enforces both limits so detailed trial logs cannot grow without bound.

## Health History

`originagent_runtime_status` reports the current health score in:

```text
evolution.evolution_health
```

Maintenance snapshots are stored in:

```text
memory/evolution_health_history.jsonl
```

`originagent_runtime_status` also reports:

```text
evolution.evolution_health_history
```

This includes snapshot count, latest score, previous score, score delta, trend, and last snapshot time. Runtime status remains an explain tool; health history snapshots are written by evolution maintenance, not by status inspection.

Retention is bounded by:

- `health_history_retention_days`: default `90`.
- `max_health_history_snapshots`: default `100`.

## What Never Happens Automatically

These boundaries are intentionally hard:

- `verified` does not mean `active`.
- Workflow proposals are not activated automatically.
- Skills are not activated automatically.
- Skills generated by evolution are not created with `always: true`.
- Trial mode does not run `write_file`, `edit_file`, `exec`, `message`, `cron`, or `spawn`.
- Trial mode does not read the real workspace.
- Trial mode does not write to the real workspace.
- Manual evolution overrides are not available unless `allow_manual_override=true`.

The intended pipeline is: observe, record signal, generate proposal when policy allows, attach static gate and sandbox evidence, review, verify when safe, monitor outcomes, and roll back or suppress when feedback shows risk.

## Runtime Status Fields

The main observability surface is `originagent_runtime_status.evolution`.

Important fields:

- `mode`, `dry_run`: current evolution posture.
- `opportunity_signals_count`, `eligible_workflow_signals`, `eligible_skill_signals`: signal inventory.
- `pending_proposals_from_evolution`: review queue pressure.
- `trial`: current trial isolation policy.
- `trial_logs`: trial log policy and aggregate counts.
- `sandbox`: sandbox pass/fail/block counts.
- `evolution_health`: current 0-100 health score and reasons.
- `evolution_health_history`: bounded score trend.
- `operator_recommendations`: bounded, redacted operational suggestions.
- `maintenance`: configured retention and cleanup policy.

These fields are redacted summaries. They do not expose raw trial outputs, raw evidence text, commands, secrets, paths outside the governed stores, or hidden audit internals.
