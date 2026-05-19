import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { countToolCalls } from "@/components/thread/AgentActivityCluster";
import { buildDisplayUnits, ThreadMessages } from "@/components/thread/ThreadMessages";
import type { UIMessage } from "@/lib/types";

describe("ThreadMessages", () => {
  it("builds display units around consecutive activity rows", () => {
    const messages: UIMessage[] = [
      {
        id: "r1",
        role: "assistant",
        content: "",
        reasoning: "thinking",
        createdAt: 1,
      },
      {
        id: "t1",
        role: "tool",
        kind: "trace",
        content: "search()",
        traces: ["search()"],
        createdAt: 2,
      },
      {
        id: "a1",
        role: "assistant",
        content: "answer",
        createdAt: 3,
      },
    ];

    expect(buildDisplayUnits(messages).map((unit) => unit.type)).toEqual([
      "cluster",
      "single",
    ]);
  });

  it("counts structured tool events by unique call id", () => {
    const messages: UIMessage[] = [
      {
        id: "t1",
        role: "tool",
        kind: "trace",
        content: "read_file()",
        traces: ["read_file()"],
        toolEvents: [
          { phase: "start", call_id: "call_1", name: "read_file", arguments: { path: "a" } },
          { phase: "end", call_id: "call_1", name: "read_file", arguments: { path: "a" } },
        ],
        createdAt: 1,
      },
      {
        id: "t2",
        role: "tool",
        kind: "trace",
        content: "web_search()",
        traces: ["web_search()"],
        toolEvents: [
          { phase: "start", call_id: "call_2", name: "web_search", arguments: { query: "x" } },
        ],
        createdAt: 2,
      },
    ];

    expect(countToolCalls(messages)).toBe(2);
  });

  it("groups consecutive reasoning and tool rows into one cluster before the answer", () => {
    const messages: UIMessage[] = [
      {
        id: "r1",
        role: "assistant",
        content: "",
        reasoning: "thinking",
        reasoningStreaming: false,
        isStreaming: true,
        createdAt: Date.now(),
      },
      {
        id: "t1",
        role: "tool",
        kind: "trace",
        content: "search()",
        traces: ["search()"],
        createdAt: Date.now(),
      },
      {
        id: "r2",
        role: "assistant",
        content: "",
        reasoning: "more thinking",
        reasoningStreaming: false,
        isStreaming: true,
        createdAt: Date.now(),
      },
      {
        id: "a1",
        role: "assistant",
        content: "final answer",
        createdAt: Date.now(),
      },
    ];

    const { container } = render(
      <ThreadMessages messages={messages} isStreaming={false} />,
    );
    const rows = Array.from(container.firstElementChild?.children ?? []);

    expect(rows).toHaveLength(2);
    expect(rows[0]).not.toHaveClass("mt-2", "mt-4", "mt-5");
    expect(rows[1]).toHaveClass("mt-4");
  });

  it("shows copy only on the last assistant slice before the next user turn", () => {
    const messages: UIMessage[] = [
      {
        id: "early",
        role: "assistant",
        content: "starting…",
        createdAt: 1,
      },
      {
        id: "t1",
        role: "tool",
        kind: "trace",
        content: "search()",
        traces: ["search()"],
        createdAt: 2,
      },
      {
        id: "late",
        role: "assistant",
        content: "final reply",
        createdAt: 3,
      },
    ];

    render(<ThreadMessages messages={messages} isStreaming={false} />);

    expect(screen.getAllByRole("button", { name: "Copy reply" })).toHaveLength(1);
    expect(screen.getByText("final reply")).toBeInTheDocument();
  });

  it("shows copy only on the second assistant when two text slices appear before user", () => {
    const messages: UIMessage[] = [
      { id: "a1", role: "assistant", content: "part one", createdAt: 1 },
      { id: "a2", role: "assistant", content: "part two", createdAt: 2 },
    ];
    render(<ThreadMessages messages={messages} isStreaming={false} />);
    expect(screen.getAllByRole("button", { name: "Copy reply" })).toHaveLength(1);
  });
});
