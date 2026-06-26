import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SignalCard } from "@/components/signals/SignalCard";
import type { OpportunitySignal } from "@/lib/types";

function makeSignal(
  overrides: Partial<OpportunitySignal> = {},
): OpportunitySignal {
  return {
    opportunity_id: "workflow_candidate:abc123",
    kind: "workflow_candidate",
    target_key: "meta.workflow.tool/grep.tool_failure.retry-grep.abc123",
    title: "Workflow candidate: tool/grep",
    summary: "Origin: meta_cognition\nRepeated pattern: Retry grep with rg fallback",
    source_pattern_id: "p1",
    evidence_sources: [
      {
        cursor: "meta:reflection:r1",
        session_key: "cli:direct",
        timestamp: "2026-06-25T00:00:00+00:00",
        preview: "reflection about grep",
      },
    ],
    first_seen_at: "2026-06-25T00:00:00+00:00",
    last_seen_at: "2026-06-25T12:00:00+00:00",
    seen_count: 3,
    priority_score: 0.74,
    risk_level: "high",
    status: "open",
    converted_proposal_id: null,
    ...overrides,
  };
}

describe("SignalCard", () => {
  it("renders signal title, summary, and metadata", () => {
    const signal = makeSignal();
    render(
      <SignalCard signal={signal} onSuppress={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByText("Workflow candidate: tool/grep")).toBeInTheDocument();
    expect(screen.getByText(/Retry grep with rg fallback/)).toBeInTheDocument();
    expect(screen.getByText("Seen 3 times")).toBeInTheDocument();
    expect(screen.getByText("Priority: 74%")).toBeInTheDocument();
    expect(screen.getByText("Evidence: 1")).toBeInTheDocument();
  });

  it("shows Workflow kind label", () => {
    const signal = makeSignal({ kind: "workflow_candidate" });
    render(
      <SignalCard signal={signal} onSuppress={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByText("Workflow")).toBeInTheDocument();
  });

  it("shows Skill kind label", () => {
    const signal = makeSignal({ kind: "skill_candidate" });
    render(
      <SignalCard signal={signal} onSuppress={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByText("Skill")).toBeInTheDocument();
  });

  it("shows suppress button for open signals", () => {
    const signal = makeSignal({ status: "open" });
    render(
      <SignalCard signal={signal} onSuppress={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Suppress" })).toBeInTheDocument();
  });

  it("shows resume button for suppressed signals", () => {
    const signal = makeSignal({
      status: "suppressed",
      suppression_reason: "manual",
    });
    render(
      <SignalCard signal={signal} onSuppress={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(screen.getByText("manual")).toBeInTheDocument();
  });

  it("shows no action button for converted signals", () => {
    const signal = makeSignal({
      status: "converted",
      converted_proposal_id: "prop1",
    });
    render(
      <SignalCard signal={signal} onSuppress={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "Suppress" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    expect(screen.getByText("Converted")).toBeInTheDocument();
  });

  it("calls onSuppress when suppress is confirmed", async () => {
    const onSuppress = vi.fn();
    const signal = makeSignal({ status: "open" });
    render(
      <SignalCard signal={signal} onSuppress={onSuppress} onResume={vi.fn()} />,
    );
    // Click the first "Suppress" button to open the confirmation dialog
    fireEvent.click(screen.getByRole("button", { name: "Suppress" }));
    // Now there are two "Suppress" buttons: one in the card (hidden behind dialog) and one in the AlertDialog
    const buttons = screen.getAllByText("Suppress");
    // Click the AlertDialog confirm button (the second one)
    fireEvent.click(buttons[1]);
    expect(onSuppress).toHaveBeenCalledWith("workflow_candidate:abc123");
  });

  it("calls onResume when resume button clicked", () => {
    const onResume = vi.fn();
    const signal = makeSignal({ status: "suppressed" });
    render(
      <SignalCard signal={signal} onSuppress={vi.fn()} onResume={onResume} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(onResume).toHaveBeenCalledWith("workflow_candidate:abc123");
  });

  it("disables buttons when disabled prop is true", () => {
    const signal = makeSignal({ status: "open" });
    render(
      <SignalCard signal={signal} onSuppress={vi.fn()} onResume={vi.fn()} disabled />,
    );
    expect(screen.getByRole("button", { name: "Suppress" })).toBeDisabled();
  });

  it("renders risk_level badge", () => {
    const highSignal = makeSignal({ risk_level: "high" });
    const { unmount } = render(
      <SignalCard signal={highSignal} onSuppress={vi.fn()} onResume={vi.fn()} />,
    );
    // "high" appears as both risk_level badge and status badge — should find at least one
    const highBadges = screen.getAllByText("high");
    expect(highBadges.length).toBeGreaterThanOrEqual(1);
    unmount();

    render(
      <SignalCard
        signal={makeSignal({ risk_level: "medium" })}
        onSuppress={vi.fn()}
        onResume={vi.fn()}
      />,
    );
    // "medium" should appear as the risk badge
    expect(screen.getByText("medium")).toBeInTheDocument();
  });
});

describe("OpportunitySignal type", () => {
  it("accepts a valid open workflow_candidate signal", () => {
    const signal: OpportunitySignal = makeSignal();
    expect(signal.status).toBe("open");
    expect(signal.kind).toBe("workflow_candidate");
    expect(signal.priority_score).toBeCloseTo(0.74);
    expect(signal.converted_proposal_id).toBeNull();
  });

  it("accepts a converted signal with proposal id", () => {
    const signal: OpportunitySignal = makeSignal({
      status: "converted",
      converted_proposal_id: "prop-001",
    });
    expect(signal.status).toBe("converted");
    expect(signal.converted_proposal_id).toBe("prop-001");
  });

  it("accepts a suppressed signal with reason", () => {
    const signal: OpportunitySignal = makeSignal({
      status: "suppressed",
      suppression_reason: "Low priority — user dismissed",
    });
    expect(signal.status).toBe("suppressed");
    expect(signal.suppression_reason).toBe("Low priority — user dismissed");
  });

  it("accepts a skill_candidate signal", () => {
    const signal: OpportunitySignal = makeSignal({
      kind: "skill_candidate",
      opportunity_id: "skill_candidate:xyz",
    });
    expect(signal.kind).toBe("skill_candidate");
  });
});
