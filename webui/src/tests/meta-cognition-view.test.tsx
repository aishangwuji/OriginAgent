import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TriggerTimeline } from "@/components/cognition/TriggerTimeline";
import { ReflectionCard } from "@/components/cognition/ReflectionCard";
import { PatternTable } from "@/components/cognition/PatternTable";
import { BridgeStats } from "@/components/cognition/BridgeStats";
import type { MetaTriggerRecord, PatternRecord, ReflectionRecord } from "@/lib/types";

describe("TriggerTimeline", () => {
  it("shows empty state when no triggers", () => {
    render(<TriggerTimeline triggers={[]} />);
    expect(screen.getByText("No triggers recorded yet.")).toBeInTheDocument();
  });

  it("renders trigger entries with type and severity", () => {
    const triggers: MetaTriggerRecord[] = [
      {
        trigger_id: "t1",
        session_key: "cli:direct",
        trigger_type: "tool_failure",
        source_type: "tool_execution_observer",
        source_reference: "grep:abc123",
        severity: "medium",
        created_at: new Date().toISOString(),
        payload: { tool_name: "grep", status: "error" },
      },
    ];
    render(<TriggerTimeline triggers={triggers} />);
    expect(screen.getByText("tool_failure")).toBeInTheDocument();
    expect(screen.getByText("medium")).toBeInTheDocument();
    expect(screen.getByText("grep:abc123")).toBeInTheDocument();
  });

  it("renders tool name and status from payload", () => {
    const triggers: MetaTriggerRecord[] = [
      {
        trigger_id: "t2",
        session_key: "cli:direct",
        trigger_type: "tool_failure",
        source_type: "tool_execution_observer",
        source_reference: "bash:def456",
        severity: "high",
        created_at: new Date().toISOString(),
        payload: { tool_name: "bash", status: "timeout" },
      },
    ];
    render(<TriggerTimeline triggers={triggers} />);
    expect(screen.getByText(/tool: bash/)).toBeInTheDocument();
    expect(screen.getByText(/timeout/)).toBeInTheDocument();
  });

  it("shows time ago text for each trigger", () => {
    const now = new Date();
    const triggers: MetaTriggerRecord[] = [
      {
        trigger_id: "t3",
        session_key: "cli:direct",
        trigger_type: "task_completion",
        source_type: "observer",
        source_reference: "task:done",
        severity: "low",
        created_at: new Date(now.getTime() - 30 * 1000).toISOString(), // 30s ago
        payload: {},
      },
    ];
    render(<TriggerTimeline triggers={triggers} />);
    expect(screen.getByText("30s ago")).toBeInTheDocument();
  });

  it("handles user_correction trigger type", () => {
    const triggers: MetaTriggerRecord[] = [
      {
        trigger_id: "t4",
        session_key: "cli:direct",
        trigger_type: "user_correction",
        source_type: "observer",
        source_reference: "user:fix",
        severity: "low",
        created_at: new Date().toISOString(),
        payload: {},
      },
    ];
    render(<TriggerTimeline triggers={triggers} />);
    expect(screen.getByText("user_correction")).toBeInTheDocument();
  });
});

describe("ReflectionCard", () => {
  it("renders reflection summary and confidence", () => {
    const reflection: ReflectionRecord = {
      reflection_id: "r1",
      session_key: "cli:direct",
      summary: "Tool retry strategy missing",
      reflection_kind: "error_review",
      outcome_class: "incorrect",
      confidence: 0.88,
      retention_hint: "candidate",
      payload: { uncertainty_score: 0.12 },
    };
    render(<ReflectionCard reflection={reflection} />);
    expect(screen.getByText("Tool retry strategy missing")).toBeInTheDocument();
    expect(screen.getByText(/Confidence: 88%/)).toBeInTheDocument();
    // uncertainty should display when present
    expect(screen.getByText(/Uncertainty: 12%/)).toBeInTheDocument();
  });

  it("renders root cause hypotheses when present", () => {
    const reflection: ReflectionRecord = {
      reflection_id: "r2",
      session_key: "cli:direct",
      summary: "Bash sandbox timeout",
      reflection_kind: "error_review",
      outcome_class: "incorrect",
      confidence: 0.75,
      retention_hint: "review",
      root_cause_hypotheses: ["Timeout too short", "No retry logic"],
      payload: {},
    };
    render(<ReflectionCard reflection={reflection} />);
    expect(screen.getByText("Root Cause Hypotheses")).toBeInTheDocument();
    expect(screen.getByText("Timeout too short")).toBeInTheDocument();
    expect(screen.getByText("No retry logic")).toBeInTheDocument();
  });

  it("renders what_failed tags", () => {
    const reflection: ReflectionRecord = {
      reflection_id: "r3",
      session_key: "cli:direct",
      summary: "Search failed",
      reflection_kind: "error_review",
      outcome_class: "incorrect",
      confidence: 0.5,
      retention_hint: "short",
      what_failed: ["web_search", "fetch"],
      payload: {},
    };
    render(<ReflectionCard reflection={reflection} />);
    expect(screen.getByText("web_search")).toBeInTheDocument();
    expect(screen.getByText("fetch")).toBeInTheDocument();
  });

  it("renders without uncertainty when not in payload", () => {
    const reflection: ReflectionRecord = {
      reflection_id: "r4",
      session_key: "cli:direct",
      summary: "Simple note",
      reflection_kind: "observation",
      outcome_class: "correct",
      confidence: 0.95,
      retention_hint: "discard",
      payload: {},
    };
    render(<ReflectionCard reflection={reflection} />);
    expect(screen.getByText("Simple note")).toBeInTheDocument();
    expect(screen.getByText(/Confidence: 95%/)).toBeInTheDocument();
    // No uncertainty text should appear
    expect(screen.queryByText(/Uncertainty:/)).not.toBeInTheDocument();
  });

  it("shows reflection kind badge", () => {
    const reflection: ReflectionRecord = {
      reflection_id: "r5",
      session_key: "cli:direct",
      summary: "Test",
      reflection_kind: "error_review",
      outcome_class: "incorrect",
      confidence: 0.9,
      retention_hint: "candidate",
      payload: {},
    };
    render(<ReflectionCard reflection={reflection} />);
    expect(screen.getByText("error_review")).toBeInTheDocument();
  });

  it("falls back to 'reflection' kind when kind is empty", () => {
    const reflection: ReflectionRecord = {
      reflection_id: "r6",
      session_key: "cli:direct",
      summary: "Fallback test",
      reflection_kind: "",
      outcome_class: "correct",
      confidence: 1.0,
      retention_hint: "discard",
      payload: {},
    };
    render(<ReflectionCard reflection={reflection} />);
    expect(screen.getByText("reflection")).toBeInTheDocument();
  });
});

describe("PatternTable", () => {
  it("shows empty state when no patterns", () => {
    render(<PatternTable patterns={[]} />);
    expect(screen.getByText("No patterns consolidated yet.")).toBeInTheDocument();
  });

  it("renders pattern rows with all columns", () => {
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
        summary: "Retry grep with rg fallback",
      },
    ];
    render(<PatternTable patterns={patterns} />);
    expect(screen.getByText("Retry grep with rg fallback")).toBeInTheDocument();
    expect(screen.getByText("0.74")).toBeInTheDocument();
    expect(screen.getByText("tool/grep")).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("workflow_candidate")).toBeInTheDocument();
  });

  it("renders multiple patterns", () => {
    const patterns: PatternRecord[] = [
      {
        pattern_id: "p1",
        pattern_key: "pk1",
        owner_id: "user-1",
        trigger_types: ["tool_failure"],
        capability_domain: "tool/bash",
        severity: "medium",
        frequency: 5,
        distinct_turn_count: 3,
        pattern_score: 0.55,
        candidate_target_type: "workflow_candidate",
        summary: "Bash timeout pattern",
      },
      {
        pattern_id: "p2",
        pattern_key: "pk2",
        owner_id: "user-1",
        trigger_types: ["user_correction"],
        capability_domain: "tool/search",
        severity: "low",
        frequency: 1,
        distinct_turn_count: 1,
        pattern_score: 0.15,
        candidate_target_type: null,
        summary: "Search query refinement",
      },
    ];
    render(<PatternTable patterns={patterns} />);
    expect(screen.getByText("Bash timeout pattern")).toBeInTheDocument();
    expect(screen.getByText("Search query refinement")).toBeInTheDocument();
  });

  it("shows dash for null candidate_target_type", () => {
    const patterns: PatternRecord[] = [
      {
        pattern_id: "p3",
        pattern_key: "pk3",
        owner_id: "user-1",
        trigger_types: ["tool_failure"],
        capability_domain: "tool/unknown",
        severity: "low",
        frequency: 1,
        distinct_turn_count: 1,
        pattern_score: 0.05,
        candidate_target_type: null,
        summary: "Unknown pattern",
      },
    ];
    render(<PatternTable patterns={patterns} />);
    expect(screen.getByText("-")).toBeInTheDocument();
  });
});

describe("BridgeStats", () => {
  it("renders working memory and memory candidate bridge cards", () => {
    render(
      <BridgeStats
        workingMemory={{
          enabled: true,
          last_status: "ok",
          decision_counts: { attention_appended: 5 },
        }}
        memoryCandidate={{
          enabled: false,
          last_status: "disabled",
          decision_counts: {},
        }}
        fastPath={{ fast_path_working_memory_written: 3 }}
      />,
    );
    expect(screen.getByText("Working Memory Bridge")).toBeInTheDocument();
    expect(screen.getByText("Memory Candidate Bridge")).toBeInTheDocument();
    expect(screen.getByText("Fast Path")).toBeInTheDocument();
  });

  it("shows decision counts in working memory bridge", () => {
    render(
      <BridgeStats
        workingMemory={{
          enabled: true,
          last_status: "ok",
          decision_counts: { attention_appended: 12, pending_questions: 3 },
        }}
        memoryCandidate={{
          enabled: true,
          last_status: "ok",
          decision_counts: { queued: 2 },
        }}
        fastPath={{}}
      />,
    );
    expect(screen.getByText("attention_appended: 12")).toBeInTheDocument();
    expect(screen.getByText("pending_questions: 3")).toBeInTheDocument();
  });

  it("hides fast path section when empty", () => {
    render(
      <BridgeStats
        workingMemory={{ enabled: false, last_status: "disabled", decision_counts: {} }}
        memoryCandidate={{ enabled: false, last_status: "disabled", decision_counts: {} }}
        fastPath={{}}
      />,
    );
    expect(screen.queryByText("Fast Path")).not.toBeInTheDocument();
  });
});
