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

## Governed Evolution Control Plane

v2.0 introduces `EvolutionControlPlane` as the single service boundary for governed evolution operations. It does not expand autonomy. It centralizes the same safe capabilities that already existed in v1.x:

- read-only status and artifact inspection.
- structured action discovery.
- read-only previews.
- guarded writes behind policy.
- dashboard-ready summaries for `originagent_runtime_status`.

The control plane has a small permission model:

- `read`: status, signal/proposal listing, inspection, recommendations, schema validation, report generation.
- `preview`: non-mutating previews for any operator action.
- `maintenance`: maintenance and feedback calibration writes.
- `override`: signal suppression/resume and trial retry writes.
- `rollback`: governed artifact rollback writes.
- `apply`: always false in the control plane; review/apply remains a separate human review path.

`originagent_runtime_status.evolution.control_plane` reports the active control-plane version, manual override state, action count, and hard safety boundaries. `originagent_runtime_status.evolution.read_model` provides a compact UI/API-oriented view over signals, proposals, health, dependencies, and snapshots.

v2.1 productizes the control plane as a dedicated tool:

```text
originagent_evolution_control
```

New integrations should prefer this tool over the generic `my` compatibility commands. `my` remains available for interactive self-inspection and operator compatibility, but `originagent_evolution_control` has a stable operation surface for UIs, CLIs, and other clients:

- `status`
- `list_actions`
- `list_recommendations`
- `list_signals`
- `list_proposals`
- `inspect_signal`
- `inspect_proposal`
- `preview_action`
- `execute_action`
- `generate_report`
- `explain_health`
- `validate_schema`

`list_actions` returns stable `ActionDescriptor` objects using schema version `originagent.evolution.action.v1`. Each descriptor includes:

- `action_id`: stable action plus target identifier when present.
- `action_kind`: normalized control-plane action.
- `target_type` and `target_id`: the governed object the action addresses.
- `permission`: `read`, `maintenance`, `override`, `rollback`, or `unknown`.
- `risk_level`: compact operator risk summary.
- `previewable` and `executable`: whether the action supports preview and execute paths.
- `requires_manual_override`: whether real execution needs `allow_manual_override=true`.
- `parameters_schema`: action-specific parameter contract.
- `suggested_my_action`: backwards-compatible `my` command shape.
- `policy`: the current policy decision for the action.

`list_recommendations` attaches the same `action_descriptor` contract to each recommendation, along with `previewable`, `executable`, `requires_manual_override`, and `suggested_next_step`. This lets a UI render actions without parsing prose.

## Control Plane Writes

The `my` tool exposes a small evolution control plane for compatibility:

- `suppress_signal`
- `resume_signal`
- `run_maintenance`
- `force_cleanup`
- `run_feedback_calibration`
- `retry_trial`
- `rollback_artifact`
- `execute_evolution_action`

These are write actions. They are disabled unless `learning.evolution.allow_manual_override=true`.

When disabled, the tool returns:

```text
Evolution manual override is disabled. Set evolution.allow_manual_override=true in config to enable.
```

This protects the evolution state from accidental mutation by ordinary task execution or by the model itself. Administrators should enable manual override only for an intentional maintenance session, then disable it again.

`execute_evolution_action` is the generic v2.0 write entry point. It accepts an object value with `action_kind` plus action-specific fields such as `target_id`, `artifact_type`, `artifact_name`, `snapshot_id`, `fixtures`, `reason`, or `force_cleanup`. It still goes through the same policy checks as the named actions.

`originagent_evolution_control` exposes the same policy through `execute_action`. Execute results use schema version `originagent.evolution.action_result.v1` and include the embedded `action` descriptor, `policy`, `allowed`, `will_write`, `result`, `error`, and `message`.

Control-plane outcome audit events are written only on real execute paths:

- `control_action_denied`: policy rejected the execution, such as missing `allow_manual_override`.
- `control_action_executed`: a write action executed successfully.
- `control_action_failed`: a write action was allowed but failed.

Read actions do not append control events. Preview actions are strictly read-only and do not append `control_action_*` outcome events.

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

In v1.4 these recommendations are structured operator actions. Each item includes:

- `code`: stable recommendation identifier.
- `severity`: `info`, `warning`, or stronger review signal.
- `action_kind`: concrete operator action such as `inspect_evolution_proposal`, `retry_trial`, `suppress_signal`, or `run_maintenance`.
- `target_type`: `proposal`, `signal`, `maintenance`, `health`, or `system`.
- `target_id`: proposal id, opportunity id, or empty for system actions.
- `requires_manual_override`: whether the real action writes governed evolution state.
- `risk_level`: compact operator risk summary.
- `preview`: short human-readable impact description.
- `suggested_my_action`: the corresponding `my` command shape.

These recommendations are redacted summaries. They do not expose raw trial output or raw evidence text, and they never execute actions by themselves.

The `my` tool also exposes read-only operator views:

- `evolution_status`.
- `list_evolution_actions`.
- `list_evolution_signals`.
- `list_evolution_proposals`.
- `inspect_signal` with `key=<opportunity_id>`.
- `inspect_evolution_proposal` with `key=<proposal_id>`.
- `explain_evolution_health`.
- `list_evolution_recommendations`.
- `preview_evolution_action`.
- `generate_evolution_report`.
- `validate_evolution_schema`.

These read actions do not require `allow_manual_override`.

`preview_evolution_action` accepts an object value such as:

```json
{
  "action_kind": "retry_trial",
  "target_id": "review_auto_workflow_123",
  "fixtures": {"notes.txt": "trial fixture text"}
}
```

Preview is strictly read-only. It reports what a real action would do, but it does not update proposal payloads, append outcome events, append trial logs, suppress signals, resume signals, or run maintenance. `retry_trial` preview uses the trial sandbox gate only; it does not invoke the real trial runner.

`generate_evolution_report` returns a Markdown report for a bounded period, defaulting to seven days. The report summarizes health, signals, recent outcomes, sandbox/review/rollback counts, recommendations, and the hard safety boundaries.

The write action `retry_trial` does require `allow_manual_override=true` and only applies to pending auto-evolution workflow proposals. Retry trial re-runs the read-only isolated trial with optional fixtures, updates the proposal payload with compact trial evidence, and writes a `trial_retried` outcome event.

`rollback_artifact` also requires `allow_manual_override=true`. Previewing rollback is read-only and reports the selected snapshot and dependency blockers. Executing rollback delegates to the governed rollback service, writes rollback outcomes, and still refuses dependency-breaking rollback unless force is explicitly supplied.

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

## Schema Validation

v1.5 adds a release-candidate schema check for governed evolution stores. It is read-only and validates the current file contents without rewriting older records.

The validator covers:

- `memory/opportunity_signals.jsonl`
- auto-evolution records in `memory/review_proposals.jsonl`
- `memory/evolution_outcomes.jsonl`
- `memory/evolution_dependencies.jsonl`
- `memory/evolution_trial_logs.jsonl`
- `memory/evolution_health_history.jsonl`
- `memory/evolution_snapshots/**/version_metadata.json`

Maintenance now includes a compact `schema_validation` summary with:

- `ok`: whether any reject-level schema issue was found.
- `record_counts`: count by governed store.
- `issue_counts`: count by severity.

Operator previews and reports surface the same summary so administrators can see schema drift before running maintenance or applying proposals. Schema validation is an observability guardrail; it does not activate artifacts, rewrite stores, or approve proposals.

In v2.0, `validate_evolution_schema` is also exposed as a read action through the control plane and `my`. It returns the same validator result without mutating any governed store.

## Release Candidate Checklist

Before treating governed evolution as ready for v2.0 control-plane work, the v1.5 release candidate should pass this end-to-end loop:

1. Append repeated historical usage entries.
2. Detect a workflow opportunity signal.
3. Let Curator convert the high-score signal into a workflow review proposal.
4. Verify payload `static_gate`, `sandbox`, `promotion_gate`, and `operator_insights`.
5. Preview `retry_trial` and confirm preview does not write proposal payloads, trial logs, outcomes, or signal state.
6. Manually apply the proposal through `ReviewProposalStore.apply`.
7. Confirm the generated workflow remains `proposal_status=proposed`, `verification_status=unverified`, and has all execution flags disabled.
8. Confirm outcome trace includes signal creation, gate evaluation, proposal generation, and review approval.
9. Run evolution maintenance and confirm schema validation is healthy.
10. Generate an operator report and confirm it still restates the hard safety boundaries.

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

- `control_plane`: v2.0 service metadata, manual override state, action count, and safety boundaries.
- `policy`: current mode, dry-run state, manual override state, and permission summary.
- `read_model`: compact dashboard-ready signal/proposal/health/dependency/snapshot summary.
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
- `schema_validation`: compact release-contract validation summary.

These fields are redacted summaries. They do not expose raw trial outputs, raw evidence text, commands, secrets, paths outside the governed stores, or hidden audit internals.
