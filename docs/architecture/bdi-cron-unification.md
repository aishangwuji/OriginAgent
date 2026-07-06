# BDI-Cron Unification Architecture

**Status:** Draft
**Date:** 2026-07-06
**Driving requirement:** Cron and BDI are two independent proactive-trigger systems that do not coordinate, causing missed coverage, duplicate work, and unpredictable behavior.

---

## 1. Problem Analysis

### 1.1 Current Architecture (Before)

```
User request: "remind me in 1 hour"

  CronTool ──────> CronService ──────> on_job callback ──────> agent._process_message()
  (agent tool)     (timer/schedule)     (cli/commands.py)       (direct dispatch)

  BDI DeliberationEngine ──> run_cycle() every 120s ──> on_intention callback
  (bdi/deliberation.py)       (LLM-driven desires)       (AgentHost)
```

Both arrows terminate at the agent loop, but they share no state, no failure awareness, and no intent coordination.

### 1.2 Concrete Failure Scenarios

**Scenario A: Silent cron delivery failure.** User creates a cron reminder. Timer fires, `on_job` dispatches `_process_message`. The processing fails (provider error, channel unavailable). `CronService` records `last_status = "error"`, but no one notices. BDI does not know the desire was not fulfilled. No recovery path exists.

**Scenario B: Duplicate proactive discovery.** User mentions "I need to check the report next week." The agent creates both a cron job AND a foresight record. BDI auto-creates a Desire from the foresight. When the time comes, cron fires AND BDI independently forms an intention. User receives two identical messages.

**Scenario C: BDI interval miss.** A time-sensitive desire has a 2-minute deadline. BDI's deliberation interval is 120s, and the previous cycle just completed. The deadline passes between cycles, wasting up to 119s before BDI detects the over-deadline desire.

**Scenario D: Restart gap.** On restart, CronService loads jobs from disk and continues firing. BDI reinitializes but recreates desires from foresight sync. Cron jobs linked to desires satisfied before restart continue firing with no cancellation mechanism.

### 1.3 Root Cause

Both systems independently evaluate "is it time to act?" with no shared reference frame:

| Concern | Cron | BDI |
|---------|------|-----|
| Timing | Exact (millisecond precision) | Approximate (120s interval) |
| Decision | "Time X has arrived, execute" | "Is this desire worth acting on right now?" |
| State | Job list on disk | Desire store + beliefs |
| Recovery | None (fire-and-forget) | None for cron-sourced events |
| Observability | `last_status`, `last_error` (per job) | Cycle audit log |

---

## 2. Proposed Architecture

### 2.1 Principle

**Cron is a mechanism (fire at time X). BDI is a strategy (decide what matters).**

The unification bridges these concerns without merging their code:

```
                          AgentLoop
  ┌───────────────────────────────────────────────────────────────┐
  │                                                               │
  │    CronService          Cron-BDI Bridge          BDI           │
  │    (mechanism)          ──────────────          (strategy)    │
  │                         │ observations │                     │
  │    timer ──> on_job ───>│              │──> beliefs          │
  │                         │ failure sync  │                     │
  │    add_job() <── API ───│              │<── override         │
  │                         │ desire links  │                     │
  │                         └──────────────┘                     │
  └───────────────────────────────────────────────────────────────┘
```

### 2.2 Component Map

| Component | File | Role |
|-----------|------|------|
| `CronService` | `cron/service.py` | Timer, job persistence, fire callback — unchanged |
| `DeliberationEngine` | `bdi/deliberation.py` | Periodic deliberation — extended with cron beliefs |
| `DesireStore` | `bdi/desire_store.py` | Desire CRUD — unchanged |
| `CronObservationStore` | `bdi/cron_observation_store.py` (new) | Maps CronJob IDs to Desire IDs, records delivery outcomes |
| `CronDesireBridge` | `bdi/cron_desire_bridge.py` (new) | Creates desires from cron jobs, syncs state, provides belief data |
| `CronTool` | `agent/tools/cron.py` | Agent tool — extended with bridge call |
| `AgentHost` | `agent/agent_host.py` | Wires the bridge, passes it to DeliberationEngine |

### 2.3 Unified State Transitions

```
PENDING ---> ACTIVE ---> INTENDED ---> FIRED ---> SATISFIED
                             |                       |
                             v                       v
                        RETRY_PENDING           FAILED
                             |
                             v
                        ALTERNATIVE_ACTION
```

- **PENDING**: Cron job exists, desire has been created but BDI has not deliberated.
- **ACTIVE**: BDI has evaluated the desire, decided to let cron handle timing.
- **INTENDED**: CronService `on_job` has fired, delivery is in flight.
- **FIRED**: Delivery succeeded, desire can move to SATISFIED.
- **RETRY_PENDING**: Delivery failed, BDI will reconsider at next cycle.
- **FAILED**: All retries exhausted, BDI knows not to expect delivery.
- **SATISFIED**: Desire completed, cron job can be disabled.
- **ALTERNATIVE_ACTION**: BDI takes over because cron delivery is unreliable.

---

## 3. Data Flow

### 3.1 User creates a timed reminder (happy path)

```
1. User: "remind me to file taxes in 3 days"
2. Agent calls CronTool.add_job()
       |
       +---> CronService.add_job() --> saves to jobs.json
       |
       +---> CronDesireBridge.on_cron_job_created(job, session_key)
                  |
                  +---> Creates a Desire with:
                  |      desire_id = "cron:{job.id}"
                  |      content = job.payload.message
                  |      deadline_at = <3 days from now>
                  |      metadata = {"cron_job_id": job.id}
                  |      status = PENDING
                  |
                  +---> Saves CronJobDesireLink to CronObservationStore

3. BDI next cycle (120s later):
       |
       +---> Sees PENDING desire with cron_job_id in metadata
       +---> CronObservationStore reports: job is active, next run in 3 days
       +---> BDI decision: "Cron will handle timing. No action needed now."
       +---> Desire --> ACTIVE (with note: "trust cron")

4. 3 days pass, CronService timer fires:
       |
       +---> on_job callback --> agent._process_message()
       +---> Success --> CronService records last_status = "ok"
       |
       +---> CronDesireBridge.on_job_completed(job_id, success=True)
                  |
                  +---> CronObservationStore: record delivery success
                  +---> Desire --> SATISFIED

5. Next BDI cycle: skipped (desire is terminal)
```

### 3.2 Cron delivery fails (recovery path)

```
1. CronService._execute_job(job)
       |
       +---> on_job callback --> agent._process_message()
       +---> FAILS (provider error, channel down)
       +---> CronService records last_status = "error"

2. CronDesireBridge.on_job_completed(job_id, success=False)
       |
       +---> CronObservationStore: record delivery failure
       |      incremental: consecutive_failures += 1
       +---> (desire status unchanged -- BDI will decide next cycle)

3. Next BDI cycle:
       |
       +---> Gathers beliefs: cron.failing_jobs includes job <X>
       +---> Sees desire is ACTIVE but cron job has consecutive_failures >= 2
       +---> BDI decides: "Cron delivery failing. Form intention directly."
       |
       +---> DeliberationIntention {
                action: "send_message",
                desire_id: "cron:abc123",
                payload: {text: "Reminder: file taxes"},
                metadata: {alternative_to_cron: true}
            }

4. Intention executed --> message delivered via agent loop.
5. Desire --> SATISFIED.
```

### 3.3 BDI takes over from cron permanently (graceful degradation)

After three consecutive failures, the deliberation engine can decide to disable the cron job and handle the desire directly:

```
1. CronObservationStore shows 3 consecutive failures for job <X>.
2. BDI deliberation cycle:
       |
       +---> Beliefs include: "cron_failure_rate(job_X) = 100%"
       +---> BDI decides: "Disable cron job <X>, handle directly."
       +---> Intention: system action with
                action = "system"
                payload = {"action": "disable_cron", "cron_job_id": "<X>"}

3. AgentHost handles the system intention:
       |   calls CronDesireBridge.disable_cron_job(job_id)
       |   which calls CronService.enable_job(job_id, enabled=False)

4. From now on, BDI manages the desire via its own cycle.
5. When desire is satisfied --> BDI marks it SATISFIED.
```

---

## 4. Cron as BDI Tool

### 4.1 CronDesireBridge class interface

New file: `OriginAgent/bdi/cron_desire_bridge.py`

```python
@dataclass(frozen=True)
class CronJobDesireLink:
    """Maps a CronJob to a Desire with observation state."""
    cron_job_id: str
    desire_id: str
    created_at: str
    last_delivery_attempt: str | None = None
    last_delivery_status: str | None = None   # "ok" | "error"
    consecutive_failures: int = 0
    failure_history: tuple[dict[str, Any], ...] = ()
    cron_disabled: bool = False

    @property
    def is_failing(self) -> bool:
        return self.consecutive_failures >= 2


class CronDesireBridge:
    """Bidirectional bridge between CronService and BDI.

    Responsibilities:
    1. When cron job created, create linked Desire (if BDI enabled).
    2. When cron job fires, record delivery outcome in observation store.
    3. When cron job fails repeatedly, surface to BDI beliefs.
    4. When BDI wants to take over, disable the cron job.
    """

    def __init__(self, cron_service, desire_store, observation_store, enabled=True):
        ...

    def on_cron_job_created(self, job, session_key, owner_id="") -> str | None:
        """Create a desire linked to this cron job. Returns desire_id or None."""
        ...

    async def on_job_completed(self, job, success, error=None) -> None:
        """Record delivery outcome. BDI picks this up on next cycle."""
        ...

    def get_observation_beliefs(self) -> dict:
        """Return cron observation data for BDI's belief set."""
        ...

    def disable_cron_job(self, cron_job_id) -> bool:
        """BDI can disable a cron job it wants to handle directly."""
        ...
```

### 4.2 CronObservationStore interface

New file: `OriginAgent/bdi/cron_observation_store.py`

```python
class CronObservationStore:
    """SQLite-backed store for cron-desire links and delivery outcomes.

    Tables:
      cron_desire_links (PK: cron_job_id)
        - desire_id, created_at, last_delivery_attempt,
          last_delivery_status, consecutive_failures, cron_disabled

      cron_delivery_log (PK: auto-increment)
        - cron_job_id, attempted_at, status, error, duration_ms
        - Indexed on (cron_job_id, attempted_at)
    """

    def __init__(self, db_path: Path):
        ...

    def save(self, link: CronJobDesireLink) -> None:
        """Upsert a cron-desire link."""

    def record_delivery(self, job_id, success, error=None) -> None:
        """Append delivery log entry, update consecutive_failures counter."""

    def get_by_job_id(self, job_id) -> CronJobDesireLink | None:
        ...

    def get_by_desire_id(self, desire_id) -> CronJobDesireLink | None:
        ...

    def list_failing(self) -> list[CronJobDesireLink]:
        """Return links with consecutive_failures >= 2."""
        ...

    def stats(self) -> dict:
        """Aggregate stats: total, active, failing, success_rate."""
        ...
```

### 4.3 Integration into DeliberationEngine beliefs

In `deliberation.py`, the `_gather_beliefs()` method gains:

```python
def _gather_beliefs(self) -> dict[str, Any]:
    beliefs = {
        "current_time": now_iso(),
        # ... existing fields unchanged ...
    }
    # NEW: Cron observation beliefs
    if self._cron_bridge is not None:
        beliefs["cron"] = self._cron_bridge.get_observation_beliefs()
    return beliefs
```

The deliberation system prompt extends with these rules:

```
Additional context: Cron observation data
- cron.bridge_enabled: whether cron-desire linking is active
- cron.failing_jobs: list of cron jobs with repeated delivery failures

Rules for cron-linked desires:
- A desire with cron_job_id in its metadata has a timer managing its timing.
- If the cron job has consecutive_failures >= 2, consider forming a direct
  intention to fulfill the desire as an alternative action.
- You can disable a failing cron job via a system intention with
  payload.action = "disable_cron" to prevent duplicate work.
```

---

## 5. BDI Observation of Cron Tasks

### 5.1 Belief structure

At each deliberation cycle, `_gather_beliefs()` returns:

```json
{
  "cron": {
    "bridge_enabled": true,
    "total_linked_jobs": 5,
    "active_jobs": 4,
    "failing_jobs": [
      {
        "cron_job_id": "abc123",
        "desire_id": "cron:abc123",
        "consecutive_failures": 3,
        "last_error": "Provider API timeout"
      }
    ],
    "recent_deliveries": {
      "last_hour": 2,
      "success_rate_24h": 0.95
    }
  }
}
```

### 5.2 When BDI considers overriding cron

Four triggers cause BDI to override a cron job:

1. **Consecutive failures** (`consecutive_failures >= 2`): BDI forms a direct intention.
2. **Overdue cron job** (deadline passed, no delivery): BDI may act preemptively.
3. **Orphaned desire** (cron job deleted, link survives): BDI takes over.
4. **Desire satisfied before cron fires**: `CronDesireBridge` can disable the linked cron job.

---

## 6. Failure Handling

### 6.1 Classification

| Failure | Detection | Response |
|---------|-----------|----------|
| Cron `on_job` callback raises | Caught by `CronService._execute_job()` | `last_status = "error"`, bridge records. BDI sees on next cycle. |
| Provider timeout | Propagates to `on_job` | Same as above. |
| Channel unreachable | Caught in dispatch | Same as above. |
| Cron job never fires (timer bug) | BDI sees desire still ACTIVE past deadline | BDI forms "overdue" intention. |
| Cron job deleted externally | Bridge `get_by_job_id()` returns None | BDI sees orphaned desire, takes over. |
| BDI engine disabled | Cron runs independently | No change. |

### 6.2 Escalation path

```
1st failure:   Bridge records. BDI notes but does not intervene.
2nd failure:   BDI forms low-severity intention: "investigate and notify user."
3rd failure:   BDI disables cron job, takes over desire management directly.
Satisfied:     BDI marks desire SATISFIED. Optionally re-enable for recurring.
```

---

## 7. Migration Path

### 7.1 Backward Compatibility

Entirely opt-in. If BDI is disabled, the bridge is not created and CronService works exactly as before.

### 7.2 Phases

| Phase | What | Files |
|-------|------|-------|
| **1** | Foundation — `CronObservationStore` + `CronDesireBridge` | 2 new files |
| **2** | BDI belief integration — `_gather_beliefs()` | `deliberation.py` |
| **3** | Cron tool bridge — `on_cron_job_created/on_job_completed` | `tools/cron.py`, `cli/commands.py` |
| **4** | Failure recovery — BDI overrides failing cron | `deliberation.py`, `agent_host.py` |
| **5** | Observability — unified cron-desire status | `introspection/service.py` |

### 7.3 Zero-Change Files

| File | Reason |
|------|--------|
| `cron/service.py` | Bridge observes from outside |
| `bdi/desire_store.py` | Uses existing `add()`, `update()` |
| `bdi/models.py` | `Desire.metadata` dict supports arbitrary keys |
| `bdi/plan_library.py` | Not related |
| `agent/active_intents.py` | Separate system |

---

## 8. Key Design Decisions

### ADR-001: Separate observation store vs extending desire metadata

**Decision**: Separate `cron_desire_links` SQLite table.

**Pros**: Clean desire model; efficient indexed queries; independent schema migration; append-only delivery log separate from desire update history.

### ADR-002: BDI polls vs cron notifying BDI directly

**Decision**: BDI polls during regular cycle.

**Pros**: No new event subscriptions; BDI retains full autonomy; complete decoupling. Up to 120s latency is acceptable for non-critical reminders.

### ADR-003: Keep CronService independent vs merge into BDI

**Decision**: Keep CronService independent with a bridge layer.

**Pros**: Zero regression for existing cron users (CognitiveScheduler, Dream, CLI); BDI restart does not affect cron jobs; backward compatible when BDI is disabled.

### ADR-004: Deterministic desire ID `cron:{job.id}`

**Decision**: Use `cron:{CronJob.id}` as the desire ID.

**Pros**: Idempotent linking on restart; immediate discovery; no separate index for common lookup.
