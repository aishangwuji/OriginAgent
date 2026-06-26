import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LearningSettings } from "@/components/settings/LearningSettings";

function defaultProps() {
  return {
    enabled: false,
    triggerCollectionEnabled: false,
    structuredReflectionEnabled: false,
    patternConsolidationEnabled: false,
    evolutionBridgeEnabled: false,
    workingMemoryBridgeEnabled: false,
    memoryCandidateBridgeEnabled: false,
    onToggleMetaCognition: vi.fn(),
    onToggleTriggerCollection: vi.fn(),
    onToggleStructuredReflection: vi.fn(),
    onTogglePatternConsolidation: vi.fn(),
    onToggleEvolutionBridge: vi.fn(),
    onToggleWorkingMemoryBridge: vi.fn(),
    onToggleMemoryCandidateBridge: vi.fn(),
    backgroundReviewEnabled: true,
    backgroundReviewSaving: false,
    onToggleBackgroundReview: vi.fn(),
    curatorEnabled: false,
    onToggleCurator: vi.fn(),
    dreamAnnotateLineAges: false,
    onToggleDreamAnnotateLineAges: vi.fn(),
  };
}

describe("LearningSettings", () => {
  it("renders all three major sections", () => {
    render(<LearningSettings {...defaultProps()} />);
    // "Meta-Cognition" appears as both section heading and toggle label — use getAllByText
    expect(screen.getAllByText("Meta-Cognition").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Background Review")).toBeInTheDocument();
    expect(screen.getByText("Dream Memory")).toBeInTheDocument();
  });

  it("shows meta-cognition master toggle", () => {
    render(<LearningSettings {...defaultProps()} />);
    const switches = screen.getAllByRole("switch");
    const metaToggle = switches.find(
      (s) => s.getAttribute("aria-label") === "Meta-Cognition",
    );
    expect(metaToggle).toBeInTheDocument();
    expect(metaToggle).toHaveAttribute("aria-checked", "false");
  });

  it("hides sub-toggles when meta-cognition is disabled", () => {
    render(<LearningSettings {...defaultProps()} enabled={false} />);
    expect(screen.queryByText("Trigger Collection")).not.toBeInTheDocument();
    expect(screen.queryByText("Structured Reflection")).not.toBeInTheDocument();
    expect(screen.queryByText("Pattern Consolidation")).not.toBeInTheDocument();
    expect(screen.queryByText("Evolution Bridge")).not.toBeInTheDocument();
    expect(screen.queryByText("Working Memory Bridge")).not.toBeInTheDocument();
    expect(screen.queryByText("Memory Candidate Bridge")).not.toBeInTheDocument();
  });

  it("shows sub-toggles when meta-cognition is enabled", () => {
    render(<LearningSettings {...defaultProps()} enabled={true} />);
    expect(screen.getByText("Trigger Collection")).toBeInTheDocument();
    expect(screen.getByText("Structured Reflection")).toBeInTheDocument();
    expect(screen.getByText("Pattern Consolidation")).toBeInTheDocument();
    expect(screen.getByText("Evolution Bridge")).toBeInTheDocument();
    expect(screen.getByText("Working Memory Bridge")).toBeInTheDocument();
    expect(screen.getByText("Memory Candidate Bridge")).toBeInTheDocument();
  });

  it("calls onToggleMetaCognition when master switch is clicked", () => {
    const onToggle = vi.fn();
    render(
      <LearningSettings {...defaultProps()} onToggleMetaCognition={onToggle} />,
    );
    const switch_ = screen.getByRole("switch", { name: "Meta-Cognition" });
    fireEvent.click(switch_);
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it("calls onToggleTriggerCollection when sub-toggle is clicked", () => {
    const onToggle = vi.fn();
    render(
      <LearningSettings
        {...defaultProps()}
        enabled={true}
        onToggleTriggerCollection={onToggle}
      />,
    );
    const switch_ = screen.getByRole("switch", { name: "Trigger Collection" });
    fireEvent.click(switch_);
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it("calls onToggleStructuredReflection when sub-toggle is clicked", () => {
    const onToggle = vi.fn();
    render(
      <LearningSettings
        {...defaultProps()}
        enabled={true}
        onToggleStructuredReflection={onToggle}
      />,
    );
    fireEvent.click(
      screen.getByRole("switch", { name: "Structured Reflection" }),
    );
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it("calls onTogglePatternConsolidation when sub-toggle is clicked", () => {
    const onToggle = vi.fn();
    render(
      <LearningSettings
        {...defaultProps()}
        enabled={true}
        onTogglePatternConsolidation={onToggle}
      />,
    );
    fireEvent.click(
      screen.getByRole("switch", { name: "Pattern Consolidation" }),
    );
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it("calls onToggleEvolutionBridge when sub-toggle is clicked", () => {
    const onToggle = vi.fn();
    render(
      <LearningSettings
        {...defaultProps()}
        enabled={true}
        onToggleEvolutionBridge={onToggle}
      />,
    );
    fireEvent.click(screen.getByRole("switch", { name: "Evolution Bridge" }));
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it("calls onToggleWorkingMemoryBridge when sub-toggle is clicked", () => {
    const onToggle = vi.fn();
    render(
      <LearningSettings
        {...defaultProps()}
        enabled={true}
        onToggleWorkingMemoryBridge={onToggle}
      />,
    );
    fireEvent.click(
      screen.getByRole("switch", { name: "Working Memory Bridge" }),
    );
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it("calls onToggleMemoryCandidateBridge when sub-toggle is clicked", () => {
    const onToggle = vi.fn();
    render(
      <LearningSettings
        {...defaultProps()}
        enabled={true}
        onToggleMemoryCandidateBridge={onToggle}
      />,
    );
    fireEvent.click(
      screen.getByRole("switch", { name: "Memory Candidate Bridge" }),
    );
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it("renders background review toggle with disabled state when saving", () => {
    render(
      <LearningSettings
        {...defaultProps()}
        backgroundReviewEnabled={false}
        backgroundReviewSaving={true}
      />,
    );
    const bgSwitch = screen.getByRole("switch", {
      name: "Background Review",
    });
    expect(bgSwitch).toBeDisabled();
  });

  it("calls onToggleBackgroundReview when clicked", () => {
    const onToggle = vi.fn();
    render(
      <LearningSettings
        {...defaultProps()}
        backgroundReviewEnabled={true}
        onToggleBackgroundReview={onToggle}
      />,
    );
    fireEvent.click(
      screen.getByRole("switch", { name: "Background Review" }),
    );
    expect(onToggle).toHaveBeenCalledWith(false);
  });

  it("calls onToggleCurator when curator switch is clicked", () => {
    const onToggle = vi.fn();
    render(
      <LearningSettings
        {...defaultProps()}
        curatorEnabled={false}
        onToggleCurator={onToggle}
      />,
    );
    fireEvent.click(screen.getByRole("switch", { name: "Curator" }));
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it("calls onToggleDreamAnnotateLineAges when switch is clicked", () => {
    const onToggle = vi.fn();
    render(
      <LearningSettings
        {...defaultProps()}
        dreamAnnotateLineAges={false}
        onToggleDreamAnnotateLineAges={onToggle}
      />,
    );
    fireEvent.click(
      screen.getByRole("switch", { name: "Dream Line-Age Annotation" }),
    );
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it("reflects checked state for all toggles when enabled", () => {
    render(
      <LearningSettings
        {...defaultProps()}
        enabled={true}
        triggerCollectionEnabled={true}
        structuredReflectionEnabled={false}
        patternConsolidationEnabled={true}
        evolutionBridgeEnabled={false}
        workingMemoryBridgeEnabled={true}
        memoryCandidateBridgeEnabled={false}
        backgroundReviewEnabled={true}
        curatorEnabled={true}
        dreamAnnotateLineAges={true}
      />,
    );
    expect(
      screen.getByRole("switch", { name: "Meta-Cognition" }),
    ).toHaveAttribute("aria-checked", "true");
    expect(
      screen.getByRole("switch", { name: "Trigger Collection" }),
    ).toHaveAttribute("aria-checked", "true");
    expect(
      screen.getByRole("switch", { name: "Structured Reflection" }),
    ).toHaveAttribute("aria-checked", "false");
    expect(
      screen.getByRole("switch", { name: "Pattern Consolidation" }),
    ).toHaveAttribute("aria-checked", "true");
    expect(
      screen.getByRole("switch", { name: "Evolution Bridge" }),
    ).toHaveAttribute("aria-checked", "false");
    expect(
      screen.getByRole("switch", { name: "Working Memory Bridge" }),
    ).toHaveAttribute("aria-checked", "true");
    expect(
      screen.getByRole("switch", { name: "Memory Candidate Bridge" }),
    ).toHaveAttribute("aria-checked", "false");
    expect(
      screen.getByRole("switch", { name: "Background Review" }),
    ).toHaveAttribute("aria-checked", "true");
    expect(
      screen.getByRole("switch", { name: "Curator" }),
    ).toHaveAttribute("aria-checked", "true");
    expect(
      screen.getByRole("switch", { name: "Dream Line-Age Annotation" }),
    ).toHaveAttribute("aria-checked", "true");
  });
});
