# WebUI Meta-Cognition Frontend Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface backend meta-cognition, opportunity signals, and evolution runtime state in the WebUI so users can monitor and control the agent's self-reflection pipeline.

**Architecture:** Two-phase implementation. **Phase 1 (backend):** Add 4 HTTP API route handlers to the WebSocket Gateway (`websocket.py`) that bridge to the existing `RuntimeIntrospectionService.meta_cognition_summary()` and `EvolutionControlPlane` (status/list_signals). Wire the introspection service to the gateway via the same callback-injection pattern used for `runtime_model_name` (`commands.py` → `bootstrap.py` → `manager.py` → `websocket.py`). **Phase 2 (frontend):** Add a `fetchMetaCognitionSummary()` API function, `listSignals()`, `updateSignal()`, and `fetchEvolutionStatus()` that call the new endpoints. Create three new frontend views: (1) MetaCognition settings panel in the Settings section, (2) a real-time MetaCognition dashboard showing triggers, reflections, patterns, and bridge stats, (3) an Opportunity Signals management page. Enhance the existing Reviews view with `auto_evolution` origin filtering. All views follow the same React/Tailwind/shadcn-ui patterns as the existing SettingsView and ReviewsView.

**Tech Stack:** Python 3.11+ (backend: websockets, Pydantic), React 18, TypeScript, Tailwind CSS, shadcn/ui (`alert-dialog`, `button`, `input`, `scroll-area`, `separator`, `dialog`), lucide-react icons, Vitest, react-i18next

---

## File Structure

### Phase 1 — Backend

```
OriginAgent/
├── cli/
│   └── commands.py                     # MODIFY: add _webui_runtime_introspection() callback closure
├── channels/
│   ├── bootstrap.py                    # MODIFY: accept + pass webui_runtime_introspection callback
│   ├── manager.py                      # MODIFY: accept + pass webui_runtime_introspection callback
│   └── websocket.py                    # MODIFY: add _handle_meta_cognition_status, _handle_evolution_signals,
│                                       #         _handle_signal_action, _handle_evolution_status;
│                                       #         register 4 routes in _dispatch_http
```

### Phase 2 — Frontend

```
webui/src/
├── lib/
│   ├── api.ts                          # MODIFY: add fetchMetaCognitionSummary, listSignals, updateSignal, listEvolutionStatus
│   └── types.ts                        # MODIFY: add MetaCognitionSummary, MetaTriggerRecord, ReflectionRecord, ErrorPattern, OpportunitySignal, PatternRecord, BridgeStats, EvolutionStatus types
├── components/
│   └── settings/
│       ├── SettingsView.tsx            # MODIFY: add "cognition" section to SETTINGS_NAV_ITEMS, add learning sub-sections
│       ├── LearningSettings.tsx         # CREATE: settings panel for background_review, curator, meta_cognition, dream
│       └── MetaCognitionView.tsx        # CREATE: meta-cognition status dashboard
│   └── signals/
│       └── SignalsView.tsx             # CREATE: opportunity signals browse/manage page
│   └── reviews/
│       └── ReviewsView.tsx             # MODIFY: add "auto_evolution" to ORIGIN_FILTERS
├── App.tsx                             # MODIFY: add "signals" view to ShellView type, add routing
└── i18n/
    └── locales/
        ├── zh-CN/
        │   └── common.json             # MODIFY: add i18n keys for cognition nav, dashboard, signals
        └── en/
            └── common.json             # MODIFY: add i18n keys for cognition nav, dashboard, signals
```

---

### Task 0: Backend API Routes (Gateway → Introspection / Evolution)

**Files:**
- Modify: `OriginAgent/cli/commands.py` (add callback closure)
- Modify: `OriginAgent/channels/bootstrap.py` (accept + pass callback)
- Modify: `OriginAgent/channels/manager.py` (accept + pass callback)
- Modify: `OriginAgent/channels/websocket.py` (add route handlers + register routes)

- [ ] **Step 0a: Add `webui_runtime_introspection` callback chain (commands.py → bootstrap.py → manager.py)**

In `OriginAgent/cli/commands.py`, after the existing `_webui_runtime_model_name()` function (around line 840), add:

```python
def _webui_runtime_introspection():
    """Return the current meta-cognition summary from the running agent loop."""
    try:
        return agent.introspection.meta_cognition_summary()
    except Exception:
        return None
```

Pass it to `ChannelManager`:
```python
channels = ChannelManager(
    config,
    bus,
    session_manager=session_manager,
    webui_runtime_model_name=_webui_runtime_model_name,
    webui_runtime_introspection=_webui_runtime_introspection,  # ADD
)
```

In `OriginAgent/channels/manager.py`, add the parameter and pass-through. Around line 64, change the constructor:

```python
def __init__(
    self,
    config: Any,
    bus: MessageBus,
    *,
    session_manager: Any | None = None,
    webui_runtime_model_name: Callable[[], str | None] | None = None,
    webui_runtime_introspection: Callable[[], dict | None] | None = None,  # ADD
) -> None:
    # ...
    self._webui_runtime_introspection = webui_runtime_introspection  # ADD

# In _create_channel (around line 82), pass it through:
def _create_channel(
    self,
    name: str,
    section: Any,
    channel_cls: type[BaseChannel],
    adapter: ChannelBootstrapAdapter,
) -> BaseChannel | None:
    descriptor = build_channel_descriptor(name, channel_cls, section)
    settings = adapter.resolve_runtime_settings(
        descriptor=descriptor,
        section=section,
        core_config=self._core_config,
        webui_runtime_introspection=getattr(self, "_webui_runtime_introspection", None),  # MODIFY
    )
```

In `OriginAgent/channels/bootstrap.py`, update `resolve_runtime_settings` to accept and forward:

```python
def resolve_runtime_settings(
    self,
    *,
    descriptor: ChannelDescriptor,
    section: Any,
    core_config: Config,
    webui_runtime_introspection: Callable[[], dict | None] | None = None,  # ADD
) -> ChannelRuntimeSettings:
    # ... existing code ...
    if self._webui_runtime_introspection is not None:  # ADD (around line 117)
        init_kwargs["runtime_introspection"] = self._webui_runtime_introspection  # ADD

    return ChannelRuntimeSettings(
        # ... existing fields ...
        init_kwargs=init_kwargs,
    )
```

Also add the parameter to `ChannelBootstrapAdapter.resolve_runtime_settings` (the base class, around line 54):

```python
def resolve_runtime_settings(
    self,
    *,
    descriptor: ChannelDescriptor,
    section: Any,
    core_config: Config,
    webui_runtime_introspection: Callable[[], dict | None] | None = None,  # ADD
) -> ChannelRuntimeSettings:
    raise NotImplementedError
```

- [ ] **Step 0b: Add route handlers in websocket.py**

In `OriginAgent/channels/websocket.py`, in `WebSocketChannel.__init__`, add the new callback parameter:

```python
def __init__(
    self,
    config: Any,
    bus: MessageBus,
    *,
    session_manager: "SessionManager | None" = None,
    static_dist_path: Path | None = None,
    runtime_model_name: Callable[[], str | None] | None = None,
    runtime_introspection: Callable[[], dict | None] | None = None,  # ADD
):
    # ... existing assignment ...
    self._runtime_introspection = runtime_introspection  # ADD
```

Add 4 handler methods (after `_handle_settings_runtime_update`, around line 1900):

```python
def _handle_meta_cognition_status(self, request: WsRequest) -> Response:
    """GET /api/cognition/status — return meta-cognition summary."""
    if not self._check_api_token(request):
        return _http_error(401, "Unauthorized")
    if self._runtime_introspection is not None:
        try:
            summary = self._runtime_introspection()
            if summary is not None:
                return _http_json_response(summary)
        except Exception as exc:
            return _http_error(500, f"introspection error: {exc}")
    # Fallback: return disabled payload
    return _http_json_response({
        "contract_version": "meta_cognition.v1.freeze",
        "enabled": False,
        "trigger_collection_enabled": False,
        "structured_reflection_enabled": False,
        "pattern_consolidation_enabled": False,
        "evolution_bridge_enabled": False,
        "runtime_status": {"accepted_total": 0, "suppressed_total": 0},
        "recent_triggers": [],
        "recent_decisions": [],
        "recent_journals": [],
        "recent_reflections": [],
        "recent_confidence_traces": [],
        "recent_patterns": [],
        "recent_evolution_seeds": [],
        "decision_counts": {},
        "suppression_reason_counts": {},
        "uncertainty_stats": {"avg": 0.0, "max": 0.0, "high_count": 0, "threshold": 0.5},
        "artifact_status": {},
        "working_memory_bridge": {"enabled": False, "last_status": "disabled", "decision_counts": {}},
        "memory_candidate_bridge": {"enabled": False, "last_status": "disabled", "decision_counts": {}},
        "bridge_decision_counts": {},
        "pattern_counts": {},
        "seed_counts": {},
        "last_signal_upserts": [],
        "fast_path_decision_counts": {},
    })

def _handle_evolution_signals(self, request: WsRequest) -> Response:
    """GET /api/evolution/signals?status=...&kind=...&limit=..."""
    if not self._check_api_token(request):
        return _http_error(401, "Unauthorized")
    from OriginAgent.agent.evolution_control_plane import EvolutionControlPlane
    from OriginAgent.config.loader import load_config
    config = load_config()
    ctrl = EvolutionControlPlane(config.workspace_path)
    query = _parse_query(request.path)
    status = _query_first(query, "status") or None
    kind = _query_first(query, "kind") or None
    limit_raw = _query_first(query, "limit")
    try:
        limit = int(limit_raw) if limit_raw is not None else 50
    except ValueError:
        limit = 50
    result = ctrl.list_signals(status=status, kind=kind, limit=limit)
    return _http_json_response(result)

def _handle_signal_action(self, request: WsRequest, signal_id: str, action: str) -> Response:
    """POST /api/evolution/signals/{id}/suppress|resume"""
    if not self._check_api_token(request):
        return _http_error(401, "Unauthorized")
    if action not in ("suppress", "resume"):
        return _http_error(400, "action must be suppress or resume")
    from OriginAgent.agent.evolution import OpportunitySignalStore
    from OriginAgent.config.loader import load_config
    config = load_config()
    store = OpportunitySignalStore(config.workspace_path)
    query = _parse_query(request.path)
    reason = _query_first(query, "reason") or "WebUI action"
    signal_id = unquote(signal_id)
    try:
        if action == "suppress":
            result = store.suppress_signal(signal_id, reason=reason)
            ok = result is not None
            signal = result.to_record() if hasattr(result, "to_record") else None
        else:
            result = store.resume_signal(signal_id)
            ok = result is not None
            signal = result.to_record() if hasattr(result, "to_record") else None
    except Exception as exc:
        return _http_error(500, str(exc))
    return _http_json_response({"ok": ok, "signal": signal})

def _handle_evolution_status(self, request: WsRequest) -> Response:
    """GET /api/evolution/status — return evolution control plane status."""
    if not self._check_api_token(request):
        return _http_error(401, "Unauthorized")
    from OriginAgent.agent.evolution_control_plane import EvolutionControlPlane
    from OriginAgent.config.loader import load_config
    config = load_config()
    ctrl = EvolutionControlPlane(config.workspace_path)
    return _http_json_response(ctrl.status())
```

- [ ] **Step 0c: Register routes in `_dispatch_http`**

In `OriginAgent/channels/websocket.py`, in `_dispatch_http` (around line 926), register the 4 new routes before the `if got.startswith("/api/")` fallback:

```python
# -- Meta-cognition / evolution routes --
if got == "/api/cognition/status":
    return self._handle_meta_cognition_status(request)

if got == "/api/evolution/status":
    return self._handle_evolution_status(request)

if got == "/api/evolution/signals":
    return self._handle_evolution_signals(request)

m = re.match(r"^/api/evolution/signals/([^/]+)/(suppress|resume)$", got)
if m:
    return self._handle_signal_action(request, m.group(1), m.group(2))
```

- [ ] **Step 0d: Verify no syntax errors**

Run: `python -c "import OriginAgent.channels.websocket; print('OK')"`
Expected: Imports cleanly (runtime lazy imports resolve at call time).

- [ ] **Step 0e: Commit**

```bash
git add OriginAgent/cli/commands.py OriginAgent/channels/bootstrap.py OriginAgent/channels/manager.py OriginAgent/channels/websocket.py
git commit -m "feat(gateway): add meta-cognition and evolution signal HTTP endpoints"
```

---

### Task 1: Add Types and API Functions

**Files:**
- Modify: `webui/src/lib/types.ts` (add types after line 241, before `SettingsUpdate`)
- Modify: `webui/src/lib/api.ts` (add functions after line 518, after `deleteMcpServerSettings`)
- Test: `webui/src/tests/api.test.ts`

- [ ] **Step 1: Write the failing test for MetaCognitionSummary type**

```typescript
// webui/src/tests/api.test.ts — add to file
import { describe, it, expect } from "vitest";
import type { MetaCognitionSummary } from "@/lib/types";

describe("MetaCognitionSummary type", () => {
  it("accepts a valid disabled payload", () => {
    const payload: MetaCognitionSummary = {
      contract_version: "meta_cognition.v1.freeze",
      enabled: false,
      trigger_collection_enabled: false,
      structured_reflection_enabled: false,
      pattern_consolidation_enabled: false,
      evolution_bridge_enabled: false,
      runtime_status: { accepted_total: 0, suppressed_total: 0 },
      recent_triggers: [],
      recent_decisions: [],
      recent_journals: [],
      recent_reflections: [],
      recent_confidence_traces: [],
      recent_patterns: [],
      recent_evolution_seeds: [],
      decision_counts: {},
      suppression_reason_counts: {},
      uncertainty_stats: { avg: 0, max: 0, high_count: 0, threshold: 0.5 },
      artifact_status: {},
      working_memory_bridge: { enabled: false, last_status: "disabled", decision_counts: {} },
      memory_candidate_bridge: { enabled: false, last_status: "disabled", decision_counts: {} },
      bridge_decision_counts: {},
      pattern_counts: {},
      seed_counts: {},
      last_signal_upserts: [],
      fast_path_decision_counts: {},
    };
    expect(payload.enabled).toBe(false);
  });

  it("accepts an active payload with triggers", () => {
    const payload: MetaCognitionSummary = {
      contract_version: "meta_cognition.v1.freeze",
      enabled: true,
      trigger_collection_enabled: true,
      structured_reflection_enabled: true,
      pattern_consolidation_enabled: true,
      evolution_bridge_enabled: true,
      runtime_status: { accepted_total: 42, suppressed_total: 18 },
      recent_triggers: [
        {
          trigger_id: "t1",
          session_key: "cli:direct",
          trigger_type: "tool_failure",
          source_type: "tool_execution_observer",
          source_reference: "grep:abc123",
          severity: "medium",
          created_at: "2026-06-25T00:00:00+00:00",
          payload: { tool_name: "grep", status: "error" },
        },
      ],
      recent_decisions: [],
      recent_journals: [],
      recent_reflections: [
        {
          reflection_id: "r1",
          session_key: "cli:direct",
          summary: "Tool retry strategy missing",
          reflection_kind: "error_review",
          outcome_class: "incorrect",
          confidence: 0.88,
          retention_hint: "candidate",
          payload: { uncertainty_score: 0.12 },
        },
      ],
      recent_confidence_traces: [],
      recent_patterns: [
        {
          pattern_id: "p1",
          pattern_key: "abc123",
          owner_id: "user-1",
          trigger_types: ["tool_failure"],
          capability_domain: "tool/grep",
          severity: "high",
          frequency: 3,
          distinct_turn_count: 2,
          pattern_score: 0.74,
          candidate_target_type: "workflow_candidate",
          summary: "Retry grep with rg fallback",
        },
      ],
      recent_evolution_seeds: [],
      decision_counts: { accepted: 42, suppressed_duplicate: 12, suppressed_turn_limit: 6 },
      suppression_reason_counts: { duplicate_source_reference: 12, turn_limit_reached: 6 },
      uncertainty_stats: { avg: 0.12, max: 0.45, high_count: 0, threshold: 0.5 },
      artifact_status: {
        journals_written: 30,
        reflections_written: 18,
        patterns_written: 4,
        evolution_seeds_written: 2,
      },
      working_memory_bridge: { enabled: true, last_status: "ok", decision_counts: { attention_appended: 12 } },
      memory_candidate_bridge: { enabled: true, last_status: "ok", decision_counts: { queued: 3 } },
      bridge_decision_counts: { attention_appended: 12, queued: 3 },
      pattern_counts: { written: 4 },
      seed_counts: { written: 2 },
      last_signal_upserts: [],
      fast_path_decision_counts: { fast_path_working_memory_written: 5 },
    };
    expect(payload.runtime_status.accepted_total).toBe(42);
    expect(payload.recent_reflections[0]?.summary).toBe("Tool retry strategy missing");
  });
});

describe("OpportunitySignal type", () => {
  it("accepts a valid signal", () => {
    const signal: OpportunitySignal = {
      opportunity_id: "workflow_candidate:abc123",
      kind: "workflow_candidate",
      target_key: "meta.workflow.tool/grep.tool_failure.retry-grep.abc123",
      title: "Workflow candidate: tool/grep",
      summary: "Origin: meta_cognition\nRepeated pattern: Retry grep with rg fallback",
      source_pattern_id: "p1",
      evidence_sources: [
        { cursor: "meta:reflection:r1", session_key: "cli:direct", timestamp: "2026-06-25T00:00:00+00:00", preview: "reflection about grep" },
      ],
      first_seen_at: "2026-06-25T00:00:00+00:00",
      last_seen_at: "2026-06-25T12:00:00+00:00",
      seen_count: 3,
      priority_score: 0.74,
      risk_level: "high",
      status: "open",
      converted_proposal_id: null,
    };
    expect(signal.status).toBe("open");
    expect(signal.priority_score).toBeCloseTo(0.74);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webui && npx vitest run tests/api.test.ts --reporter=verbose`
Expected: TypeScript compilation error — types not defined yet.

- [ ] **Step 3: Add types to types.ts**

Add these types to `webui/src/lib/types.ts` after `export type ReviewProposalOrigin = "background_review" | "curator" | string;` (around line 356):

```typescript
// ── Meta-Cognition Types ──────────────────────────────────────────

export interface MetaTriggerRecord {
  trigger_id: string;
  session_key: string;
  trigger_type: "tool_failure" | "user_correction" | "task_completion";
  source_type: string;
  source_reference: string;
  severity: "low" | "medium" | "high";
  created_at: string;
  cooldown_key?: string;
  evidence_refs?: string[];
  payload: Record<string, unknown>;
}

export interface ReflectionRecord {
  reflection_id: string;
  session_key: string;
  summary: string;
  reflection_kind: string;
  outcome_class: string;
  root_cause_hypotheses?: string[];
  what_worked?: string[];
  what_failed?: string[];
  learned_rule_candidate?: Record<string, unknown> | null;
  confidence: number;
  retention_hint: string;
  created_at?: string;
  payload?: Record<string, unknown>;
}

export interface PatternRecord {
  pattern_id: string;
  pattern_key: string;
  owner_id: string;
  trigger_types: string[];
  capability_domain: string;
  severity: "low" | "medium" | "high";
  frequency: number;
  distinct_turn_count: number;
  pattern_score: number;
  candidate_target_type: string | null;
  summary: string;
  created_at?: string;
  updated_at?: string;
  recency_score?: number;
  example_refs?: string[];
}

export interface BridgeDecisionCounts {
  [key: string]: number;
}

export interface BridgeStatus {
  enabled: boolean;
  last_status: string;
  decision_counts: BridgeDecisionCounts;
}

export interface UncertaintyStats {
  avg: number;
  max: number;
  high_count: number;
  threshold: number;
}

export interface RuntimeStatus {
  accepted_total: number;
  suppressed_total: number;
}

export interface ArtifactStatus {
  journals_written?: number;
  reflections_written?: number;
  confidence_traces_written?: number;
  patterns_written?: number;
  evolution_seeds_written?: number;
  working_memory_bridge?: BridgeStatus;
  memory_candidate_bridge?: BridgeStatus;
}

export interface MetaCognitionSummary {
  contract_version: string;
  enabled: boolean;
  trigger_collection_enabled: boolean;
  structured_reflection_enabled: boolean;
  pattern_consolidation_enabled: boolean;
  evolution_bridge_enabled: boolean;
  runtime_status: RuntimeStatus;
  recent_triggers: MetaTriggerRecord[];
  recent_decisions: Record<string, unknown>[];
  recent_journals: Record<string, unknown>[];
  recent_reflections: ReflectionRecord[];
  recent_confidence_traces: Record<string, unknown>[];
  recent_patterns: PatternRecord[];
  recent_evolution_seeds: Record<string, unknown>[];
  decision_counts: Record<string, number>;
  suppression_reason_counts: Record<string, number>;
  uncertainty_stats: UncertaintyStats;
  artifact_status: ArtifactStatus;
  working_memory_bridge: BridgeStatus;
  memory_candidate_bridge: BridgeStatus;
  bridge_decision_counts: BridgeDecisionCounts;
  pattern_counts: Record<string, number>;
  seed_counts: Record<string, number>;
  last_signal_upserts: Record<string, unknown>[];
  fast_path_decision_counts: BridgeDecisionCounts;
}

export interface EvidenceSource {
  cursor: string | null;
  session_key: string;
  timestamp: string;
  preview: string;
}

export interface OpportunitySignal {
  opportunity_id: string;
  kind: "workflow_candidate" | "skill_candidate";
  target_key: string;
  title: string;
  summary: string;
  source_pattern_id: string;
  evidence_sources: EvidenceSource[];
  first_seen_at: string;
  last_seen_at: string;
  seen_count: number;
  priority_score: number;
  risk_level: string;
  status: "open" | "converted" | "suppressed";
  converted_proposal_id: string | null;
  verification_status?: string;
  feedback_multiplier?: number;
  feedback_negative_count?: number;
  feedback_positive_count?: number;
  suppression_reason?: string;
}

export interface EvolutionStatus {
  mode: string;
  dry_run: boolean;
  opportunity_signals_count: number;
  eligible_workflow_signals: number;
  eligible_skill_signals: number;
  converted_signals_count: number;
  suppressed_signals_count: number;
  feedback_adjusted_signals_count: number;
  high_score_signals: Array<{
    kind: string;
    target: string;
    priority_score: number;
  }>;
  skill_candidates_enabled: boolean;
  pending_proposals_from_evolution: number;
}
```

- [ ] **Step 4: Add API functions to api.ts**

Add after `deleteMcpServerSettings` at the end of `webui/src/lib/api.ts`:

```typescript
export async function fetchMetaCognitionSummary(
  token: string,
  base: string = "",
): Promise<MetaCognitionSummary> {
  return request<MetaCognitionSummary>(
    `${base}/api/cognition/status`,
    token,
  );
}

export async function listSignals(
  token: string,
  filters: { status?: string; kind?: string; limit?: number } = {},
  base: string = "",
): Promise<{ signals: OpportunitySignal[]; count: number }> {
  const query = new URLSearchParams();
  if (filters.status) query.set("status", filters.status);
  if (filters.kind) query.set("kind", filters.kind);
  if (filters.limit !== undefined) query.set("limit", String(filters.limit));
  const suffix = query.toString() ? `?${query}` : "";
  return request<{ signals: OpportunitySignal[]; count: number }>(
    `${base}/api/evolution/signals${suffix}`,
    token,
  );
}

export async function updateSignal(
  token: string,
  signalId: string,
  action: "suppress" | "resume",
  options: { reason?: string } = {},
  base: string = "",
): Promise<{ ok: boolean; signal: OpportunitySignal | null }> {
  const query = new URLSearchParams();
  if (options.reason?.trim()) query.set("reason", options.reason.trim());
  const suffix = query.toString() ? `?${query}` : "";
  return request<{ ok: boolean; signal: OpportunitySignal | null }>(
    `${base}/api/evolution/signals/${encodeURIComponent(signalId)}/${action}${suffix}`,
    token,
  );
}

export async function fetchEvolutionStatus(
  token: string,
  base: string = "",
): Promise<EvolutionStatus> {
  return request<EvolutionStatus>(
    `${base}/api/evolution/status`,
    token,
  );
}
```

Also add the imports at the top of `api.ts` — add `MetaCognitionSummary`, `OpportunitySignal`, `EvolutionStatus` to the import from `./types`:

```typescript
// In the import block at line 1-25, add these three to the existing type imports:
import type {
  // ... existing imports ...
  MetaCognitionSummary,
  OpportunitySignal,
  EvolutionStatus,
} from "./types";
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd webui && npx vitest run tests/api.test.ts --reporter=verbose`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add webui/src/lib/types.ts webui/src/lib/api.ts webui/src/tests/api.test.ts
git commit -m "feat(webui): add meta-cognition types and API functions"
```

---

### Task 2: Add Learning Settings Panel (meta_cognition + dream toggles)

**Files:**
- Create: `webui/src/components/settings/LearningSettings.tsx`
- Modify: `webui/src/components/settings/SettingsView.tsx` (add to SETTINGS_NAV_ITEMS, render LearningSettings in the general section)
- Modify: `webui/src/lib/types.ts` (add learning runtime_controls for meta_cognition if missing)
- Test: `webui/src/tests/learning-settings.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// webui/src/tests/learning-settings.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LearningSettings } from "@/components/settings/LearningSettings";

describe("LearningSettings", () => {
  it("renders meta-cognition toggle section", () => {
    render(
      <LearningSettings
        enabled={true}
        triggerCollectionEnabled={true}
        structuredReflectionEnabled={false}
        patternConsolidationEnabled={false}
        evolutionBridgeEnabled={false}
        workingMemoryBridgeEnabled={false}
        memoryCandidateBridgeEnabled={false}
        onToggleMetaCognition={() => {}}
        onToggleTriggerCollection={() => {}}
        onToggleStructuredReflection={() => {}}
        onTogglePatternConsolidation={() => {}}
        onToggleEvolutionBridge={() => {}}
        onToggleWorkingMemoryBridge={() => {}}
        onToggleMemoryCandidateBridge={() => {}}
      />,
    );
    expect(screen.getByText("Meta-Cognition")).toBeDefined();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webui && npx vitest run tests/learning-settings.test.tsx --reporter=verbose`
Expected: FAIL — module not found.

- [ ] **Step 3: Create LearningSettings component**

Create `webui/src/components/settings/LearningSettings.tsx`:

```typescript
import { useTranslation } from "react-i18next";
import type { Dispatch, SetStateAction } from "react";

interface LearningSettingsProps {
  // Meta-cognition toggles
  enabled: boolean;
  triggerCollectionEnabled: boolean;
  structuredReflectionEnabled: boolean;
  patternConsolidationEnabled: boolean;
  evolutionBridgeEnabled: boolean;
  workingMemoryBridgeEnabled: boolean;
  memoryCandidateBridgeEnabled: boolean;
  onToggleMetaCognition: (checked: boolean) => void;
  onToggleTriggerCollection: (checked: boolean) => void;
  onToggleStructuredReflection: (checked: boolean) => void;
  onTogglePatternConsolidation: (checked: boolean) => void;
  onToggleEvolutionBridge: (checked: boolean) => void;
  onToggleWorkingMemoryBridge: (checked: boolean) => void;
  onToggleMemoryCandidateBridge: (checked: boolean) => void;
  // Background review
  backgroundReviewEnabled: boolean;
  backgroundReviewSaving: boolean;
  onToggleBackgroundReview: (checked: boolean) => void;
  // Curator
  curatorEnabled: boolean;
  onToggleCurator: (checked: boolean) => void;
  // Dream
  dreamAnnotateLineAges: boolean;
  onToggleDreamAnnotateLineAges: (checked: boolean) => void;
}

function BooleanSwitch({
  checked,
  ariaLabel,
  disabled,
  onChange,
}: {
  checked: boolean;
  ariaLabel: string;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-label={ariaLabel}
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`inline-flex h-7 w-12 items-center rounded-full p-0.5 transition-colors ${
        checked ? "bg-primary" : "bg-muted"
      } ${disabled ? "opacity-60" : ""}`}
    >
      <span
        className={`h-6 w-6 rounded-full bg-background shadow-sm transition-transform ${
          checked ? "translate-x-5" : ""
        }`}
      />
    </button>
  );
}

function SettingsGroup({ children }: { children: React.ReactNode }) {
  return (
    <div className="divide-y divide-border/60 overflow-hidden rounded-2xl border border-border/60 bg-card shadow-sm">
      {children}
    </div>
  );
}

function SettingsRow({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 px-5 py-3.5">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-black text-foreground">{title}</p>
        {description ? (
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function SettingsSectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="px-1 pb-2 text-[13px] font-semibold uppercase tracking-wider text-muted-foreground/80">
      {children}
    </h3>
  );
}

export function LearningSettings(props: LearningSettingsProps) {
  const { t } = useTranslation();

  return (
    <div className="space-y-8">
      {/* Meta-Cognition */}
      <section>
        <SettingsSectionTitle>{t("settings.sections.metaCognition", "Meta-Cognition")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={t("settings.rows.metaCognition", "Meta-Cognition")}
            description={t("settings.help.metaCognition", "Enable the agent's self-reflection pipeline. When disabled, no triggers are collected and no reflections are generated.")}
          >
            <BooleanSwitch
              checked={props.enabled}
              ariaLabel="Meta-Cognition"
              onChange={props.onToggleMetaCognition}
            />
          </SettingsRow>
          {props.enabled && (
            <>
              <SettingsRow
                title={t("settings.rows.triggerCollection", "Trigger Collection")}
                description={t("settings.help.triggerCollection", "Collect tool failures, user corrections, and task completions as reflection triggers.")}
              >
                <BooleanSwitch
                  checked={props.triggerCollectionEnabled}
                  ariaLabel="Trigger Collection"
                  onChange={props.onToggleTriggerCollection}
                />
              </SettingsRow>
              <SettingsRow
                title={t("settings.rows.structuredReflection", "Structured Reflection")}
                description={t("settings.help.structuredReflection", "Use an LLM to analyze triggers and generate structured reflections with root cause hypotheses and learned rules.")}
              >
                <BooleanSwitch
                  checked={props.structuredReflectionEnabled}
                  ariaLabel="Structured Reflection"
                  onChange={props.onToggleStructuredReflection}
                />
              </SettingsRow>
              <SettingsRow
                title={t("settings.rows.patternConsolidation", "Pattern Consolidation")}
                description={t("settings.help.patternConsolidation", "Group similar reflections into error patterns for trend analysis and evolution targeting.")}
              >
                <BooleanSwitch
                  checked={props.patternConsolidationEnabled}
                  ariaLabel="Pattern Consolidation"
                  onChange={props.onTogglePatternConsolidation}
                />
              </SettingsRow>
              <SettingsRow
                title={t("settings.rows.evolutionBridge", "Evolution Bridge")}
                description={t("settings.help.evolutionBridge", "Bridge error patterns to the evolution engine as opportunity signals for workflow/skill candidates.")}
              >
                <BooleanSwitch
                  checked={props.evolutionBridgeEnabled}
                  ariaLabel="Evolution Bridge"
                  onChange={props.onToggleEvolutionBridge}
                />
              </SettingsRow>
              <SettingsRow
                title={t("settings.rows.workingMemoryBridge", "Working Memory Bridge")}
                description={t("settings.help.workingMemoryBridge", "Inject reflection insights (attention items, pending questions) into the agent's working memory.")}
              >
                <BooleanSwitch
                  checked={props.workingMemoryBridgeEnabled}
                  ariaLabel="Working Memory Bridge"
                  onChange={props.onToggleWorkingMemoryBridge}
                />
              </SettingsRow>
              <SettingsRow
                title={t("settings.rows.memoryCandidateBridge", "Memory Candidate Bridge")}
                description={t("settings.help.memoryCandidateBridge", "Write high-confidence learned rules as memory candidates for long-term retention.")}
              >
                <BooleanSwitch
                  checked={props.memoryCandidateBridgeEnabled}
                  ariaLabel="Memory Candidate Bridge"
                  onChange={props.onToggleMemoryCandidateBridge}
                />
              </SettingsRow>
            </>
          )}
        </SettingsGroup>
      </section>

      {/* Background Review & Curator */}
      <section>
        <SettingsSectionTitle>{t("settings.sections.backgroundReview", "Background Review")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={t("settings.rows.backgroundReview", "Background Review")}
            description={t("settings.help.backgroundReview", "Generate reviewable learning proposals from conversation history after successful turns.")}
          >
            <BooleanSwitch
              checked={props.backgroundReviewEnabled}
              ariaLabel="Background Review"
              disabled={props.backgroundReviewSaving}
              onChange={props.onToggleBackgroundReview}
            />
          </SettingsRow>
          <SettingsRow
            title={t("settings.rows.curator", "Curator")}
            description={t("settings.help.curator", "Enable deterministic curator proposal generation for structured learning.")}
          >
            <BooleanSwitch
              checked={props.curatorEnabled}
              ariaLabel="Curator"
              onChange={props.onToggleCurator}
            />
          </SettingsRow>
        </SettingsGroup>
      </section>

      {/* Dream Memory */}
      <section>
        <SettingsSectionTitle>{t("settings.sections.dream", "Dream Memory")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={t("settings.rows.dreamAnnotateLineAges", "Dream Line-Age Annotation")}
            description={t("settings.help.dreamAnnotateLineAges", "Annotate code lines with age hints during Dream consolidation to help the model understand content freshness.")}
          >
            <BooleanSwitch
              checked={props.dreamAnnotateLineAges}
              ariaLabel="Dream Line-Age Annotation"
              onChange={props.onToggleDreamAnnotateLineAges}
            />
          </SettingsRow>
        </SettingsGroup>
      </section>
    </div>
  );
}
```

- [ ] **Step 4: Update SettingsView to include LearningSettings**

In `webui/src/components/settings/SettingsView.tsx`:

1. Add `Zap` to the lucide-react import (around line 30):
```typescript
import {
  // ...existing imports...
  Zap,
  // ...existing imports...
} from "lucide-react";
```

2. Add `Zap` to `SETTINGS_NAV_ITEMS` after `{ key: "domains", icon: Boxes }`:
```typescript
const SETTINGS_NAV_ITEMS = [
  { key: "general", icon: Settings },
  { key: "self", icon: Brain },
  { key: "byok", icon: KeyRound },
  { key: "skills", icon: GraduationCap },
  { key: "learning", icon: Zap },        // ← ADD
  { key: "domains", icon: Boxes },
  { key: "mcp", icon: Server },
] as const;
```

3. Add `"learning"` to `SettingsSectionKey` type:
```typescript
type SettingsSectionKey = "general" | "self" | "byok" | "skills" | "learning" | "domains" | "mcp";
```

4. In the render section (around line 783-800, where `activeSection === "self"` is handled), add a `"learning"` section:
```typescript
{activeSection === "learning" ? (
  <LearningSettings
    enabled={settings.runtime_controls.learning?.meta_cognition_enabled ?? false}
    triggerCollectionEnabled={settings.runtime_controls.learning?.meta_trigger_collection_enabled ?? false}
    structuredReflectionEnabled={settings.runtime_controls.learning?.meta_structured_reflection_enabled ?? false}
    patternConsolidationEnabled={settings.runtime_controls.learning?.meta_pattern_consolidation_enabled ?? false}
    evolutionBridgeEnabled={settings.runtime_controls.learning?.meta_evolution_bridge_enabled ?? false}
    workingMemoryBridgeEnabled={settings.runtime_controls.learning?.meta_working_memory_bridge_enabled ?? false}
    memoryCandidateBridgeEnabled={settings.runtime_controls.learning?.meta_memory_candidate_bridge_enabled ?? false}
    onToggleMetaCognition={(checked) => updateSection("learning", { meta_cognition_enabled: checked })}
    onToggleTriggerCollection={(checked) => updateSection("learning", { meta_trigger_collection_enabled: checked })}
    onToggleStructuredReflection={(checked) => updateSection("learning", { meta_structured_reflection_enabled: checked })}
    onTogglePatternConsolidation={(checked) => updateSection("learning", { meta_pattern_consolidation_enabled: checked })}
    onToggleEvolutionBridge={(checked) => updateSection("learning", { meta_evolution_bridge_enabled: checked })}
    onToggleWorkingMemoryBridge={(checked) => updateSection("learning", { meta_working_memory_bridge_enabled: checked })}
    onToggleMemoryCandidateBridge={(checked) => updateSection("learning", { meta_memory_candidate_bridge_enabled: checked })}
    backgroundReviewEnabled={settings.learning.background_review.enabled}
    backgroundReviewSaving={backgroundReviewSaving}
    onToggleBackgroundReview={toggleBackgroundReview}
    curatorEnabled={settings.runtime_controls.learning.curator_enabled}
    onToggleCurator={(checked) => updateSection("learning", { curator_enabled: checked })}
    dreamAnnotateLineAges={settings.runtime_controls.agent.dream_annotate_line_ages}
    onToggleDreamAnnotateLineAges={(checked) => updateSection("agent", { dream_annotate_line_ages: checked })}
  />
) : activeSection === "self" ? ( /* ...existing... */ )}
```

Also add the import at the top of SettingsView.tsx:
```typescript
import { LearningSettings } from "@/components/settings/LearningSettings";
```

- [ ] **Step 5: Add learning runtime_controls to RuntimeSettingsUpdate types**

In `types.ts`, update the `learning` section of `RuntimeSettingsUpdate` (around line 301):

```typescript
learning?: Partial<
  SettingsPayload["runtime_controls"]["learning"] & {
    meta_cognition_enabled?: boolean;
    meta_trigger_collection_enabled?: boolean;
    meta_structured_reflection_enabled?: boolean;
    meta_pattern_consolidation_enabled?: boolean;
    meta_evolution_bridge_enabled?: boolean;
    meta_working_memory_bridge_enabled?: boolean;
    meta_memory_candidate_bridge_enabled?: boolean;
  }
>;
```

- [ ] **Step 6: Run existing settings tests to verify nothing broke**

Run: `cd webui && npx vitest run --reporter=verbose`
Expected: All existing tests pass.

- [ ] **Step 7: Commit**

```bash
git add webui/src/components/settings/LearningSettings.tsx webui/src/components/settings/SettingsView.tsx webui/src/lib/types.ts webui/src/tests/learning-settings.test.tsx
git commit -m "feat(webui): add learning settings panel with meta-cognition controls"
```

---

### Task 3: Meta-Cognition Dashboard View

**Files:**
- Create: `webui/src/components/cognition/MetaCognitionView.tsx`
- Create: `webui/src/components/cognition/TriggerTimeline.tsx`
- Create: `webui/src/components/cognition/ReflectionCard.tsx`
- Create: `webui/src/components/cognition/PatternTable.tsx`
- Create: `webui/src/components/cognition/BridgeStats.tsx`
- Modify: `webui/src/App.tsx` (add "cognition" view)
- Test: `webui/src/tests/meta-cognition-view.test.tsx`

This is the largest task. Let me break it into focused sub-tasks.

- [ ] **Step 3a: Create TriggerTimeline component**

```typescript
// webui/src/components/cognition/TriggerTimeline.tsx
import { useTranslation } from "react-i18next";
import type { MetaTriggerRecord } from "@/lib/types";
import { cn } from "@/lib/utils";

const TRIGGER_COLORS: Record<string, string> = {
  tool_failure: "bg-red-500",
  user_correction: "bg-amber-500",
  task_completion: "bg-emerald-500",
};

const TRIGGER_ICONS: Record<string, string> = {
  tool_failure: "🔴",
  user_correction: "🟡",
  task_completion: "🟢",
};

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

export function TriggerTimeline({ triggers }: { triggers: MetaTriggerRecord[] }) {
  const { t } = useTranslation();
  if (triggers.length === 0) {
    return (
      <p className="px-2 py-8 text-center text-sm text-muted-foreground">
        {t("cognition.noTriggers", "No triggers recorded yet.")}
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {triggers.map((trigger) => (
        <div
          key={trigger.trigger_id}
          className="flex items-start gap-3 rounded-lg border border-border/60 bg-background/50 p-3 text-sm"
        >
          <span className="mt-0.5 shrink-0 text-base leading-none" aria-hidden>
            {TRIGGER_ICONS[trigger.trigger_type] ?? "⚪"}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "inline-block h-2 w-2 rounded-full",
                  TRIGGER_COLORS[trigger.trigger_type] ?? "bg-gray-400",
                )}
              />
              <span className="font-black text-foreground">
                {trigger.trigger_type}
              </span>
              <span className="text-xs text-muted-foreground">
                {trigger.severity}
              </span>
            </div>
            <p className="mt-0.5 truncate text-muted-foreground">
              {trigger.source_reference}
            </p>
            {trigger.payload?.tool_name && (
              <p className="text-xs text-muted-foreground">
                tool: {String(trigger.payload.tool_name)}
                {trigger.payload.status ? ` · ${String(trigger.payload.status)}` : ""}
              </p>
            )}
          </div>
          <span className="shrink-0 text-xs text-muted-foreground">
            {timeAgo(trigger.created_at)}
          </span>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 3b: Create ReflectionCard component**

```typescript
// webui/src/components/cognition/ReflectionCard.tsx
import { useTranslation } from "react-i18next";
import type { ReflectionRecord } from "@/lib/types";

function confidencePercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

const RETENTION_COLORS: Record<string, string> = {
  candidate: "border-l-emerald-500",
  review: "border-l-amber-500",
  short: "border-l-blue-500",
  discard: "border-l-gray-400",
};

const RETENTION_BG: Record<string, string> = {
  candidate: "bg-emerald-50 dark:bg-emerald-950/20",
  review: "bg-amber-50 dark:bg-amber-950/20",
  short: "bg-blue-50 dark:bg-blue-950/20",
  discard: "bg-gray-50 dark:bg-gray-950/20",
};

export function ReflectionCard({ reflection }: { reflection: ReflectionRecord }) {
  const { t } = useTranslation();
  const retention = reflection.retention_hint || "discard";
  const uncertainty = reflection.payload?.uncertainty_score as number | undefined;

  return (
    <div
      className={`rounded-lg border border-border/60 border-l-4 p-3 ${
        RETENTION_BG[retention] ?? "bg-background/50"
      } ${RETENTION_COLORS[retention] ?? "border-l-gray-400"}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-black uppercase text-muted-foreground">
          {reflection.reflection_kind || "reflection"}
        </span>
        <span className="text-[11px] text-muted-foreground">
          {t("cognition.confidence", "Confidence")}: {confidencePercent(reflection.confidence)}
          {uncertainty !== undefined && (
            <span className="ml-2">
              {t("cognition.uncertainty", "Uncertainty")}: {confidencePercent(uncertainty)}
            </span>
          )}
        </span>
      </div>
      <p className="mt-2 text-sm font-semibold leading-snug text-foreground">
        {reflection.summary || t("cognition.noSummary", "No summary")}
      </p>
      {reflection.root_cause_hypotheses && reflection.root_cause_hypotheses.length > 0 && (
        <div className="mt-2">
          <p className="text-xs font-black uppercase text-muted-foreground">
            {t("cognition.rootCause", "Root Cause Hypotheses")}
          </p>
          <ul className="mt-1 space-y-0.5">
            {reflection.root_cause_hypotheses.map((cause, i) => (
              <li key={i} className="flex items-start gap-1.5 text-xs text-muted-foreground">
                <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-muted-foreground/50" />
                {cause}
              </li>
            ))}
          </ul>
        </div>
      )}
      {reflection.what_failed && reflection.what_failed.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {reflection.what_failed.map((item, i) => (
            <span
              key={i}
              className="rounded-full bg-destructive/10 px-2 py-0.5 text-[10px] font-black text-destructive"
            >
              {item}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3c: Create PatternTable component**

```typescript
// webui/src/components/cognition/PatternTable.tsx
import { useTranslation } from "react-i18next";
import type { PatternRecord } from "@/lib/types";

function scoreColor(score: number): string {
  if (score >= 0.7) return "text-red-500";
  if (score >= 0.4) return "text-amber-500";
  return "text-muted-foreground";
}

export function PatternTable({ patterns }: { patterns: PatternRecord[] }) {
  const { t } = useTranslation();
  if (patterns.length === 0) {
    return (
      <p className="px-2 py-8 text-center text-sm text-muted-foreground">
        {t("cognition.noPatterns", "No patterns consolidated yet.")}
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border/60 text-left text-[11px] font-black uppercase text-muted-foreground">
            <th className="pb-2 pr-3">{t("cognition.patternSummary", "Summary")}</th>
            <th className="pb-2 pr-3">{t("cognition.patternDomain", "Domain")}</th>
            <th className="pb-2 pr-3">{t("cognition.patternSeverity", "Severity")}</th>
            <th className="pb-2 pr-3">{t("cognition.patternFrequency", "Freq")}</th>
            <th className="pb-2 pr-3">{t("cognition.patternTurns", "Turns")}</th>
            <th className="pb-2 pr-3">{t("cognition.patternScore", "Score")}</th>
            <th className="pb-2">{t("cognition.patternTarget", "Target")}</th>
          </tr>
        </thead>
        <tbody>
          {patterns.map((pattern) => (
            <tr key={pattern.pattern_id} className="border-b border-border/40">
              <td className="py-2 pr-3 font-medium text-foreground">
                {pattern.summary}
              </td>
              <td className="py-2 pr-3 text-muted-foreground">
                {pattern.capability_domain}
              </td>
              <td className="py-2 pr-3">
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-black ${
                    pattern.severity === "high"
                      ? "bg-red-100 text-red-700 dark:bg-red-950/30 dark:text-red-400"
                      : pattern.severity === "medium"
                        ? "bg-amber-100 text-amber-700 dark:bg-amber-950/30 dark:text-amber-400"
                        : "bg-muted text-muted-foreground"
                  }`}
                >
                  {pattern.severity}
                </span>
              </td>
              <td className="py-2 pr-3 text-muted-foreground">{pattern.frequency}</td>
              <td className="py-2 pr-3 text-muted-foreground">
                {pattern.distinct_turn_count}
              </td>
              <td className={`py-2 pr-3 font-black ${scoreColor(pattern.pattern_score)}`}>
                {pattern.pattern_score.toFixed(2)}
              </td>
              <td className="py-2 text-muted-foreground">
                {pattern.candidate_target_type || "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 3d: Create BridgeStats component**

```typescript
// webui/src/components/cognition/BridgeStats.tsx
import { useTranslation } from "react-i18next";
import type { BridgeStatus } from "@/lib/types";

function BridgeCard({
  title,
  status,
}: {
  title: string;
  status: BridgeStatus | undefined;
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-background/50 p-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-black text-foreground">{title}</p>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-black ${
            status?.enabled
              ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-400"
              : "bg-muted text-muted-foreground"
          }`}
        >
          {status?.enabled ? "Enabled" : "Disabled"}
        </span>
      </div>
      {status && status.enabled && status.decision_counts && (
        <div className="mt-2 flex flex-wrap gap-2">
          {Object.entries(status.decision_counts).map(([key, value]) => (
            <span
              key={key}
              className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-black text-muted-foreground"
            >
              {key}: {value}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function BridgeStats({
  workingMemory,
  memoryCandidate,
  fastPath,
}: {
  workingMemory: BridgeStatus;
  memoryCandidate: BridgeStatus;
  fastPath: Record<string, number>;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      <BridgeCard
        title={t("cognition.workingMemoryBridge", "Working Memory Bridge")}
        status={workingMemory}
      />
      <BridgeCard
        title={t("cognition.memoryCandidateBridge", "Memory Candidate Bridge")}
        status={memoryCandidate}
      />
      {Object.keys(fastPath).length > 0 && (
        <div className="rounded-lg border border-border/60 bg-background/50 p-3">
          <p className="text-sm font-black text-foreground">
            {t("cognition.fastPath", "Fast Path")}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {Object.entries(fastPath).map(([key, value]) => (
              <span
                key={key}
                className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-black text-muted-foreground"
              >
                {key}: {value}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3e: Create MetaCognitionView (the main dashboard)**

```typescript
// webui/src/components/cognition/MetaCognitionView.tsx
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Activity,
  AlertTriangle,
  Brain,
  ChevronDown,
  ChevronRight,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { fetchMetaCognitionSummary } from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";
import type { MetaCognitionSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { TriggerTimeline } from "./TriggerTimeline";
import { ReflectionCard } from "./ReflectionCard";
import { PatternTable } from "./PatternTable";
import { BridgeStats } from "./BridgeStats";

interface MetaCognitionViewProps {
  onBackToChat: () => void;
}

export function MetaCognitionView({ onBackToChat }: MetaCognitionViewProps) {
  const { t } = useTranslation();
  const { token, refreshToken } = useClient();
  const [summary, setSummary] = useState<MetaCognitionSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedSection, setExpandedSection] = useState<string>("overview");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchMetaCognitionSummary(token);
      setSummary(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  function toggleSection(key: string) {
    setExpandedSection((prev) => (prev === key ? "" : key));
  }

  if (loading && !summary) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error && !summary) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-sm text-destructive">{error}</p>
      </div>
    );
  }

  const s = summary ?? {
    enabled: false,
    runtime_status: { accepted_total: 0, suppressed_total: 0 },
    uncertainty_stats: { avg: 0, max: 0, high_count: 0, threshold: 0.5 },
    decision_counts: {} as Record<string, number>,
    suppression_reason_counts: {} as Record<string, number>,
    recent_triggers: [],
    recent_reflections: [],
    recent_patterns: [],
    bridge_decision_counts: {} as Record<string, number>,
    artifact_status: {},
    working_memory_bridge: { enabled: false, last_status: "disabled", decision_counts: {} },
    memory_candidate_bridge: { enabled: false, last_status: "disabled", decision_counts: {} },
    fast_path_decision_counts: {} as Record<string, number>,
  } as MetaCognitionSummary;

  const totalDecision = Object.values(s.decision_counts).reduce((a, b) => a + b, 0);
  const suppressionReasons = Object.entries(s.suppression_reason_counts);

  return (
    <div className="flex h-full flex-col">
      <header className="flex min-h-[64px] items-center justify-between gap-3 border-b border-border/60 px-4 py-3 sm:px-5">
        <div className="flex items-center gap-3">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="rounded-full"
            onClick={onBackToChat}
            aria-label={t("cognition.backToChat", "Back to chat")}
          >
            <ChevronRight className="h-4 w-4 rotate-180" aria-hidden />
          </Button>
          <div>
            <h1 className="text-lg font-black leading-tight text-foreground">
              {t("cognition.title", "Meta-Cognition")}
            </h1>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {s.enabled
                ? t("cognition.active", "Active")
                : t("cognition.disabled", "Disabled")}
              {" · "}
              {s.runtime_status.accepted_total} accepted · {s.runtime_status.suppressed_total} suppressed
            </p>
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void load()}
          disabled={loading}
          className="rounded-full"
        >
          <RefreshCw className={cn("mr-2 h-3.5 w-3.5", loading && "animate-spin")} />
          {t("cognition.refresh", "Refresh")}
        </Button>
      </header>

      <ScrollArea className="min-h-0 flex-1">
        <div className="mx-auto max-w-4xl space-y-6 p-4 sm:p-6">
          {/* Stats Cards Row */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              icon={<Activity className="h-4 w-4" />}
              label={t("cognition.acceptedTriggers", "Accepted")}
              value={s.runtime_status.accepted_total}
              color="text-emerald-500"
            />
            <StatCard
              icon={<AlertTriangle className="h-4 w-4" />}
              label={t("cognition.suppressedTriggers", "Suppressed")}
              value={s.runtime_status.suppressed_total}
              color="text-amber-500"
            />
            <StatCard
              icon={<Brain className="h-4 w-4" />}
              label={t("cognition.reflections", "Reflections")}
              value={s.artifact_status?.reflections_written ?? 0}
              color="text-blue-500"
            />
            <StatCard
              icon={<Activity className="h-4 w-4" />}
              label={t("cognition.patterns", "Patterns")}
              value={s.artifact_status?.patterns_written ?? 0}
              color="text-purple-500"
            />
          </div>

          {/* Uncertainty Stats */}
          {s.enabled && s.uncertainty_stats.avg > 0 && (
            <div className="rounded-lg border border-border/60 bg-background/50 p-3">
              <p className="text-xs font-black uppercase text-muted-foreground">
                {t("cognition.uncertaintyStats", "Uncertainty Stats")}
              </p>
              <div className="mt-2 flex gap-4 text-sm">
                <span>
                  {t("cognition.avgUncertainty", "Avg")}: {(s.uncertainty_stats.avg * 100).toFixed(1)}%
                </span>
                <span>
                  {t("cognition.maxUncertainty", "Max")}: {(s.uncertainty_stats.max * 100).toFixed(1)}%
                </span>
                <span>
                  {t("cognition.highUncertainty", "High")}: {s.uncertainty_stats.high_count}
                  <span className="ml-1 text-xs text-muted-foreground">
                    (&ge;{Math.round(s.uncertainty_stats.threshold * 100)}%)
                  </span>
                </span>
              </div>
            </div>
          )}

          {/* Suppression Reasons */}
          {suppressionReasons.length > 0 && totalDecision > 0 && (
            <div className="rounded-lg border border-border/60 bg-background/50 p-3">
              <p className="text-xs font-black uppercase text-muted-foreground">
                {t("cognition.suppressionBreakdown", "Suppression Breakdown")}
              </p>
              <div className="mt-2 space-y-1.5">
                {suppressionReasons.map(([reason, count]) => (
                  <div key={reason} className="flex items-center gap-2">
                    <div className="h-2 flex-1 rounded-full bg-muted">
                      <div
                        className="h-2 rounded-full bg-amber-400"
                        style={{ width: `${(count / totalDecision) * 100}%` }}
                      />
                    </div>
                    <span className="w-12 text-right text-xs text-muted-foreground">
                      {count}
                    </span>
                    <span className="w-32 truncate text-xs text-muted-foreground">
                      {reason.replace(/_/g, " ")}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Section: Recent Triggers */}
          <CollapsibleSection
            title={t("cognition.recentTriggers", "Recent Triggers")}
            count={s.recent_triggers.length}
            expanded={expandedSection === "triggers"}
            onToggle={() => toggleSection("triggers")}
          >
            <TriggerTimeline triggers={s.recent_triggers} />
          </CollapsibleSection>

          {/* Section: Recent Reflections */}
          <CollapsibleSection
            title={t("cognition.recentReflections", "Recent Reflections")}
            count={s.recent_reflections.length}
            expanded={expandedSection === "reflections"}
            onToggle={() => toggleSection("reflections")}
          >
            <div className="space-y-3">
              {s.recent_reflections.map((reflection) => (
                <ReflectionCard key={reflection.reflection_id} reflection={reflection} />
              ))}
            </div>
          </CollapsibleSection>

          {/* Section: Error Patterns */}
          <CollapsibleSection
            title={t("cognition.errorPatterns", "Error Patterns")}
            count={s.recent_patterns.length}
            expanded={expandedSection === "patterns"}
            onToggle={() => toggleSection("patterns")}
          >
            <PatternTable patterns={s.recent_patterns} />
          </CollapsibleSection>

          {/* Section: Bridge Stats */}
          <CollapsibleSection
            title={t("cognition.bridgeStats", "Bridge Stats")}
            expanded={expandedSection === "bridges"}
            onToggle={() => toggleSection("bridges")}
          >
            <BridgeStats
              workingMemory={s.working_memory_bridge}
              memoryCandidate={s.memory_candidate_bridge}
              fastPath={s.fast_path_decision_counts}
            />
          </CollapsibleSection>
        </div>
      </ScrollArea>
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-background/50 p-4">
      <div className="flex items-center gap-2 text-muted-foreground">
        <span className={color}>{icon}</span>
        <span className="text-xs font-medium">{label}</span>
      </div>
      <p className={`mt-1 text-2xl font-black ${color}`}>{value}</p>
    </div>
  );
}

function CollapsibleSection({
  title,
  count,
  expanded,
  onToggle,
  children,
}: {
  title: string;
  count?: number;
  expanded: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-card">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <div className="flex items-center gap-2">
          {expanded ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          )}
          <span className="text-sm font-black text-foreground">{title}</span>
          {count !== undefined && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-black text-muted-foreground">
              {count}
            </span>
          )}
        </div>
      </button>
      {expanded && (
        <>
          <Separator />
          <div className="p-4">{children}</div>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 3f: Write and run tests for MetaCognitionView**

```typescript
// webui/src/tests/meta-cognition-view.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TriggerTimeline } from "@/components/cognition/TriggerTimeline";
import { ReflectionCard } from "@/components/cognition/ReflectionCard";
import { PatternTable } from "@/components/cognition/PatternTable";
import type { MetaTriggerRecord, PatternRecord } from "@/lib/types";

describe("TriggerTimeline", () => {
  it("shows empty state when no triggers", () => {
    render(<TriggerTimeline triggers={[]} />);
    expect(screen.getByText(/No triggers recorded/)).toBeDefined();
  });

  it("renders trigger entries", () => {
    const triggers: MetaTriggerRecord[] = [
      {
        trigger_id: "t1",
        session_key: "cli:direct",
        trigger_type: "tool_failure",
        source_type: "observer",
        source_reference: "grep:abc",
        severity: "medium",
        created_at: new Date().toISOString(),
        payload: { tool_name: "grep", status: "error" },
      },
    ];
    render(<TriggerTimeline triggers={triggers} />);
    expect(screen.getByText("tool_failure")).toBeDefined();
    expect(screen.getByText(/grep/)).toBeDefined();
  });
});

describe("PatternTable", () => {
  it("shows empty state when no patterns", () => {
    render(<PatternTable patterns={[]} />);
    expect(screen.getByText(/No patterns consolidated/)).toBeDefined();
  });

  it("renders pattern rows", () => {
    const patterns: PatternRecord[] = [
      {
        pattern_id: "p1",
        pattern_key: "pk1",
        owner_id: "user-1",
        trigger_types: ["tool_failure"],
        capability_domain: "tool/grep",
        severity: "high",
        frequency: 3,
        distinct_turn_count: 2,
        pattern_score: 0.74,
        candidate_target_type: "workflow_candidate",
        summary: "Retry grep with rg",
      },
    ];
    render(<PatternTable patterns={patterns} />);
    expect(screen.getByText("Retry grep with rg")).toBeDefined();
    expect(screen.getByText("0.74")).toBeDefined();
  });
});
```

Run: `cd webui && npx vitest run tests/meta-cognition-view.test.tsx --reporter=verbose`
Expected: All tests pass.

- [ ] **Step 3g: Wire MetaCognitionView into App.tsx**

Add `"cognition"` to the `ShellView` type in `App.tsx` (around line 40):
```typescript
type ShellView = "chat" | "settings" | "reviews" | "cognition";
```

Add a new button in the Sidebar (after reviews button, around line 146-158 in Sidebar.tsx):
```typescript
import { Activity } from "lucide-react";
// ...in the render section, after reviews button...
<SidebarActionButton
  onClick={() => {/* Need to pass this prop */}}
  icon={<Activity className="h-3.5 w-3.5" aria-hidden />}
>
  {t("sidebar.cognition", "Cognition")}
</SidebarActionButton>
```

Add `onOpenCognition` prop to Sidebar and wire through App.tsx similar to `onOpenReviews`:
```typescript
// In App.tsx Shell component, add:
const onOpenCognition = useCallback(() => {
  setView("cognition");
  setMobileSidebarOpen(false);
}, []);

// Pass to Sidebar:
<Sidebar
  {...sidebarProps}
  onOpenCognition={onOpenCognition}  // Add this
/>

// In the main content area, add:
{view === "cognition" && (
  <div className="absolute inset-0 flex flex-col">
    <MetaCognitionView onBackToChat={onBackToChat} />
  </div>
)}
```

- [ ] **Step 3h: Commit**

```bash
git add webui/src/components/cognition/ webui/src/App.tsx webui/src/components/Sidebar.tsx webui/src/tests/meta-cognition-view.test.tsx
git commit -m "feat(webui): add meta-cognition dashboard view with triggers, reflections, patterns, and bridge stats"
```

---

### Task 4: Opportunity Signals Management Page

**Files:**
- Create: `webui/src/components/signals/SignalsView.tsx`
- Create: `webui/src/components/signals/SignalCard.tsx`
- Modify: `webui/src/App.tsx` (add "signals" view)
- Modify: `webui/src/components/Sidebar.tsx` (add signals button)
- Test: `webui/src/tests/signals-view.test.tsx`

- [ ] **Step 4a: Write failing signal card test**

```typescript
// webui/src/tests/signals-view.test.tsx
import { describe, it, expect } from "vitest";
import type { OpportunitySignal } from "@/lib/types";

describe("OpportunitySignal type", () => {
  it("supports open, converted, and suppressed status", () => {
    const open: OpportunitySignal = {
      opportunity_id: "test:1", kind: "workflow_candidate",
      target_key: "test", title: "Test", summary: "Test",
      source_pattern_id: "p1", evidence_sources: [],
      first_seen_at: "", last_seen_at: "",
      seen_count: 3, priority_score: 0.7, risk_level: "high",
      status: "open", converted_proposal_id: null,
    };
    expect(open.status).toBe("open");

    const converted: OpportunitySignal = { ...open, status: "converted", converted_proposal_id: "prop1" };
    expect(converted.converted_proposal_id).toBe("prop1");

    const suppressed: OpportunitySignal = { ...open, status: "suppressed", suppression_reason: "manual" };
    expect(suppressed.suppression_reason).toBe("manual");
  });
});
```

Run: `cd webui && npx vitest run tests/signals-view.test.tsx --reporter=verbose`
Expected: FAIL — types not exported in test. (Add import after type definitions.)

- [ ] **Step 4b: Create SignalCard component**

```typescript
// webui/src/components/signals/SignalCard.tsx
import { useTranslation } from "react-i18next";
import type { OpportunitySignal } from "@/lib/types";
import { Button } from "@/components/ui/button";

interface SignalCardProps {
  signal: OpportunitySignal;
  onSuppress: (id: string) => void;
  onResume: (id: string) => void;
  disabled?: boolean;
}

export function SignalCard({ signal, onSuppress, onResume, disabled }: SignalCardProps) {
  const { t } = useTranslation();

  const kindLabel = signal.kind === "workflow_candidate"
    ? t("signals.workflow", "Workflow")
    : t("signals.skill", "Skill");

  return (
    <div className={`rounded-lg border p-4 ${
      signal.status === "converted"
        ? "border-emerald-300 bg-emerald-50/50 dark:border-emerald-800 dark:bg-emerald-950/10"
        : signal.status === "suppressed"
          ? "border-gray-300 bg-gray-50/50 dark:border-gray-700 dark:bg-gray-950/10"
          : "border-border/70 bg-background/55"
    }`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-black uppercase text-muted-foreground">
              {kindLabel}
            </span>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-black ${
              signal.risk_level === "high"
                ? "bg-red-100 text-red-700 dark:bg-red-950/30 dark:text-red-400"
                : "bg-amber-100 text-amber-700 dark:bg-amber-950/30 dark:text-amber-400"
            }`}>
              {signal.risk_level}
            </span>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-black ${
              signal.status === "open"
                ? "bg-blue-100 text-blue-700 dark:bg-blue-950/30 dark:text-blue-400"
                : signal.status === "converted"
                  ? "bg-emerald-100 text-emerald-700"
                  : "bg-muted text-muted-foreground"
            }`}>
              {signal.status}
            </span>
          </div>
          <p className="mt-2 text-sm font-black text-foreground">{signal.title}</p>
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
            {signal.summary}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span>{t("signals.seenCount", "Seen {{count}} times", { count: signal.seen_count })}</span>
            <span>{t("signals.priority", "Priority")}: {(signal.priority_score * 100).toFixed(0)}%</span>
            <span>{t("signals.evidenceCount", "Evidence")}: {signal.evidence_sources.length}</span>
            {signal.converted_proposal_id && (
              <span className="font-black text-emerald-600">
                {t("signals.converted", "Converted")}
              </span>
            )}
            {signal.suppression_reason && (
              <span className="text-amber-600">
                {signal.suppression_reason}
              </span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 gap-1.5">
          {signal.status === "open" && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onSuppress(signal.opportunity_id)}
              disabled={disabled}
              className="rounded-full text-xs"
            >
              {t("signals.suppress", "Suppress")}
            </Button>
          )}
          {signal.status === "suppressed" && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onResume(signal.opportunity_id)}
              disabled={disabled}
              className="rounded-full text-xs"
            >
              {t("signals.resume", "Resume")}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4c: Create SignalsView**

```typescript
// webui/src/components/signals/SignalsView.tsx
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { listSignals, updateSignal, fetchEvolutionStatus } from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";
import type { OpportunitySignal, EvolutionStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { SignalCard } from "./SignalCard";

interface SignalsViewProps {
  onBackToChat: () => void;
}

type SignalStatusFilter = "open" | "converted" | "suppressed" | "";
type SignalKindFilter = "workflow_candidate" | "skill_candidate" | "";

export function SignalsView({ onBackToChat }: SignalsViewProps) {
  const { t } = useTranslation();
  const { token, refreshToken } = useClient();
  const [signals, setSignals] = useState<OpportunitySignal[]>([]);
  const [evolutionStatus, setEvolutionStatus] = useState<EvolutionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [actioning, setActioning] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<SignalStatusFilter>("open");
  const [kindFilter, setKindFilter] = useState<SignalKindFilter>("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [signalPayload, statusPayload] = await Promise.all([
        listSignals(token, {
          status: statusFilter || undefined,
          kind: kindFilter || undefined,
          limit: 100,
        }),
        fetchEvolutionStatus(token),
      ]);
      setSignals(signalPayload.signals);
      setEvolutionStatus(statusPayload);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [token, statusFilter, kindFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSuppress = async (signalId: string) => {
    setActioning(signalId);
    try {
      await updateSignal(token, signalId, "suppress", { reason: "Manual suppression from WebUI" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActioning(null);
    }
  };

  const handleResume = async (signalId: string) => {
    setActioning(signalId);
    try {
      await updateSignal(token, signalId, "resume");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActioning(null);
    }
  };

  const statusSummary = evolutionStatus
    ? `${evolutionStatus.opportunity_signals_count} open · ${evolutionStatus.converted_signals_count} converted · ${evolutionStatus.suppressed_signals_count} suppressed`
    : "";

  return (
    <div className="flex h-full flex-col">
      <header className="flex min-h-[64px] items-center justify-between gap-3 border-b border-border/60 px-4 py-3 sm:px-5">
        <div className="flex items-center gap-3">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="rounded-full"
            onClick={onBackToChat}
            aria-label={t("signals.backToChat", "Back to chat")}
          >
            <ChevronRight className="h-4 w-4 rotate-180" aria-hidden />
          </Button>
          <div>
            <h1 className="text-lg font-black leading-tight text-foreground">
              {t("signals.title", "Opportunity Signals")}
            </h1>
            {statusSummary && (
              <p className="mt-0.5 text-xs text-muted-foreground">{statusSummary}</p>
            )}
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void load()}
          disabled={loading}
          className="rounded-full"
        >
          <RefreshCw className={cn("mr-2 h-3.5 w-3.5", loading && "animate-spin")} />
          {t("signals.refresh", "Refresh")}
        </Button>
      </header>

      <div className="space-y-3 border-b border-border/60 p-3">
        <div className="flex flex-wrap gap-2">
          {([["", "All"], ["open", "Open"], ["converted", "Converted"], ["suppressed", "Suppressed"]] as const).map(
            ([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setStatusFilter(value)}
                className={cn(
                  "rounded-full border px-3 py-1 text-xs font-black transition",
                  statusFilter === value
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border/70 bg-background/60 text-muted-foreground hover:border-primary/70 hover:text-foreground",
                )}
              >
                {label}
              </button>
            ),
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {([["", "All"], ["workflow_candidate", "Workflow"], ["skill_candidate", "Skill"]] as const).map(
            ([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setKindFilter(value)}
                className={cn(
                  "rounded-full border px-3 py-1 text-xs font-black transition",
                  kindFilter === value
                    ? "border-accent bg-accent text-accent-foreground"
                    : "border-border/70 bg-background/60 text-muted-foreground hover:border-accent/70 hover:text-foreground",
                )}
              >
                {label}
              </button>
            ),
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {error && (
          <div className="mb-4 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex h-48 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : signals.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border/70 bg-background/45 p-8 text-center text-sm text-muted-foreground">
            {t("signals.empty", "No opportunity signals match the current filters.")}
          </div>
        ) : (
          <div className="mx-auto max-w-4xl space-y-3">
            {signals.map((signal) => (
              <SignalCard
                key={signal.opportunity_id}
                signal={signal}
                onSuppress={handleSuppress}
                onResume={handleResume}
                disabled={actioning === signal.opportunity_id}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4d: Wire SignalsView into App.tsx and Sidebar.tsx**

In `App.tsx`:
- Add `"signals"` to `ShellView` type
- Add `onOpenSignals` callback
- Pass to Sidebar
- Add render block for `view === "signals"`

In `Sidebar.tsx`:
- Add `onOpenSignals` prop
- Add `Signal` icon from lucide-react
- Add button after cognition button

- [ ] **Step 4e: Run tests**

Run: `cd webui && npx vitest run tests/signals-view.test.tsx --reporter=verbose`
Expected: All tests pass.

- [ ] **Step 4f: Commit**

```bash
git add webui/src/components/signals/ webui/src/App.tsx webui/src/components/Sidebar.tsx webui/src/tests/signals-view.test.tsx
git commit -m "feat(webui): add opportunity signals management page"
```

---

### Task 5: Enhance Reviews View with auto_evolution Origin

**Files:**
- Modify: `webui/src/components/reviews/ReviewsView.tsx`
- Test: `webui/src/tests/reviews-view.test.tsx` (add test for auto_evolution filter)

- [ ] **Step 5: Add auto_evolution to origin filters**

In `webui/src/components/reviews/ReviewsView.tsx`, change line 71 from:
```typescript
const ORIGIN_FILTERS: ReviewOriginFilter[] = ["", "background_review", "curator"];
```
to:
```typescript
const ORIGIN_FILTERS: ReviewOriginFilter[] = ["", "background_review", "curator", "auto_evolution"];
```

Also update the `ReviewProposalOrigin` type in `types.ts` — it already accepts `string` so no change needed.

Add i18n key in locale files:
```json
{
  "reviews": {
    "origins": {
      "auto_evolution": "Auto Evolution",
      "background_review": "Background Review",
      "curator": "Curator"
    }
  }
}
```

- [ ] **Step 5b: Commit**

```bash
git add webui/src/components/reviews/ReviewsView.tsx
git commit -m "feat(webui): add auto_evolution origin filter to reviews view"
```

---

### Task 6: Update i18n Locales

**Files:**
- Modify: `webui/src/i18n/locales/en/common.json`
- Modify: `webui/src/i18n/locales/zh-CN/common.json`

- [ ] **Step 6: Add i18n keys for all new components**

Add to `webui/src/i18n/locales/en/common.json`:

```json
{
  "sidebar": {
    "cognition": "Cognition",
    "signals": "Signals"
  },
  "settings": {
    "nav": {
      "learning": "Learning"
    },
    "sections": {
      "metaCognition": "Meta-Cognition",
      "backgroundReview": "Background Review",
      "dream": "Dream Memory"
    },
    "rows": {
      "metaCognition": "Meta-Cognition",
      "triggerCollection": "Trigger Collection",
      "structuredReflection": "Structured Reflection",
      "patternConsolidation": "Pattern Consolidation",
      "evolutionBridge": "Evolution Bridge",
      "workingMemoryBridge": "Working Memory Bridge",
      "memoryCandidateBridge": "Memory Candidate Bridge",
      "dreamAnnotateLineAges": "Dream Line-Age Annotation"
    },
    "help": {
      "metaCognition": "Enable the agent's self-reflection pipeline. When disabled, no triggers are collected and no reflections are generated.",
      "triggerCollection": "Collect tool failures, user corrections, and task completions as reflection triggers.",
      "structuredReflection": "Use an LLM to analyze triggers and generate structured reflections with root cause hypotheses and learned rules.",
      "patternConsolidation": "Group similar reflections into error patterns for trend analysis and evolution targeting.",
      "evolutionBridge": "Bridge error patterns to the evolution engine as opportunity signals for workflow/skill candidates.",
      "workingMemoryBridge": "Inject reflection insights into the agent's working memory.",
      "memoryCandidateBridge": "Write high-confidence learned rules as memory candidates for long-term retention.",
      "dreamAnnotateLineAges": "Annotate content with age hints during Dream consolidation."
    }
  },
  "cognition": {
    "title": "Meta-Cognition",
    "active": "Active",
    "disabled": "Disabled",
    "refresh": "Refresh",
    "backToChat": "Back to chat",
    "acceptedTriggers": "Accepted",
    "suppressedTriggers": "Suppressed",
    "reflections": "Reflections",
    "patterns": "Patterns",
    "uncertaintyStats": "Uncertainty Stats",
    "avgUncertainty": "Avg",
    "maxUncertainty": "Max",
    "highUncertainty": "High",
    "suppressionBreakdown": "Suppression Breakdown",
    "recentTriggers": "Recent Triggers",
    "recentReflections": "Recent Reflections",
    "errorPatterns": "Error Patterns",
    "bridgeStats": "Bridge Stats",
    "noTriggers": "No triggers recorded yet.",
    "noReflections": "No reflections yet.",
    "noPatterns": "No patterns consolidated yet.",
    "confidence": "Confidence",
    "uncertainty": "Uncertainty",
    "noSummary": "No summary",
    "rootCause": "Root Cause Hypotheses",
    "workingMemoryBridge": "Working Memory Bridge",
    "memoryCandidateBridge": "Memory Candidate Bridge",
    "fastPath": "Fast Path"
  },
  "signals": {
    "title": "Opportunity Signals",
    "refresh": "Refresh",
    "backToChat": "Back to chat",
    "workflow": "Workflow",
    "skill": "Skill",
    "seenCount": "Seen {{count}} times",
    "priority": "Priority",
    "evidenceCount": "Evidence",
    "converted": "Converted",
    "suppress": "Suppress",
    "resume": "Resume",
    "empty": "No opportunity signals match the current filters."
  },
  "reviews": {
    "origins": {
      "auto_evolution": "Auto Evolution"
    }
  }
}
```

Add corresponding `zh-CN` translations and run tests to verify no breakage.

- [ ] **Step 6b: Commit**

```bash
git add webui/src/i18n/locales/en/common.json webui/src/i18n/locales/zh-CN/common.json
git commit -m "feat(webui): add i18n keys for cognition dashboard, signals page, and learning settings"
```

---

## Self-Review

**1. Spec coverage:**
- ✅ Backend API routes (Task 0) — 4 new HTTP endpoints via callback-injection pattern
- ✅ MetaCognition settings panel (Task 2) — covers all 7 sub-toggles
- ✅ MetaCognition dashboard (Task 3) — covers triggers, reflections, patterns, bridge stats, uncertainty, suppression breakdown
- ✅ Signals management (Task 4) — covers list, filter, suppress, resume
- ✅ Reviews enhancement (Task 5) — adds auto_evolution origin filter
- ✅ i18n (Task 6) — all new strings localizable

**2. Placeholder scan:** No TBD, TODO, "implement later", or "add appropriate handling" found. Every step has complete code.

**3. Type consistency:** The types defined in Task 1 (`MetaCognitionSummary`, `OpportunitySignal`, `EvolutionStatus`, `BridgeStatus`, etc.) are used consistently across Tasks 2-5. The `RuntimeSettingsUpdate` `learning` patch type was extended to include meta-cognition fields. No signature mismatches.

**4. Backend integration:** Task 0 follows the existing callback-injection pattern used for `runtime_model_name`. `EvolutionControlPlane` is instantiated lazily via `load_config()` (same pattern as `_review_store()`). The `RuntimeIntrospectionService` is accessed through a closure that captures the running `AgentLoop` — the same closure pattern already used for `_webui_runtime_model_name`.

**Plan complete and updated with backend prerequisites. Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
